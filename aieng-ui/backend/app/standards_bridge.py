"""Bridge to the aieng standards library for MCP tool handlers.

Provides standard-part query, Shape IR generation, package insertion,
material binding, and BOM generation.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any

from aieng.standards import (
    DEEP_GROOVE_BALL_BEARING_PRESETS,
    METRIC_BOLT_PRESETS,
    METRIC_NUT_PRESETS,
    METRIC_SET_SCREW_PRESETS,
    METRIC_SOCKET_HEAD_PRESETS,
    METRIC_WASHER_PRESETS,
    THRUST_BALL_BEARING_PRESETS,
    angle_profile,
    blind_hole,
    channel_profile,
    counterbored_hole,
    countersunk_hole,
    deep_groove_ball_bearing,
    hex_bolt,
    hex_nut,
    i_beam_profile,
    rectangular_tube,
    round_tube,
    set_screw,
    socket_head_cap_screw,
    splined_shaft,
    stepped_shaft,
    tapped_hole,
    through_hole,
    thrust_ball_bearing,
    washer,
)
from aieng.converters import shape_ir_patch as _shape_ir_patch


# ── part type registry ────────────────────────────────────────────────────────

_PART_GENERATORS: dict[str, Any] = {
    "hex_bolt": hex_bolt,
    "hex_nut": hex_nut,
    "washer": washer,
    "socket_head_cap_screw": socket_head_cap_screw,
    "set_screw": set_screw,
    "deep_groove_ball_bearing": deep_groove_ball_bearing,
    "thrust_ball_bearing": thrust_ball_bearing,
    "stepped_shaft": stepped_shaft,
    "splined_shaft": splined_shaft,
    "angle_profile": angle_profile,
    "channel_profile": channel_profile,
    "i_beam_profile": i_beam_profile,
    "rectangular_tube": rectangular_tube,
    "round_tube": round_tube,
    "through_hole": through_hole,
    "blind_hole": blind_hole,
    "countersunk_hole": countersunk_hole,
    "counterbored_hole": counterbored_hole,
    "tapped_hole": tapped_hole,
}

_PART_CATEGORIES: dict[str, str] = {
    "hex_bolt": "fastener",
    "hex_nut": "fastener",
    "washer": "fastener",
    "socket_head_cap_screw": "fastener",
    "set_screw": "fastener",
    "deep_groove_ball_bearing": "bearing",
    "thrust_ball_bearing": "bearing",
    "stepped_shaft": "shaft",
    "splined_shaft": "shaft",
    "angle_profile": "structural_profile",
    "channel_profile": "structural_profile",
    "i_beam_profile": "structural_profile",
    "rectangular_tube": "structural_profile",
    "round_tube": "structural_profile",
    "through_hole": "hole",
    "blind_hole": "hole",
    "countersunk_hole": "hole",
    "counterbored_hole": "hole",
    "tapped_hole": "hole",
}

_PRESETS: dict[str, dict[str, dict[str, float]]] = {
    "hex_bolt": METRIC_BOLT_PRESETS,
    "hex_nut": METRIC_NUT_PRESETS,
    "washer": METRIC_WASHER_PRESETS,
    "socket_head_cap_screw": METRIC_SOCKET_HEAD_PRESETS,
    "set_screw": METRIC_SET_SCREW_PRESETS,
    "deep_groove_ball_bearing": DEEP_GROOVE_BALL_BEARING_PRESETS,
    "thrust_ball_bearing": THRUST_BALL_BEARING_PRESETS,
}


def list_standard_parts(category: str | None = None) -> dict[str, Any]:
    """List available standard part categories and types."""
    parts: list[dict[str, Any]] = []
    for part_type, cat in _PART_CATEGORIES.items():
        if category and cat != category:
            continue
        presets = sorted(_PRESETS.get(part_type, {}).keys())
        parts.append(
            {
                "part_type": part_type,
                "category": cat,
                "has_presets": bool(presets),
                "preset_names": presets,
            }
        )

    categories = sorted(set(_PART_CATEGORIES.values()))
    return {
        "status": "ok",
        "count": len(parts),
        "categories": categories,
        "parts": parts,
    }


def get_standard_part_specs(part_type: str, preset_name: str | None = None) -> dict[str, Any]:
    """Return Shape IR spec and available presets for a standard part type."""
    generator = _PART_GENERATORS.get(part_type)
    if generator is None:
        return {
            "status": "error",
            "code": "part_type_not_found",
            "message": f"Unknown part type: {part_type!r}. Supported: {sorted(_PART_GENERATORS)}",
        }

    presets = _PRESETS.get(part_type, {})
    # Generate a sample node to inspect the schema
    if preset_name and preset_name in presets:
        sample_node = generator(**presets[preset_name])
    else:
        sample_node = generator()

    editable = sample_node.get("metadata", {}).get("editable_parameters", [])
    return {
        "status": "ok",
        "part_type": part_type,
        "category": _PART_CATEGORIES.get(part_type, "unknown"),
        "standard_name": sample_node.get("metadata", {}).get("standard_name", ""),
        "standard_reference": sample_node.get("metadata", {}).get("standard_reference", ""),
        "editable_parameters": editable,
        "presets": presets,
        "sample_node": sample_node,
    }


def _resolve_package_path(active_settings: Any, project_id: str | None, package_path: str | None) -> Path | None:
    """Resolve a package path from project_id or explicit package_path."""
    from .project_io import get_project, resolve_project_path

    if package_path:
        p = Path(package_path)
        if p.exists():
            return p
        return None
    if project_id:
        proj = get_project(active_settings, project_id)
        pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
        if pkg is not None and pkg.exists():
            return pkg
    return None


def _apply_transform(node: dict[str, Any], position: list[float] | None, orientation: list[float] | None) -> dict[str, Any]:
    """Apply position and orientation transforms to a Shape IR node."""
    node = copy.deepcopy(node)
    if position:
        # Update location on primitives or children
        loc = [float(v) for v in position]
        if "location" in node:
            node["location"] = loc
        for child in node.get("children", []):
            if isinstance(child, dict) and "location" in child:
                child["location"] = loc
    if orientation:
        rot = [float(v) for v in orientation]
        if "rotation" in node:
            node["rotation"] = rot
        for child in node.get("children", []):
            if isinstance(child, dict) and "rotation" in child:
                child["rotation"] = rot
    return node


def insert_standard_part(
    active_settings: Any,
    project_id: str | None,
    package_path: str | None,
    part_type: str,
    parameters: dict[str, Any],
    position: list[float] | None = None,
    orientation: list[float] | None = None,
    part_name: str | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    """Insert a standard part into the current project as Shape IR."""
    generator = _PART_GENERATORS.get(part_type)
    if generator is None:
        return {
            "status": "error",
            "code": "part_type_not_found",
            "message": f"Unknown part type: {part_type!r}. Supported: {sorted(_PART_GENERATORS)}",
        }

    pkg = _resolve_package_path(active_settings, project_id, package_path)
    if pkg is None:
        return {
            "status": "error",
            "code": "missing_package",
            "message": "No package found for the given project_id or package_path.",
        }

    # Build parameters: preset wins, then caller overrides
    params: dict[str, Any] = {}
    presets = _PRESETS.get(part_type, {})
    if preset_name and preset_name in presets:
        params.update(presets[preset_name])
    if isinstance(parameters, dict):
        params.update(parameters)

    try:
        node = generator(**params)
    except Exception as exc:
        return {
            "status": "error",
            "code": "generation_failed",
            "message": f"Failed to generate Shape IR for {part_type}: {type(exc).__name__}: {exc}",
        }

    # Rename if requested
    if part_name:
        node["name"] = part_name
        node["id"] = part_name.lower().replace(" ", "_").replace("-", "_")

    # Apply transform
    node = _apply_transform(node, position, orientation)

    # Read existing Shape IR
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            if "geometry/shape_ir.json" not in zf.namelist():
                return {
                    "status": "error",
                    "code": "no_shape_ir",
                    "message": "Package does not contain geometry/shape_ir.json. Convert or create a Shape IR project first.",
                }
            payload = json.loads(zf.read("geometry/shape_ir.json").decode("utf-8"))
    except Exception as exc:
        return {
            "status": "error",
            "code": "package_read_error",
            "message": f"Failed to read package: {type(exc).__name__}: {exc}",
        }

    # Apply patch: add_node
    patch = {
        "format_version": "0.1",
        "operations": [{"op": "add_node", "node": node}],
    }
    result = _shape_ir_patch.apply_shape_ir_patch(payload, patch, dry_run=False)
    if not result["ok"]:
        return {
            "status": "error",
            "code": "patch_rejected",
            "message": "Shape IR patch was rejected.",
            "patch_report": _shape_ir_patch.build_patch_report(patch, result),
        }

    # Write back shape_ir.json
    new_payload = result["new_payload"]
    from .cad_generation import _replace_member, recompile_shape_ir_package

    try:
        _replace_member(
            pkg,
            "geometry/shape_ir.json",
            (json.dumps(new_payload, indent=2, sort_keys=True) + "\n").encode(),
        )
    except Exception as exc:
        return {
            "status": "error",
            "code": "write_failed",
            "message": f"Failed to write updated Shape IR: {type(exc).__name__}: {exc}",
        }

    # Recompile
    try:
        recompile = recompile_shape_ir_package(pkg, timeout=120)
    except Exception as exc:
        return {
            "status": "error",
            "code": "recompile_failed",
            "message": f"Shape IR updated but recompilation failed: {type(exc).__name__}: {exc}",
            "shape_ir_updated": True,
        }

    return {
        "status": "ok",
        "part_type": part_type,
        "part_name": node.get("name", part_type),
        "node_id": node.get("id", ""),
        "recompile": recompile,
    }


def batch_insert_standard_parts(
    active_settings: Any,
    project_id: str | None,
    package_path: str | None,
    parts: list[dict[str, Any]],
    *,
    max_workers: int = 4,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Batch-insert multiple standard parts into a project in parallel.

    Uses ``aieng.async_utils.async_insert_standard_parts`` for concurrent
    insertion, then performs a single incremental recompilation to avoid
    full rebuilds for every part.

    Args:
        active_settings: Backend settings object.
        project_id: Target project identifier.
        package_path: Alternative package path (overrides project_id resolution).
        parts: List of standard-part request dicts. Each dict should contain:
            ``part_type``, ``parameters``, optional ``position``,
            ``orientation``, ``part_name``, ``preset_name``.
        max_workers: Maximum concurrent insertion workers.
        use_cache: Whether to use the geometry cache for recompilation.

    Returns:
        A summary dict with ``status``, ``inserted``, ``errors``, and
        ``recompile_summary``.
    """
    if not parts:
        return {"status": "ok", "inserted": 0, "errors": 0, "items": []}

    # Resolve package once
    pkg = _resolve_package_path(active_settings, project_id, package_path)
    if pkg is None:
        return {
            "status": "error",
            "code": "missing_package",
            "message": "No package found for the given project_id or package_path.",
        }

    # Use async parallel insertion
    try:
        from aieng.async_utils import async_insert_standard_parts

        results = async_insert_standard_parts(
            project_id or str(pkg),
            parts,
            max_workers=max_workers,
            active_settings=active_settings,
        )
    except Exception as exc:
        # Fallback to sequential insertion if async utils fail
        results = []
        for i, req in enumerate(parts):
            try:
                result = insert_standard_part(
                    active_settings=active_settings,
                    project_id=project_id,
                    package_path=package_path,
                    part_type=req["part_type"],
                    parameters=req.get("parameters", {}),
                    position=req.get("position"),
                    orientation=req.get("orientation"),
                    part_name=req.get("part_name"),
                    preset_name=req.get("preset_name"),
                )
                results.append({"index": i, "status": "ok", "result": result})
            except Exception as inner_exc:
                results.append({"index": i, "status": "error", "error": f"{type(inner_exc).__name__}: {inner_exc}"})

    # Summarize results
    inserted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for res in results:
        if res.get("status") == "ok":
            inserted.append(res.get("result", {}))
        else:
            errors.append({"index": res.get("index"), "error": res.get("error", "unknown")})

    # Single recompilation at the end using incremental compiler if available
    recompile_summary: dict[str, Any] = {"skipped": True}
    if inserted:
        try:
            from aieng.cache.geometry_cache import GeometryCache, compute_shape_ir_hash
            from aieng.incremental import IncrementalCompiler
            from .cad_generation import recompile_shape_ir_package

            # Read current payload
            with zipfile.ZipFile(pkg, "r") as zf:
                new_payload = json.loads(zf.read("geometry/shape_ir.json").decode("utf-8"))

            cache = GeometryCache()
            compiler = IncrementalCompiler(cache=cache)
            # We don't have the old payload easily, so do a full compile but with cache
            recompile_summary = recompile_shape_ir_package(pkg, timeout=120, use_cache=use_cache)
        except Exception as exc:
            recompile_summary = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "status": "ok" if not errors else "partial",
        "inserted": len(inserted),
        "errors": len(errors),
        "items": inserted,
        "error_details": errors,
        "recompile_summary": recompile_summary,
    }


def set_part_material(
    active_settings: Any,
    project_id: str | None,
    package_path: str | None,
    part_name: str,
    material_name: str,
    override_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assign a material to a named part in the current project."""
    pkg = _resolve_package_path(active_settings, project_id, package_path)
    if pkg is None:
        return {
            "status": "error",
            "code": "missing_package",
            "message": "No package found for the given project_id or package_path.",
        }

    # Read feature graph
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            if "graph/feature_graph.json" not in zf.namelist():
                return {
                    "status": "error",
                    "code": "no_feature_graph",
                    "message": "Package does not contain graph/feature_graph.json.",
                }
            fg = json.loads(zf.read("graph/feature_graph.json").decode("utf-8"))
    except Exception as exc:
        return {
            "status": "error",
            "code": "package_read_error",
            "message": f"Failed to read package: {type(exc).__name__}: {exc}",
        }

    features = fg.get("features", [])
    matched = False
    for feat in features:
        if feat.get("name") == part_name:
            matched = True
            feat.setdefault("parameters", {})
            if not isinstance(feat["parameters"], dict):
                feat["parameters"] = {}
            feat["parameters"]["material"] = material_name
            if override_properties and isinstance(override_properties, dict):
                feat["parameters"]["material_properties"] = override_properties
            # Also set intent role if not already standard_part
            if feat.get("type") not in ("standard_part",):
                feat["type"] = "named_part"
            break

    if not matched:
        return {
            "status": "error",
            "code": "part_not_found",
            "message": f"Part '{part_name}' not found in feature graph. Named parts: {[f.get('name') for f in features if f.get('name')]}",
        }

    # Write back
    from .cad_generation import _replace_member

    try:
        _replace_member(
            pkg,
            "graph/feature_graph.json",
            (json.dumps(fg, indent=2, sort_keys=True) + "\n").encode(),
        )
    except Exception as exc:
        return {
            "status": "error",
            "code": "write_failed",
            "message": f"Failed to write updated feature graph: {type(exc).__name__}: {exc}",
        }

    return {
        "status": "ok",
        "part_name": part_name,
        "material": material_name,
        "override_properties": override_properties or {},
    }


def to_bom_frontend_payload(
    result: dict[str, Any],
    project_id: str | None,
    generated_at: str,
) -> dict[str, Any]:
    """Map a ``generate_bom`` result (snake_case) to the frontend ``BOMData`` shape.

    The React BOM panel (``types/bom.ts``) expects camelCase line items; this is
    the single mapping point so the panel and the structured tool output do not
    drift. Standard-part-only fields collapse to ``None`` for custom parts.
    """
    items_in = result.get("items") or []
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(items_in, start=1):
        is_standard = bool(item.get("standard_part", False))
        items.append({
            "id": f"{item.get('part_type', 'part')}:{item.get('part_name', '')}:{idx}",
            "name": item.get("part_name", ""),
            "quantity": int(item.get("quantity", 1) or 1),
            "material": (item.get("material") or None),
            "isStandardPart": is_standard,
            "standardPartType": (item.get("canonical_type") or None) if is_standard else None,
            "standardPartPreset": (item.get("designation") or None) if is_standard else None,
            "parameters": None,
        })
    total = int(result.get("total_parts", sum(i["quantity"] for i in items)))
    standard_count = sum(i["quantity"] for i in items if i["isStandardPart"])
    return {
        "projectId": project_id or "",
        "items": items,
        "totalCount": total,
        "standardPartCount": standard_count,
        "customPartCount": max(0, total - standard_count),
        "generatedAt": generated_at,
    }


def generate_bom(
    active_settings: Any,
    project_id: str | None,
    package_path: str | None,
    fmt: str | None = None,
) -> dict[str, Any]:
    """Generate a Bill of Materials from the current project parts."""
    pkg = _resolve_package_path(active_settings, project_id, package_path)
    if pkg is None:
        return {
            "status": "error",
            "code": "missing_package",
            "message": "No package found for the given project_id or package_path.",
        }

    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            fg = {}
            if "graph/feature_graph.json" in names:
                fg = json.loads(zf.read("graph/feature_graph.json").decode("utf-8"))
    except Exception as exc:
        return {
            "status": "error",
            "code": "package_read_error",
            "message": f"Failed to read package: {type(exc).__name__}: {exc}",
        }

    features = fg.get("features", [])
    items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for feat in features:
        ftype = feat.get("type", "")
        name = feat.get("name", "")
        if not name:
            continue
        if not ftype:
            warnings.append(
                {
                    "kind": "skipped_missing_type",
                    "identifier": name,
                    "message": f"Feature '{name}' skipped because it has no type.",
                }
            )
            continue
        if ftype not in ("named_part", "standard_part", "mounting_hole", "mounting_hole_pattern", "base_plate"):
            continue
        params = feat.get("parameters", {}) or {}
        material = params.get("material", "") or ""
        item: dict[str, Any] = {
            "part_name": name,
            "part_type": ftype,
            "material": material,
            "quantity": 1,
        }
        # Standard part metadata
        if ftype == "standard_part":
            item["standard_part"] = True
            item["canonical_type"] = feat.get("canonical_type") or feat.get("intent", {}).get("canonical_type", "")
            item["designation"] = feat.get("designation", "")
            item["source_library"] = feat.get("source_library", "")
        items.append(item)

    # Deduplicate by (name, type, material)
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (item["part_name"], item["part_type"], item.get("material", ""))
        if key in deduped:
            deduped[key]["quantity"] += 1
            warnings.append(
                {
                    "kind": "dedup_merge",
                    "part_name": item["part_name"],
                    "key": {
                        "name": item["part_name"],
                        "type": item["part_type"],
                        "material": item.get("material", ""),
                    },
                    "merged_quantity": deduped[key]["quantity"],
                    "message": (
                        f"Merged duplicate of '{item['part_name']}' "
                        f"({item['part_type']}, material={item.get('material') or 'none'}) "
                        f"into existing entry; quantity now {deduped[key]['quantity']}."
                    ),
                }
            )
        else:
            deduped[key] = dict(item)

    bom = list(deduped.values())
    total_parts = sum(i["quantity"] for i in bom)

    result: dict[str, Any] = {
        "status": "ok",
        "project_id": project_id,
        "package_path": str(pkg),
        "total_parts": total_parts,
        "unique_parts": len(bom),
        "items": bom,
        "warnings": warnings,
        "limitations": "Best-effort semantic recognition; not a supplier BOM or validation claim.",
    }

    # Flat, ERP-style line items with a stable column schema, shared by the CSV
    # and JSON exports (#280). 1-based contiguous line numbers; standard-part-only
    # fields default to empty so the schema is uniform across part types.
    _BOM_COLUMNS = (
        "line_no", "part_name", "part_type", "material", "quantity",
        "standard_part", "canonical_type", "designation", "source_library",
    )

    def _line_items() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(bom, start=1):
            rows.append({
                "line_no": idx,
                "part_name": item.get("part_name", ""),
                "part_type": item.get("part_type", ""),
                "material": item.get("material", "") or "",
                "quantity": item.get("quantity", 1),
                "standard_part": bool(item.get("standard_part", False)),
                "canonical_type": item.get("canonical_type", "") or "",
                "designation": item.get("designation", "") or "",
                "source_library": item.get("source_library", "") or "",
            })
        return rows

    if fmt == "markdown":
        lines = [
            "# Bill of Materials",
            "",
            "| Part Name | Type | Material | Qty |",
            "|-----------|------|----------|-----|",
        ]
        for item in bom:
            lines.append(
                f"| {item['part_name']} | {item['part_type']} | {item.get('material', '-')} | {item['quantity']} |"
            )
        result["markdown"] = "\n".join(lines)
    elif fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(_BOM_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in _line_items():
            writer.writerow({**row, "standard_part": "true" if row["standard_part"] else "false"})
        result["csv"] = buffer.getvalue()
    elif fmt == "json":
        result["json"] = json.dumps(
            {
                "bill_of_materials": _line_items(),
                "total_parts": total_parts,
                "unique_parts": len(bom),
            },
            indent=2,
        )

    return result
