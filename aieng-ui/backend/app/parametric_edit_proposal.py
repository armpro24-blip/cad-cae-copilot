"""Structured, reviewable parametric edit proposals for CAD change governance.

Issue #432: deepen CAD modification from free-form agent output into reviewable,
reversible parametric edit proposals. This module builds a structured proposal
without mutating the package, then hands it to the caller for explicit review.
The actual edit is applied only through ``cad.edit_parameter`` after approval.
"""

from __future__ import annotations

import json
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .project_io import project_dir

LOGGER = logging.getLogger("app.parametric_edit_proposal")

_PROPOSALS_DIRNAME = "parametric_edit_proposals"
_PROPOSALS_FILENAME = "proposals.jsonl"

# Feature types that carry protected engineering semantics. Editing these
# dimensions can invalidate assembly mates, CAE boundary conditions, or
# manufacturing intent, so the proposal must surface the risk explicitly.
_PROTECTED_FEATURE_TYPES: frozenset[str] = frozenset({
    "mounting_hole_pattern",
    "mounting_hole",
    "interface_face",
    "load_interface",
    "boss",
    "flange",
    "rib",
})

# Substrings in parameter or constant names that suggest protected geometry.
_PROTECTED_NAME_TOKENS: frozenset[str] = frozenset({
    "hole", "bolt", "mount", "interface", "boss", "bearing", "axle",
    "flange", "rib", "wall", "floor", "thread", "pitch", "diameter",
})


def _load_package_json(pkg_path: Path, member: str) -> Any:
    try:
        with zipfile.ZipFile(pkg_path, "r") as zf:
            if member in zf.namelist():
                return json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        pass
    return None


def _feature_parameter_from_graph(
    feature_graph: Any, feature_id: str, parameter_name: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (feature, parameter) from the feature graph, or (None, None)."""
    if not isinstance(feature_graph, dict):
        return None, None
    features = feature_graph.get("features")
    if not isinstance(features, list):
        return None, None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        if feature.get("id") != feature_id and feature.get("feature_id") != feature_id:
            continue
        params = feature.get("parameters")
        if isinstance(params, dict):
            param = params.get(parameter_name)
            if isinstance(param, dict):
                return feature, param
        elif isinstance(params, list):
            for param in params:
                if isinstance(param, dict) and (
                    param.get("name") == parameter_name
                    or param.get("cad_parameter_name") == parameter_name
                ):
                    return feature, param
    return None, None


def _protected_feature_risks(
    feature: dict[str, Any], parameter: dict[str, Any], pkg_path: Path
) -> list[dict[str, Any]]:
    """Surface protected-feature and design-intent risks for a proposed edit."""
    risks: list[dict[str, Any]] = []
    feature_type = str(feature.get("type") or "")
    feature_name = str(feature.get("name") or "")
    param_name = str(parameter.get("name") or parameter.get("cad_parameter_name") or "")
    const_name = str(parameter.get("cad_parameter_name") or param_name)

    if feature_type in _PROTECTED_FEATURE_TYPES:
        risks.append({
            "kind": "protected_feature_type",
            "feature_type": feature_type,
            "message": (
                f"Target feature is a '{feature_type}' — editing it may invalidate "
                f"assembly mates, CAE boundary conditions, or manufacturing intent."
            ),
        })

    search_text = f"{feature_name} {param_name} {const_name}".lower()
    matched = sorted({t for t in _PROTECTED_NAME_TOKENS if t in search_text})
    if matched:
        risks.append({
            "kind": "protected_geometry_signal",
            "matched_tokens": matched,
            "message": (
                f"Parameter name signals protected geometry ({', '.join(matched)}); "
                "confirm the change does not break bolt patterns, interfaces, or fits."
            ),
        })

    # Check whether any assembly interface or mate references this feature by name.
    assembly_ir = _load_package_json(pkg_path, "assembly/assembly_ir.json")
    if isinstance(assembly_ir, dict):
        parts = assembly_ir.get("parts") or []
        interfaces = assembly_ir.get("interfaces") or []
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("geometry_ref") == feature_name:
                    risks.append({
                        "kind": "assembly_part_reference",
                        "part_id": part.get("part_id"),
                        "message": (
                            f"Feature '{feature_name}' is referenced as assembly part "
                            f"'{part.get('part_id')}' — the edit may change interface/mate geometry."
                        ),
                    })
        if isinstance(interfaces, list):
            for interface in interfaces:
                if not isinstance(interface, dict):
                    continue
                if interface.get("part_id") == feature_name or interface.get("part_id") == feature.get("id"):
                    risks.append({
                        "kind": "assembly_interface_reference",
                        "interface_id": interface.get("interface_id"),
                        "message": (
                            "This feature has an assembly interface — editing it may require "
                            "re-validating mates and CAE boundary conditions."
                        ),
                    })

    return risks


def _design_target_impacts(
    feature: dict[str, Any], parameter: dict[str, Any], pkg_path: Path
) -> list[dict[str, Any]]:
    """List design targets that may be affected by changing this parameter."""
    impacts: list[dict[str, Any]] = []
    feature_name = str(feature.get("name") or "")
    param_name = str(parameter.get("name") or "")
    const_name = str(parameter.get("cad_parameter_name") or param_name)
    search_tokens = {t for t in f"{feature_name} {param_name} {const_name}".lower().split() if len(t) > 2}

    design_targets_doc = _load_package_json(pkg_path, "task/design_targets.json")
    if design_targets_doc is None:
        # Try YAML paths by reading raw bytes and using yaml if available.
        try:
            import yaml
            with zipfile.ZipFile(pkg_path, "r") as zf:
                for path in ("task/design_targets.yaml", "task/design_targets.yml"):
                    if path in zf.namelist():
                        raw = zf.read(path).decode("utf-8", errors="replace")
                        design_targets_doc = yaml.safe_load(raw)
                        break
        except Exception:
            design_targets_doc = None

    if not isinstance(design_targets_doc, dict):
        return impacts
    targets = design_targets_doc.get("targets")
    if not isinstance(targets, list):
        return impacts

    for target in targets:
        if not isinstance(target, dict):
            continue
        target_id = target.get("target_id") or target.get("id")
        label = str(target.get("label") or target.get("target_type") or "")
        metric = str(target.get("metric") or target.get("target_type") or "")
        target_text = f"{target_id} {label} {metric}".lower()
        # A target is considered potentially affected when it shares a meaningful
        # token with the feature/parameter or when it is a geometry/size/mass target.
        if search_tokens & set(target_text.split()):
            impacts.append({
                "target_id": target_id,
                "label": label,
                "metric": metric,
                "reason": "Target naming overlaps with the edited parameter/feature.",
            })
        elif metric.lower() in {"mass", "volume", "size", "weight", "dimensions"}:
            impacts.append({
                "target_id": target_id,
                "label": label,
                "metric": metric,
                "reason": "Any geometry change may affect this target and requires re-verification.",
            })

    return impacts


def _unit_for_parameter(parameter: dict[str, Any]) -> str:
    """Best-effort unit extraction from parameter metadata."""
    unit = parameter.get("unit")
    if unit:
        return str(unit)
    name = str(parameter.get("name") or parameter.get("cad_parameter_name") or "").lower()
    if "angle" in name or "rotation" in name or "deg" in name:
        return "deg"
    if "count" in name or "number" in name or "holes" in name:
        return ""
    return "mm"


def propose_parametric_edit(
    settings: Any,
    project_id: str,
    feature_id: str,
    parameter_name: str,
    new_value: Any,
    reason: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Build a structured, read-only parametric edit proposal.

    The proposal includes the target pointer, old/new values, unit, scope,
    protected-feature risks, design-target impacts, and a preview of the
    geometry diff computed without modifying the package. It does NOT mutate
    the project/package — approval and execution are separate steps handled by
    ``cad.edit_parameter``.
    """
    from . import cad_generation as _cg
    from .project_io import (
        _validate_cad_parameter_edit_contract,
        get_project,
        resolve_project_path,
    )

    # 1. Load project & package
    try:
        project = get_project(settings, project_id)
    except Exception as exc:
        return {"status": "error", "code": "project_not_found", "message": f"{exc}"}

    pkg_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if pkg_path is None or not pkg_path.exists():
        return {
            "status": "error",
            "code": "package_not_found",
            "message": ".aieng package not found — generate a model first",
        }

    # 2. Validate contract (reads feature_graph.json, checks min/max bounds)
    try:
        contract = _validate_cad_parameter_edit_contract(
            pkg_path, feature_id, parameter_name, new_value
        )
    except ValueError as exc:
        return {"status": "error", "code": "invalid_contract", "message": str(exc)}

    param_info = contract["parameter"]
    cad_parameter_name = param_info.get("cad_parameter_name") or parameter_name
    previous_value = param_info.get("current_value")
    edited_feature = contract.get("feature") or {}
    feature_type = str(edited_feature.get("type") or "")

    # 3. Preview the edit in memory (no package write)
    preview = _cg.preview_build123d_parameter_edit(
        settings=settings,
        project_id=project_id,
        feature_id=feature_id,
        parameter_name=parameter_name,
        new_value=new_value,
        timeout=timeout,
    )
    if preview.get("status") != "ok":
        # Preview failures still return a proposal, but the preview block is honest
        # about what could not be computed and the approval gate must be stricter.
        preview = {
            "status": "preview_unavailable",
            "reason": preview.get("message") or "could not compute geometry preview",
        }

    # 4. Protected-feature / design-target risk analysis
    protected_risks = _protected_feature_risks(edited_feature, param_info, pkg_path)
    design_target_impacts = _design_target_impacts(edited_feature, param_info, pkg_path)

    scope = "local"
    if feature_type == "global_params":
        scope = "global"
    elif feature_type == "model_params":
        scope = "unscoped"

    scope_risk = None
    if scope == "global":
        scope_risk = {
            "scope": "global",
            "reason": "parameter belongs to shared/global dimensions and may affect multiple parts",
            "confirmation_field": "confirmScopeRisk",
        }
    elif scope == "unscoped":
        scope_risk = {
            "scope": "unscoped",
            "reason": "parameter could not be bound to one named part",
            "confirmation_field": "confirmScopeRisk",
        }

    proposal_id = f"pep_{uuid.uuid4().hex[:12]}"

    proposal: dict[str, Any] = {
        "status": "ok",
        "schema_version": "0.1",
        "proposal_id": proposal_id,
        "project_id": project_id,
        "approval_required": True,
        "target": {
            "feature_id": feature_id,
            "parameter_name": parameter_name,
            "cad_parameter_name": cad_parameter_name,
            "feature_name": edited_feature.get("name"),
            "feature_type": feature_type,
            "pointer": f"@feature:{feature_id}",
        },
        "change": {
            "old_value": previous_value,
            "new_value": new_value,
            "unit": _unit_for_parameter(param_info),
            "reason": reason or "",
        },
        "scope": scope,
        "scope_risk": scope_risk,
        "risks": {
            "protected_features": protected_risks,
            "design_target_impacts": design_target_impacts,
        },
        "expected_impact": _expected_impact_summary(preview, protected_risks, design_target_impacts),
        "preview": preview,
        "next_action": {
            "tool": "cad.edit_parameter",
            "input": {
                "project_id": project_id,
                "featureId": feature_id,
                "parameterName": parameter_name,
                "newValue": new_value,
                "proposal_id": proposal_id,
                "confirmScopeRisk": scope_risk is not None,
            },
            "reason": "Apply the approved proposal; requires explicit user approval.",
        },
    }

    # If the preview failed, add an explicit warning and force approval.
    if preview.get("status") != "ok":
        proposal["approval_required"] = True
        proposal["warnings"] = [
            f"Geometry preview unavailable ({preview.get('reason')}); "
            "review the old/new values carefully before approving."
        ]

    return proposal


def _expected_impact_summary(
    preview: dict[str, Any],
    protected_risks: list[dict[str, Any]],
    design_target_impacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Honest expected-impact summary — never fabricates solver evidence."""
    mass_impact: dict[str, Any] = {"status": "unknown", "note": "requires recompute after edit"}
    if isinstance(preview, dict) and preview.get("status") == "ok":
        before_volume = preview.get("before_volume_mm3")
        after_volume = preview.get("after_volume_mm3")
        if isinstance(before_volume, (int, float)) and isinstance(after_volume, (int, float)) and before_volume > 0:
            delta = after_volume - before_volume
            mass_impact = {
                "status": "preview_volume_delta",
                "before_volume_mm3": before_volume,
                "after_volume_mm3": after_volume,
                "delta_mm3": delta,
                "delta_percent": round((delta / before_volume) * 100, 4),
                "note": "Volume delta is a geometric proxy; mass depends on material density.",
            }

    stress_impact = {"status": "unknown", "note": "requires new static solver run after edit"}

    target_notes = []
    if design_target_impacts:
        target_notes.append(
            f"{len(design_target_impacts)} design target(s) may be affected; verify after edit."
        )
    if protected_risks:
        target_notes.append(
            f"{len(protected_risks)} protected-feature risk(s); confirm interfaces and fits."
        )

    return {
        "mass": mass_impact,
        "stress": stress_impact,
        "design_targets": {
            "affected_count": len(design_target_impacts),
            "note": "; ".join(target_notes) if target_notes else "No known design-target overlap.",
        },
        "summary": (
            "Geometry will change; downstream CAE evidence must be revalidated. "
            "Mass/stress numbers are not computed until after the edit and a new solver run."
        ),
    }


def _proposals_dir_path(settings: Any, project_id: str) -> Path:
    """Return the proposals directory path without creating it."""
    return project_dir(settings, project_id) / _PROPOSALS_DIRNAME


def _proposals_file_path(settings: Any, project_id: str) -> Path:
    """Return the proposals file path without creating the directory."""
    return _proposals_dir_path(settings, project_id) / _PROPOSALS_FILENAME


def _ensure_proposals_dir(settings: Any, project_id: str) -> Path:
    path = _proposals_dir_path(settings, project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _proposals_file(settings: Any, project_id: str) -> Path:
    """Return the proposals file path, creating the directory if needed (write path)."""
    return _ensure_proposals_dir(settings, project_id) / _PROPOSALS_FILENAME


def save_parametric_edit_proposal(
    settings: Any, project_id: str, proposal: dict[str, Any]
) -> dict[str, Any]:
    """Persist a proposal to the project's proposals JSONL file.

    Returns the proposal unchanged. Best-effort: failures are logged and the
    proposal is still returned so the caller can review it immediately.
    """
    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        return proposal
    try:
        path = _proposals_file(settings, project_id)
        record = {"proposal_id": proposal_id, "proposal": proposal}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        LOGGER.warning("Failed to persist proposal %s: %s", proposal_id, exc)
    return proposal


def load_parametric_edit_proposal(
    settings: Any, project_id: str, proposal_id: str
) -> dict[str, Any] | None:
    """Load a previously persisted proposal by id."""
    path = _proposals_file_path(settings, project_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, dict) and record.get("proposal_id") == proposal_id:
                    proposal = record.get("proposal")
                    if isinstance(proposal, dict):
                        return proposal
    except Exception as exc:
        LOGGER.warning("Failed to load proposal %s: %s", proposal_id, exc)
    return None
