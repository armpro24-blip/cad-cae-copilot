"""Stable advisory ``blocked_reason_code`` values for solver readiness/preflight.

These codes are additive client hints. They do not replace human-readable
``blocked_reason`` messages and must not be used to bypass approval gating or
to auto-run the solver.
"""

from __future__ import annotations

from typing import Any, Iterable

MISSING_ANALYSIS_TYPE = "missing_analysis_type"
MISSING_MATERIAL = "missing_material"
MISSING_LOADS = "missing_loads"
MISSING_CONSTRAINTS = "missing_constraints"
MISSING_MESH = "missing_mesh"
MISSING_SOLVER = "missing_solver"
SOLVER_UNAVAILABLE = "solver_unavailable"
STALE_TOPOLOGY_REFERENCE = "stale_topology_reference"
TARGET_NOT_FOUND = "target_not_found"
NSET_BINDING_INVALID = "nset_binding_invalid"
DECK_NOT_PREPARED = "deck_not_prepared"
APPROVAL_REQUIRED = "approval_required"

_CODE_DETAILS: dict[str, dict[str, str]] = {
    MISSING_ANALYSIS_TYPE: {
        "label": "Missing analysis type",
        "description": "The CAE setup does not declare the analysis type to prepare.",
        "recommended_action": "Create or update simulation/solver_settings.json with an analysis_type such as linear_static.",
    },
    MISSING_MATERIAL: {
        "label": "Missing material",
        "description": "A required material assignment is absent or cannot be resolved.",
        "recommended_action": "Assign material properties before preparing solver input or making engineering claims.",
    },
    MISSING_LOADS: {
        "label": "Missing loads",
        "description": "No load case was found for the requested simulation run.",
        "recommended_action": "Create a simulation/load_cases/<load_case_id>.json file with at least one load.",
    },
    MISSING_CONSTRAINTS: {
        "label": "Missing constraints",
        "description": "The setup does not define supports or boundary constraints.",
        "recommended_action": "Add constraints or supports before generating a solver deck.",
    },
    MISSING_MESH: {
        "label": "Missing mesh",
        "description": "No mesh deck was found in the package.",
        "recommended_action": "Write a mesh handoff contract, import a meshed CalculiX deck, or generate a mesh artifact.",
    },
    MISSING_SOLVER: {
        "label": "Missing solver target",
        "description": "The solver target is not declared in the CAE setup.",
        "recommended_action": "Set solver to CalculiX in simulation/solver_settings.json.",
    },
    SOLVER_UNAVAILABLE: {
        "label": "Solver unavailable",
        "description": "The configured CalculiX executable cannot be found.",
        "recommended_action": "Install CalculiX, ensure ccx is on PATH, or set AIENG_CCX_CMD.",
    },
    STALE_TOPOLOGY_REFERENCE: {
        "label": "Stale topology reference",
        "description": "CAE face references no longer match the current geometry topology.",
        "recommended_action": "Re-run AI preprocessing to rebind loads and boundary conditions, or patch face IDs manually.",
    },
    TARGET_NOT_FOUND: {
        "label": "Target not found",
        "description": "A referenced part, face, artifact, or other target could not be matched.",
        "recommended_action": "Correct the target reference before running the requested operation.",
    },
    NSET_BINDING_INVALID: {
        "label": "NSET binding invalid",
        "description": "Loads or boundary conditions reference NSETs that are undefined, empty, or point to faces outside the current topology.",
        "recommended_action": "Update simulation/cae_mapping.json so every referenced NSET maps to at least one valid topology face.",
    },
    DECK_NOT_PREPARED: {
        "label": "Solver deck not prepared",
        "description": "The CalculiX input deck for this run is missing.",
        "recommended_action": "Run cae.generate_solver_input after mesh, solver settings, loads, constraints, and material are ready.",
    },
    APPROVAL_REQUIRED: {
        "label": "Approval required",
        "description": "The requested action crosses an approval-gated engineering boundary.",
        "recommended_action": "Review the planned action and approve it explicitly before execution.",
    },
}

# Inputs surfaced by simulation_readiness.CORE_INPUTS map cleanly to a code.
_MISSING_INPUT_CODES: dict[str, str] = {
    "analysis_type": MISSING_ANALYSIS_TYPE,
    "material": MISSING_MATERIAL,
    "loads": MISSING_LOADS,
    "constraints": MISSING_CONSTRAINTS,
    "mesh": MISSING_MESH,
    "solver": MISSING_SOLVER,
}


def detail_for_code(code: str) -> dict[str, str]:
    """Return stable human-readable metadata for a blocked reason code."""
    detail = _CODE_DETAILS.get(code)
    if detail is None:
        return {
            "code": code,
            "label": code.replace("_", " ").capitalize(),
            "description": "Unrecognized blocker code.",
            "recommended_action": "Inspect the human-readable blocked_reason and tool output.",
        }
    return {"code": code, **detail}


def details_for_codes(codes: Iterable[str]) -> list[dict[str, str]]:
    """Return deduplicated code details in the same order as ``codes``."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in codes:
        if not isinstance(code, str) or not code or code in seen:
            continue
        seen.add(code)
        out.append(detail_for_code(code))
    return out


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
    if not preflight.get("nset_binding_valid", True):
        codes.add(NSET_BINDING_INVALID)
    return sorted(codes)


def codes_for_run_solver_action(preflight: dict[str, Any]) -> list[str]:
    """Return codes for the blocked ``cae.run_solver`` next_action item.

    Always includes ``approval_required`` because running the solver is gated
    by explicit approval, plus any technical readiness blockers.
    """
    codes = set(codes_for_preflight(preflight))
    codes.add(APPROVAL_REQUIRED)
    return sorted(codes)
