"""Mesh-convergence orchestration — solve a project at several mesh sizes, then GCI.

Sweeps the Gmsh target element size across a refinement sequence, solving the
project's CURRENT static geometry at each size with :func:`solve_package_static`
(read-only on the package — no copies needed), and runs the pure ASME V&V-20
analyzer (:mod:`aieng.converters.mesh_convergence`) per metric to report whether
``max_von_mises_stress`` / ``max_displacement`` are mesh-converged.

The per-size solve is injected as ``evaluate_value`` so the orchestration is
unit-testable without Gmsh/CalculiX. Mutates nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from aieng.converters.mesh_convergence import (
    DEFAULT_CONVERGED_GCI_PERCENT,
    DEFAULT_GCI_SAFETY_FACTOR,
    analyze_mesh_convergence,
)

MESH_CONVERGENCE_REPORT_PATH = "analysis/mesh_convergence_report.json"

# size → {size, metrics:{...}, solver_executed, [node_count], [error]}
EvaluateSize = Callable[[float], dict[str, Any]]

_DEFAULT_METRICS = ("max_von_mises_stress", "max_displacement")
_MAX_LEVELS = 6  # each level is a full solve; fine meshes are expensive


def make_default_evaluate_size(
    baseline_pkg: Path, *, timeout: int
) -> EvaluateSize:
    """Production per-size evaluator: solve the package at a given mesh size.

    ``solve_package_static`` is read-only on the package (it works in a temp dir),
    so every mesh size solves the same baseline geometry without mutating it.

    ``rebind_faces`` against the package ITSELF is deliberate. A convergence
    study changes only the mesh density — the geometry is byte-identical — so
    the face references are still correct even when the recorded
    ``topology_hash`` is stale (any CAD rebuild after CAE setup marks it so).
    Without this the whole study failed with "CAE face references do not match
    current topology" on exactly the normal workflow (build CAD, set up CAE,
    rebuild CAD once), which made the tool that answers "can I trust this
    number" unusable. The rebind here is exact, not a guess: source and target
    topology are the same file.
    """
    from .simulation_runner import solve_package_static

    def _evaluate(size: float) -> dict[str, Any]:
        solve = solve_package_static(
            baseline_pkg,
            mesh_size_mm=size,
            timeout=timeout,
            rebind_faces=True,
            baseline_package_path=baseline_pkg,
        )
        return {
            "size": size,
            "metrics": dict(solve.get("metrics") or {}),
            "solver_executed": bool(solve.get("solver_executed")),
            "solve_status": solve.get("status"),
            "error": solve.get("error") if not solve.get("solver_executed") else None,
        }

    return _evaluate


def run_mesh_convergence(
    settings: Any,
    project_id: str,
    *,
    mesh_sizes: list[float],
    metrics: list[str] | None = None,
    safety_factor: float = DEFAULT_GCI_SAFETY_FACTOR,
    converged_gci_percent: float = DEFAULT_CONVERGED_GCI_PERCENT,
    timeout: int = 180,
    evaluate_value: EvaluateSize | None = None,
) -> dict[str, Any]:
    """Solve the project at each mesh size and assess convergence per metric.

    Returns per-metric GCI reports plus the raw per-size solves. Recommends
    refining further when the finest-grid GCI exceeds the threshold. Mutates
    nothing; the project geometry/setup is unchanged.
    """
    from .project_io import get_project, resolve_project_path

    clean_sizes: list[float] = []
    for s in mesh_sizes or []:
        try:
            fs = float(s)
        except (TypeError, ValueError):
            continue
        if fs > 0 and fs not in clean_sizes:
            clean_sizes.append(fs)
    if len(clean_sizes) < 2:
        return {"status": "error", "code": "bad_input",
                "message": "mesh_sizes must contain at least 2 distinct positive sizes (3+ recommended for a GCI)"}
    if len(clean_sizes) > _MAX_LEVELS:
        return {"status": "error", "code": "too_many_levels",
                "message": f"mesh convergence is capped at {_MAX_LEVELS} mesh sizes (each is a full solve)"}
    clean_sizes.sort(reverse=True)  # coarse → fine for readability

    metric_names = [m for m in (metrics or _DEFAULT_METRICS) if isinstance(m, str)] or list(_DEFAULT_METRICS)

    try:
        project = get_project(settings, project_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "code": "project_not_found", "message": str(exc)}
    pkg = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if pkg is None or not Path(pkg).exists():
        return {"status": "error", "code": "no_package", "message": ".aieng package not found"}

    if evaluate_value is None:
        evaluate_value = make_default_evaluate_size(Path(pkg), timeout=timeout)

    solves = [evaluate_value(s) for s in clean_sizes]
    solved = [s for s in solves if s.get("solver_executed")]

    convergence: dict[str, Any] = {}
    for metric in metric_names:
        levels = [
            {"size": s["size"], "value": (s.get("metrics") or {}).get(metric)}
            for s in solved
            if isinstance((s.get("metrics") or {}).get(metric), (int, float))
        ]
        convergence[metric] = analyze_mesh_convergence(
            levels,
            metric_name=metric,
            safety_factor=safety_factor,
            converged_gci_percent=converged_gci_percent,
        )

    verdicts = {m: rep.get("verdict") for m, rep in convergence.items()}
    all_converged = bool(convergence) and all(
        convergence[m].get("converged") is True for m in convergence
    )
    any_indeterminate = any(
        convergence[m].get("converged") is None for m in convergence
    )

    if not solved:
        overall = "no_solves"
    elif all_converged:
        overall = "converged"
    elif any_indeterminate:
        overall = "indeterminate"
    else:
        overall = "not_converged"

    report: dict[str, Any] = {
        "status": "ok",
        "tool": "cae.mesh_convergence",
        "project_id": project_id,
        "mesh_sizes": clean_sizes,
        "solved_count": len(solved),
        "solves": solves,
        "convergence": convergence,
        "verdicts": verdicts,
        "overall_verdict": overall,
        "next_step": (
            "All requested metrics are mesh-converged within the GCI threshold."
            if overall == "converged"
            else "Add a finer mesh size (smaller mesh_size_mm) and re-run; the reported "
                 "results still carry the per-metric discretization uncertainty (GCI)."
            if overall == "not_converged"
            else "Provide at least three successful solves at progressively finer meshes "
                 "for a Grid Convergence Index."
        ),
        "honesty": {
            "baseline_modified": False,
            "is_discretization_uncertainty_only": True,
            "production_ready": False,
        },
    }

    try:
        from .project_io import write_json_artifact_to_package
        write_json_artifact_to_package(pkg, MESH_CONVERGENCE_REPORT_PATH, report)
        report["artifact_path"] = MESH_CONVERGENCE_REPORT_PATH
    except Exception as exc:  # noqa: BLE001
        report["artifact_write_error"] = str(exc)

    return report
