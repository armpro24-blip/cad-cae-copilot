"""Parametric sizing-sweep orchestration — the optimize→verify loop on static FEA.

Sweeps ONE editable dimension across a set of values, **solving each variant with
the real static solver** (Gmsh + CalculiX), then ranks the variants by an
objective (min mass / displacement / stress) subject to a stress/displacement
constraint via the pure :mod:`aieng.converters.sizing_sweep` ranker.

Honesty + safety contract:
- The **baseline package is never mutated** — each variant is built and solved on a
  throwaway copy.
- **Recommend-only** by default: ``apply_winner`` applies the winner through the
  audited ``cad.edit_parameter`` path (snapshot + audit log + stale marking).
- A variant that fails to build or solve is reported honestly (``solver_executed``
  False) and can never be recommended as verified; a variant that passes only on
  a mesh whose accuracy band is ``unreliable`` is not recommendable either.
- The constraint boundary between grid points is reported as an interpolated
  ESTIMATE; only ``refine_boundary`` turns it into a solved variant.

The per-variant build+solve step is injected as ``evaluate_value`` so the
orchestration is unit-testable without Gmsh/CalculiX; the production default wires
the real package-copy edit + :func:`simulation_runner.solve_package_static`.
"""
from __future__ import annotations

import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from aieng.converters.sizing_sweep import estimate_constraint_boundary, rank_sizing_sweep

SIZING_SWEEP_REPORT_PATH = "analysis/sizing_sweep_report.json"
MAX_SWEEP_VALUES = 25

# A per-variant evaluator: value → {value, metrics, solver_executed, [error]}.
EvaluateValue = Callable[[float], dict[str, Any]]


def _clean_value(value: float) -> float:
    """Drop float noise from generated grid values (4.000000000000001 → 4.0)."""
    return round(float(value), 9)


def _expand_sweep_values(
    values: list[float] | None,
    range_spec: dict[str, Any] | None,
    package_path: Path,
    feature_id: str,
    parameter_name: str,
) -> tuple[list[float], dict[str, Any] | None]:
    """Normalize an explicit value list or a {min, max, steps/step} range.

    Returns ``(clean_values, range_metadata)``. ``range_metadata`` is non-None when
    the caller supplied a range object; it echoes the requested interval, the
    interval actually swept, and whether clamping to the parameter's declared
    min/max changed it. Values are deduped and capped at ``MAX_SWEEP_VALUES``.
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
            requested = (float(rmin), float(rmax))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad_input: range min/max must be numbers: {exc}") from exc
        if requested[1] < requested[0]:
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
        rmin_f, rmax_f = requested
        if param_min is not None:
            rmin_f = max(rmin_f, float(param_min))
        if param_max is not None:
            rmax_f = min(rmax_f, float(param_max))
        if rmax_f < rmin_f:
            # The whole requested range lies outside the declared bounds. Sweeping
            # the clamped "range" would walk backwards across values nobody asked for.
            raise ValueError(
                f"bad_input: range [{requested[0]:g}, {requested[1]:g}] lies outside the "
                f"parameter's declared bounds [{param_min}, {param_max}]"
            )

        if "steps" in range_spec:
            steps = int(range_spec["steps"])
            if steps < 2:
                raise ValueError("bad_input: range steps must be >= 2")
            if steps > MAX_SWEEP_VALUES:
                raise ValueError("too_many_values: range expands to more than 25 values (MVP cap)")
            step = (rmax_f - rmin_f) / (steps - 1)
            raw_values = [rmin_f + i * step for i in range(steps)]
        elif "step" in range_spec:
            step = float(range_spec["step"])
            if not step > 0:
                raise ValueError("bad_input: range step must be > 0")
            n_steps = int((rmax_f - rmin_f) / step + 1e-9) + 1
            if n_steps > MAX_SWEEP_VALUES:
                raise ValueError("too_many_values: range expands to more than 25 values (MVP cap)")
            raw_values = [rmin_f + i * step for i in range(n_steps)]
        else:
            raise ValueError("bad_input: range must include steps or step")

        range_clean = list(dict.fromkeys(_clean_value(v) for v in raw_values))
        clamped = (rmin_f, rmax_f) != requested
        return range_clean, {
            "min": rmin_f,
            "max": rmax_f,
            "requested": {"min": requested[0], "max": requested[1]},
            "clamped": clamped,
            "steps_or_step": range_spec.get("steps", range_spec.get("step")),
            "declared_bounds": {"min": param_min, "max": param_max},
            **({"note": (
                f"requested range [{requested[0]:g}, {requested[1]:g}] was clamped to the "
                f"declared bounds; swept [{rmin_f:g}, {rmax_f:g}]"
            )} if clamped else {}),
        }

    if values is None:
        raise ValueError("bad_input: values or range is required")
    if not isinstance(values, list):
        raise ValueError("bad_input: values must be a list of numbers")
    clean: list[float] = []
    for v in values:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv) and fv not in clean:
            clean.append(fv)
    if len(clean) > MAX_SWEEP_VALUES:
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


def mass_metrics(topology_map: dict[str, Any], density_kg_m3: float | None) -> dict[str, Any]:
    """``volume`` (mm³) and the ``mass`` the min_mass objective ranks on.

    One definition for the sweep and the DOE study, which used to disagree: the
    DOE read a ``volume_mm3`` solver metric that no solver path writes, so its
    default objective had no mass to rank and could never recommend anything,
    and the two multiplied ``density`` by factors 1e9 apart.

    With ``density_kg_m3`` the mass is in kg (mm³ × 1e-9 m³/mm³ × kg/m³);
    without it, mass is the volume in mm³ — the same ranking for one material.
    """
    volume = _total_solid_volume(topology_map)
    if volume is None:
        return {}
    if density_kg_m3:
        return {"volume": volume, "mass": volume * 1e-9 * float(density_kg_m3), "mass_unit": "kg"}
    return {"volume": volume, "mass": volume, "mass_unit": "mm3 (volume; no density given)"}


def replace_constants(source_code: str, replacements: dict[str, float]) -> tuple[str, list[str]]:
    """Set UPPER_SNAKE_CASE constants in build123d source; return (source, not_found).

    Mirrors ``edit_build123d_parameter``'s text edit. A constant that is not
    found is RETURNED, not ignored: the DOE path used to drop it silently and
    solve the baseline value labelled as the requested one.
    """
    patterns = {
        name: re.compile(rf"^([ \t]*)({re.escape(name)})([ \t]*=[ \t]*)([0-9]+\.?[0-9]*)(.*)$")
        for name in replacements
    }
    found: set[str] = set()
    new_lines: list[str] = []
    for line in source_code.splitlines():
        for name, pattern in patterns.items():
            if name in found:
                continue
            m = pattern.match(line)
            if m:
                line = f"{m.group(1)}{m.group(2)}{m.group(3)}{replacements[name]}{m.group(5)}"
                found.add(name)
                break
        new_lines.append(line)
    return "\n".join(new_lines), [n for n in replacements if n not in found]


def _is_solver_abort(solve: dict[str, Any]) -> bool:
    """True when the solver ran but died without results (as opposed to never running).

    Distinguishes "CalculiX rejected this mesh" from setup gaps like a missing
    material or stale face references, which have their own explanations and
    must not be blamed on the mesh. A deck CalculiX could not even READ (e.g. an
    undefined node set) is a setup gap too, however it was reported.
    """
    status = str(solve.get("status") or "")
    error = str(solve.get("error") or "")
    if status in {"no_setup", "tools_unavailable", "stale_topology_references"}:
        return False
    if solve.get("failure_kind") == "solver_crash_undiagnosed":
        return False  # a crash with nothing in the log says nothing about the mesh
    diagnosis = " ".join(str(d) for d in solve.get("solver_diagnosis") or []).upper()
    if "ERROR READING" in diagnosis or "NOT YET BEEN DEFINED" in diagnosis:
        return False
    return "no FRD" in error or "returned code" in error or status == "solver_failed"


# What CalculiX says when the MESH is what it rejects. Anything else it reports
# is not evidence about the mesh, and "use a finer mesh" is then a guess.
# `solver_diagnosis` holds `_diagnose_solver_log`'s rewritten messages, not the
# raw log: "ERROR IN FACE" / "INCONSISTENT ORIENTATION" arrive as "Mesh has
# inconsistently oriented faces", so match that wording too.
_MESH_EVIDENCE = (
    "JACOBIAN", "DISTORT", "NEGATIVE VOLUME", "INCONSISTENT ORIENTATION",
    "ERROR IN FACE", "INCONSISTENTLY ORIENTED",
)
# ccx's own array-sizing errors ("*ERROR in bounadd: increase nboun_"): its
# counting pass under-sized an internal array. Measured on ccx 2.23 / Windows,
# the identical deck also solved on other runs (#549).
_INTERNAL_LIMIT = re.compile(r"INCREASE\s+N\w*_")


def _abort_cause(solve: dict[str, Any]) -> str:
    """``mesh`` / ``solver_internal_limit`` / ``solver_error``, from what ccx reported."""
    diagnosis = " ".join(str(d) for d in solve.get("solver_diagnosis") or []).upper()
    if any(token in diagnosis for token in _MESH_EVIDENCE):
        return "mesh"
    if _INTERNAL_LIMIT.search(diagnosis):
        return "solver_internal_limit"
    return "solver_error"


def record_from_solve(
    solve: dict[str, Any], topology_map: dict[str, Any], density: float | None,
    mesh_size_mm: float | None,
) -> dict[str, Any]:
    """Variant fields shared by the sweep and the DOE study (no ``value`` key)."""
    metrics = dict(solve.get("metrics") or {})
    metrics.update(mass_metrics(topology_map, density))
    record: dict[str, Any] = {
        "metrics": metrics,
        "solver_executed": bool(solve.get("solver_executed")),
        "error": solve.get("error") if not solve.get("solver_executed") else None,
        "solve_status": solve.get("status"),
        "mesh_accuracy": solve.get("mesh_accuracy"),
        # Set when CalculiX crashed silently and the same deck was run again;
        # a success after a retry is an ordinary result, but it is labelled.
        "solver_retry": solve.get("solver_retry"),
    }
    if not record["solver_executed"] and solve.get("failure_kind") == "solver_crash_undiagnosed":
        record["likely_cause"] = "solver_crash"
        record["suggested_retry"] = {
            "mesh_size_mm": mesh_size_mm,
            "reason": (
                "CalculiX exited without a result and without reporting any error"
                + (" (also on its automatic retry)" if solve.get("solver_retry") else "")
                + ". That is not evidence about the mesh or the design: measured with "
                "ccx 2.23, identical decks crash intermittently and solve on a re-run. "
                "Re-run this variant at the SAME mesh size."
            ),
        }
    # A variant can fail because the swept dimension changed the mesh, and
    # saying only "not solved" sends the caller looking at their design. But the
    # cause is read from what CalculiX REPORTED: every abort used to be blamed on
    # the mesh, including ccx's internal `increase nboun_` sizing error.
    elif not record["solver_executed"] and _is_solver_abort(solve):
        cause = _abort_cause(solve)
        record["likely_cause"] = cause
        if cause == "mesh":
            record["suggested_retry"] = {
                "mesh_size_mm": round(float(mesh_size_mm) * 0.75, 3) if mesh_size_mm else None,
                "reason": (
                    "CalculiX rejected elements of this variant's mesh (see "
                    "solver_diagnosis) while the geometry built successfully. Re-run "
                    "with a smaller mesh_size_mm; the design itself is not necessarily "
                    "the problem."
                ),
            }
        elif cause == "solver_internal_limit":
            record["suggested_retry"] = {
                "mesh_size_mm": mesh_size_mm,
                "reason": (
                    "CalculiX hit one of its own internal array limits (see "
                    "solver_diagnosis). That is not evidence about the mesh or the "
                    "design; it has been observed on ccx 2.23 for other decks that then "
                    "solved unchanged on a re-run (#549). Re-run at the SAME mesh size; "
                    "if it reproduces, it is a solver issue."
                ),
            }
        else:
            record["suggested_retry"] = {
                "mesh_size_mm": mesh_size_mm,
                "reason": (
                    "CalculiX aborted with the error in solver_diagnosis, which does "
                    "not identify the mesh. Read that error before changing the mesh "
                    "or the design."
                ),
            }
    if solve.get("solver_diagnosis"):
        record["solver_diagnosis"] = solve["solver_diagnosis"]
    return record


def validate_density(density: Any) -> float | None:
    """``None`` (rank by volume) or a positive finite kg/m^3; anything else raises.

    A negative density turns min_mass into max_mass — the ranker would then
    recommend the HEAVIEST feasible design, stamped safe_to_apply.
    """
    if density is None:
        return None
    if isinstance(density, bool) or not isinstance(density, (int, float)):
        raise ValueError(f"bad_input: density must be a number in kg/m^3, got {density!r}")
    if not math.isfinite(float(density)) or density <= 0:
        raise ValueError(f"bad_input: density must be a positive finite number in kg/m^3, got {density!r}")
    return float(density)


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
    solves the new geometry, and returns scalar metrics plus a volume-based ``mass``.
    The baseline is never touched.
    """
    from . import cad_generation as _cg
    from .project_io import _validate_cad_parameter_edit_contract
    from .simulation_runner import solve_package_static

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
            modified_source, missing = replace_constants(source_code, {cad_parameter_name: value})
            if missing:
                return {"value": value, "metrics": {}, "solver_executed": False,
                        "error": f"constant {cad_parameter_name} not found in source.py"}

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
            return {"value": value, **record_from_solve(solve, topo, density, mesh_size_mm)}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return _evaluate


def _refinement_value(estimate: dict[str, Any]) -> float:
    """The boundary estimate rounded TOWARD the feasible end, at 1% of the bracket."""
    bracket = estimate["bracket"]
    lo, hi = sorted((bracket["feasible_value"], bracket["infeasible_value"]))
    resolution = 10.0 ** (math.floor(math.log10(hi - lo)) - 2)
    toward_feasible_up = bracket["feasible_value"] > bracket["infeasible_value"]
    steps = estimate["value"] / resolution
    rounded = (math.ceil(steps) if toward_feasible_up else math.floor(steps)) * resolution
    return _clean_value(rounded)


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
    refine_boundary: bool = False,
    evaluate_value: EvaluateValue | None = None,
) -> dict[str, Any]:
    """Sweep one parameter across ``values`` or a ``range``, solve each variant, and rank them.

    Resolves the project's package, evaluates each value via ``evaluate_value``
    (production default: copy → edit → solve), and feeds the per-variant results to
    the pure ranker. The report carries a ``boundary_estimate`` — where between the
    recommended value and its infeasible neighbour the active constraint is met,
    interpolated, not solved. ``refine_boundary=True`` spends ONE more solve at
    that estimate (rounded toward the feasible side); the result is ranked like
    any other variant, so it is recommended only if it actually solves feasible.
    By default the baseline is never modified; ``apply_winner=True`` applies a
    ``safe_to_apply`` recommendation through the audited ``cad.edit_parameter``.
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
        # Validate the ranking inputs BEFORE paying for N solves.
        density = validate_density(density)
        rank_sizing_sweep([], objective=objective, stress_limit=stress_limit,
                          safety_factor=safety_factor, displacement_limit=displacement_limit)
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

    def _rank(vs: list[dict[str, Any]]) -> dict[str, Any]:
        return rank_sizing_sweep(
            vs,
            objective=objective,
            stress_limit=stress_limit,
            safety_factor=safety_factor,
            displacement_limit=displacement_limit,
            parameter_name=parameter_name,
        )

    report = _rank(variants)
    boundary = estimate_constraint_boundary(report)
    refinement: dict[str, Any] | None = None
    if refine_boundary and boundary.get("status") == "estimated":
        refine_value = _refinement_value(boundary)
        if refine_value in clean_values:
            refinement = {"value": refine_value, "status": "skipped",
                          "reason": "the rounded boundary value was already swept"}
        else:
            refined = {**evaluate_value(refine_value), "origin": "boundary_refinement"}
            variants.append(refined)
            report = _rank(variants)
            outcome = next(v for v in report["variants"] if v.get("value") == refine_value)
            refinement = {
                "value": refine_value,
                "status": outcome["status"],
                "reason": outcome.get("reason"),
                "recommended": report.get("recommended") is not None
                and report["recommended"].get("value") == refine_value,
            }
    elif refine_boundary:
        refinement = {"status": "skipped", "reason": boundary.get("reason")}

    warnings: list[str] = []
    if objective == "min_mass" and stress_limit is None and displacement_limit is None:
        warnings.append(
            "min_mass with no stress_limit or displacement_limit is unconstrained: it "
            "recommends the lightest value swept, whatever its stress"
        )
    report.update({
        "status": "ok",
        "tool": "opt.sizing_sweep",
        "project_id": project_id,
        "feature_id": feature_id,
        "swept_values": clean_values,
        "range": range_metadata,
        "boundary_estimate": boundary,
        "refinement": refinement,
        "warnings": warnings,
        "apply_winner_requested": apply_winner,
    })

    # Optional: apply the winning value through the audited edit path.
    if apply_winner and report.get("recommended") is not None and report.get("safe_to_apply"):
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
            # The sweep solved the rippled geometry for every variant, so a shared
            # constant's reach is what was evaluated and approved as one operation.
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
            if report.get("recommended") is not None and report.get("safe_to_apply")
            else "No value recommended — see recommendation_reason."
        )

    try:
        from .project_io import write_json_artifact_to_package
        write_json_artifact_to_package(pkg, SIZING_SWEEP_REPORT_PATH, report)
        report["artifact_path"] = SIZING_SWEEP_REPORT_PATH
    except Exception as exc:  # noqa: BLE001
        report["artifact_write_error"] = str(exc)

    return report
