"""Typed engineering action planning for chat-first CAD/CAE workflows.

This module is intentionally lightweight and deterministic: it does not call an
LLM and it never executes CAD/CAE tools.  Its job is to turn an engineer prompt
into a structured action candidate so the frontend/runtime can stop relying on
ad-hoc keyword ordering scattered through UI code.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

SCHEMA_VERSION = "0.1"

Intent = str

_MATERIAL_ALIASES: dict[str, str] = {
    "al6061": "Al6061-T6",
    "al6061-t6": "Al6061-T6",
    "6061": "Al6061-T6",
    "al7075": "Al7075-T6",
    "al7075-t6": "Al7075-T6",
    "7075": "Al7075-T6",
    "steel-1045": "Steel-1045",
    "steel 1045": "Steel-1045",
    "1045": "Steel-1045",
    "steel-316l": "Steel-316L",
    "steel 316l": "Steel-316L",
    "316l": "Steel-316L",
    "ti-6al-4v": "Ti-6Al-4V",
    "ti64": "Ti-6Al-4V",
    "titanium": "Ti-6Al-4V",
    "nylon": "Nylon-PA66",
    "pa66": "Nylon-PA66",
    "petg-cf": "PETG-CF",
    "petg": "PETG-CF",
    "cast iron": "Cast-Iron-Grey",
    "steel": "Steel-1045",
    "aluminum": "Al6061-T6",
    "aluminium": "Al6061-T6",
}


def build_engineering_action_plan(
    *,
    settings: Any,
    project_id: str,
    message: str,
) -> dict[str, Any]:
    """Return a read-only action candidate for a project chat message."""

    from .project_io import get_project, resolve_project_path

    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")

    package_path: Path | None = None
    has_generated_cad = False
    has_setup = False
    has_results = False
    try:
        project = get_project(settings, project_id)
        package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    except HTTPException:
        package_path = None

    if package_path and package_path.exists():
        names = _package_names(package_path)
        has_generated_cad = "geometry/source.py" in names or "geometry/generated.step" in names
        has_setup = "simulation/setup.yaml" in names
        # Not `simulation/results_summary.json` alone: that is the pre-canonical
        # path, and it exists in 0 of the 44 packages on disk — so this field
        # was False for every project, including the 8 that have results. The
        # library detector owns the definition; this entry point consumes it.
        has_results = any(name in names for name in _RESULT_MEMBERS)

    return classify_engineering_message(
        text,
        has_generated_cad=has_generated_cad,
        has_setup=has_setup,
        has_results=has_results,
        project_id=project_id,
    )


#: Members that mean "this package carries results", kept in step with
#: `aieng.cae_artifact_detector._RESULT_PATHS` — the library's definition. A
#: test asserts the two stay equal rather than drifting apart again.
_RESULT_MEMBERS = (
    "results/result_summary.json",
    "results/evidence_index.json",
    "results/field_regions.json",
    "results/field_summary.json",
    "simulation/results_summary.json",
)


def classify_engineering_message(
    message: str,
    *,
    has_generated_cad: bool = False,
    has_setup: bool = False,
    has_results: bool = False,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Classify a prompt into one engineering action candidate.

    Priority matters.  More specific simulation/target/material/mesh intents are
    evaluated before broad CAD refinement triggers like "change" or "make".
    """

    lower = message.lower().strip()
    extracted = _extract_inputs(lower)

    candidates: list[tuple[Intent, float, str]] = []

    if _matches_simulate(lower):
        candidates.append(("simulate", 0.95, "explicit simulation execution phrase"))
    if _matches_set_target(lower):
        candidates.append(("set_target", 0.92, "design target metric + threshold"))
    if _matches_change_material(lower):
        candidates.append(("change_material", 0.9, "material-change phrase"))
    if _matches_refine_mesh(lower):
        candidates.append(("refine_mesh", 0.9, "mesh-refinement phrase"))
    if _matches_preprocess(lower):
        candidates.append(("preprocess", 0.86, "FEA setup/preprocessing phrase"))
    if _matches_generate_cad(lower):
        candidates.append(("generate", 0.82, "CAD creation phrase plus part noun"))
    if has_generated_cad and _matches_refine_cad(lower):
        candidates.append(("refine", 0.72, "existing CAD plus edit/refine phrase"))

    if candidates:
        intent, confidence, reason = candidates[0]
    else:
        intent, confidence, reason = "none", 0.35, "no engineering execution intent detected"

    action = _action_for_intent(intent)
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "message": message,
        "intent": intent,
        "confidence": confidence,
        "reason": reason,
        "extracted_inputs": extracted,
        "project_state": {
            "has_generated_cad": has_generated_cad,
            "has_setup": has_setup,
            "has_results": has_results,
        },
        "action": action,
        "execution_policy": {
            "candidate_only": True,
            "must_not_auto_execute_external_tools": intent in {"simulate", "refine_mesh"},
            "approval_tier": action.get("approval_tier") if action else "auto",
        },
    }


def _package_names(package_path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            return set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return set()


def _extract_inputs(lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    material = _extract_material(lower)
    if material:
        out["material_hint"] = material
    mesh_size = _extract_mesh_size_mm(lower)
    if mesh_size is not None:
        out["mesh_size_mm"] = mesh_size
    return out


def _extract_material(lower: str) -> str | None:
    for alias, material in sorted(_MATERIAL_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in lower:
            return material
    return None


def _extract_mesh_size_mm(lower: str) -> float | None:
    match = re.search(r"(?:mesh(?:\s+size)?\s*(?:to|=|at)?\s*)(\d+(?:\.\d+)?)\s*mm", lower)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+mesh", lower)
    return float(match.group(1)) if match else None


def _matches_simulate(lower: str) -> bool:
    phrases = (
        "run simulation", "mesh and solve", "start simulation", "execute simulation",
        "run analysis", "run solver", "run the simulation", "start the simulation",
    )
    return any(p in lower for p in phrases) or lower == "simulate" or lower.startswith("simulate ")


def _matches_preprocess(lower: str) -> bool:
    fea_verbs = ("set up", "setup", "configure", "prepare", "generate fea", "run fea", "start fea")
    fea_nouns = (
        "fea", "fea setup", "finite element", "simulation setup", "structural analysis",
        "boundary condition", "mesh setup", "preprocessing", "pre-processing",
    )
    return (
        any(v in lower for v in fea_verbs) and any(n in lower for n in fea_nouns)
    ) or "preprocess" in lower or "pre-processing" in lower


def _matches_set_target(lower: str) -> bool:
    phrases = (
        "set max stress", "set max displacement", "set stress limit", "set displacement limit",
        "set stress target", "set displacement target", "stress limit", "displacement limit",
        "stress target", "displacement target", "add target", "set target", "design target",
        "stress must be", "displacement must be", "stress should be", "displacement should be",
        "stress <= ", "displacement <= ", "stress < ", "displacement < ",
    )
    metric_words = ("stress", "displacement", "deflection", "von mises", "sigma")
    has_value = re.search(r"\d+\s*(mpa|mm)\b", lower) is not None
    return any(p in lower for p in phrases) or (
        has_value
        and any(w in lower for w in metric_words)
        and any(w in lower for w in ("set", "limit", "target", "must", "should", "<=", "<"))
    )


def _matches_change_material(lower: str) -> bool:
    phrases = (
        "change material", "switch material", "use material", "try material",
        "change to ", "switch to ", "try with ", "material to ",
    )
    return (any(p in lower for p in phrases) or lower.startswith("use ")) and _extract_material(lower) is not None


def _matches_refine_mesh(lower: str) -> bool:
    phrases = ("refine mesh", "finer mesh", "smaller mesh", "mesh to ", "mesh size", "mesh refinement", "denser mesh")
    return any(p in lower for p in phrases)


def _matches_refine_cad(lower: str) -> bool:
    triggers = (
        "make", "increase", "decrease", "change", "add", "remove",
        "thicker", "taller", "wider", "longer", "shorter", "bigger", "smaller",
        "refine", "adjust", "update", "modify",
    )
    return any(t in lower for t in triggers)


def _matches_generate_cad(lower: str) -> bool:
    gen_phrases = (
        "generate", "create a", "create the", "design a", "design the",
        "make a", "make the", "model a", "model the", "build a", "build the",
        "draw a", "draw the",
    )
    part_nouns = (
        "part", "bracket", "plate", "housing", "mount", "gear", "enclosure",
        "fixture", "block", "shaft", "flange", "cap", "cover", "holder",
        "beam", "rod", "body", "component", "bushing", "sleeve", "clamp", "adapter",
    )
    return any(p in lower for p in gen_phrases) and any(n in lower for n in part_nouns)


def _action_for_intent(intent: str) -> dict[str, Any] | None:
    actions: dict[str, dict[str, Any]] = {
        "generate": {
            "id": "cad.generate",
            "endpoint": "POST /api/projects/{project_id}/generate-cad",
            "approval_tier": "confirm",
            "writes": ["geometry/generated.step", "geometry/topology_map.json", "graph/feature_graph.json", "geometry/source.py"],
            "external_tools": ["Claude", "build123d"],
        },
        "refine": {
            "id": "cad.refine",
            "endpoint": "POST /api/projects/{project_id}/refine-cad",
            "approval_tier": "confirm",
            "writes": ["geometry/generated.step", "geometry/topology_map.json", "graph/feature_graph.json", "geometry/source.py"],
            "external_tools": ["Claude", "build123d"],
        },
        "preprocess": {
            "id": "cae.preprocess",
            "endpoint": "POST /api/projects/{project_id}/ai-preprocessing",
            "approval_tier": "confirm",
            "writes": ["simulation/setup.yaml", "simulation/cae_mapping.json"],
            "external_tools": ["Claude"],
        },
        "simulate": {
            "id": "cae.simulate",
            "endpoint": "MCP cae.prepare_solver_run -> cae.run_solver",
            "tool_chain": ["cae.prepare_solver_run", "cae.run_solver"],
            "approval_tier": "gate",
            "writes": [
                "simulation/runs/{run_id}/solver_run.json",
                "simulation/runs/{run_id}/solver_log.txt",
                "simulation/runs/{run_id}/outputs/result.frd",
            ],
            "external_tools": ["CalculiX"],
        },
        "change_material": {
            "id": "cae.change_material",
            "endpoint": "POST /api/projects/{project_id}/ai-preprocessing",
            "approval_tier": "confirm",
            "writes": ["simulation/setup.yaml", "simulation/cae_mapping.json"],
            "external_tools": ["Claude"],
        },
        "refine_mesh": {
            "id": "cae.refine_mesh",
            "endpoint": "MCP cae.generate_mesh -> cae.prepare_solver_run",
            "tool_chain": ["cae.generate_mesh", "cae.prepare_solver_run"],
            "approval_tier": "gate",
            "writes": ["simulation/mesh/mesh.inp", "simulation/mesh/mesh_metadata.json"],
            "external_tools": ["Gmsh"],
        },
        "set_target": {
            "id": "targets.set_from_chat",
            "endpoint": "POST /api/projects/{project_id}/chat-set-target",
            "approval_tier": "auto",
            "writes": ["task/design_targets.yaml"],
            "external_tools": [],
        },
    }
    action = actions.get(intent)
    if action is None:
        return None
    return deepcopy(action)


__all__ = ["SCHEMA_VERSION", "build_engineering_action_plan", "classify_engineering_message"]
