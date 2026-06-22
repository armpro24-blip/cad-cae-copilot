"""Deterministic /simulate readiness report (v1.5 + v2 direct setup reading).

Pure and side-effect-free. Reports whether the six core simulation inputs are
present / missing / defaultable / unknown so the /simulate agent can ask the user
for missing *required* inputs instead of guessing.

Readiness source priority (v2):
  1. a direct CAE setup artifact (simulation/setup.{yaml,yml,json},
     cae/setup.{yaml,yml,json}, or a workspace artifact of kind
     cae_setup / simulation_setup) — read via ``load_simulation_setup``;
  2. else the ``cae`` block of ``aieng.agent_context`` (v1.5 fallback);
  3. else ``not_found`` with the required inputs missing and defaultable inputs
     defaulting.

This module never runs the solver, never touches CAD, and never bypasses
approval — it only produces prompt/context.
"""

from __future__ import annotations

from typing import Any, Callable

import yaml

from ..blocked_reason_codes import codes_for_readiness_report
from ..cae_payload_profile import profile_payload
from .mention_binding import bindings_to_targets, mention_status_word

# --- Status vocabulary ------------------------------------------------------
STATUS_PRESENT = "present"        # the setup clearly defines this input
STATUS_MISSING = "missing"        # a required input is absent (must ask the user)
STATUS_DEFAULTABLE = "defaultable"  # not set, but a sensible default exists for planning
STATUS_UNKNOWN = "unknown"        # cannot determine (e.g. explicitly unavailable)

# --- The six core inputs ----------------------------------------------------
# Static requires loads; modal (natural frequency) does not — it solves the
# unloaded structure but needs material *density* for the mass matrix; buckling
# requires a reference load. Required inputs are therefore analysis-type-aware.
REQUIRED_INPUTS: tuple[str, ...] = ("material", "loads", "constraints")
DEFAULTABLE_INPUTS: tuple[str, ...] = ("analysis_type", "mesh", "solver")
CORE_INPUTS: tuple[str, ...] = ("analysis_type", "material", "loads", "constraints", "mesh", "solver")

_ANALYSIS_REQUIRED_INPUTS: dict[str, tuple[str, ...]] = {
    "static": ("material", "loads", "constraints"),
    "modal": ("material", "constraints"),
    "buckling": ("material", "loads", "constraints"),
}

# Canonical analysis types + common spellings (mirrors deck_generator).
_ANALYSIS_TYPE_ALIASES: dict[str, str] = {
    "": "static",
    "static": "static",
    "linear_static": "static",
    "modal": "modal",
    "frequency": "modal",
    "eigenfrequency": "modal",
    "eigen": "modal",
    "natural_frequency": "modal",
    "buckling": "buckling",
    "buckle": "buckling",
    "linear_buckling": "buckling",
}

_DEFAULT_ANALYSIS_TYPE = "linear_static"
_DEFAULT_SOLVER = "CalculiX"


def normalize_analysis_type(value: Any) -> str:
    """Canonicalize an analysis-type value to static / modal / buckling."""
    key = str(value or "static").strip().lower().replace("-", "_").replace(" ", "_")
    return _ANALYSIS_TYPE_ALIASES.get(key, "static")

# Ordered candidate setup-artifact paths inside the .aieng package.
SETUP_CANDIDATE_PATHS: tuple[str, ...] = (
    "simulation/setup.yaml",
    "simulation/setup.yml",
    "simulation/setup.json",
    "cae/setup.yaml",
    "cae/setup.yml",
    "cae/setup.json",
)

_SETUP_ARTIFACT_KINDS = ("cae_setup", "simulation_setup")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    """Non-empty / non-zero / not-False presence test."""
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _count(cae: dict[str, Any], *keys: str) -> int:
    """Best-effort count across list-valued and *_count integer keys."""
    total = 0
    for key in keys:
        value = cae.get(key)
        if isinstance(value, (list, tuple)):
            total = max(total, len(value))
        elif isinstance(value, int) and not isinstance(value, bool):
            total = max(total, value)
    return total


def _draft_has(draft: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = draft.get(key)
        if isinstance(value, (list, tuple, dict)) and len(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _explicitly_unavailable(value: Any) -> bool:
    """True only when the setup *explicitly* marks something unavailable.

    A plain absence is NOT unavailable (it stays defaultable); only an explicit
    ``False`` / ``{"available": False}`` counts.
    """
    if value is False:
        return True
    if isinstance(value, dict) and value.get("available") is False:
        return True
    return False


# --- Setup loading ----------------------------------------------------------


def _parse_setup_text(text: Any) -> dict[str, Any] | None:
    """Parse YAML/JSON setup text into a dict, or None (safe on malformed input)."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = yaml.safe_load(text)  # YAML is a JSON superset — handles both
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def load_simulation_setup(
    read_member: Callable[[str], Any] | None = None,
    *,
    artifacts: Any = None,
) -> dict[str, Any] | None:
    """Load a direct CAE setup artifact. Pure given ``read_member``.

    ``read_member(path)`` returns the text of a package member (or None). Tries
    the canonical setup paths first, then any workspace ``artifacts`` of kind
    ``cae_setup`` / ``simulation_setup`` (inline ``data``/``setup``/``content``
    dicts, or a ``path`` resolved via ``read_member``). Returns
    ``{"data", "setup_source", "setup_source_kind"}`` or None. Never raises.
    """
    reader = read_member if callable(read_member) else (lambda _name: None)

    for path in SETUP_CANDIDATE_PATHS:
        data = _parse_setup_text(reader(path))
        if data:
            return {"data": data, "setup_source": path, "setup_source_kind": "setup_artifact"}

    for artifact in artifacts or []:
        if not isinstance(artifact, dict):
            continue
        kind = artifact.get("kind") or artifact.get("type")
        if kind not in _SETUP_ARTIFACT_KINDS:
            continue
        inline = artifact.get("data") or artifact.get("setup") or artifact.get("content")
        source = artifact.get("id") or artifact.get("name") or artifact.get("path") or str(kind)
        if isinstance(inline, dict) and inline:
            return {"data": inline, "setup_source": str(source), "setup_source_kind": "workspace_artifact"}
        path = artifact.get("path")
        if isinstance(path, str) and path:
            data = _parse_setup_text(reader(path))
            if data:
                return {"data": data, "setup_source": path, "setup_source_kind": "workspace_artifact"}
    return None


# --- Input extraction (normalize a source into presence/value/signal) -------


def _extract_from_setup_artifact(data: Any) -> dict[str, Any]:
    data = _as_dict(data)
    mesh_raw = data.get("mesh")
    return {
        "material_present": _truthy(data.get("material") or data.get("materials")),
        "loads_present": _truthy(data.get("loads") or data.get("load_cases") or data.get("load")),
        "constraints_present": _truthy(
            data.get("constraints") or data.get("boundary_conditions") or data.get("supports") or data.get("fixtures")
        ),
        "analysis_value": data.get("analysis_type") or data.get("analysis"),
        "analysis_signal": data.get("analysis_type") or data.get("analysis"),
        "mesh_value": (mesh_raw if not isinstance(mesh_raw, bool) else None) or data.get("mesh_params"),
        "mesh_signal": mesh_raw,
        "solver_value": (data.get("solver") if not isinstance(data.get("solver"), bool) else None),
        "solver_signal": data.get("solver"),
    }


def _extract_from_cae_block(cae: Any) -> dict[str, Any]:
    cae = _as_dict(cae)
    draft = _as_dict(cae.get("fea_setup_draft"))
    material_present = _count(cae, "materials", "materials_count") > 0 or _draft_has(draft, "material", "materials")
    loads_present = _count(cae, "loads", "loads_count") > 0 or _draft_has(draft, "loads", "load_cases", "load")
    constraints_present = (
        _count(cae, "boundary_conditions", "boundary_conditions_count", "constraints_count") > 0
        or _draft_has(draft, "boundary_conditions", "constraints", "supports", "fixtures")
    )
    analysis_value = cae.get("analysis_type") or draft.get("analysis_type")
    mesh_raw = cae.get("mesh")
    mesh_value = (mesh_raw if not isinstance(mesh_raw, bool) else None) or cae.get("mesh_params") or draft.get("mesh")
    solver_status = cae.get("solver_status")
    solver_named = solver_status if isinstance(solver_status, dict) and solver_status.get("solver") else None
    solver_value = cae.get("solver") or draft.get("solver") or solver_named
    has_setup = bool(
        cae.get("present")
        or material_present
        or loads_present
        or constraints_present
        or draft
        or analysis_value
        or mesh_value
        or solver_value
    )
    return {
        "material_present": material_present,
        "loads_present": loads_present,
        "constraints_present": constraints_present,
        "analysis_value": analysis_value,
        "analysis_signal": cae.get("analysis_type"),
        "mesh_value": mesh_value,
        "mesh_signal": mesh_raw,
        "solver_value": solver_value,
        "solver_signal": solver_status,
        "has_setup": has_setup,
    }


# --- Report assembly --------------------------------------------------------


def _defaultable_entry(present_value: Any, signal: Any, default: str) -> dict[str, Any]:
    # Check explicit-unavailable BEFORE presence so {"available": False} is unknown.
    if _explicitly_unavailable(present_value) or _explicitly_unavailable(signal):
        return {"status": STATUS_UNKNOWN, "required": False, "detail": "explicitly unavailable"}
    if _truthy(present_value):
        detail = str(present_value if not isinstance(present_value, (dict, list)) else "set")
        return {"status": STATUS_PRESENT, "required": False, "detail": detail}
    return {"status": STATUS_DEFAULTABLE, "required": False, "detail": f"will default to {default}"}


def _required_entry(present: bool, present_detail: str, missing_detail: str) -> dict[str, Any]:
    return {
        "status": STATUS_PRESENT if present else STATUS_MISSING,
        "required": True,
        "detail": present_detail if present else missing_detail,
    }


def build_simulation_readiness_report(
    cae: dict[str, Any] | None = None,
    *,
    setup_artifact: dict[str, Any] | None = None,
    mention_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a structured, deterministic simulation-readiness report.

    Priority: a direct ``setup_artifact`` (from ``load_simulation_setup``) wins
    over the ``cae`` agent-context block, which wins over ``not_found``.
    ``mention_bindings`` (resolved @part/@artifact) become the report ``targets``.
    The report never asserts that the solver ran.
    """
    if isinstance(setup_artifact, dict) and isinstance(setup_artifact.get("data"), dict) and setup_artifact["data"]:
        fields = _extract_from_setup_artifact(setup_artifact["data"])
        setup_source = setup_artifact.get("setup_source") or "setup_artifact"
        setup_source_kind = setup_artifact.get("setup_source_kind") or "setup_artifact"
    else:
        fields = _extract_from_cae_block(cae)
        if fields.pop("has_setup"):
            setup_source, setup_source_kind = "cae_setup", "agent_context"
        else:
            setup_source, setup_source_kind = "not_found", "none"
    fields.pop("has_setup", None)

    analysis_type = normalize_analysis_type(fields.get("analysis_value"))
    required = _ANALYSIS_REQUIRED_INPUTS.get(analysis_type, REQUIRED_INPUTS)

    def _input_entry(name: str, present: bool, present_detail: str, missing_detail: str) -> dict[str, Any]:
        # A required input that is absent is MISSING (ask the user). An input not
        # required for this analysis type (e.g. loads for modal) is never missing:
        # present → PRESENT, absent → DEFAULTABLE with a "not required" note.
        if name in required:
            return _required_entry(present, present_detail, missing_detail)
        if present:
            return {"status": STATUS_PRESENT, "required": False, "detail": present_detail}
        return {
            "status": STATUS_DEFAULTABLE,
            "required": False,
            "detail": f"not required for {analysis_type} analysis",
        }

    inputs: dict[str, dict[str, Any]] = {
        "analysis_type": _defaultable_entry(fields["analysis_value"], fields["analysis_signal"], _DEFAULT_ANALYSIS_TYPE),
        "material": _input_entry("material", fields["material_present"], "material assigned", "no material assigned"),
        "loads": _input_entry("loads", fields["loads_present"], "load(s) defined", "no loads defined"),
        "constraints": _input_entry(
            "constraints", fields["constraints_present"],
            "constraint(s)/support(s) defined", "no constraints/supports defined",
        ),
        "mesh": _defaultable_entry(fields["mesh_value"], fields["mesh_signal"], "auto mesh"),
        "solver": _defaultable_entry(fields["solver_value"], fields["solver_signal"], _DEFAULT_SOLVER),
    }
    # analysis_type present detail: show the configured value.
    if inputs["analysis_type"]["status"] == STATUS_PRESENT:
        inputs["analysis_type"]["detail"] = str(fields["analysis_value"])

    missing_required_inputs = [name for name in required if inputs[name]["status"] == STATUS_MISSING]
    defaultable_inputs = [name for name in CORE_INPUTS if inputs[name]["status"] == STATUS_DEFAULTABLE]

    # Modal needs material density for the mass matrix; flag honestly when the
    # material is present but no density signal is.
    notes: list[str] = []
    if analysis_type == "modal":
        notes.append(
            "Modal analysis: loads are not required; material density (*DENSITY) "
            "is required to build the mass matrix."
        )

    report: dict[str, Any] = {
        "setup_source": setup_source,
        "setup_source_kind": setup_source_kind,
        "analysis_type": analysis_type,
        "required_inputs": list(required),
        "solver_executed": False,  # invariant for /simulate
        "ready_for_solver": not missing_required_inputs,
        "inputs": inputs,
        "missing_required_inputs": missing_required_inputs,
        "defaultable_inputs": defaultable_inputs,
        "notes": notes,
        "targets": bindings_to_targets(mention_bindings),
    }
    report["blocked_reason_codes"] = codes_for_readiness_report(report)
    report["summary"] = summarize_simulation_readiness(report)
    profile_payload(report, label="simulation_readiness.report")
    return report


def summarize_simulation_readiness(report: dict[str, Any]) -> str:
    """Human-readable readiness section injected into the /simulate prompt."""
    inputs = _as_dict(report.get("inputs"))
    parts = ", ".join(f"{name}={inputs.get(name, {}).get('status', STATUS_UNKNOWN)}" for name in CORE_INPUTS)
    missing = report.get("missing_required_inputs") or []
    lines = [
        "Simulation readiness (deterministic, no solver run): "
        f"analysis_type={report.get('analysis_type', 'static')}; "
        f"setup_source={report.get('setup_source')} ({report.get('setup_source_kind')}); {parts}.",
    ]
    for note in report.get("notes") or []:
        lines.append(note)

    targets = _as_dict(report.get("targets"))
    target_bits: list[str] = []
    for kind in ("parts", "artifacts"):
        for entry in targets.get(kind, []) or []:
            value = entry.get("value")
            mark = mention_status_word(entry.get("known"))
            target_bits.append(f"{kind[:-1]}:{value} ({mark})")
    if target_bits:
        lines.append("Referenced targets: " + ", ".join(target_bits) + ".")

    if missing:
        lines.append(
            "Missing REQUIRED inputs: " + ", ".join(missing) + ". "
            "Ask the user to provide these before proposing to run the solver, and "
            "do NOT claim the simulation has run."
        )
    else:
        required = ", ".join(report.get("required_inputs") or REQUIRED_INPUTS)
        lines.append(
            f"All required inputs ({required}) are present. "
            "Produce the simulation plan; defaultable inputs may use defaults. "
            "The solver has NOT been run — running it needs a separate, approved "
            "cae.run_solver step."
        )
    return " ".join(lines)
