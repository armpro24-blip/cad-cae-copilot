"""Stable advisory ``blocked_reason_code`` values for solver readiness/preflight.

These codes are additive client hints. They do not replace human-readable
``blocked_reason`` messages and must not be used to bypass approval gating or
to auto-run the solver.
"""

from __future__ import annotations

from typing import Any

MISSING_ANALYSIS_TYPE = "missing_analysis_type"
MISSING_MATERIAL = "missing_material"
MISSING_LOADS = "missing_loads"
MISSING_CONSTRAINTS = "missing_constraints"
MISSING_MESH = "missing_mesh"
MISSING_SOLVER = "missing_solver"
SOLVER_UNAVAILABLE = "solver_unavailable"
STALE_TOPOLOGY_REFERENCE = "stale_topology_reference"
TARGET_NOT_FOUND = "target_not_found"
DECK_NOT_PREPARED = "deck_not_prepared"
APPROVAL_REQUIRED = "approval_required"

# Inputs surfaced by simulation_readiness.CORE_INPUTS map cleanly to a code.
_MISSING_INPUT_CODES: dict[str, str] = {
    "analysis_type": MISSING_ANALYSIS_TYPE,
    "material": MISSING_MATERIAL,
    "loads": MISSING_LOADS,
    "constraints": MISSING_CONSTRAINTS,
    "mesh": MISSING_MESH,
    "solver": MISSING_SOLVER,
}


def codes_for_readiness_report(report: dict[str, Any]) -> list[str]:
    """Return stable codes for a simulation-readiness report.

    Required inputs that are missing receive ``missing_<input>``. Explicitly
    unavailable defaultable inputs receive ``missing_mesh`` / ``solver_unavailable``
    / ``missing_analysis_type``. Mentioned targets that are known not to exist
    receive ``target_not_found``.
    """
    codes: set[str] = set()
    inputs = report.get("inputs") or {}

    for name in report.get("missing_required_inputs") or []:
        code = _MISSING_INPUT_CODES.get(name)
        if code:
            codes.add(code)

    for name, meta in inputs.items():
        if meta.get("status") != "unknown":
            continue
        if name == "solver":
            codes.add(SOLVER_UNAVAILABLE)
        elif name == "mesh":
            codes.add(MISSING_MESH)
        elif name == "analysis_type":
            codes.add(MISSING_ANALYSIS_TYPE)

    targets = report.get("targets") or {}
    for kind in ("parts", "artifacts"):
        for entry in targets.get(kind) or []:
            if entry.get("known") is False:
                codes.add(TARGET_NOT_FOUND)

    return sorted(codes)


def codes_for_preflight(preflight: dict[str, Any]) -> list[str]:
    """Return stable codes for a ``cae.prepare_solver_run`` preflight dict.

    The returned codes describe why ``ready_to_run`` is false. They are
    advisory and never change solver execution behavior.
    """
    codes: set[str] = set()
    if not preflight.get("has_mesh"):
        codes.add(MISSING_MESH)
    if not preflight.get("has_solver_settings"):
        # solver_settings.json carries both analysis_type and the solver target.
        codes.add(MISSING_ANALYSIS_TYPE)
        codes.add(MISSING_SOLVER)
    if not preflight.get("has_load_case"):
        codes.add(MISSING_LOADS)
    if not preflight.get("has_input_deck"):
        codes.add(DECK_NOT_PREPARED)
    if not preflight.get("ccx_available"):
        codes.add(SOLVER_UNAVAILABLE)
    if not preflight.get("topology_references_valid", True):
        codes.add(STALE_TOPOLOGY_REFERENCE)
    return sorted(codes)


def codes_for_run_solver_action(preflight: dict[str, Any]) -> list[str]:
    """Return codes for the blocked ``cae.run_solver`` next_action item.

    Always includes ``approval_required`` because running the solver is gated
    by explicit approval, plus any technical readiness blockers.
    """
    codes = set(codes_for_preflight(preflight))
    codes.add(APPROVAL_REQUIRED)
    return sorted(codes)
