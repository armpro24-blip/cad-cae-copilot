from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import math
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from .config import (
    AIENG_EXT,
    PROJECT_ID,
    PROJECT_TEMPLATE,
    Settings,
    STEP_EXTENSIONS,
    now_iso,
    read_json,
    write_json,
)
from .core_dependencies import (  # noqa: F401
    CLAIM_PROPOSAL_ARTIFACT_PREFIX,
    CLAIM_PROPOSAL_STATUSES,
    _CORE_AUDIT_EVENTS_PATH,
    _CORE_REVALIDATION_STATUS_PATH,
    _STALE_EVIDENCE_CATEGORIES,
    _build_audit_event,
    _build_claim_proposal,
    _build_review_readiness,
    _classify_artifact_path,
    _core_build_claim_support_packet,
    _core_build_revalidation_response,
    _core_generate_artifact_manifest,
    _core_record_geometry_edit_status,
    _core_record_solver_validation_status,
    _core_resolve_evidence_reference,
    _core_run_package_consistency_checks,
    _parse_audit_events_jsonl,
    _serialize_audit_events_jsonl,
    _validate_claim_proposal_request,
)
from .package_inspection import read_package_json


def ensure_dirs(settings: Settings) -> None:
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.projects_root.mkdir(parents=True, exist_ok=True)


def project_dir(settings: Settings, project_id: str) -> Path:
    if not PROJECT_ID.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    return settings.projects_root / project_id


def metadata_path(settings: Settings, project_id: str) -> Path:
    return project_dir(settings, project_id) / "metadata.json"


def default_project(name: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        **PROJECT_TEMPLATE,
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def normalize_project(project: dict[str, Any]) -> dict[str, Any]:
    normalized = {**PROJECT_TEMPLATE, **(project or {})}
    normalized["name"] = str(normalized.get("name") or "Untitled project")
    return normalized


def project_relpath(settings: Settings, project_id: str, path: Path) -> str:
    return str(path.relative_to(project_dir(settings, project_id))).replace("\\", "/")


def save_project(settings: Settings, project: dict[str, Any]) -> dict[str, Any]:
    project = normalize_project(project)
    project["updated_at"] = now_iso()
    base = project_dir(settings, project["id"])
    for folder in ("source", "packages", "viewer", "logs"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    write_json(metadata_path(settings, project["id"]), project)
    return project


def get_project(settings: Settings, project_id: str) -> dict[str, Any]:
    path = metadata_path(settings, project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="project not found")
    return normalize_project(read_json(path, {}))


def resolve_project_path(settings: Settings, project_id: str, relpath: str | None) -> Path | None:
    if not relpath:
        return None
    resolved = (project_dir(settings, project_id) / relpath).resolve()
    try:
        resolved.relative_to(project_dir(settings, project_id).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project path") from exc
    return resolved


def write_artifact_to_package(
    package_path: str | Path,
    artifact_path: str,
    source_path: str | Path,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write a single artifact file into an existing `.aieng` package ZIP.

    Uses the standard temp-file + atomic-move pattern so the package is
    never in a partially-written state.

    Args:
        package_path: Path to the `.aieng` package.
        artifact_path: Destination path inside the ZIP (e.g. ``results/computed_metrics.json``).
        source_path: Path to the file on disk to copy into the package.
        overwrite: Whether to overwrite an existing entry.

    Returns:
        Artifact metadata dict with ``path``, ``kind``, ``role``, ``source_path``.
    """
    path = Path(package_path)
    source = Path(source_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")
    if not source.exists():
        raise FileNotFoundError(f"source file not found: {source}")

    with zipfile.ZipFile(path, mode="r") as package:
        names = set(package.namelist())
        if "manifest.json" not in names:
            raise ValueError("package is missing manifest.json")
        if not overwrite and artifact_path in names:
            raise FileExistsError(
                f"{artifact_path} already exists in package; use overwrite=True to replace"
            )
        manifest = json.loads(package.read("manifest.json"))
        existing_members: list[tuple[zipfile.ZipInfo, bytes]] = []
        seen: set[str] = set()
        for info in package.infolist():
            if info.filename in seen or info.filename == artifact_path or info.filename == "manifest.json":
                continue
            seen.add(info.filename)
            data = b"" if info.is_dir() else package.read(info.filename)
            existing_members.append((info, data))

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data in existing_members:
                out_package.writestr(info, data)
            out_package.writestr("manifest.json", manifest_json)
            out_package.writestr(artifact_path, source.read_bytes())
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "path": artifact_path,
        "kind": Path(artifact_path).stem,
        "role": "artifact",
        "source_path": str(source),
    }


def write_json_artifact_to_package(
    package_path: str | Path,
    artifact_path: str,
    data: Any,
    *,
    overwrite: bool = True,
    indent: int | None = 2,
    sort_keys: bool = True,
) -> dict[str, Any]:
    """Write a JSON artifact into an existing `.aieng` package ZIP atomically.

    Mirrors :func:`write_artifact_to_package` but accepts an in-memory dict/list
    instead of a source file path.
    """
    path = Path(package_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    payload = json.dumps(data, indent=indent, sort_keys=sort_keys, default=str).encode("utf-8")

    with zipfile.ZipFile(path, mode="r") as package:
        names = set(package.namelist())
        if "manifest.json" not in names:
            raise ValueError("package is missing manifest.json")
        if not overwrite and artifact_path in names:
            raise FileExistsError(
                f"{artifact_path} already exists in package; use overwrite=True to replace"
            )
        manifest = json.loads(package.read("manifest.json"))
        existing_members: list[tuple[zipfile.ZipInfo, bytes]] = []
        seen: set[str] = set()
        for info in package.infolist():
            if info.filename in seen or info.filename == artifact_path or info.filename == "manifest.json":
                continue
            seen.add(info.filename)
            data_bytes = b"" if info.is_dir() else package.read(info.filename)
            existing_members.append((info, data_bytes))

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data_bytes in existing_members:
                out_package.writestr(info, data_bytes)
            out_package.writestr("manifest.json", manifest_json)
            out_package.writestr(artifact_path, payload)
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "path": artifact_path,
        "kind": Path(artifact_path).stem,
        "role": "artifact",
    }


# ---------------------------------------------------------------------------
# Artifact review (Phase 26) — read-only inspection of .aieng package contents
# ---------------------------------------------------------------------------
# Purpose: enable humans to review what an agent or runtime tool wrote into a
# .aieng package. Read-only — does NOT execute solvers, mutate packages, or
# advance claims. This is evidence review, not engineering computation.

_ARTIFACT_MAX_TEXT_BYTES = 256 * 1024
_ARTIFACT_MAX_PARSE_BYTES = 2 * 1024 * 1024

_ARTIFACT_TEXT_SUFFIXES = frozenset(
    {".json", ".md", ".txt", ".yaml", ".yml", ".log", ".csv", ".inp"}
)
_ARTIFACT_MEDIA_HINTS: dict[str, str] = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".inp": "text/plain",
    ".frd": "application/octet-stream",
    ".vtu": "application/octet-stream",
    ".vtk": "application/octet-stream",
    ".step": "application/octet-stream",
    ".stp": "application/octet-stream",
    ".stl": "application/octet-stream",
    ".glb": "model/gltf-binary",
}


def _is_safe_artifact_path(p: str) -> bool:
    """Return True if `p` is a safe relative archive path.

    Rejects empty strings, leading separators, backslashes, and any `..`
    segment. Archive paths use forward slashes only.
    """
    if not p:
        return False
    if p.startswith("/") or p.startswith("./"):
        return False
    if "\\" in p:
        return False
    parts = p.split("/")
    if any(seg in ("", "..", ".") for seg in parts):
        return False
    return True


def _classify_artifact_media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _ARTIFACT_MEDIA_HINTS.get(suffix, "application/octet-stream")


def _read_artifact_from_package(
    package_path: Path,
    artifact_path: str,
) -> dict[str, Any]:
    """Read a single artifact from a `.aieng` package as a review payload.

    Always returns 200-shape data; callers handle 400/404 separately.
    """
    response: dict[str, Any] = {
        "path": artifact_path,
        "exists": False,
        "media_type": _classify_artifact_media_type(artifact_path),
        "warnings": [],
    }

    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            try:
                info = archive.getinfo(artifact_path)
            except KeyError:
                return response
            response["exists"] = True
            response["size_bytes"] = info.file_size
            data = archive.read(artifact_path)
    except zipfile.BadZipFile:
        response["warnings"].append("package is not a valid zip archive")
        return response

    suffix = Path(artifact_path).suffix.lower()
    is_textual = suffix in _ARTIFACT_TEXT_SUFFIXES
    has_null = b"\x00" in data[:4096]
    if has_null:
        is_textual = False
        response["warnings"].append("binary content detected; text omitted")

    if is_textual:
        if info.file_size <= _ARTIFACT_MAX_TEXT_BYTES:
            try:
                response["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                response["warnings"].append("utf-8 decode failed; text omitted")
        else:
            response["warnings"].append(
                f"file size {info.file_size} bytes exceeds inline text cap "
                f"{_ARTIFACT_MAX_TEXT_BYTES}; text omitted"
            )

    if suffix == ".json" and info.file_size <= _ARTIFACT_MAX_PARSE_BYTES and not has_null:
        try:
            response["parsed_json"] = json.loads(data)
        except json.JSONDecodeError as exc:
            response["warnings"].append(f"json parse failed: {exc.msg}")
    elif suffix == ".json" and info.file_size > _ARTIFACT_MAX_PARSE_BYTES:
        response["warnings"].append(
            f"file size {info.file_size} bytes exceeds parse cap "
            f"{_ARTIFACT_MAX_PARSE_BYTES}; parsed_json omitted"
        )

    return response


def _json_diff_paths(
    before: Any,
    after: Any,
    prefix: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Compute RFC-6901 JSON Pointer paths for changes between two JSON values.

    Returns (changed_paths, added_paths, removed_paths). Comparison is
    structural and recursive. Lists are compared element-by-element up to the
    shorter length; the tail is reported under added/removed. Primitive
    inequality at a leaf produces a changed path.

    Path encoding: `/` separators, `~0` for `~` and `~1` for `/` per RFC-6901.
    """
    def _escape(token: str) -> str:
        return token.replace("~", "~0").replace("/", "~1")

    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    if isinstance(before, dict) and isinstance(after, dict):
        before_keys = set(before.keys())
        after_keys = set(after.keys())
        for key in sorted(before_keys & after_keys):
            sub_changed, sub_added, sub_removed = _json_diff_paths(
                before[key], after[key], f"{prefix}/{_escape(str(key))}"
            )
            changed.extend(sub_changed)
            added.extend(sub_added)
            removed.extend(sub_removed)
        for key in sorted(after_keys - before_keys):
            added.append(f"{prefix}/{_escape(str(key))}")
        for key in sorted(before_keys - after_keys):
            removed.append(f"{prefix}/{_escape(str(key))}")
    elif isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for i in range(common):
            sub_changed, sub_added, sub_removed = _json_diff_paths(
                before[i], after[i], f"{prefix}/{i}"
            )
            changed.extend(sub_changed)
            added.extend(sub_added)
            removed.extend(sub_removed)
        for i in range(common, len(after)):
            added.append(f"{prefix}/{i}")
        for i in range(common, len(before)):
            removed.append(f"{prefix}/{i}")
    else:
        if before != after:
            changed.append(prefix or "")

    return changed, added, removed


# ---------------------------------------------------------------------------
# Solver input deck import (Phase 29)
# ---------------------------------------------------------------------------
# Closes the biggest functional gap in the vertical CAE MVP: the runtime
# previously assumed a `.inp` deck already existed inside the package. This
# importer accepts a pre-existing deck (typically authored externally) and
# writes it into the canonical run path so cae.run_solver can find it.
#
# This is import only — no mesh generation, no input deck generation, no
# physical correctness validation. The minimal parse below just confirms
# CalculiX keyword syntax is plausible; it does not validate the analysis.

_SOLVER_INPUT_MAX_BYTES = 10 * 1024 * 1024
_RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _is_safe_run_id(run_id: str) -> bool:
    return bool(_RUN_ID_PATTERN.match(run_id))


def _parse_calculix_input_deck(text: str) -> dict[str, Any]:
    """Minimal CalculiX `.inp` keyword scan.

    Returns ``{"keywords": [...], "keyword_count": N, "warnings": [...]}``.
    Detects CalculiX keyword lines (lines starting with ``*`` and not ``**``
    which is a comment). Does NOT validate the analysis: card order, parameter
    values, mesh consistency, and material laws are all out of scope.
    """
    keywords: list[str] = []
    warnings: list[str] = []
    saw_step = False
    saw_node = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("**"):
            continue
        if not stripped.startswith("*"):
            continue
        head = stripped[1:].split(",", 1)[0].strip().upper()
        if not head:
            continue
        keywords.append(head)
        if head == "STEP":
            saw_step = True
        elif head == "NODE":
            saw_node = True

    if not keywords:
        warnings.append("no CalculiX keywords (lines starting with '*') detected")
    if not saw_node:
        warnings.append("no *NODE block detected; deck may be incomplete")
    if not saw_step:
        warnings.append("no *STEP block detected; deck may be incomplete")

    return {
        "keywords": keywords,
        "keyword_count": len(keywords),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# cae.apply_setup_patch — constants and helpers
# ---------------------------------------------------------------------------

_ALLOWED_PATCH_PREFIXES = ("simulation/cae_imports/", "simulation/load_cases/")
_ALLOWED_PATCH_EXACT = frozenset(
    {"simulation/solver_settings.json", "simulation/cae_mapping.json", "graph/constraints.json"}
)
_SUPPORTED_PATCH_OPERATIONS = frozenset(
    {"create_file", "replace_json", "merge_object", "append_array_item"}
)
# Artifacts that become stale whenever setup files are changed.
_SETUP_STALE_ARTIFACTS = [
    "simulation/preprocessing_summary.json",
    "simulation/preprocessing_summary.md",
    "results/result_summary.json",
    "results/evidence_index.json",
    "results/postprocessing_summary.md",
]
_GEOMETRY_STALE_ARTIFACTS = [
    "geometry/topology_map.json",
    "graph/aag.json",
    "graph/feature_graph.json",
    "objects/interface_graph.json",
    "objects/object_registry.json",
    "visual/annotation_layers.json",
    "visual/model_manifest.json",
    "simulation/mesh_handoff_contract.json",
    "simulation/mesh/mesh_metadata.json",
    "results/computed_metrics.json",
    "results/field_regions.json",
    "results/field_summary.json",
    "results/field_summary.md",
    *_SETUP_STALE_ARTIFACTS,
]

# Path inside the .aieng ZIP where geometry-edit revalidation state is recorded.
# Alias for aieng.revalidation_status.REVALIDATION_STATUS_PATH.
REVALIDATION_STATUS_PATH: str = _CORE_REVALIDATION_STATUS_PATH

# Path inside the .aieng ZIP for the append-only runtime audit event log.
# Alias for aieng.audit_event.AUDIT_EVENTS_PATH.
AUDIT_EVENTS_PATH: str = _CORE_AUDIT_EVENTS_PATH

# Path inside the .aieng ZIP where the on-demand artifact manifest is written.
# Currently generated on demand only (no package write); constant reserved for future use.
ARTIFACT_MANIFEST_PATH = "manifest/artifacts.json"

# Directory inside the .aieng ZIP where claim proposal artifacts are written.
# Alias for aieng.claim_proposal.CLAIM_PROPOSAL_ARTIFACT_PREFIX.
CLAIM_PROPOSALS_DIR: str = CLAIM_PROPOSAL_ARTIFACT_PREFIX

# Allowed values for the proposed_status field in a claim proposal.
# Alias for aieng.claim_proposal.CLAIM_PROPOSAL_STATUSES.
_VALID_PROPOSED_STATUSES: frozenset[str] = CLAIM_PROPOSAL_STATUSES

# Artifact classification and stale-evidence categories are provided by aieng core
# (imported above via aieng.package_manifest and aieng.evidence_resolver).

# Supported CAE result fields for the compact field-summary endpoints.
# Maps public field_name → {unit, metric_key (in computed_metrics.json),
# evidence_role, extrema}. Includes both canonical names (used by the frontend
# field picker) and the legacy aliases "stress"/"displacement" for backward
# compatibility.
_CAE_RESULT_FIELDS: dict[str, dict[str, Any]] = {
    # Legacy aliases
    "displacement": {
        "unit": "mm",
        "metric_key": "max_displacement",
        "evidence_role": "displacement_extrema",
        "extrema": "maximum",
    },
    "stress": {
        "unit": "MPa",
        "metric_key": "max_von_mises_stress",
        "evidence_role": "stress_extrema",
        "extrema": "maximum",
    },
    # Stress scalar fields
    "von_mises": {
        "unit": "MPa",
        "metric_key": "max_von_mises_stress",
        "evidence_role": "stress_extrema",
        "extrema": "maximum",
    },
    "sxx": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "syy": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "szz": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "sxy": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "sxz": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "syz": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    # Principal / equivalent stress fields
    "s1": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "s2": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "s3": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "tresca": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    "max_shear": {"unit": "MPa", "metric_key": "max_von_mises_stress", "evidence_role": "stress_extrema", "extrema": "maximum"},
    # Displacement fields
    "disp_magnitude": {
        "unit": "mm",
        "metric_key": "max_displacement",
        "evidence_role": "displacement_extrema",
        "extrema": "maximum",
    },
    "ux": {"unit": "mm", "metric_key": "max_displacement", "evidence_role": "displacement_extrema", "extrema": "maximum"},
    "uy": {"unit": "mm", "metric_key": "max_displacement", "evidence_role": "displacement_extrema", "extrema": "maximum"},
    "uz": {"unit": "mm", "metric_key": "max_displacement", "evidence_role": "displacement_extrema", "extrema": "maximum"},
    # Safety factor
    "safety_factor": {
        "unit": "",
        "metric_key": "minimum_safety_factor",
        "evidence_role": "safety_factor_extrema",
        "extrema": "minimum",
    },
}

# Static tool capability profile — factual description of what each tool
# does and requires.  Environment availability (ccx) is resolved
# at request time and injected by the /api/runtime/capabilities endpoint.
_TOOL_CAPABILITY_PROFILE: list[dict[str, Any]] = [
    {
        "name": "cae.run_solver",
        "implemented": True,
        "requires_approval": True,
        "writes_artifacts": True,
        "artifact_paths": [
            "simulation/runs/{run_id}/solver_run.json",
            "simulation/runs/{run_id}/solver_log.txt",
            "simulation/runs/{run_id}/outputs/result.frd",
        ],
        "produces_evidence": True,
        "modifies_geometry": False,
        "requires_revalidation": False,
        "advances_claims": False,
        "external_binary": "ccx",
        "external_binary_env_var": None,
    },
    {
        "name": "postprocess.refresh_cae_summary",
        "implemented": True,
        "requires_approval": False,
        "writes_artifacts": True,
        "artifact_paths": [
            "results/result_summary.json",
            "results/evidence_index.json",
            "results/postprocessing_summary.md",
            "results/fields/displacement.summary.json",
            "results/fields/stress.summary.json",
        ],
        "produces_evidence": True,
        "modifies_geometry": False,
        "requires_revalidation": False,
        "advances_claims": False,
        "external_binary": None,
        "external_binary_env_var": None,
    },
    {
        "name": "cae-result-fields",
        "implemented": True,
        "requires_approval": False,
        "writes_artifacts": False,
        "artifact_paths": [],
        "produces_evidence": False,
        "modifies_geometry": False,
        "requires_revalidation": False,
        "advances_claims": False,
        "read_only": True,
        "supported_fields": [
            "von_mises", "sxx", "syy", "szz", "sxy", "sxz", "syz",
            "s1", "s2", "s3", "tresca", "max_shear",
            "disp_magnitude", "ux", "uy", "uz",
            "safety_factor",
            # Legacy aliases kept for backward compatibility
            "stress", "displacement",
        ],
        "external_binary": None,
        "external_binary_env_var": None,
    },
    {
        "name": "claims.propose_update",
        "implemented": True,
        "requires_approval": False,
        "writes_artifacts": True,
        "artifact_paths": ["claims/proposals/{proposal_id}.json"],
        "produces_evidence": False,
        "modifies_geometry": False,
        "requires_revalidation": False,
        "advances_claims": False,
        "creates_proposal": True,
        "requires_explicit_acceptance_workflow": True,
        "external_binary": None,
        "external_binary_env_var": None,
    },
]


def _is_allowed_patch_path(p: str) -> bool:
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    if p in _ALLOWED_PATCH_EXACT:
        return True
    return any(p.startswith(prefix) for prefix in _ALLOWED_PATCH_PREFIXES)


def _parse_json_pointer(pointer: str) -> list[str]:
    """Decode a JSON Pointer (RFC 6901) into a list of path tokens."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    tokens = pointer[1:].split("/")
    return [t.replace("~1", "/").replace("~0", "~") for t in tokens]


def _json_pointer_get(obj: Any, tokens: list[str]) -> Any:
    cur: Any = obj
    for t in tokens:
        if isinstance(cur, dict):
            cur = cur[t]
        elif isinstance(cur, list):
            cur = cur[int(t)]
        else:
            raise KeyError(t)
    return cur


def _json_pointer_set(obj: Any, tokens: list[str], value: Any) -> None:
    """Set a value at the JSON Pointer location (mutates obj in-place)."""
    if not tokens:
        raise ValueError("Cannot replace root document via pointer")
    cur: Any = obj
    for t in tokens[:-1]:
        if isinstance(cur, dict):
            cur = cur[t]
        elif isinstance(cur, list):
            cur = cur[int(t)]
        else:
            raise KeyError(t)
    last = tokens[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list):
        cur[int(last)] = value
    else:
        raise KeyError(last)


def _apply_single_patch(
    existing_content: bytes | None,
    op: dict[str, Any],
    path: str,
) -> bytes:
    """Apply one patch operation; returns new file bytes."""
    action = op.get("action_type") or op.get("operation") or ""
    patch_type = op.get("patch_type", "")

    if action == "create_file":
        content = op.get("content")
        if content is None:
            raise ValueError("create_file requires 'content'")
        if isinstance(content, (dict, list)):
            return (json.dumps(content, indent=2, sort_keys=True) + "\n").encode()
        return str(content).encode()

    if action in ("replace_json", "merge_object", "append_array_item"):
        pointer_str: str = op.get("pointer", "")
        # Accept `content` as an alias for `value`, consistent with create_file and the
        # documented cae.apply_setup_patch examples (which use `content` for merges).
        value: Any = op.get("value") if op.get("value") is not None else op.get("content")
        if existing_content is None:
            # merge_object into a missing file = create it, so the first setup write can
            # use the same merge op as later merges (the materials example does this).
            if action == "merge_object" and not pointer_str:
                doc: Any = {}
            else:
                raise ValueError(f"{action} requires an existing file at {path!r}")
        else:
            try:
                doc = json.loads(existing_content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"existing file at {path!r} is not valid JSON: {exc}") from exc

        if action == "replace_json":
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                before = op.get("before")
                if before is not None:
                    current = _json_pointer_get(doc, tokens)
                    if current != before:
                        raise ValueError(
                            f"before mismatch at {pointer_str!r}: "
                            f"expected {before!r}, got {current!r}"
                        )
                _json_pointer_set(doc, tokens, value)
            else:
                if not isinstance(value, dict):
                    raise ValueError("replace_json without pointer requires value to be a dict")
                before = op.get("before")
                if before is not None and doc != before:
                    raise ValueError("before mismatch: document does not match expected value")
                doc = value
        elif action == "merge_object":
            if not isinstance(value, dict):
                raise ValueError("merge_object requires value to be a dict")
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                target = _json_pointer_get(doc, tokens)
                if not isinstance(target, dict):
                    raise ValueError(f"merge_object target at {pointer_str!r} is not an object")
                target.update(value)
            else:
                if not isinstance(doc, dict):
                    raise ValueError("merge_object without pointer requires document to be an object")
                doc.update(value)
        elif action == "append_array_item":
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                target = _json_pointer_get(doc, tokens)
                if not isinstance(target, list):
                    raise ValueError(f"append_array_item target at {pointer_str!r} is not an array")
                target.append(value)
            else:
                if not isinstance(doc, list):
                    raise ValueError("append_array_item without pointer requires document to be an array")
                doc.append(value)

        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()

    raise ValueError(f"unsupported action_type: {action!r}")


# Whole-document before/after snapshots are only echoed for full-replace /
# merge / append diffs; for a large setup/mesh/load-case file that doubles the
# response size for no agent benefit (changed_paths/added_paths already convey
# the delta). Cap the echo: keep small docs verbatim, replace large ones with a
# marker. ~4 KB ≈ a comfortably-sized setup doc; bigger gets summarized.
_ARTIFACT_DOC_MAX_CHARS = 4000


def _capped_artifact_doc(doc: Any) -> Any:
    """Return ``doc`` unchanged if small, else a compact size marker."""
    if doc is None:
        return None
    try:
        size = len(json.dumps(doc, ensure_ascii=False))
    except (TypeError, ValueError):
        return doc
    if size <= _ARTIFACT_DOC_MAX_CHARS:
        return doc
    return {
        "_omitted": "document too large to echo; see changed_paths/added_paths/removed_paths",
        "size_bytes": size,
    }


def _apply_patches_to_package(
    package_path: Path,
    patches: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """
    Apply all patches atomically to the package; returns (changed_paths, warning_msgs, artifact_diffs).
    Reads the whole ZIP, applies patches in-memory, writes a new ZIP atomically.
    """
    with zipfile.ZipFile(package_path, mode="r") as zf:
        existing_names = set(zf.namelist())
        manifest_data = json.loads(zf.read("manifest.json")) if "manifest.json" in existing_names else {}
        members: dict[str, bytes] = {}
        for name in existing_names:
            info = zf.getinfo(name)
            if not info.is_dir():
                members[name] = zf.read(name)

    changed_paths: list[str] = []
    warnings_out: list[str] = []
    artifact_diffs: list[dict[str, Any]] = []

    for patch in patches:
        path: str = patch.get("path", "")
        existing_bytes: bytes | None = members.get(path)
        new_bytes = _apply_single_patch(existing_bytes, patch, path)
        members[path] = new_bytes
        changed_paths.append(path)

        action = patch.get("action_type") or patch.get("operation") or ""
        diff_meta: dict[str, Any] = {
            "path": path,
            "operation": action,
            "json_pointer": patch.get("pointer", ""),
        }

        if action == "create_file":
            diff_meta["before"] = None
            diff_meta["after"] = patch.get("content")
            diff_meta["changed_paths"] = []
            diff_meta["added_paths"] = [""]
            diff_meta["removed_paths"] = []
        elif action in ("replace_json", "merge_object", "append_array_item"):
            before_doc = json.loads(existing_bytes) if existing_bytes else None
            after_doc = json.loads(new_bytes)
            changed, added, removed = _json_diff_paths(before_doc, after_doc)
            diff_meta["changed_paths"] = changed
            diff_meta["added_paths"] = added
            diff_meta["removed_paths"] = removed
            if action == "replace_json" and patch.get("pointer"):
                pointer_str = patch.get("pointer", "")
                tokens = _parse_json_pointer(pointer_str)
                diff_meta["before"] = _json_pointer_get(before_doc, tokens) if before_doc is not None else None
                diff_meta["after"] = _json_pointer_get(after_doc, tokens)
            else:
                diff_meta["before"] = _capped_artifact_doc(before_doc)
                diff_meta["after"] = _capped_artifact_doc(after_doc)
        else:
            # Fallback for any future actions
            diff_meta["before"] = None
            diff_meta["after"] = None
            diff_meta["changed_paths"] = []
            diff_meta["added_paths"] = []
            diff_meta["removed_paths"] = []

        artifact_diffs.append(diff_meta)

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".aieng", dir=package_path.parent
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        manifest_json = (json.dumps(manifest_data, indent=2, sort_keys=True) + "\n").encode()
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            out_zf.writestr("manifest.json", manifest_json)
            seen: set[str] = {"manifest.json"}
            for name, data in members.items():
                if name in seen or name == "manifest.json":
                    continue
                seen.add(name)
                out_zf.writestr(name, data)
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return changed_paths, warnings_out, artifact_diffs


def _compute_stale_artifacts(
    changed_paths: list[str],
    refreshed_paths: list[str],
) -> list[str]:
    """Return stale artifact paths: those in _SETUP_STALE_ARTIFACTS not yet refreshed."""
    stale = []
    refreshed_set = set(refreshed_paths)
    for art in _SETUP_STALE_ARTIFACTS:
        if art not in refreshed_set:
            stale.append(art)
    return stale


def compute_topology_hash(topology_map: dict[str, Any] | None) -> str | None:
    """Return a stable SHA-256 hash of the topology map.

    The hash is computed from a canonical JSON representation so that equivalent
    topology maps produce identical hashes regardless of key ordering or
    formatting.  ``None`` is returned for empty or non-serializable input.
    """
    if not topology_map:
        return None
    try:
        canonical = json.dumps(
            topology_map,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_FACE_POINTER_RE = re.compile(r"@face:([A-Za-z0-9_]+)")


def validate_cae_topology_references(package_path: str | Path) -> dict[str, Any]:
    """Validate that face-scoped CAE references still match the current topology.

    Compares the topology hash recorded in ``simulation/cae_mapping.json`` and
    ``simulation/setup.yaml`` with the hash of the current
    ``geometry/topology_map.json``.  Also checks that every referenced ``face_id``
    exists in the current topology.

    Returns a dictionary with:
      - ``topology_available`` / ``topology_hash_current`` / ``topology_hash_expected``
      - ``hash_status``:
          ``"ok"`` | ``"mismatch"`` | ``"missing_hash"`` |
          ``"missing_topology"`` | ``"no_face_refs"`` | ``"no_topology"``
      - ``cae_mapping_stale``: bool
      - ``valid``: bool
      - ``missing_face_ids``: list[str]
      - ``stale_references``: list[dict] with location and face_id
      - ``warnings``: human-readable list
    """
    result: dict[str, Any] = {
        "topology_available": False,
        "topology_hash_current": None,
        "topology_hash_expected": None,
        "hash_status": "no_topology",
        "cae_mapping_stale": False,
        "valid": True,
        "missing_face_ids": [],
        "stale_references": [],
        "warnings": [],
    }

    pkg = Path(package_path)
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            topology_raw = (
                zf.read("geometry/topology_map.json")
                if "geometry/topology_map.json" in names
                else None
            )
            cae_mapping_raw = (
                zf.read("simulation/cae_mapping.json")
                if "simulation/cae_mapping.json" in names
                else None
            )
            setup_raw = (
                zf.read("simulation/setup.yaml")
                if "simulation/setup.yaml" in names
                else None
            )
            load_case_names = [
                n
                for n in names
                if n.startswith("simulation/load_cases/") and n.endswith(".json")
            ]
            load_cases: dict[str, Any] = {}
            for lc in load_case_names:
                try:
                    load_cases[lc] = json.loads(zf.read(lc))
                except Exception:
                    pass
    except Exception as exc:
        result["warnings"].append(f"Failed to read package: {exc}")
        result["valid"] = False
        return result

    topology: dict[str, Any] | None = None
    face_index: dict[str, dict[str, Any]] = {}
    if topology_raw is not None:
        try:
            topology = json.loads(topology_raw)
            result["topology_available"] = True
            result["topology_hash_current"] = compute_topology_hash(topology)
            face_index = {
                e["id"]: e
                for e in (topology.get("entities") or [])
                if isinstance(e, dict) and e.get("type") == "face" and "id" in e
            }
        except json.JSONDecodeError as exc:
            result["warnings"].append(
                f"geometry/topology_map.json is not valid JSON: {exc}"
            )
            result["valid"] = False
            return result

    cae_mapping: dict[str, Any] = {}
    if cae_mapping_raw is not None:
        try:
            cae_mapping = json.loads(cae_mapping_raw)
        except json.JSONDecodeError:
            result["warnings"].append("simulation/cae_mapping.json is not valid JSON.")

    expected_hash: str | None = None
    if isinstance(cae_mapping, dict):
        expected_hash = cae_mapping.get("topology_hash")
    if not expected_hash and setup_raw is not None:
        try:
            setup_doc = yaml.safe_load(setup_raw)
            if isinstance(setup_doc, dict):
                expected_hash = setup_doc.get("topology_hash")
        except Exception:
            pass
    result["topology_hash_expected"] = expected_hash

    if isinstance(cae_mapping, dict) and cae_mapping.get("stale"):
        result["cae_mapping_stale"] = True

    stale_refs: list[dict[str, Any]] = []
    missing_face_ids: set[str] = set()
    referenced_face_ids: set[str] = set()

    def _add_reference(face_id: str, context: dict[str, Any]) -> None:
        referenced_face_ids.add(face_id)
        if face_id not in face_index:
            missing_face_ids.add(face_id)
            stale_refs.append(context)

    if isinstance(cae_mapping, dict):
        for i, mapping in enumerate(cae_mapping.get("mappings") or []):
            if not isinstance(mapping, dict):
                continue
            for fid in mapping.get("face_ids") or []:
                _add_reference(
                    str(fid),
                    {
                        "location": "simulation/cae_mapping.json",
                        "mapping_index": i,
                        "cae_entity": mapping.get("cae_entity"),
                        "feature_id": (mapping.get("maps_to") or {}).get("feature_id"),
                        "face_id": fid,
                        "reason": "face_id not found in current topology",
                    },
                )

    for lc_path, lc_doc in load_cases.items():
        if not isinstance(lc_doc, dict):
            continue

        def _scan(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    _scan(value, f"{path}.{key}" if path else key)
            elif isinstance(obj, list):
                for idx, value in enumerate(obj):
                    _scan(value, f"{path}[{idx}]")
            elif isinstance(obj, str):
                for match in _FACE_POINTER_RE.finditer(obj):
                    fid = match.group(1)
                    _add_reference(
                        fid,
                        {
                            "location": lc_path,
                            "pointer": path,
                            "face_id": fid,
                            "reason": "face pointer not found in current topology",
                        },
                    )

        _scan(lc_doc)

    if setup_raw is not None:
        try:
            setup_doc = yaml.safe_load(setup_raw)
            if isinstance(setup_doc, dict):
                for section in ("boundary_conditions", "loads"):
                    for i, item in enumerate(setup_doc.get(section) or []):
                        if not isinstance(item, dict):
                            continue
                        for fid in item.get("target_face_ids") or []:
                            _add_reference(
                                str(fid),
                                {
                                    "location": "simulation/setup.yaml",
                                    "section": section,
                                    "index": i,
                                    "face_id": fid,
                                    "reason": "target_face_id not found in current topology",
                                },
                            )
        except Exception:
            pass

    has_face_refs = bool(referenced_face_ids)

    if result["topology_available"]:
        if has_face_refs:
            if expected_hash:
                result["hash_status"] = (
                    "ok" if expected_hash == result["topology_hash_current"] else "mismatch"
                )
            else:
                result["hash_status"] = "missing_hash"
        else:
            result["hash_status"] = "no_face_refs"
    else:
        result["hash_status"] = "missing_topology" if has_face_refs else "no_topology"

    result["missing_face_ids"] = sorted(missing_face_ids)
    result["stale_references"] = stale_refs

    if result["hash_status"] == "missing_hash":
        result["warnings"].append(
            "CAE setup artifacts do not record a topology_hash; "
            "face references cannot be checked for topology drift."
        )
    elif result["hash_status"] == "mismatch":
        result["warnings"].append(
            f"Topology hash mismatch: expected {expected_hash}, "
            f"current {result['topology_hash_current']}. "
            "CAE face references may be stale."
        )
    elif result["hash_status"] == "missing_topology":
        result["warnings"].append(
            "geometry/topology_map.json missing; cannot validate face references."
        )
    if result["cae_mapping_stale"]:
        result["warnings"].append(
            "simulation/cae_mapping.json is marked stale — "
            "re-run AI preprocessing to refresh face references."
        )
    if missing_face_ids:
        result["warnings"].append(
            f"Referenced face IDs missing from current topology: {sorted(missing_face_ids)}."
        )

    result["valid"] = (
        result["hash_status"]
        in {"ok", "missing_hash", "no_face_refs", "no_topology"}
        and not result["cae_mapping_stale"]
        and not missing_face_ids
    )
    return result


# ── Adaptive CAE face rebind helpers ──────────────────────────────────────────

def _face_centroid(face: dict[str, Any]) -> tuple[float, float, float]:
    """Return the centroid of a face entity from its bounding box."""
    bbox = face.get("bounding_box", [])
    if len(bbox) >= 6:
        return (
            (float(bbox[0]) + float(bbox[3])) / 2.0,
            (float(bbox[1]) + float(bbox[4])) / 2.0,
            (float(bbox[2]) + float(bbox[5])) / 2.0,
        )
    return (0.0, 0.0, 0.0)


def _bbox_diagonal(bbox: list[float]) -> float:
    if len(bbox) >= 6:
        return math.sqrt(
            (bbox[3] - bbox[0]) ** 2
            + (bbox[4] - bbox[1]) ** 2
            + (bbox[5] - bbox[2]) ** 2
        )
    return 0.0


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float] | None:
    x, y, z = v
    mag = math.sqrt(x * x + y * y + z * z)
    if mag < 1e-9:
        return None
    return (x / mag, y / mag, z / mag)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _compute_body_id_map(
    old_topology: dict[str, Any], new_topology: dict[str, Any]
) -> dict[str, str]:
    """Map old solid body IDs to new solid body IDs by name, then bbox IoU."""
    old_solids = [
        e for e in (old_topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "solid" and "id" in e
    ]
    new_solids = [
        e for e in (new_topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "solid" and "id" in e
    ]
    if not old_solids or not new_solids:
        return {}

    # First pass: unique name match.
    old_by_name: dict[str, dict[str, Any]] = {}
    for s in old_solids:
        name = s.get("name") or s["id"]
        old_by_name.setdefault(name, s)
    new_by_name: dict[str, list[dict[str, Any]]] = {}
    for s in new_solids:
        name = s.get("name") or s["id"]
        new_by_name.setdefault(name, []).append(s)

    mapping: dict[str, str] = {}
    for old in old_solids:
        name = old.get("name") or old["id"]
        if name in new_by_name and len(new_by_name[name]) == 1:
            mapping[old["id"]] = new_by_name[name][0]["id"]

    # Fallback: bbox volume overlap for any unmatched old bodies.
    def _volume(bb: list[float]) -> float:
        if len(bb) < 6:
            return 0.0
        return max(0.0, bb[3] - bb[0]) * max(0.0, bb[4] - bb[1]) * max(0.0, bb[5] - bb[2])

    def _overlap(a: list[float], b: list[float]) -> float:
        if len(a) < 6 or len(b) < 6:
            return 0.0
        dx = max(0.0, min(a[3], b[3]) - max(a[0], b[0]))
        dy = max(0.0, min(a[4], b[4]) - max(a[1], b[1]))
        dz = max(0.0, min(a[5], b[5]) - max(a[2], b[2]))
        return dx * dy * dz

    for old in old_solids:
        if old["id"] in mapping:
            continue
        old_bb = old.get("bounding_box", [])
        best_iou = 0.0
        best_new_id: str | None = None
        for new in new_solids:
            if new["id"] in mapping.values():
                continue
            new_bb = new.get("bounding_box", [])
            inter = _overlap(old_bb, new_bb)
            union = _volume(old_bb) + _volume(new_bb) - inter
            iou = inter / union if union > 0 else 0.0
            if iou > best_iou:
                best_iou = iou
                best_new_id = new["id"]
        if best_new_id and best_iou > 0.1:
            mapping[old["id"]] = best_new_id
    return mapping


def _score_face_match(
    old_face: dict[str, Any],
    new_face: dict[str, Any],
    scale: float,
) -> float:
    """Return a similarity score in [0, 1] for two face entities.

    Higher is better. ``scale`` is a characteristic body length used to normalize
    centroid distances.
    """
    old_type = old_face.get("surface_type", "unknown")
    new_type = new_face.get("surface_type", "unknown")
    if old_type != new_type:
        return 0.0

    old_area = float(old_face.get("area") or 0.0)
    new_area = float(new_face.get("area") or 0.0)
    old_centroid = _face_centroid(old_face)
    new_centroid = _face_centroid(new_face)
    dist = _distance(old_centroid, new_centroid)
    centroid_score = max(0.0, 1.0 - dist / max(scale, 1e-6))

    if old_type == "plane":
        old_normal = _normalize(tuple(old_face.get("normal", [0, 0, 1])))
        new_normal = _normalize(tuple(new_face.get("normal", [0, 0, 1])))
        if old_normal is None or new_normal is None:
            normal_score = 0.0
        else:
            normal_score = abs(_dot(old_normal, new_normal))
        return 0.5 * normal_score + 0.5 * centroid_score

    if old_type == "cylinder":
        old_axis = _normalize(tuple(old_face.get("normal", [0, 0, 1])))
        new_axis = _normalize(tuple(new_face.get("normal", [0, 0, 1])))
        axis_score = abs(_dot(old_axis or (0, 0, 1), new_axis or (0, 0, 1)))
        old_r = float(old_face.get("radius") or 0.0)
        new_r = float(new_face.get("radius") or 0.0)
        if old_r <= 0 or new_r <= 0:
            radius_score = 0.0
        else:
            radius_score = max(0.0, 1.0 - abs(old_r - new_r) / max(old_r, new_r))
        return 0.45 * axis_score + 0.35 * radius_score + 0.2 * centroid_score

    if old_type == "sphere":
        old_r = float(old_face.get("radius") or 0.0)
        new_r = float(new_face.get("radius") or 0.0)
        if old_r <= 0 or new_r <= 0:
            radius_score = 0.0
        else:
            radius_score = max(0.0, 1.0 - abs(old_r - new_r) / max(old_r, new_r))
        return 0.6 * radius_score + 0.4 * centroid_score

    # Generic fallback: area + centroid.
    if old_area > 0 and new_area > 0:
        area_score = max(0.0, 1.0 - abs(old_area - new_area) / max(old_area, new_area))
    else:
        area_score = 0.0
    return 0.5 * area_score + 0.5 * centroid_score


def _match_face(
    old_face: dict[str, Any],
    candidate_faces: list[dict[str, Any]],
    scale: float,
    threshold: float,
) -> tuple[dict[str, Any] | None, float, bool]:
    """Pick the best new face for an old face and report ambiguity.

    Returns ``(new_face_or_none, best_score, is_ambiguous)``.
    """
    if not candidate_faces:
        return None, 0.0, False

    scored = [
        (face, _score_face_match(old_face, face, scale))
        for face in candidate_faces
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_face, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    if best_score < threshold:
        return None, best_score, False
    # Ambiguous if the runner-up is nearly as good.
    ambiguous = (best_score - second_score) < 0.08 and second_score >= threshold * 0.9
    return best_face, best_score, ambiguous


def rebind_cae_faces(
    old_cae_mapping: dict[str, Any],
    old_topology: dict[str, Any],
    new_topology: dict[str, Any],
    *,
    default_threshold: float = 0.75,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Rebind face-scoped CAE references from an old topology to a new topology.

    Matches each referenced old face to the geometrically closest new face using
    surface type, centroid, normal/axis, radius, and area. Confidence is scored
    per match; low-confidence or ambiguous matches stay unresolved so callers can
    refuse the solve honestly instead of silently mis-binding loads/BCs.

    Args:
        old_cae_mapping: the baseline ``cae_mapping.json`` document (contains
            ``mappings`` with ``face_ids``).
        old_topology: the baseline ``topology_map.json`` the mapping was bound to.
        new_topology: the regenerated ``topology_map.json`` to rebind against.
        default_threshold: minimum score for a match to be accepted.
        thresholds: per-surface-type overrides, e.g. ``{"plane": 0.85}``.

    Returns:
        A dict with:
          - ``cae_mapping``: a deep copy of ``old_cae_mapping`` with ``face_ids``
            rewritten to the new topology and ``topology_hash`` updated.
          - ``rebinds``: list of ``{old_face_id, new_face_id, score, confidence}``.
          - ``unresolved_face_ids``: face IDs that could not be matched.
          - ``ambiguous_face_ids``: face IDs with a close runner-up.
          - ``all_resolved``: True when every referenced face was rebound.
          - ``topology_hash``: hash of ``new_topology`` written into the mapping.
    """
    thresholds = dict(thresholds) if thresholds else {}
    thresholds.setdefault("plane", 0.85)
    thresholds.setdefault("cylinder", 0.80)
    thresholds.setdefault("sphere", 0.80)

    body_map = _compute_body_id_map(old_topology, new_topology)

    old_faces = {
        e["id"]: e
        for e in (old_topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "face" and "id" in e
    }
    new_faces_list = [
        e
        for e in (new_topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "face" and "id" in e
    ]

    # Characteristic scale for centroid-distance normalization.
    solids = [e for e in (new_topology.get("entities") or []) if isinstance(e, dict) and e.get("type") == "solid"]
    scale = max(
        (_bbox_diagonal(s.get("bounding_box", [])) for s in solids),
        default=1.0,
    ) or 1.0

    new_cae_mapping = copy.deepcopy(old_cae_mapping)
    new_cae_mapping["topology_hash"] = compute_topology_hash(new_topology)
    new_cae_mapping["rebind_metadata"] = {
        "rebound": True,
        "rebind_method": "geometric_nearest_face",
        "body_id_map": body_map,
    }

    rebinds: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    ambiguous: set[str] = set()
    old_to_new: dict[str, str] = {}

    mappings = new_cae_mapping.get("mappings") if isinstance(new_cae_mapping.get("mappings"), list) else []

    # Collect unique referenced old face IDs across all mappings.
    referenced_old_ids: set[str] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for fid in mapping.get("face_ids") or []:
            referenced_old_ids.add(str(fid))

    for old_id in referenced_old_ids:
        old_face = old_faces.get(old_id)
        if old_face is None:
            unresolved.add(old_id)
            continue

        surface_type = old_face.get("surface_type", "unknown")
        threshold = thresholds.get(surface_type, default_threshold)

        # Restrict candidates to the matching body when we have a body map.
        old_body = old_face.get("body_id")
        new_body = body_map.get(old_body) if old_body else None
        if new_body:
            candidates = [f for f in new_faces_list if f.get("body_id") == new_body]
        else:
            candidates = new_faces_list

        best, score, is_ambiguous = _match_face(old_face, candidates, scale, threshold)
        if best is None:
            unresolved.add(old_id)
            continue
        if is_ambiguous:
            ambiguous.add(old_id)
        new_id = best["id"]
        old_to_new[old_id] = new_id
        level = "high" if score >= 0.92 else ("medium" if score >= threshold else "low")
        rebinds.append({
            "old_face_id": old_id,
            "new_face_id": new_id,
            "score": round(score, 4),
            "confidence": level,
            "surface_type": surface_type,
        })

    # Rewrite face_ids in mappings.
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        new_face_ids: list[str] = []
        for fid in mapping.get("face_ids") or []:
            fid_str = str(fid)
            if fid_str in unresolved or fid_str in ambiguous:
                # Keep the old ID so downstream diagnostics are clear.
                new_face_ids.append(fid_str)
            else:
                new_face_ids.append(old_to_new.get(fid_str, fid_str))
        mapping["face_ids"] = new_face_ids

    new_cae_mapping["rebind_metadata"]["rebinds"] = rebinds
    new_cae_mapping["rebind_metadata"]["unresolved_face_ids"] = sorted(unresolved)
    new_cae_mapping["rebind_metadata"]["ambiguous_face_ids"] = sorted(ambiguous)

    return {
        "cae_mapping": new_cae_mapping,
        "topology_hash": compute_topology_hash(new_topology),
        "rebinds": rebinds,
        "unresolved_face_ids": sorted(unresolved),
        "ambiguous_face_ids": sorted(ambiguous),
        "all_resolved": not unresolved and not ambiguous,
    }


def _feature_list_from_graph(feature_graph: dict[str, Any]) -> list[dict[str, Any]]:
    features = feature_graph.get("features", [])
    if isinstance(features, dict):
        return [v for v in features.values() if isinstance(v, dict)]
    if isinstance(features, list):
        return [v for v in features if isinstance(v, dict)]
    return []


def _validate_cad_parameter_edit_contract(
    package_path: Path,
    feature_id: str,
    parameter_name: str,
    new_value: Any,
) -> dict[str, Any]:
    with zipfile.ZipFile(package_path, "r") as zf:
        names = set(zf.namelist())
        if "graph/feature_graph.json" not in names:
            raise ValueError("graph/feature_graph.json missing; cannot validate editable CAD parameter")
        feature_graph = json.loads(zf.read("graph/feature_graph.json"))

    feature = next((f for f in _feature_list_from_graph(feature_graph) if f.get("id") == feature_id), None)
    if feature is None:
        raise ValueError(f"feature_id not found in feature graph: {feature_id}")

    params = feature.get("parameters", [])
    if isinstance(params, dict):
        params = [{"name": k, **(v if isinstance(v, dict) else {"current_value": v})} for k, v in params.items()]
    if not isinstance(params, list):
        raise ValueError(f"feature {feature_id} does not declare editable parameters")

    param = next(
        (
            p for p in params
            if isinstance(p, dict)
            and (p.get("name") == parameter_name or p.get("cad_parameter_name") == parameter_name)
        ),
        None,
    )
    if param is None:
        raise ValueError(f"parameter {parameter_name!r} is not declared on feature {feature_id!r}")

    editability = param.get("editability")
    if editability is False or (isinstance(editability, dict) and editability.get("executable") is False):
        raise ValueError(f"parameter {parameter_name!r} on feature {feature_id!r} is not editable")

    if isinstance(new_value, (int, float)) and not isinstance(new_value, bool):
        min_value = param.get("min_value")
        max_value = param.get("max_value")
        if min_value is not None and new_value < min_value:
            raise ValueError(f"new_value {new_value!r} is below min_value {min_value!r}")
        if max_value is not None and new_value > max_value:
            raise ValueError(f"new_value {new_value!r} is above max_value {max_value!r}")

    _model_kind = feature_graph.get("model_kind", "auto")
    if _model_kind not in ("mechanical", "organic", "auto"):
        _model_kind = "auto"
    return {
        "feature": feature,
        "parameter": param,
        "cad_object_name": feature.get("cad_object_name") or feature_id,
        "cad_parameter_name": param.get("cad_parameter_name") or parameter_name,
        "model_kind": _model_kind,
    }


def _write_modified_step_into_package(
    package_path: Path,
    source_step: Path,
    *,
    feature_id: str,
    parameter_name: str,
) -> str:
    if not source_step.exists():
        raise FileNotFoundError(f"modified STEP artifact not found: {source_step}")
    safe_feature = "".join(c if c.isalnum() or c in "._-" else "_" for c in feature_id)
    safe_param = "".join(c if c.isalnum() or c in "._-" else "_" for c in parameter_name)
    dest = f"geometry/modified_{safe_feature}_{safe_param}.step"

    with zipfile.ZipFile(package_path, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != dest]
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {"resources": {}}

    resources = manifest.setdefault("resources", {})
    geometry = resources.setdefault("geometry", {})
    if not isinstance(geometry, dict):
        geometry = {}
        resources["geometry"] = geometry
    geometry["modified"] = dest

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen or info.filename == "manifest.json":
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "geometry/" not in seen:
                out_zf.writestr("geometry/", b"")
            out_zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            out_zf.writestr(dest, source_step.read_bytes())
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return dest


def _read_revalidation_status(package_path: Path) -> dict[str, Any] | None:
    """Read state/revalidation_status.json from the package; returns None if absent."""
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            if REVALIDATION_STATUS_PATH in zf.namelist():
                return json.loads(zf.read(REVALIDATION_STATUS_PATH))
    except Exception:
        pass
    return None


def _write_revalidation_status_dict(package_path: Path, status: dict[str, Any]) -> None:
    """Atomically write a pre-built revalidation status dict into the package."""
    with zipfile.ZipFile(package_path, "r") as zf:
        members = [
            (info, b"" if info.is_dir() else zf.read(info.filename))
            for info in zf.infolist()
            if info.filename != REVALIDATION_STATUS_PATH
        ]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen:
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "state/" not in seen:
                out_zf.writestr("state/", b"")
            out_zf.writestr(
                REVALIDATION_STATUS_PATH,
                json.dumps(status, indent=2) + "\n",
            )
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _write_revalidation_status(
    package_path: Path,
    *,
    requires_revalidation: bool,
    reason: str,
    triggering_tool: str,
    affected_artifacts: list[str],
    current_geometry_revision: int | None = None,
    last_validated_geometry_revision: int | None = None,
    stale_since_geometry_revision: int | None = None,
    validated_by_run_id: str | None = None,
) -> None:
    """Atomically write/overwrite state/revalidation_status.json inside the package.

    Runtime compatibility writer for pre-assembled status values. New geometry
    edit / solver validation transitions should use the core revalidation
    helpers via _record_geometry_edit_in_package and
    _record_solver_validation_in_package.

    When requires_revalidation=True, downstream CAE results are stale relative
    to the current geometry. When False, the geometry and results are in sync
    (a new solver run has completed).

    Revision fields (all optional, all defaulting to None if omitted):
      current_geometry_revision         — monotonically incremented on each edit
      last_validated_geometry_revision  — the revision last validated by a solver run
      stale_since_geometry_revision     — the revision at which staleness began
      validated_by_run_id               — run_id of the solver run that cleared stale
    """
    status: dict[str, Any] = {
        "schema_version": "0.2",
        "geometry_modified": requires_revalidation,
        "requires_revalidation": requires_revalidation,
        "reason": reason,
        "triggering_tool": triggering_tool,
        "affected_artifacts": affected_artifacts,
        "affected_domains": ["result_summary", "field_summaries", "solver_outputs"],
        "claim_advancement": "none",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "current_geometry_revision": current_geometry_revision,
        "last_validated_geometry_revision": last_validated_geometry_revision,
        "stale_since_geometry_revision": stale_since_geometry_revision,
        "validated_by_run_id": validated_by_run_id,
    }
    _write_revalidation_status_dict(package_path, status)


def _record_geometry_edit_in_package(
    package_path: Path,
    *,
    affected_artifacts: list[str],
) -> int:
    """Record a geometry edit: increment current_geometry_revision, mark stale.

    Returns the new current_geometry_revision.
    """
    prev = _read_revalidation_status(package_path)
    new_status = _core_record_geometry_edit_status(prev, affected_artifacts=affected_artifacts)
    _write_revalidation_status_dict(package_path, new_status)
    return new_status["current_geometry_revision"]


def _record_solver_validation_in_package(
    package_path: Path,
    *,
    run_id: str | None = None,
) -> None:
    """Record that a solver run validated the current geometry revision."""
    prev = _read_revalidation_status(package_path)
    new_status = _core_record_solver_validation_status(prev, run_id=run_id)
    _write_revalidation_status_dict(package_path, new_status)


def _build_revalidation_response(rs: dict[str, Any] | None) -> dict[str, Any]:
    """Build the revalidation_status dict for injection into API responses."""
    return _core_build_revalidation_response(rs)


def _read_audit_events_from_package(package_path: Path) -> list[dict[str, Any]]:
    """Read all audit events from audit/events.jsonl; returns [] if absent or unreadable."""
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            if AUDIT_EVENTS_PATH in zf.namelist():
                raw = zf.read(AUDIT_EVENTS_PATH).decode("utf-8")
                return _parse_audit_events_jsonl(raw)
    except Exception:
        pass
    return []


def _append_audit_event_to_package(
    package_path: Path,
    event: dict[str, Any],
) -> None:
    """Atomically append one audit event line to audit/events.jsonl inside the package."""
    existing = _read_audit_events_from_package(package_path)
    new_jsonl = _serialize_audit_events_jsonl(existing) + _serialize_audit_events_jsonl([event])
    with zipfile.ZipFile(package_path, "r") as zf:
        members = [
            (info, b"" if info.is_dir() else zf.read(info.filename))
            for info in zf.infolist()
            if info.filename != AUDIT_EVENTS_PATH
        ]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen:
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "audit/" not in seen:
                out_zf.writestr("audit/", b"")
            out_zf.writestr(AUDIT_EVENTS_PATH, new_jsonl.encode("utf-8"))
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _generate_artifact_manifest(package_path: Path) -> dict[str, Any]:
    """Generate the on-demand artifact manifest from the current package ZIP state.

    Read-only: does not write to the package or advance any engineering claim.
    Delegates classification and manifest assembly to aieng.package_manifest.
    """
    rs = _read_revalidation_status(package_path)
    with zipfile.ZipFile(package_path, "r") as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))
    return _core_generate_artifact_manifest(names, revalidation_status=rs)




def _resolve_evidence_reference(
    *,
    path: str,
    pkg_names: set[str],
    evidence_entries: list[dict[str, Any]],
    revalidation_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Thin wrapper around aieng.evidence_resolver.resolve_evidence_reference.

    Preserves the call-site argument names used throughout this module while
    delegating all logic to the core pure function.
    """
    return _core_resolve_evidence_reference(
        path=path,
        package_paths=pkg_names,
        evidence_entries=evidence_entries,
        revalidation_status=revalidation_status,
    )


def _run_package_consistency_checks(package_path: Path) -> list[dict[str, Any]]:
    """Thin wrapper: opens the .aieng ZIP once and delegates to aieng core.

    Does not mutate the package or advance claims.
    """
    _DISP_PATH = "results/fields/displacement.summary.json"
    _STRESS_PATH = "results/fields/stress.summary.json"
    _EI_PATH = "results/evidence_index.json"

    with zipfile.ZipFile(package_path, "r") as zf:
        pkg_names: set[str] = set(zf.namelist())
        evidence_raw = zf.read(_EI_PATH) if _EI_PATH in pkg_names else None
        audit_raw = zf.read(AUDIT_EVENTS_PATH) if AUDIT_EVENTS_PATH in pkg_names else None
        disp_raw = zf.read(_DISP_PATH) if _DISP_PATH in pkg_names else None
        stress_raw = zf.read(_STRESS_PATH) if _STRESS_PATH in pkg_names else None
        proposal_paths = sorted(
            n for n in pkg_names if fnmatch.fnmatch(n, "claims/proposals/*.json")
        )
        proposal_data = [(p, zf.read(p)) for p in proposal_paths]

    rs = _read_revalidation_status(package_path)

    return _core_run_package_consistency_checks(
        package_paths=pkg_names,
        evidence_raw=evidence_raw,
        audit_raw=audit_raw,
        revalidation_status=rs,
        displacement_summary_raw=disp_raw,
        stress_summary_raw=stress_raw,
        claim_proposals=proposal_data,
    )




def _write_claim_proposal_to_package(
    package_path: Path,
    proposal: dict[str, Any],
) -> str:
    """Atomically write a claim proposal into claims/proposals/{proposal_id}.json.

    Returns the internal package path. Never creates or modifies claim maps.
    """
    proposal_id = proposal["proposal_id"]
    internal_path = f"{CLAIM_PROPOSALS_DIR}/{proposal_id}.json"
    data = (json.dumps(proposal, indent=2) + "\n").encode("utf-8")

    with zipfile.ZipFile(package_path, "r") as zf:
        members = [
            (info, b"" if info.is_dir() else zf.read(info.filename))
            for info in zf.infolist()
            if info.filename != internal_path
        ]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, d in members:
                if info.filename in seen:
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, d)
            if "claims/" not in seen:
                out_zf.writestr("claims/", b"")
            if f"{CLAIM_PROPOSALS_DIR}/" not in seen:
                out_zf.writestr(f"{CLAIM_PROPOSALS_DIR}/", b"")
            out_zf.writestr(internal_path, data)
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return internal_path


def _read_claim_proposals_from_package(
    package_path: Path,
) -> list[dict[str, Any]]:
    """Return all claim proposal dicts from claims/proposals/*.json, sorted by created_at then proposal_id.

    Read-only. Never modifies the package or creates claim maps.
    Returns an empty list when the package has no proposals.
    """
    proposals: list[dict[str, Any]] = []
    with zipfile.ZipFile(package_path, "r") as zf:
        for name in zf.namelist():
            if fnmatch.fnmatch(name, f"{CLAIM_PROPOSALS_DIR}/*.json"):
                try:
                    proposals.append(json.loads(zf.read(name)))
                except (json.JSONDecodeError, KeyError):
                    pass
    proposals.sort(key=lambda p: (p.get("created_at") or "", p.get("proposal_id") or ""))
    return proposals



def _build_claim_support_packet(
    *,
    proposal: dict[str, Any],
    proposal_path: str,
    pkg_names: set[str],
    evidence_entries: list[dict[str, Any]],
    revalidation_status: dict[str, Any] | None,
    audit_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a read-only support packet for a claim proposal.

    Runtime composition only: evidence resolution and review-readiness semantics
    are delegated to aieng core; this helper wires already-read package state
    into the stable support-packet response shape.

    Pure function — all inputs are already-read package metadata. Does not
    open the ZIP, does not mutate any artifact, and never creates or advances
    claim maps. Reuses _resolve_evidence_reference for each supporting evidence
    path.
    """
    ev_paths: list[str] = proposal.get("supporting_evidence") or []
    resolved_evidence: list[dict[str, Any]] = [
        _resolve_evidence_reference(
            path=p,
            pkg_names=pkg_names,
            evidence_entries=evidence_entries,
            revalidation_status=revalidation_status,
        )
        for p in ev_paths
    ]

    stale_count = sum(
        1 for ref in resolved_evidence
        if "evidence_from_stale_geometry_state" in ref.get("warnings", [])
    )
    missing_count = sum(1 for ref in resolved_evidence if not ref.get("usable_for_claim_proposal"))

    # Runtime filtering only; stable packet shape is assembled by aieng core.
    related: list[dict[str, Any]] = [
        e for e in audit_events
        if proposal_path in (e.get("artifacts_written") or [])
    ]

    review_readiness = _build_review_readiness(
        ev_paths=ev_paths,
        missing_count=missing_count,
        stale_count=stale_count,
        proposal_status=proposal.get("status"),
        pkg_names=pkg_names,
    )

    return _core_build_claim_support_packet(
        proposal=proposal,
        proposal_path=proposal_path,
        resolved_supporting_evidence=resolved_evidence,
        related_audit_events=related,
        review_readiness=review_readiness,
    )


def _unpack_geometry_from_package(package_path: Path, internal_path: str) -> Path:
    """Extract a geometry file from inside a .aieng package to a temporary file.

    Returns the path to the temporary file. The caller is responsible for cleanup.
    """
    with zipfile.ZipFile(package_path, "r") as zf:
        data = zf.read(internal_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="aieng_mesh_geometry_"))
    temp_path = temp_dir / Path(internal_path).name
    temp_path.write_bytes(data)
    return temp_path


def _write_mesh_into_package_atomic(
    package_path: Path,
    mesh_file: Path,
    internal_path: str,
) -> str:
    """Atomically write a mesh file into a .aieng package.

    Reads all existing members, updates manifest.resources.simulation.mesh,
    writes a new ZIP, and moves it over the original.
    """
    with zipfile.ZipFile(package_path, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != internal_path]
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {"resources": {}}

    resources = manifest.setdefault("resources", {})
    sim = resources.setdefault("simulation", {})
    if not isinstance(sim, dict):
        sim = {}
        resources["simulation"] = sim
    sim["mesh"] = internal_path

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen or info.filename == "manifest.json":
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "simulation/" not in seen:
                out_zf.writestr("simulation/", b"")
            if "simulation/mesh/" not in seen:
                out_zf.writestr("simulation/mesh/", b"")
            out_zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            out_zf.writestr(internal_path, mesh_file.read_bytes())
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return internal_path


def convert_stl_to_glb(stl_path: Path, glb_path: Path) -> dict[str, Any]:
    try:
        import trimesh
    except Exception as exc:
        return {"ok": False, "error": f"trimesh unavailable: {type(exc).__name__}: {exc}"}

    try:
        loaded = trimesh.load_mesh(stl_path, force="mesh")
        if isinstance(loaded, trimesh.Scene):
            scene = loaded
        else:
            scene = trimesh.Scene(loaded)
        glb_bytes = scene.export(file_type="glb")
        if isinstance(glb_bytes, str):
            glb_bytes = glb_bytes.encode("utf-8")
        glb_path.write_bytes(glb_bytes)
        bounds = scene.bounds.tolist() if getattr(scene, "bounds", None) is not None else None
        return {"ok": True, "glb_path": str(glb_path), "glb_size": glb_path.stat().st_size, "bounds": bounds}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def write_audit_log(settings: Settings, project_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    audit_id = uuid.uuid4().hex
    path = project_dir(settings, project_id) / "logs" / f"{kind}_{audit_id}.json"
    write_json(path, payload)
    return {
        "audit_id": audit_id,
        "audit_path": project_relpath(settings, project_id, path),
        "audit_url": f"/assets/projects/{project_id}/{project_relpath(settings, project_id, path)}",
    }


def extract_step_from_package(settings: Settings, project_id: str, package_path: Path) -> Path:
    for member in ("geometry/source.step", "geometry/normalized.step"):
        with zipfile.ZipFile(package_path) as archive:
            if member not in archive.namelist():
                continue
            suffix = Path(member).suffix or ".step"
            target = project_dir(settings, project_id) / "source" / f"{package_path.stem}_extracted{suffix}"
            target.write_bytes(archive.read(member))
            return target
    raise HTTPException(status_code=400, detail="package does not contain source STEP geometry")


def ensure_step_source(settings: Settings, project_id: str, project: dict[str, Any]) -> Path:
    source = resolve_project_path(settings, project_id, project.get("source_step"))
    if source and source.exists():
        return source
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path and package_path.exists():
        extracted = extract_step_from_package(settings, project_id, package_path)
        project["source_step"] = project_relpath(settings, project_id, extracted)
        save_project(settings, project)
        return extracted
    raise HTTPException(status_code=400, detail="STEP source not found")
