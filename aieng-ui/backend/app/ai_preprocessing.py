"""AI-driven FEA preprocessing setup generator.

Given a CAD model (via .aieng package) and a natural-language task description,
calls Claude to decide material, boundary conditions, loads, and mesh strategy —
then writes simulation/setup.yaml and simulation/cae_mapping.json into the package.

Geometry understanding is provided by the pluggable GeometryProvider system
(see geometry_providers.py). StaticPackageProvider reads the canonical
.aieng package and is always sufficient for build123d-generated geometry.
"""
from __future__ import annotations

import copy
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from .config import ensure_aieng_on_path
from .geometry_providers import (
    GeometryContext,
    StaticPackageProvider,
    build_geometry_context,
)
from .project_io import compute_topology_hash

_SYSTEM_PROMPT = """\
You are a structural FEA engineer. Your job is to generate a complete finite element \
analysis setup (material, boundary conditions, loads, mesh strategy) based on a \
CAD geometry description and a task specification.

Respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.
"""

_SETUP_SCHEMA = """\
{
  "material": "<material name from catalog>",
  "material_reason": "<one sentence why this material fits the task>",
  "boundary_conditions": [
    {
      "id": "bc_001",
      "target_feature_id": "<feature_id from graph, or null>",
      "target_face_ids": ["<face_id from topology, or empty list>"],
      "target_pointers": ["@face:<face_id> or @group:<group_id>, if B-Rep digest provides one"],
      "target_description": "<human-readable description of what surface is constrained>",
      "type": "fixed",
      "reason": "<why this surface is the support>"
    }
  ],
  "loads": [
    {
      "id": "load_001",
      "target_feature_id": "<feature_id from graph, or null>",
      "target_face_ids": ["<face_id from topology, or empty list>"],
      "target_pointers": ["@face:<face_id> or @group:<group_id>, if B-Rep digest provides one"],
      "target_description": "<human-readable description of the loaded surface>",
      "type": "force",
      "value_n": <positive float>,
      "direction": [<x>, <y>, <z>],
      "reason": "<why this surface receives this load>"
    }
  ],
  "mesh": {
    "target_size_mm": <float>,
    "refinement_note": "<where to refine and why>",
    "reason": "<overall mesh strategy reasoning>"
  },
  "analysis_type": "static_structural",
  "assumptions": ["<assumption 1>", "<assumption 2>"],
  "warnings": ["<any concern about ambiguous geometry or task>"]
}
"""


def _read_package_member(package_path: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            if member in zf.namelist():
                return zf.read(member)
    except Exception:
        pass
    return None


def _load_valid_face_ids(package_path: Path) -> set[str]:
    """Return the set of face IDs present in the package's topology_map.json."""
    raw = _read_package_member(package_path, "geometry/topology_map.json")
    if not raw:
        return set()
    try:
        topology = json.loads(raw)
        # Support both {entities: [...]} and {faces: [...]} schemas.
        entities = topology.get("entities") or topology.get("faces") or []
        return {
            e["id"]
            for e in entities
            if isinstance(e, dict) and e.get("type") == "face" and "id" in e
        }
    except Exception:
        return set()


def _load_known_materials() -> set[str]:
    """Return the set of material names from the MATERIALS catalog."""
    ensure_aieng_on_path()
    try:
        from aieng.context.materials import MATERIALS

        return set(MATERIALS.keys())
    except ImportError:
        return {"Al6061-T6", "Al7075-T6", "Steel-1045", "Steel-316L", "Ti-6Al-4V", "Cast-Iron-Grey", "Nylon-PA66", "PETG-CF"}


def _resolve_target_pointers_to_faces(
    pointers: list[Any],
    entity_index: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Resolve B-Rep pointers such as ``@face:face_001`` to face IDs."""

    face_ids: list[str] = []
    warnings: list[str] = []
    for raw in pointers:
        pointer = str(raw or "").strip()
        if not pointer:
            continue
        if not pointer.startswith("@") or ":" not in pointer:
            warnings.append(f"invalid target pointer '{pointer}'")
            continue
        kind, entity_id = pointer[1:].split(":", 1)
        entry = entity_index.get(entity_id)
        if not isinstance(entry, dict):
            warnings.append(f"target pointer '{pointer}' not found in B-Rep entity index")
            continue
        entry_kind = entry.get("kind")
        if kind == "face" and entry_kind == "face":
            face_ids.append(entity_id)
        elif kind == "group" and entry_kind == "group":
            members = [str(fid) for fid in entry.get("members") or []]
            if members:
                face_ids.extend(members)
            else:
                warnings.append(f"target pointer '{pointer}' group has no face members")
        else:
            warnings.append(f"target pointer '{pointer}' resolves to unsupported kind '{entry_kind}'")
    return list(dict.fromkeys(face_ids)), warnings


def _validate_and_normalize_fea_setup(
    fea_setup: dict[str, Any],
    valid_face_ids: set[str],
    known_materials: set[str],
    entity_index: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate Claude's FEA setup against actual topology; normalize material name.

    Checks performed:
    - Material exists in catalog (falls back to Al6061-T6 with a warning)
    - All target_face_ids appear in the package topology
    - Load value_n is positive and within a plausible range
    - Truncated NSET names do not collide

    Returns (normalized_setup, validation_warnings).
    """
    setup = copy.deepcopy(fea_setup)
    warnings: list[str] = []

    # ── Material validation ──────────────────────────────────────────────────
    material = setup.get("material", "")
    if not material or material not in known_materials:
        fallback = "Al6061-T6"
        if material:
            warnings.append(
                f"Material '{material}' not in catalog; falling back to '{fallback}'"
            )
        else:
            warnings.append(f"No material specified; defaulting to '{fallback}'")
        setup["material"] = fallback

    # ?? B-Rep pointer normalization ?????????????????????????????????????????
    if entity_index:
        for item_kind, items in (("BC", setup.get("boundary_conditions") or []), ("Load", setup.get("loads") or [])):
            for item in items:
                resolved, pointer_warnings = _resolve_target_pointers_to_faces(item.get("target_pointers") or [], entity_index)
                for warning in pointer_warnings:
                    warnings.append(f"{item_kind} '{item.get('id', '?')}': {warning}")
                if resolved:
                    existing = [str(fid) for fid in item.get("target_face_ids") or []]
                    item["target_face_ids"] = list(dict.fromkeys(existing + resolved))
                    item["selection_source"] = "brep_pointer"

    # ── Face ID validation ───────────────────────────────────────────────────
    if valid_face_ids:
        for bc in setup.get("boundary_conditions") or []:
            for fid in bc.get("target_face_ids") or []:
                if fid and fid not in valid_face_ids:
                    warnings.append(
                        f"BC '{bc.get('id', '?')}': face_id '{fid}' not found in topology — mapping may fail at meshing"
                    )
        for ld in setup.get("loads") or []:
            for fid in ld.get("target_face_ids") or []:
                if fid and fid not in valid_face_ids:
                    warnings.append(
                        f"Load '{ld.get('id', '?')}': face_id '{fid}' not found in topology — mapping may fail at meshing"
                    )

    # ── Load value sanity ────────────────────────────────────────────────────
    for ld in setup.get("loads") or []:
        v = ld.get("value_n")
        if v is not None:
            try:
                fv = float(v)
                if fv <= 0:
                    warnings.append(
                        f"Load '{ld.get('id', '?')}': value_n={v} is not positive — verify load definition"
                    )
                elif fv > 1e9:
                    warnings.append(
                        f"Load '{ld.get('id', '?')}': value_n={v} N exceeds 1 GN — verify units"
                    )
            except (TypeError, ValueError):
                warnings.append(
                    f"Load '{ld.get('id', '?')}': value_n='{v}' is not numeric"
                )

    # ── NSET collision detection ─────────────────────────────────────────────
    nset_seen: dict[str, str] = {}
    for bc in setup.get("boundary_conditions") or []:
        feat_id = bc.get("target_feature_id")
        if feat_id:
            nset = re.sub(r"[^A-Z0-9_]", "_", feat_id.upper())[:16]
            owner = f"BC '{bc.get('id', '?')}'"
            if nset in nset_seen:
                warnings.append(
                    f"NSET name collision '{nset}': shared by {nset_seen[nset]} and {owner}"
                )
            else:
                nset_seen[nset] = owner
    for ld in setup.get("loads") or []:
        feat_id = ld.get("target_feature_id")
        if feat_id:
            nset = (re.sub(r"[^A-Z0-9_]", "_", feat_id.upper())[:16] + "_L")[:20]
            owner = f"Load '{ld.get('id', '?')}'"
            if nset in nset_seen:
                warnings.append(
                    f"NSET name collision '{nset}': shared by {nset_seen[nset]} and {owner}"
                )
            else:
                nset_seen[nset] = owner

    return setup, warnings


def _build_user_prompt(
    geometry_context: str,
    task_description: str,
    material_hint: str | None,
    mesh_hint: str | None,
    material_catalog: str,
    brep_digest: str | None = None,
) -> str:
    parts = [
        "AVAILABLE MATERIALS (use the exact name):",
        material_catalog,
        "",
        "GEOMETRY:",
        geometry_context,
    ]
    if brep_digest:
        parts += [
            "",
            "B-REP POINTER DIGEST:",
            brep_digest,
            "",
            "When selecting supports or loads, prefer target_pointers from the B-Rep digest.",
            "If a group pointer exactly matches a bolt pattern or load/support region, include it in target_pointers and also include the resolved target_face_ids.",
        ]
    parts += [
        "",
        f'TASK: "{task_description}"',
    ]
    if material_hint:
        parts.append(f'\nMATERIAL HINT (prefer this if suitable): "{material_hint}"')
    if mesh_hint:
        parts.append(f'\nMESH HINT: "{mesh_hint}" (coarse ≈ 5mm, medium ≈ 2.5mm, fine ≈ 1mm relative to part size)')
    parts += [
        "",
        "OUTPUT SCHEMA (respond ONLY with a JSON object matching this schema):",
        _SETUP_SCHEMA,
    ]
    return "\n".join(parts)


def _coerce_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.MULTILINE)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(stripped[start : end + 1])
        else:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object")
    return parsed


def _build_brep_context(package_path: Path) -> tuple[str | None, dict[str, Any], dict[str, bytes]]:
    """Build transient B-Rep digest/entity index and serializable artifacts."""

    try:
        from . import brep_graph

        topo_raw = _read_package_member(package_path, "geometry/topology_map.json")
        if not topo_raw:
            return None, {}, {}
        topology = json.loads(topo_raw)
        feature_raw = _read_package_member(package_path, "graph/feature_graph.json")
        feature_graph = json.loads(feature_raw) if feature_raw else {}
        result = brep_graph.build_brep_graph_from_topology(
            topology,
            feature_graph=feature_graph,
            digest_limit=30,
        )
        artifacts = {
            brep_graph.BREP_GRAPH_MEMBER: json.dumps(result["brep_graph"], indent=2, ensure_ascii=False).encode(),
            brep_graph.ENTITY_INDEX_MEMBER: json.dumps(result["entity_index"], indent=2, ensure_ascii=False).encode(),
            brep_graph.BREP_DIGEST_MEMBER: result["digest"].encode("utf-8"),
        }
        return result["digest"], result["entity_index"], artifacts
    except Exception:
        return None, {}, {}


def call_claude_for_fea_setup(
    geometry_context: str,
    task_description: str,
    material_hint: str | None = None,
    mesh_hint: str | None = None,
    brep_digest: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    settings: Any | None = None,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the configured LLM to generate a structured FEA setup."""
    if settings is None:
        raise HTTPException(status_code=503, detail="LLM settings are required for AI preprocessing")

    ensure_aieng_on_path()
    try:
        from aieng.context.materials import list_materials_for_llm
        material_catalog = list_materials_for_llm()
    except ImportError:
        material_catalog = "Al6061-T6, Steel-1045, Ti-6Al-4V, Cast-Iron-Grey, Nylon-PA66"

    user_prompt = _build_user_prompt(
        geometry_context=geometry_context,
        task_description=task_description,
        material_hint=material_hint,
        mesh_hint=mesh_hint,
        material_catalog=material_catalog,
        brep_digest=brep_digest,
    )

    from . import agent_engine

    config = dict(llm_config or {})
    if not config:
        config = {
            "provider": "anthropic",
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "base_url": os.environ.get("ANTHROPIC_BASE_URL") or None,
        }
    if model:
        config["model"] = model
    if api_key:
        config["api_key"] = api_key
    provider = agent_engine._build_provider(settings, config)
    try:
        raw_text = provider.generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=True,
        )
    except TypeError:
        raw_text = provider.generate(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt)
    return _coerce_json(raw_text)


def _fea_setup_to_setup_yaml(fea_setup: dict[str, Any]) -> dict[str, Any]:
    """Convert Claude's FEA setup JSON to the existing simulation/setup.yaml schema.

    The material field is expected to already be validated and normalized by
    _validate_and_normalize_fea_setup before this is called.
    """
    material_name = fea_setup.get("material", "Al6061-T6")

    ensure_aieng_on_path()
    try:
        from aieng.context.materials import MATERIALS
        mat_props = MATERIALS.get(material_name) or MATERIALS.get("Al6061-T6")
    except ImportError:
        mat_props = {"youngs_modulus_mpa": 69000, "poisson_ratio": 0.33, "density_kg_m3": 2700, "yield_strength_mpa": 276}

    bcs = []
    for i, bc in enumerate(fea_setup.get("boundary_conditions") or [], start=1):
        feat_id = _selection_key(bc, "bc", i)
        bcs.append({
            "id": bc.get("id") or f"bc_{i:03d}",
            "target_feature": feat_id,
            "target_pointers": bc.get("target_pointers") or [],
            "target_face_ids": bc.get("target_face_ids") or [],
            "type": bc.get("type", "fixed"),
            "reason": bc.get("reason", ""),
        })

    loads = []
    for i, ld in enumerate(fea_setup.get("loads") or [], start=1):
        feat_id = _selection_key(ld, "load", i)
        direction = ld.get("direction") or [0.0, 0.0, -1.0]
        loads.append({
            "id": ld.get("id") or f"load_{i:03d}",
            "target_feature": feat_id,
            "target_pointers": ld.get("target_pointers") or [],
            "target_face_ids": ld.get("target_face_ids") or [],
            "type": ld.get("type", "force"),
            "value_n": float(ld.get("value_n") or 0.0),
            "direction": direction,
            "reason": ld.get("reason", ""),
        })

    mesh = fea_setup.get("mesh") or {}

    return {
        "schema_version": "0.1",
        "ai_generated": True,
        "analysis_type": fea_setup.get("analysis_type", "static_structural"),
        "material_name": material_name,
        "material_reason": fea_setup.get("material_reason", ""),
        "materials": {
            material_name: mat_props,
        },
        "boundary_conditions": bcs,
        "loads": loads,
        "mesh": {
            "target_size_mm": float(mesh.get("target_size_mm") or 2.5),
            "refinement_note": mesh.get("refinement_note", ""),
        },
        "assumptions": fea_setup.get("assumptions") or [],
        "warnings": fea_setup.get("warnings") or [],
    }


def _selection_key(item: dict[str, Any], prefix: str, index: int) -> str:
    """Stable feature-like key used by setup.yaml and cae_mapping.json."""

    feature_id = str(item.get("target_feature_id") or "").strip()
    if feature_id:
        return feature_id
    pointers = [str(p) for p in item.get("target_pointers") or [] if str(p).strip()]
    if pointers:
        raw = pointers[0].replace("@", "").replace(":", "_")
        return re.sub(r"[^A-Za-z0-9_]", "_", raw)[:48]
    return f"{prefix}_{index:03d}"


def _fea_setup_to_cae_mapping(fea_setup: dict[str, Any]) -> dict[str, Any]:
    """Generate a cae_mapping.json that links feature IDs to CalculiX NSET names."""
    mappings = []
    for i, bc in enumerate(fea_setup.get("boundary_conditions") or [], start=1):
        feat_id = _selection_key(bc, "bc", i)
        nset_name = re.sub(r"[^A-Z0-9_]", "_", feat_id.upper())[:16]
        mappings.append({
            "cae_entity": nset_name,
            "maps_to": {
                "feature_id": feat_id,
                "description": bc.get("target_description", ""),
                "role": "fixed_support",
                "target_pointers": bc.get("target_pointers") or [],
                "selection_source": bc.get("selection_source") or ("brep_pointer" if bc.get("target_pointers") else "ai_generated"),
            },
            "confidence": "ai_generated",
            "face_ids": bc.get("target_face_ids") or [],
        })
    for i, ld in enumerate(fea_setup.get("loads") or [], start=1):
        feat_id = _selection_key(ld, "load", i)
        nset_name = re.sub(r"[^A-Z0-9_]", "_", feat_id.upper())[:16] + "_L"
        mappings.append({
            "cae_entity": nset_name[:20],
            "maps_to": {
                "feature_id": feat_id,
                "description": ld.get("target_description", ""),
                "role": "load_application",
                "load_type": ld.get("type", "force"),
                "value_n": ld.get("value_n"),
                "direction": ld.get("direction"),
                "target_pointers": ld.get("target_pointers") or [],
                "selection_source": ld.get("selection_source") or ("brep_pointer" if ld.get("target_pointers") else "ai_generated"),
            },
            "confidence": "ai_generated",
            "face_ids": ld.get("target_face_ids") or [],
        })
    from aieng.simulation.cae_mapping_writer import METHOD_AI, finalize_cae_mapping

    return finalize_cae_mapping(
        {
            "ai_generated": True,
            "selection_pointer_syntax": {"face": "@face:<face_id>", "group": "@group:<group_id>"},
            "mappings": mappings,
        },
        method=METHOD_AI,
    )


def _write_both_to_package(package_path: Path, files: dict[str, bytes]) -> None:
    """Atomically add/replace multiple members in the .aieng ZIP package.

    Either all files are written or none — uses a temp file + atomic rename so a
    crash mid-write cannot leave the package in a partially-updated state.
    """
    import zipfile as _zf

    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with _zf.ZipFile(package_path, "r") as src, _zf.ZipFile(tmp, "w", _zf.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename not in files:
                    dst.writestr(item, src.read(item.filename))
            for archive_path, content_bytes in files.items():
                dst.writestr(archive_path, content_bytes)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def run_ai_preprocessing(
    settings: Any,
    project_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Orchestrate AI preprocessing for a project.

    Reads geometry from the .aieng package, calls Claude, validates the result
    against the actual topology, then writes back simulation/setup.yaml and
    simulation/cae_mapping.json atomically.
    """
    from .project_io import get_project, resolve_project_path

    task_description = str(payload.get("task_description") or "").strip()
    if not task_description:
        raise HTTPException(status_code=400, detail="task_description is required")

    material_hint = str(payload.get("material_hint") or "").strip() or None
    mesh_hint = str(payload.get("mesh_hint") or "").strip() or None
    write_files = bool(payload.get("write_files", True))
    api_key = payload.get("api_key")
    llm_config = payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else None

    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path is None or not package_path.exists():
        raise HTTPException(status_code=404, detail=".aieng package not found")

    # Build geometry context from the canonical .aieng package.
    geo_ctx = build_geometry_context(package_path)
    geometry_text = geo_ctx.to_llm_text()
    use_brep_graph = bool(payload.get("use_brep_graph", True))
    brep_digest: str | None = None
    brep_entity_index: dict[str, Any] = {}
    brep_artifacts: dict[str, bytes] = {}
    if use_brep_graph:
        brep_digest, brep_entity_index, brep_artifacts = _build_brep_context(package_path)

    fea_setup = call_claude_for_fea_setup(
        geometry_context=geometry_text,
        task_description=task_description,
        material_hint=material_hint,
        mesh_hint=mesh_hint,
        brep_digest=brep_digest,
        api_key=api_key,
        settings=settings,
        llm_config=llm_config,
    )

    # Validate and normalize before converting — catches bad face IDs, unknown
    # materials, and unsafe load values before anything is written to disk.
    valid_face_ids = _load_valid_face_ids(package_path)
    known_materials = _load_known_materials()
    fea_setup, validation_warnings = _validate_and_normalize_fea_setup(
        fea_setup, valid_face_ids, known_materials, brep_entity_index
    )

    setup_yaml_data = _fea_setup_to_setup_yaml(fea_setup)
    cae_mapping_data = _fea_setup_to_cae_mapping(fea_setup)

    # Record the topology revision that these face references were bound against.
    # If the CAD geometry is later edited, this hash lets the solver path detect
    # stale face references before running an invalid analysis.
    topology_hash: str | None = None
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            if "geometry/topology_map.json" in zf.namelist():
                topo = json.loads(zf.read("geometry/topology_map.json").decode("utf-8"))
                topology_hash = compute_topology_hash(topo)
    except Exception:
        topology_hash = None
    if topology_hash:
        setup_yaml_data["topology_hash"] = topology_hash
        cae_mapping_data["topology_hash"] = topology_hash

    written: list[str] = []
    if write_files:
        setup_yaml_bytes = yaml.dump(setup_yaml_data, allow_unicode=True, sort_keys=False).encode()
        cae_mapping_bytes = json.dumps(cae_mapping_data, indent=2, ensure_ascii=False).encode()
        files_to_write = {
            "simulation/setup.yaml": setup_yaml_bytes,
            "simulation/cae_mapping.json": cae_mapping_bytes,
        }
        if payload.get("write_brep_graph", True):
            files_to_write.update(brep_artifacts)
        # Atomic write: both files land together or neither does.
        _write_both_to_package(package_path, files_to_write)
        written.extend(files_to_write.keys())

        # Record each bound face's character while the binding is known-good, so
        # a later topology change can be RE-VERIFIED instead of blanket-refused.
        try:
            from .project_io import annotate_cae_mapping_face_character

            annotate_cae_mapping_face_character(package_path)
        except Exception:  # noqa: BLE001 - annotation is best-effort, never fatal
            pass

    all_warnings = validation_warnings + (geo_ctx.warnings or [])

    return {
        "schema_version": "0.1",
        "project_id": project_id,
        "task_description": task_description,
        "geometry_providers_used": geo_ctx.providers_used,
        "geometry_context_length": len(geometry_text),
        "brep_digest_included": bool(brep_digest),
        "brep_digest_length": len(brep_digest or ""),
        "fea_setup": fea_setup,
        "setup_yaml": setup_yaml_data,
        "cae_mapping": cae_mapping_data,
        "written_artifacts": written,
        "write_files": write_files,
        "geometry_warnings": geo_ctx.warnings,
        "validation_warnings": validation_warnings,
        "all_warnings": all_warnings,
    }
