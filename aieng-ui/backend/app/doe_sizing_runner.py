"""Multi-parameter DOE sizing study runner.

Builds on opt.sizing_sweep: instead of varying one dimension, this explores a
small design space of 2+ editable parameters jointly. Each design point is
built and solved with the real static solver (via solve_package_static); the
existing ranker scores feasibility and recommends the best point. Baseline is
never mutated.

Design generation:
- ``full_factorial`` — the Cartesian product of each parameter's value list.
- ``lhs`` — a Latin hypercube (McKay, Beckman & Conover 1979, Technometrics
  21:239-245): each parameter's interval is cut into ``budget`` equal strata,
  one point is drawn uniformly inside each, and the strata are paired by an
  independent random permutation per parameter. Every 1-D projection therefore
  has exactly one point per stratum. Seeded; the seed is reported.
"""
from __future__ import annotations

import itertools
import math
import random
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from aieng.converters.sizing_sweep import rank_sizing_sweep

from .sizing_sweep_runner import record_from_solve, replace_constants

DesignPoint = dict[str, float]
EvaluateDesignPoint = Callable[[DesignPoint], dict[str, Any]]

MAX_DOE_BUDGET = 64


def _safe_param_key(feature_id: str, parameter_name: str) -> str:
    return f"{feature_id}#{parameter_name}"


def _parse_param_key(key: str) -> tuple[str, str]:
    feature_id, parameter_name = key.split("#", 1)
    return feature_id, parameter_name


def _spec_ids(spec: dict[str, Any]) -> tuple[str, str]:
    return (
        str(spec.get("featureId") or spec.get("feature_id") or ""),
        str(spec.get("parameterName") or spec.get("parameter_name") or ""),
    )


def _declared_bounds(package_path: Path, feature_id: str, parameter_name: str) -> tuple[Any, Any]:
    from .project_io import _validate_cad_parameter_edit_contract

    contract = _validate_cad_parameter_edit_contract(
        package_path, feature_id, parameter_name, None
    )
    param = contract.get("parameter", {})
    return param.get("min_value"), param.get("max_value")


def _clamped_range(
    spec: dict[str, Any], package_path: Path
) -> tuple[float, float]:
    """A parameter's ``range`` {min, max}, clamped to its declared bounds."""
    feature_id, parameter_name = _spec_ids(spec)
    range_spec = spec.get("range")
    if not isinstance(range_spec, dict):
        raise ValueError(f"parameter {feature_id}/{parameter_name}: range must be an object")
    rmin = range_spec.get("min")
    rmax = range_spec.get("max")
    if rmin is None or rmax is None:
        raise ValueError(f"parameter {feature_id}/{parameter_name}: range needs min and max")
    try:
        rmin_f = float(rmin)
        rmax_f = float(rmax)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"parameter {feature_id}/{parameter_name}: min/max must be numbers") from exc
    if rmax_f < rmin_f:
        raise ValueError(f"parameter {feature_id}/{parameter_name}: max must be >= min")
    param_min, param_max = _declared_bounds(package_path, feature_id, parameter_name)
    if param_min is not None:
        rmin_f = max(rmin_f, float(param_min))
    if param_max is not None:
        rmax_f = min(rmax_f, float(param_max))
    if rmax_f < rmin_f:
        raise ValueError(
            f"parameter {feature_id}/{parameter_name}: range lies outside the declared "
            f"bounds [{param_min}, {param_max}]"
        )
    return rmin_f, rmax_f


def _expand_parameter_values(
    spec: dict[str, Any],
    package_path: Path,
) -> list[float]:
    """Expand one parameter spec to a list of values (values or range)."""
    feature_id, parameter_name = _spec_ids(spec)
    values = spec.get("values")
    range_spec = spec.get("range")
    if values is None and range_spec is None:
        raise ValueError(f"parameter {feature_id}/{parameter_name}: provide values or range")

    if values is not None:
        _declared_bounds(package_path, feature_id, parameter_name)  # the parameter must exist
        if not isinstance(values, list):
            raise ValueError(f"parameter {feature_id}/{parameter_name}: values must be a list")
        clean: list[float] = []
        for v in values:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv) and fv not in clean:
                clean.append(fv)
        if len(clean) > 25:
            raise ValueError(f"parameter {feature_id}/{parameter_name}: capped at 25 values")
        if not clean:
            raise ValueError(f"parameter {feature_id}/{parameter_name}: values list is empty")
        return clean

    rmin_f, rmax_f = _clamped_range(spec, package_path)
    if "steps" in range_spec:
        steps = int(range_spec["steps"])
        if steps < 2 or steps > 25:
            raise ValueError(f"parameter {feature_id}/{parameter_name}: steps must be 2..25")
        step = (rmax_f - rmin_f) / (steps - 1)
        raw = [rmin_f + i * step for i in range(steps)]
    elif "step" in range_spec:
        step = float(range_spec["step"])
        if not step > 0:
            raise ValueError(f"parameter {feature_id}/{parameter_name}: step must be > 0")
        n_steps = int((rmax_f - rmin_f) / step + 1e-9) + 1
        if n_steps > 25:
            raise ValueError(f"parameter {feature_id}/{parameter_name}: range expands to >25 values")
        raw = [rmin_f + i * step for i in range(n_steps)]
    else:
        raise ValueError(f"parameter {feature_id}/{parameter_name}: range needs steps or step")
    return list(dict.fromkeys(round(v, 9) for v in raw))


def _latin_hypercube(
    bounds: dict[str, tuple[float, float]], n: int, rng: random.Random
) -> list[DesignPoint]:
    """``n`` points, exactly one in each of ``n`` equal strata of every parameter."""
    columns: dict[str, list[float]] = {}
    for key, (lo, hi) in bounds.items():
        column = [lo + (hi - lo) * (i + rng.random()) / n for i in range(n)]
        rng.shuffle(column)
        columns[key] = [round(v, 6) for v in column]
    return [{key: columns[key][i] for key in bounds} for i in range(n)]


def _generate_design_points(
    parameter_values: dict[str, list[float]] | None,
    method: str,
    budget: int,
    seed: int | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> list[DesignPoint]:
    """Full factorial over ``parameter_values``, or an LHS of ``budget`` points over ``bounds``."""
    if not 2 <= int(budget) <= MAX_DOE_BUDGET:
        raise ValueError(f"budget must be between 2 and {MAX_DOE_BUDGET}, got {budget}")

    if method == "full_factorial":
        keys = list((parameter_values or {}).keys())
        value_lists = [parameter_values[k] for k in keys]
        total = math.prod(len(v) for v in value_lists)
        if total > budget:
            raise ValueError(
                f"full_factorial would produce {total} points; budget is {budget}. "
                "Increase budget or use lhs."
            )
        return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]

    if method == "lhs":
        if not bounds:
            raise ValueError("lhs needs a {min, max} range for every parameter")
        return _latin_hypercube(bounds, int(budget), random.Random(seed))

    raise ValueError(f"unknown DOE method: {method}")


def _summarize_point(point: DesignPoint) -> str:
    return ", ".join(f"{k.split('#', 1)[1]}={v:g}" for k, v in point.items())


def make_default_evaluate_design_point(
    settings: Any,
    baseline_pkg: Path,
    parameter_specs: list[dict[str, Any]],
    *,
    mesh_size_mm: float | None,
    timeout: int,
    density: float | None,
) -> EvaluateDesignPoint:
    """Return an evaluator that builds and solves one multi-parameter design point."""
    from . import cad_generation as _cg
    from .project_io import _validate_cad_parameter_edit_contract
    from .simulation_runner import solve_package_static

    # Pre-compute cad_parameter_name lookups by validating against the baseline.
    contract_map: dict[str, dict[str, Any]] = {}
    for spec in parameter_specs:
        feature_id, parameter_name = _spec_ids(spec)
        contract_map[_safe_param_key(feature_id, parameter_name)] = (
            _validate_cad_parameter_edit_contract(baseline_pkg, feature_id, parameter_name, None)
        )

    def _failed(point: DesignPoint, error: str) -> dict[str, Any]:
        return {
            "value": _summarize_point(point),
            "parameters": dict(point),
            "metrics": {},
            "solver_executed": False,
            "error": error,
        }

    def _evaluate(point: DesignPoint) -> dict[str, Any]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aieng_doe_"))
        tmp_pkg = tmp_dir / "variant.aieng"
        try:
            shutil.copy2(baseline_pkg, tmp_pkg)

            replacements: dict[str, float] = {}
            for key, value in point.items():
                feature_id, parameter_name = _parse_param_key(key)
                contract = contract_map[key]
                try:
                    _validate_cad_parameter_edit_contract(
                        tmp_pkg, feature_id, parameter_name, value
                    )
                except ValueError as exc:
                    return _failed(point, f"invalid edit: {exc}")
                cad_parameter_name = contract["parameter"].get("cad_parameter_name") or parameter_name
                if cad_parameter_name in replacements:
                    return _failed(point, "duplicate cad parameter names across parameters")
                replacements[cad_parameter_name] = value

            with zipfile.ZipFile(tmp_pkg, "r") as zf:
                source_code = zf.read("geometry/source.py").decode("utf-8")
            modified_source, missing = replace_constants(source_code, replacements)
            if missing:
                return _failed(point, f"constant(s) not found in source.py: {', '.join(missing)}")

            # Use the first contract's model_kind; mixed kinds are unlikely.
            first_contract = next(iter(contract_map.values()))
            try:
                built = _cg._execute_build123d_cached(
                    settings,
                    modified_source,
                    mode="replace",
                    model_kind=first_contract.get("model_kind", "auto"),
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001
                return _failed(point, f"build failed: {type(exc).__name__}: {exc}")

            _cg._write_cad_artifacts(
                pkg_path=tmp_pkg,
                step_bytes=built["step_bytes"],
                stl_bytes=built.get("stl_bytes") or b"",
                topology_map=built["topo"],
                feature_graph=built["feature_graph"],
                generated_code=modified_source,
                glb_bytes=built.get("glb_bytes"),
            )

            solve = solve_package_static(
                tmp_pkg,
                mesh_size_mm=mesh_size_mm,
                timeout=timeout,
                rebind_faces=True,
                baseline_package_path=baseline_pkg,
            )
            return {
                "value": _summarize_point(point),
                "parameters": dict(point),
                **record_from_solve(solve, built["topo"], density, mesh_size_mm),
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return _evaluate


def run_doe_sizing_study(
    settings: Any,
    project_id: str,
    *,
    parameters: list[dict[str, Any]],
    method: str = "full_factorial",
    budget: int = 64,
    objective: str = "min_mass",
    stress_limit: float | None = None,
    safety_factor: float = 1.0,
    displacement_limit: float | None = None,
    mesh_size_mm: float | None = None,
    timeout: int = 180,
    density: float | None = None,
    seed: int | None = None,
    evaluate_design_point: EvaluateDesignPoint | None = None,
) -> dict[str, Any]:
    """Run a multi-parameter DOE sizing study.

    Each design point is built and solved with the real static solver. The
    baseline package is never mutated. Returns a ranker report enriched with the
    per-point solve-status table and the parameter names. ``lhs`` samples each
    parameter's ``range`` interval (``steps``/``step`` are not used) with
    ``budget`` points; an explicit ``values`` list is refused for ``lhs``
    because a discrete level set is not a Latin hypercube.
    """
    from .project_io import get_project, resolve_project_path

    if not parameters:
        return {"status": "error", "code": "bad_input", "message": "parameters list is required"}
    if len(parameters) < 2:
        return {
            "status": "error",
            "code": "bad_input",
            "message": "DOE study requires at least 2 parameters; use opt.sizing_sweep for one.",
        }

    try:
        project = get_project(settings, project_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "code": "project_not_found", "message": str(exc)}
    pkg = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if pkg is None or not Path(pkg).exists():
        return {"status": "error", "code": "no_package", "message": ".aieng package not found"}

    if method == "lhs" and seed is None:
        # Reproducible by construction: an unseeded run reports the seed it drew.
        seed = random.SystemRandom().randrange(2**31)

    try:
        rank_sizing_sweep([], objective=objective, stress_limit=stress_limit,
                          safety_factor=safety_factor, displacement_limit=displacement_limit)
        parameter_values: dict[str, list[float]] = {}
        bounds: dict[str, tuple[float, float]] = {}
        for spec in parameters:
            feature_id, parameter_name = _spec_ids(spec)
            if not feature_id or not parameter_name:
                return {"status": "error", "code": "bad_input", "message": "each parameter needs featureId and parameterName"}
            key = _safe_param_key(feature_id, parameter_name)
            if method == "lhs":
                if spec.get("range") is None:
                    raise ValueError(
                        f"parameter {feature_id}/{parameter_name}: lhs samples a continuous "
                        "interval — give range {min, max} (an explicit values list is not a "
                        "Latin hypercube; use full_factorial for discrete levels)"
                    )
                bounds[key] = _clamped_range(spec, Path(pkg))
            else:
                parameter_values[key] = _expand_parameter_values(spec, Path(pkg))
        design_points = _generate_design_points(
            parameter_values, method, budget, seed=seed, bounds=bounds
        )
    except ValueError as exc:
        return {"status": "error", "code": "bad_input", "message": str(exc)}

    if evaluate_design_point is None:
        evaluate_design_point = make_default_evaluate_design_point(
            settings,
            Path(pkg),
            parameters,
            mesh_size_mm=mesh_size_mm,
            timeout=timeout,
            density=density,
        )

    variants = [evaluate_design_point(point) for point in design_points]

    report = rank_sizing_sweep(
        variants,
        objective=objective,
        stress_limit=stress_limit,
        safety_factor=safety_factor,
        displacement_limit=displacement_limit,
        parameter_name="design_point",
    )
    warnings: list[str] = []
    if objective == "min_mass" and stress_limit is None and displacement_limit is None:
        warnings.append(
            "min_mass with no stress_limit or displacement_limit is unconstrained: it "
            "recommends the lightest design point, whatever its stress"
        )
    report.update({
        "status": "ok",
        "tool": "opt.doe_sizing_study",
        "project_id": project_id,
        "method": method,
        "budget": budget,
        "seed": seed if method == "lhs" else None,
        "design_points_count": len(design_points),
        "parameters": [
            {
                "feature_id": _spec_ids(spec)[0],
                "parameter_name": _spec_ids(spec)[1],
                **({"interval": list(bounds[_safe_param_key(*_spec_ids(spec))])}
                   if method == "lhs" else
                   {"values": parameter_values[_safe_param_key(*_spec_ids(spec))]}),
            }
            for spec in parameters
        ],
        "warnings": warnings,
        "next_step": (
            "Apply the recommended design point through approval-gated cad.edit_parameter "
            "calls (one per parameter). This study modified nothing."
            if report.get("recommended") is not None and report.get("safe_to_apply")
            else "No feasible design point found — see recommendation_reason."
        ),
    })
    return report
