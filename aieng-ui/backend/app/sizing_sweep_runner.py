"""Parametric sizing-sweep orchestration — the optimize→verify loop on static FEA.

Sweeps ONE editable dimension across a set of values, **solving each variant with
the real static solver** (Gmsh + CalculiX), then ranks the variants by an
objective (min mass / displacement / stress) subject to a stress/displacement
constraint via the pure :mod:`aieng.converters.sizing_sweep` ranker.

Honesty + safety contract:
- The **baseline package is never mutated** — each variant is built and solved on a
  throwaway copy.
- **Recommend-only**: the winning value is returned for the caller to apply through
  the existing approval-gated ``cad.edit_parameter``; this module applies nothing.
- A variant that fails to build or solve is reported honestly (``solver_executed``
  False) and can never be recommended as verified.

The per-variant build+solve step is injected as ``evaluate_value`` so the
orchestration is unit-testable without Gmsh/CalculiX; the production default wires
the real package-copy edit + :func:`simulation_runner.solve_package_static`.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from aieng.converters.sizing_sweep import rank_sizing_sweep

SIZING_SWEEP_REPORT_PATH = "analysis/sizing_sweep_report.json"

# A per-variant evaluator: value → {value, metrics, solver_executed, [error]}.
EvaluateValue = Callable[[float], dict[str, Any]]


def _expand_sweep_values(
    values: list[float] | None,
    range_spec: dict[str, Any] | None,
    package_path: Path,
    feature_id: str,
    parameter_name: str,
) -> tuple[list[float], dict[str, Any] | None]:
    """Normalize an explicit value list or a {min, max, steps/step} range.

    Returns ``(clean_values, range_metadata)``. ``range_metadata`` is non-None when
    the caller supplied a range object, so the report can echo what was expanded.
    The returned values are clamped to the parameter's declared min/max, deduped,
    and capped at 25.
    """
    from .project_io import _validate_cad_parameter_edit_contract

    if values is not None and range_spec is not None:
        raise ValueError("provide either values or range, not both")

    if range_spec is not None:
        if not isinstance(range_spec, dict):
            raise ValueError("range must be an object with min/max and steps or step")
        rmin = range_spec.get("min")
        rmax = range_spec.get("max")
        if rmin is None or rmax is None:
            raise ValueError("range must include min and max")
        try:
            rmin_f = float(rmin)
            rmax_f = float(rmax)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad_input: range min/max must be numbers: {exc}") from exc
        if rmax_f < rmin_f:
            raise ValueError("bad_input: range max must be >= min")

        # Load parameter bounds (pass None so the contract check doesn't reject a
        # specific test value).
        contract = _validate_cad_parameter_edit_contract(
            package_path, feature_id, parameter_name, None
        )
        param = contract.get("parameter", {})
        param_min = param.get("min_value")
        param_max = param.get("max_value")

        # Clamp the range to declared bounds before generating values so that
        # step-based sweeps align with the feasible interval.
        if param_min is not None:
            rmin_f = max(rmin_f, float(param_min))
        if param_max is not None:
            rmax_f = min(rmax_f, float(param_max))

        if "steps" in range_spec:
            steps = int(range_spec["steps"])
            if steps < 2:
                raise ValueError("bad_input: range steps must be >= 2")
            if steps > 25:
                raise ValueError("too_many_values: range expands to more than 25 values (MVP cap)")
            step = (rmax_f - rmin_f) / (steps - 1)
            raw_values = [rmin_f + i * step for i in range(steps)]
        elif "step" in range_spec:
            step = float(range_spec["step"])
            if step == 0:
                raise ValueError("bad_input: range step cannot be zero")
            n_steps = int((rmax_f - rmin_f) / step) + 1
            if n_steps > 25:
                raise ValueError("too_many_values: range expands to more than 25 values (MVP cap)")
            raw_values = [rmin_f + i * step for i in range(n_steps)]
        else:
            raise ValueError("bad_input: range must include steps or step")

        range_clean: list[float] = []
        range_seen: set[float] = set()
        for v in raw_values:
            if v not in range_seen:
                range_clean.append(v)
                range_seen.add(v)
        if not range_clean:
            raise ValueError("bad_input: range expands to an empty value list after clamping")
        return range_clean, {
            "min": rmin_f,
            "max": rmax_f,
            "steps_or_step": range_spec.get("steps", range_spec.get("step")),
            "clamped_to_bounds": (param_min, param_max),
        }

    if values is None:
        raise ValueError("bad_input: values or range is required")
    if not isinstance(values, list):
        raise ValueError("bad_input: values must be a list of numbers")
    clean: list[float] = []
    seen: set[float] = set()
    for v in values:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv not in seen:
            clean.append(fv)
            seen.add(fv)
    if len(clean) > 25:
        raise ValueError("too_many_values: sweep is capped at 25 values per run (MVP scope)")
    if not clean:
        raise ValueError("bad_input: values must contain at least one valid number")
    return clean, None


def _total_solid_volume(topology_map: dict[str, Any]) -> float | None:
    """Sum solid-entity volumes from a topology map (mass ∝ volume for one material)."""
    if not isinstance(topology_map, dict):
        return None
    total = 0.0
    seen = False
    for ent in topology_map.get("entities", []) or []:
        if isinstance(ent, dict) and ent.get("type") == "solid":
            vol = ent.get("volume")
            if isinstance(vol, (int, float)) and not isinstance(vol, bool):
                total += float(vol)
                seen = True
    return total if seen else None


def _is_solver_abort(solve: dict[str, Any]) -> bool:
    """True when the solver ran but died without results (as opposed to never running).

    Distinguishes "CalculiX rejected this mesh" from setup gaps like a missing
    material or stale face references, which have their own explanations and
    must not be blamed on the mesh.
    """
    status = str(solve.get("status") or "")
    error = str(solve.get("error") or "")
    if status in {"no_setup", "tools_unavailable", "stale_topology_references"}:
        return False
    return "no FRD" in error or "returned code" in error or status == "solver_failed"


def make_default_evaluate_value(
    settings: Any,
    baseline_pkg: Path,
    *,
    feature_id: str,
    parameter_name: str,
    mesh_size_mm: float | None,
    timeout: int,
    density: float | None,
) -> EvaluateValue:
    """Build the production per-variant evaluator: copy → edit → solve → metrics.

    Each call copies ``baseline_pkg`` to a throwaway, applies the parametric edit
    at package level (reusing the same primitives as ``cad.edit_parameter``),
    solves the new geometry, and returns scalar metrics plus a volume-based ``mass``
    proxy. The baseline is never touched.
    """
    from . import cad_generation as _cg
    from .project_io import _validate_cad_parameter_edit_contract
    from .simulation_runner import solve_package_static
    import re

    def _evaluate(value: float) -> dict[str, Any]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="aieng_sweep_"))
        tmp_pkg = tmp_dir / "variant.aieng"
        try:
            shutil.copy2(baseline_pkg, tmp_pkg)

            # 1. Validate the edit contract (bounds, cad_parameter_name) on the copy.
            try:
                contract = _validate_cad_parameter_edit_contract(
                    tmp_pkg, feature_id, parameter_name, value
                )
            except ValueError as exc:
                return {"value": value, "metrics": {}, "solver_executed": False,
                        "error": f"invalid edit: {exc}"}
            cad_parameter_name = contract["parameter"].get("cad_parameter_name") or parameter_name

            # 2. Text-replace the UPPER_SNAKE_CASE constant (mirrors edit_build123d_parameter).
            with zipfile.ZipFile(tmp_pkg, "r") as zf:
                source_code = zf.read("geometry/source.py").decode("utf-8")
            pattern = rf'^([ \t]*)({re.escape(cad_parameter_name)})([ \t]*=[ \t]*)([0-9]+\.?[0-9]*)(.*)$'
            new_lines, found = [], False
            for line in source_code.splitlines():
                m = re.match(pattern, line)
                if m:
                    new_lines.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{m.group(5)}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                return {"value": value, "metrics": {}, "solver_executed": False,
                        "error": f"constant {cad_parameter_name} not found in source.py"}
            modified_source = "\n".join(new_lines)

            # 3. Re-execute build123d and write the new geometry into the copy.
            try:
                built = _cg._execute_build123d_cached(
                    settings, modified_source, mode="replace",
                    model_kind=contract.get("model_kind", "auto"), timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001  (DesignRuleViolation, build errors)
                return {"value": value, "metrics": {}, "solver_executed": False,
                        "error": f"build failed: {type(exc).__name__}: {exc}"}
            topo = built["topo"]
            _cg._write_cad_artifacts(
                pkg_path=tmp_pkg,
                step_bytes=built["step_bytes"],
                stl_bytes=built.get("stl_bytes") or b"",
                topology_map=topo,
                feature_graph=built["feature_graph"],
                generated_code=modified_source,
                glb_bytes=built.get("glb_bytes"),
            )

            # 4. Solve the variant geometry, rebinding baseline face references to the
            #    regenerated variant topology. The baseline package is never mutated.
            solve = solve_package_static(
                tmp_pkg,
                mesh_size_mm=mesh_size_mm,
                timeout=timeout,
                rebind_faces=True,
                baseline_package_path=baseline_pkg,
            )
            metrics = dict(solve.get("metrics") or {})

            # 5. Mass proxy from solid volume (∝ mass for a single material).
            volume = _total_solid_volume(topo)
            if volume is not None:
                metrics["volume"] = volume
                metrics["mass"] = volume * density if density else volume

            record = {
                "value": value,
                "metrics": metrics,
                "solver_executed": bool(solve.get("solver_executed")),
                "error": solve.get("error") if not solve.get("solver_executed") else None,
                "solve_status": solve.get("status"),
            }
            # A variant can fail purely because the swept dimension changed the
            # mesh: measured on the reference bracket, 8 mm aborted CalculiX
            # (code 201, no FRD) at mesh_size 4 mm and solved cleanly at 3 mm,
            # with identical geometry, BCs and load. Saying only "not solved"
            # sends the caller looking at their design instead of their mesh.
            if not record["solver_executed"] and _is_solver_abort(solve):
                record["likely_cause"] = "mesh"
                record["suggested_retry"] = {
                    "mesh_size_mm": round(float(mesh_size_mm) * 0.75, 3) if mesh_size_mm else None,
                    "reason": (
                        "The solver aborted without producing results while the geometry "
                        "built successfully — at this mesh size this variant's shape meshes "
                        "into elements CalculiX rejects. Re-run the sweep with a smaller "
                        "mesh_size_mm; the geometry itself is not necessarily the problem."
                    ),
                }
            return record
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return _evaluate


def run_sizing_sweep(
    settings: Any,
    project_id: str,
    *,
    feature_id: str,
    parameter_name: str,
    values: list[float] | None = None,
    range: dict[str, Any] | None = None,  # noqa: A002  # shadows builtin for API clarity
    objective: str = "min_mass",
    stress_limit: float | None = None,
    safety_factor: float = 1.0,
    displacement_limit: float | None = None,
    mesh_size_mm: float | None = None,
    timeout: int = 180,
    density: float | None = None,
    apply_winner: bool = False,
    evaluate_value: EvaluateValue | None = None,
) -> dict[str, Any]:
    """Sweep one parameter across ``values`` or a ``range``, solve each variant, and rank them.

    Resolves the project's package, evaluates each value via ``evaluate_value``
    (production default: copy → edit → solve), and feeds the per-variant results to
    the pure ranker. By default the baseline is never modified; set
    ``apply_winner=True`` to apply the recommended value through the existing
    approval-gated ``cad.edit_parameter`` path.
    """
    from .project_io import get_project, resolve_project_path

    try:
        project = get_project(settings, project_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "code": "project_not_found", "message": str(exc)}
    pkg = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if pkg is None or not Path(pkg).exists():
        return {"status": "error", "code": "no_package", "message": ".aieng package not found"}

    try:
        clean_values, range_metadata = _expand_sweep_values(
            values, range, Path(pkg), feature_id, parameter_name
        )
    except ValueError as exc:
        msg = str(exc)
        code = "bad_input"
        if msg.startswith("too_many_values:"):
            code = "too_many_values"
            msg = msg.split(":", 1)[1].strip()
        elif msg.startswith("bad_input:"):
            msg = msg.split(":", 1)[1].strip()
        return {"status": "error", "code": code, "message": msg}

    if evaluate_value is None:
        evaluate_value = make_default_evaluate_value(
            settings, Path(pkg), feature_id=feature_id, parameter_name=parameter_name,
            mesh_size_mm=mesh_size_mm, timeout=timeout, density=density,
        )

    variants = [evaluate_value(v) for v in clean_values]

    report = rank_sizing_sweep(
        variants,
        objective=objective,
        stress_limit=stress_limit,
        safety_factor=safety_factor,
        displacement_limit=displacement_limit,
        parameter_name=parameter_name,
    )

    # The ranker rebuilds each variant from the metrics it scores, so the
    # "why did this one die" fields the evaluator attached do not survive it.
    # Carry them across by value — without them a mesh-killed variant reads as a
    # bad design (measured: 8 mm aborted ccx at mesh 4 mm, solved at 3 mm).
    hints = {
        v.get("value"): v for v in variants
        if isinstance(v, dict) and v.get("likely_cause")
    }
    if hints:
        for ranked in report.get("variants") or []:
            source = hints.get(ranked.get("value")) if isinstance(ranked, dict) else None
            if source:
                ranked["likely_cause"] = source["likely_cause"]
                ranked["suggested_retry"] = source.get("suggested_retry")
    report.update({
        "status": "ok",
        "tool": "opt.sizing_sweep",
        "project_id": project_id,
        "feature_id": feature_id,
        "swept_values": clean_values,
        "range": range_metadata,
        "apply_winner_requested": apply_winner,
    })

    # Optional: apply the winning value through the audited edit path.
    if apply_winner and report.get("recommended") is not None:
        from .cad_generation import edit_build123d_parameter

        winner = report["recommended"]["value"]
        edit_result = edit_build123d_parameter(
            settings,
            project_id,
            feature_id,
            parameter_name,
            winner,
            timeout=timeout,
            response_detail="summary",
            confirm_scope_risk=True,
        )
        report["apply_status"] = edit_result.get("status", "error")
        report["applied_value"] = winner
        if edit_result.get("status") == "ok":
            report["regression_diff"] = edit_result.get("regression_diff")
            report["baseline_modified"] = True
            report["next_step"] = (
                f"Applied recommended value {winner} to baseline via cad.edit_parameter. "
                "Review regression_diff and viewer thumbnail."
            )
        else:
            report["baseline_modified"] = False
            report["apply_error"] = edit_result.get("message") or edit_result.get("error")
            report["next_step"] = (
                f"Recommended value {winner} could not be applied: {report['apply_error']}. "
                "Apply it manually with cad.edit_parameter."
            )
    else:
        report["baseline_modified"] = False
        report["next_step"] = (
            "Apply the recommended value with cad.edit_parameter (approval-gated). "
            "This sweep modified nothing."
            if report.get("recommended") is not None
            else "No value recommended — see recommendation_reason."
        )

    try:
        from .project_io import write_json_artifact_to_package
        write_json_artifact_to_package(pkg, SIZING_SWEEP_REPORT_PATH, report)
        report["artifact_path"] = SIZING_SWEEP_REPORT_PATH
    except Exception as exc:  # noqa: BLE001
        report["artifact_write_error"] = str(exc)

    return report
