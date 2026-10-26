"""Assembly-aware topology-optimization setup v0.

Derives a *single selected design part* topology-optimization problem from
Assembly IR/Assembly CAE v0 evidence.  This is setup only: no optimizer is run
and no writeback is performed here.  Connections remain simplified proxies; real
contact, friction, bolt preload, and simultaneous multi-part optimization are out
of scope.
"""
from __future__ import annotations

import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION
from aieng.converters.assembly_cae import ASSEMBLY_CAE_MODEL_PATH, ASSEMBLY_RESULT_MAP_PATH
from aieng.converters.assembly_interface_resolution import (
    ASSEMBLY_CONNECTION_GEOMETRY_PATH,
    INTERFACE_RESOLUTION_PATH,
)
from aieng.converters.assembly_ir import (
    ASSEMBLY_IR_PATH,
    CONNECTION_GRAPH_PATH,
    CONVERSION_MANIFEST_PATH,
    PART_REGISTRY_PATH,
)
from aieng.converters.topology_optimization import (
    DEFAULT_MAX_ITERS_2D,
    build_guidance_field,
    run_topology_optimization,
    topology_result_to_shape_ir,
)

ASSEMBLY_TOPOPT_PROBLEM_PATH = "analysis/assembly_topopt_problem.json"
ASSEMBLY_TOPOPT_DERIVATION_PATH = "diagnostics/assembly_topopt_derivation.json"
STANDARD_TOPOPT_PROBLEM_PATH = "analysis/topology_optimization_problem.json"
ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH = "analysis/assembly_topology_optimization.json"
ASSEMBLY_TOPOPT_EXECUTION_PATH = "diagnostics/assembly_topopt_execution.json"
ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH = "diagnostics/assembly_post_optimization_verification.json"
ASSEMBLY_OPTIMIZATION_SUMMARY_PATH = "analysis/assembly_optimization_summary.json"
ASSEMBLY_DESIGN_RECOMMENDATIONS_PATH = "analysis/assembly_design_recommendations.json"
ASSEMBLY_POSTPROCESS_REPORT_PATH = "diagnostics/assembly_postprocess_report.json"
ASSEMBLY_NEXT_ACTIONS_PATH = "analysis/assembly_next_actions.json"
ASSEMBLY_MULTIPART_TOPOPT_PROBLEM_PATH = "analysis/assembly_multipart_topopt_problem.json"
ASSEMBLY_MULTIPART_TOPOPT_DERIVATION_PATH = "diagnostics/assembly_multipart_topopt_derivation.json"
PART_TOPOLOGY_OPTIMIZATION_TEMPLATE = "parts/{part_id}/analysis/topology_optimization.json"
PART_OPTIMIZED_SHAPE_IR_TEMPLATE = "parts/{part_id}/geometry/optimized_shape_ir.json"

_SOURCE_ARTIFACTS = [
    ASSEMBLY_IR_PATH,
    PART_REGISTRY_PATH,
    CONNECTION_GRAPH_PATH,
    INTERFACE_RESOLUTION_PATH,
    ASSEMBLY_CONNECTION_GEOMETRY_PATH,
    ASSEMBLY_CAE_MODEL_PATH,
    ASSEMBLY_RESULT_MAP_PATH,
]
_OPTIMIZABLE_ROLES = {"design_part"}
_NON_OPTIMIZABLE_ROLES = {"reference_part", "fixture", "load_source", "fastener", "external_context"}
_PRESERVE_ROLES = {"mounting_face", "bolt_hole", "weld_face", "contact_face", "support_face"}
_STRESS_TYPES = {"stress", "von_mises_stress", "principal_stress"}
_DEFLECTION_TYPES = {"displacement", "deflection"}
_ENFORCEABLE_CONFIDENCE = {"high", "medium"}
_LIMITATIONS = [
    "Assembly-aware topology optimization v0 derives a problem for one selected design part only.",
    "Connection/interface regions are preserved from simplified proxy connection evidence.",
    "Loads/supports may be proxy-derived from assembly connections.",
    "Real nonlinear contact and bolt preload are not modeled.",
    "Reference, fixture, fastener, load_source, and frozen parts are not optimized.",
    "Result guidance is advisory unless use_result_guidance=true is explicitly requested.",
    "Multi-part simultaneous optimization is future work.",
]
_POSTPROCESS_LIMITATIONS = [
    "Assembly design recommendations v0 are rule-based advisory post-processing only.",
    "Recommendations do not rerun topology optimization automatically.",
    "Recommendations do not certify physical correctness, interface equivalence, or production CAD readiness.",
    "Real nonlinear contact, friction, and bolt preload are not modeled.",
    "Proceed-to-dimension-optimization and mesh-to-CAD recommendations are advisory future-step suggestions only.",
]
_UNSUPPORTED_CLAIM_KEYS = {
    "contact_physics_modeled",
    "contact_friction_modeled",
    "friction_modeled",
    "bolt_preload_modeled",
    "bolt_preload_modelled",
    "multi_part_simultaneous_optimization",
    "multi_part_optimization",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _read_json(zf: zipfile.ZipFile, names: set[str], member: str) -> Any:
    if member not in names:
        return None
    try:
        return json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        return None


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _replace_members(path: Path, members: dict[str, bytes]) -> None:
    if not path.exists() or not members:
        return
    tmp = path.with_suffix(".tmp.aieng")
    try:
        with zipfile.ZipFile(path, "r") as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename not in members:
                    dst.writestr(item, src.read(item.filename))
            for name, data in members.items():
                dst.writestr(name, data)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _bbox_center(b: list[float]) -> list[float]:
    return [(float(b[i]) + float(b[i + 3])) / 2 for i in range(3)]


def _bbox_union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
        max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes),
    ]


def _bbox_diag(b: list[float]) -> float:
    return math.sqrt(sum((float(b[i + 3]) - float(b[i])) ** 2 for i in range(3)))


def _part_bbox_from_topology(topo_index: dict[str, dict[str, Any]]) -> list[float] | None:
    boxes = [e.get("bounding_box") for e in topo_index.values() if isinstance(e.get("bounding_box"), list) and len(e["bounding_box"]) == 6]
    return _bbox_union([[float(x) for x in b] for b in boxes])


def _topo_index(topo_doc: Any) -> dict[str, dict[str, Any]]:
    ents = (topo_doc or {}).get("entities") if isinstance(topo_doc, dict) else None
    return {str(e["id"]): e for e in _as_list(ents) if isinstance(e, dict) and e.get("id")}


def _build_topology_by_part(zf: zipfile.ZipFile, names: set[str], assembly: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    shared = _topo_index(_read_json(zf, names, "geometry/topology_map.json"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for part in _as_list(assembly.get("parts")):
        if not isinstance(part, dict) or not part.get("id"):
            continue
        pid = str(part["id"])
        idx: dict[str, dict[str, Any]] = {}
        for cand in (part.get("topology_ref"), f"parts/{pid}/topology_map.json", f"geometry/parts/{pid}/topology_map.json"):
            if cand and cand in names:
                idx = _topo_index(_read_json(zf, names, str(cand)))
                if idx:
                    break
        if not idx and shared:
            scoped = {eid: e for eid, e in shared.items() if e.get("body_id") in {pid, part.get("name")}}
            idx = scoped or shared
        if idx:
            out[pid] = idx
    return out


def _collect_interfaces(assembly: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for iface in _as_list(assembly.get("interfaces")):
        if isinstance(iface, dict) and iface.get("id"):
            out[str(iface["id"])] = iface
    for part in _as_list(assembly.get("parts")):
        if not isinstance(part, dict):
            continue
        for iface in _as_list(part.get("interfaces")):
            if isinstance(iface, dict) and iface.get("id"):
                rec = {**iface}
                rec.setdefault("part_id", part.get("id"))
                out[str(rec["id"])] = rec
    return out


def _grid_frame(bbox: list[float], *, dimension: str, resolution: int, resolution_3d: int) -> tuple[dict[str, Any], dict[str, Any]]:
    mins, maxs = bbox[:3], bbox[3:]
    ext = [max(float(maxs[i]) - float(mins[i]), 1e-9) for i in range(3)]
    axes = ["x", "y", "z"]
    if dimension == "3d":
        longest = max(ext)
        nx = max(int(round(resolution_3d * ext[0] / longest)), 2)
        ny = max(int(round(resolution_3d * ext[1] / longest)), 2)
        nz = max(int(round(resolution_3d * ext[2] / longest)), 2)
        grid = {"nx": nx, "ny": ny, "nz": nz}
        frame = {
            "origin": [float(x) for x in mins],
            "u_axis": "x", "v_axis": "y", "w_axis": "z",
            "cell_size": [round(ext[0] / nx, 6), round(ext[1] / ny, 6), round(ext[2] / nz, 6)],
        }
        return grid, frame
    u, v, w = sorted(range(3), key=lambda k: ext[k], reverse=True)
    nelx = max(int(resolution), 2)
    nely = max(int(round(resolution * ext[v] / ext[u])), 2)
    grid = {"nelx": nelx, "nely": nely}
    frame = {
        "origin": [float(x) for x in mins],
        "u_axis": axes[u], "v_axis": axes[v],
        "cell_size": [round(ext[u] / nelx, 6), round(ext[v] / nely, 6)],
        "thickness": round(ext[w], 6),
        "out_of_plane_axis": axes[w],
    }
    return grid, frame


def _axis_index(axis: str) -> int:
    return {"x": 0, "y": 1, "z": 2}[axis]


def _cell_of_point(p: list[float], bbox: list[float], grid: dict[str, Any], frame: dict[str, Any], dimension: str) -> list[int]:
    mins, maxs = bbox[:3], bbox[3:]
    ext = [max(maxs[i] - mins[i], 1e-9) for i in range(3)]
    if dimension == "3d":
        return [
            min(max(int((p[0] - mins[0]) / ext[0] * int(grid["nx"])), 0), int(grid["nx"]) - 1),
            min(max(int((p[1] - mins[1]) / ext[1] * int(grid["ny"])), 0), int(grid["ny"]) - 1),
            min(max(int((p[2] - mins[2]) / ext[2] * int(grid["nz"])), 0), int(grid["nz"]) - 1),
        ]
    u, v = _axis_index(frame["u_axis"]), _axis_index(frame["v_axis"])
    return [
        min(max(int((p[u] - mins[u]) / ext[u] * int(grid["nelx"])), 0), int(grid["nelx"]) - 1),
        min(max(int((p[v] - mins[v]) / ext[v] * int(grid["nely"])), 0), int(grid["nely"]) - 1),
    ]


def _cells_of_bbox(bb: list[float], bbox: list[float], grid: dict[str, Any], frame: dict[str, Any], dimension: str) -> list[list[int]]:
    c0 = _cell_of_point([bb[0], bb[1], bb[2]], bbox, grid, frame, dimension)
    c1 = _cell_of_point([bb[3], bb[4], bb[5]], bbox, grid, frame, dimension)
    ranges = [range(min(c0[i], c1[i]), max(c0[i], c1[i]) + 1) for i in range(len(c0))]
    if dimension == "3d":
        return [[i, j, k] for i in ranges[0] for j in ranges[1] for k in ranges[2]]
    return [[i, j] for i in ranges[0] for j in ranges[1]]


def _select_design_part(
    *,
    assembly: dict[str, Any],
    registry: dict[str, Any],
    selected_part_id: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str], list[str]]:
    diagnostics: list[str] = []
    warnings: list[str] = []
    parts_by_id = {p.get("id"): p for p in _as_list(assembly.get("parts")) if isinstance(p, dict)}
    reg_by_id = {p.get("part_id"): p for p in _as_list(registry.get("parts")) if isinstance(p, dict)}
    intent = assembly.get("analysis_intent") if isinstance(assembly.get("analysis_intent"), dict) else {}
    allowed = set(str(x) for x in _as_list(intent.get("allowed_optimization_parts")))
    frozen = set(str(x) for x in _as_list(intent.get("frozen_parts")))
    candidates: list[dict[str, Any]] = []
    for pid, part in parts_by_id.items():
        if not pid:
            continue
        reg = reg_by_id.get(pid, {})
        role = part.get("role") or reg.get("role")
        editable = bool(reg.get("editable", part.get("editable", role == "design_part")))
        if role in _NON_OPTIMIZABLE_ROLES or str(pid) in frozen:
            continue
        if role in _OPTIMIZABLE_ROLES and editable:
            if allowed and str(pid) not in allowed:
                continue
            candidates.append({**part, **{k: v for k, v in reg.items() if k not in part}, "part_id": pid})
    if not candidates:
        diagnostics.append("no_design_part")
        return None, candidates, diagnostics, warnings
    if selected_part_id:
        for c in candidates:
            if str(c.get("part_id") or c.get("id")) == str(selected_part_id):
                return c, candidates, diagnostics, warnings
        diagnostics.append("selected_part_not_optimizable")
        return None, candidates, diagnostics, warnings
    if len(candidates) > 1:
        diagnostics.append("multiple_design_parts_needs_selection")
        return None, candidates, diagnostics, warnings
    return candidates[0], candidates, diagnostics, warnings


def _derive_preserve_regions(
    *,
    selected_part_id: str,
    assembly: dict[str, Any],
    interface_resolution: dict[str, Any],
    connection_geometry: dict[str, Any],
    grid: dict[str, Any] | None,
    frame: dict[str, Any] | None,
    bbox: list[float] | None,
    dimension: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    interfaces = _collect_interfaces(assembly)
    resolved = interface_resolution.get("interfaces") if isinstance(interface_resolution.get("interfaces"), dict) else {}
    geo_by_id = {g.get("connection_id"): g for g in _as_list(connection_geometry.get("connections")) if isinstance(g, dict)}
    regions: list[dict[str, Any]] = []
    for idx, conn in enumerate(_as_list(assembly.get("connections"))):
        if not isinstance(conn, dict):
            continue
        cid = str(conn.get("id") or f"connection_{idx:03d}")
        geo = geo_by_id.get(cid, {})
        if geo.get("geometry_status") == "invalid":
            warnings.append(f"invalid_connection_not_preserved:{cid}")
            continue
        for side in ("a", "b"):
            if str(conn.get(f"part_{side}")) != selected_part_id:
                continue
            iid = conn.get(f"interface_{side}")
            iface = interfaces.get(str(iid)) if iid else None
            rec = resolved.get(str(iid)) if iid and isinstance(resolved, dict) else None
            role = (iface or {}).get("semantic_role")
            if role not in _PRESERVE_ROLES and str(conn.get("type")) not in {"bolted_proxy", "welded_proxy", "contact_proxy"}:
                continue
            world = (rec or {}).get("world") if isinstance(rec, dict) else {}
            rbbox = world.get("bbox") if isinstance(world, dict) else None
            centroid = world.get("centroid") if isinstance(world, dict) else None
            topo_refs = (iface or {}).get("topology_refs") if isinstance((iface or {}).get("topology_refs"), dict) else {}
            status = (rec or {}).get("resolution_status", "unresolved")
            if status == "unresolved" or not rbbox:
                warnings.append(f"unresolved_interface_preserve_warning:{iid}")
            cells = _cells_of_bbox(rbbox, bbox, grid, frame, dimension) if (rbbox and bbox and grid and frame) else []
            regions.append({
                "region_id": f"preserve_{iid or cid}",
                "reason": "proxy_connection_preserve_reason",
                "connection_id": cid,
                "connection_type": conn.get("type"),
                "interface_id": iid,
                "part_id": selected_part_id,
                "semantic_role": role,
                "topology_refs": topo_refs,
                "world_bbox": rbbox,
                "world_centroid": centroid,
                "resolution_status": status,
                "preserve_min_density": 0.95,
                "cells": cells,
                "proxy_derived": True,
            })
    return regions, warnings


def _result_guidance(
    *,
    selected_part_id: str,
    result_map: dict[str, Any],
    use_result_guidance: bool,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    preserve: list[dict[str, Any]] = []
    stiffness: list[dict[str, Any]] = []
    recorded_low: list[dict[str, Any]] = []
    for r in _as_list(result_map.get("mapped_results")):
        if not isinstance(r, dict) or str(r.get("part_id")) != selected_part_id:
            continue
        conf = str(r.get("confidence") or "low").lower()
        entry = {
            "region_id": r.get("region_id"),
            "part_id": r.get("part_id"),
            "interface_id": r.get("interface_id"),
            "connection_id": r.get("connection_id"),
            "load_case_id": r.get("load_case_id"),
            "result_type": r.get("result_type"),
            "quantity": r.get("quantity") or r.get("result_type"),
            "value": r.get("value"),
            "unit": r.get("unit"),
            "confidence": conf,
            "mapping_method": r.get("mapping_method"),
            "location": r.get("location"),
            "source_ir_node": r.get("source_ir_node"),
            "proxy_derived": bool(r.get("proxy_derived") or r.get("connection_id")),
            "enforced": bool(use_result_guidance and conf in _ENFORCEABLE_CONFIDENCE),
        }
        rtype = str(r.get("result_type") or "").lower()
        if conf not in _ENFORCEABLE_CONFIDENCE:
            recorded_low.append(entry)
            warnings.append(f"low_confidence_result_guidance_recorded:{r.get('region_id')}")
        if rtype in _STRESS_TYPES:
            preserve.append(entry)
        elif rtype in _DEFLECTION_TYPES:
            stiffness.append(entry)
    for u in _as_list(result_map.get("unmapped_regions")):
        if isinstance(u, dict):
            warnings.append(f"unmapped_region_diagnostic:{u.get('region_id')}")
    return {
        "available": bool(preserve or stiffness or recorded_low),
        "advisory_only": not use_result_guidance,
        "use_result_guidance": bool(use_result_guidance),
        "preserve_or_reinforce_regions": preserve,
        "stiffness_sensitive_regions": stiffness,
        "recorded_low_confidence_regions": recorded_low,
        "sources": {"consumed": [ASSEMBLY_RESULT_MAP_PATH]},
    }, warnings


def _derive_bcs(
    *,
    selected_part_id: str,
    assembly_model: dict[str, Any],
    preserve_regions: list[dict[str, Any]],
    bbox: list[float],
    grid: dict[str, Any],
    frame: dict[str, Any],
    dimension: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    warnings: list[str] = []
    diagnostics: list[str] = []
    iface_regions = {r.get("interface_id"): r for r in preserve_regions}
    supports: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    bc = assembly_model.get("boundary_conditions") if isinstance(assembly_model.get("boundary_conditions"), dict) else {}
    for s in _as_list(bc.get("supports")):
        if not isinstance(s, dict) or str(s.get("part_id")) != selected_part_id:
            continue
        region = iface_regions.get(s.get("interface_id"))
        cells = region.get("cells") if region else []
        if cells:
            supports.append({"cells": cells, "from": s, "proxy_derived": True})
    for ld in _as_list(bc.get("loads")):
        if not isinstance(ld, dict) or str(ld.get("part_id")) != selected_part_id:
            continue
        region = iface_regions.get(ld.get("interface_id"))
        cells = region.get("cells") if region else []
        direction = ld.get("direction") or ld.get("vector") or [0.0, -1.0, 0.0]
        value = float(ld.get("value_n") or ld.get("magnitude") or ld.get("value") or 1.0)
        if not cells and ld.get("world_centroid"):
            cells = [_cell_of_point(ld["world_centroid"], bbox, grid, frame, dimension)]
        if not cells:
            continue
        if dimension == "3d":
            loads.append({
                "cells": cells,
                "fx": value * float(direction[0]), "fy": value * float(direction[1]), "fz": value * float(direction[2]),
                "from": ld, "proxy_derived": True,
            })
        else:
            u, v, w = _axis_index(frame["u_axis"]), _axis_index(frame["v_axis"]), _axis_index(frame["out_of_plane_axis"])
            fx, fy, fw = value * float(direction[u]), value * float(direction[v]), value * float(direction[w])
            if fx or fy:
                loads.append({"cells": cells, "fx": fx, "fy": fy, "from": ld, "proxy_derived": True})
            elif fw:
                warnings.append(f"load_on_interface_{ld.get('interface_id')}_out_of_plane_skipped")
    # Load transfer through fixture/reference connection acts as support on selected part.
    for r in preserve_regions:
        if r.get("semantic_role") == "load_face":
            continue
        if r.get("connection_type") in {"rigid_tie", "bonded", "bolted_proxy", "welded_proxy"} and r.get("cells"):
            if not any(s.get("from", {}).get("interface_id") == r.get("interface_id") for s in supports):
                supports.append({
                    "cells": r["cells"],
                    "from": {"interface_id": r.get("interface_id"), "connection_id": r.get("connection_id"), "type": "proxy_connection_support"},
                    "proxy_derived": True,
                })
    if not supports or not loads:
        diagnostics.append("missing_loads_supports")
    return {"supports": supports, "loads": loads}, diagnostics, warnings


def derive_assembly_topopt_problem(
    *,
    assembly: dict[str, Any],
    part_registry: dict[str, Any],
    connection_graph: dict[str, Any] | None = None,
    interface_resolution: dict[str, Any] | None = None,
    connection_geometry: dict[str, Any] | None = None,
    assembly_cae_model: dict[str, Any] | None = None,
    assembly_result_map: dict[str, Any] | None = None,
    topology_by_part: dict[str, dict[str, dict[str, Any]]] | None = None,
    selected_part_id: str | None = None,
    dimension: str = "2d",
    resolution: int = 48,
    resolution_3d: int = 16,
    volfrac: float = 0.5,
    penalty: float = 3.0,
    rmin: float = 1.5,
    max_iters: int = DEFAULT_MAX_ITERS_2D,
    use_result_guidance: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Derive assembly-aware topopt setup and optional standard optimizer problem."""
    del connection_graph  # reserved for future graph-specific heuristics
    assembly = assembly if isinstance(assembly, dict) else {}
    part_registry = part_registry if isinstance(part_registry, dict) else {}
    interface_resolution = interface_resolution if isinstance(interface_resolution, dict) else {}
    connection_geometry = connection_geometry if isinstance(connection_geometry, dict) else {}
    assembly_cae_model = assembly_cae_model if isinstance(assembly_cae_model, dict) else {}
    assembly_result_map = assembly_result_map if isinstance(assembly_result_map, dict) else {}
    topology_by_part = topology_by_part or {}
    diagnostics: list[str] = []
    warnings: list[str] = []

    target, candidates, d, w = _select_design_part(
        assembly=assembly, registry=part_registry, selected_part_id=selected_part_id)
    diagnostics.extend(d)
    warnings.extend(w)
    if target is None:
        problem = _empty_problem(
            status="needs_user_input",
            diagnostics=diagnostics,
            warnings=warnings,
            candidates=candidates,
            selected_part_id=selected_part_id,
        )
        return problem, _diag(problem), None
    pid = str(target.get("part_id") or target.get("id"))
    topo = topology_by_part.get(pid, {})
    bbox = _part_bbox_from_topology(topo)
    if not topo:
        diagnostics.append("missing_topology")
    if not bbox:
        diagnostics.append("missing_design_space_bbox")
    geom = target.get("geometry_ref")
    if geom and not str(geom).lower().endswith((".step", ".stp", ".json", ".shape_ir.json", ".shape.json")):
        diagnostics.append("unsupported_geometry_ref")
        warnings.append("unsupported_geometry_ref")
    if not bbox:
        problem = _empty_problem(
            status="needs_user_input",
            diagnostics=diagnostics,
            warnings=warnings,
            candidates=candidates,
            selected_part_id=pid,
            target=target,
        )
        return problem, _diag(problem), None

    dim = "3d" if str(dimension).lower() == "3d" else "2d"
    grid, frame = _grid_frame(bbox, dimension=dim, resolution=resolution, resolution_3d=resolution_3d)
    preserve_regions, pw = _derive_preserve_regions(
        selected_part_id=pid,
        assembly=assembly,
        interface_resolution=interface_resolution,
        connection_geometry=connection_geometry,
        grid=grid,
        frame=frame,
        bbox=bbox,
        dimension=dim,
    )
    warnings.extend(pw)
    result_guidance, gw = _result_guidance(
        selected_part_id=pid,
        result_map=assembly_result_map,
        use_result_guidance=use_result_guidance,
    )
    warnings.extend(gw)
    bcs, bd, bw = _derive_bcs(
        selected_part_id=pid,
        assembly_model=assembly_cae_model,
        preserve_regions=preserve_regions,
        bbox=bbox,
        grid=grid,
        frame=frame,
        dimension=dim,
    )
    diagnostics.extend(bd)
    warnings.extend(bw)
    runnable = bool(bcs.get("supports") and bcs.get("loads"))
    status = "ready" if runnable else "needs_user_input"
    target_block = {
        "part_id": pid,
        "geometry_ref": target.get("geometry_ref"),
        "source_ir_node": target.get("source_ir_node"),
        "role": target.get("role"),
        "editable": target.get("editable", True),
        "topology_available": bool(topo),
        "design_space_bbox": bbox,
        "design_space_node": target.get("source_ir_node") or pid,
    }
    standard = None
    if runnable:
        standard = {
            "grid": grid,
            "volfrac": volfrac,
            "penalty": penalty,
            "rmin": rmin,
            "max_iters": max_iters,
            "objective": "compliance_minimization",
            "bcs": bcs,
            "design_space_node": target_block["design_space_node"],
            "source_ir_node": target.get("source_ir_node") or pid,
            "load_case_id": (assembly_cae_model or {}).get("load_case_id") or "assembly_load_case_1",
            "material": target.get("material"),
            "constraints": {
                "preserve_regions": preserve_regions,
                "preserve_min_density": 0.95,
                "derived_from_assembly_proxy_model": True,
            },
            "result_guidance": result_guidance,
            "use_result_guidance": bool(use_result_guidance),
            "preserve_min_density": 0.95,
            "derivation": {
                "source": "assembly_cae_v0",
                "selected_part_id": pid,
                "frame": frame,
                "design_space_bbox": bbox,
                "derived": True,
                "warnings": warnings,
                "limitations": list(_LIMITATIONS),
                "proxy_load_transfer": any(x.get("proxy_derived") for x in _as_list(bcs.get("supports")) + _as_list(bcs.get("loads"))),
                "derived_from_assembly_proxy_model": True,
                "contact_physics_modeled": False,
                "bolt_preload_modeled": False,
            },
        }
    problem = {
        "format": "aieng.assembly_topopt_problem",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "generated_at_utc": _now(),
        "status": status,
        "selected_part_id": pid,
        "target_part": target_block,
        "candidate_parts": [
            {"part_id": c.get("part_id") or c.get("id"), "role": c.get("role"), "editable": c.get("editable", True)}
            for c in candidates
        ],
        "dimension_recommendation": dim,
        "grid": grid,
        "frame": frame,
        "bcs": bcs,
        "preserve_regions": preserve_regions,
        "preserve_mask_guidance": {
            "available": bool(any(r.get("cells") for r in preserve_regions)),
            "preserve_min_density": 0.95,
            "cell_regions": [{"region_id": r.get("region_id"), "cells": r.get("cells", [])} for r in preserve_regions],
        },
        "result_guidance": result_guidance,
        "standard_problem_emitted": standard is not None,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "limitations": list(_LIMITATIONS),
        "provenance": {
            "created_by": "aieng.assembly_topopt_v0",
            "source_artifacts": list(_SOURCE_ARTIFACTS),
            "derived_from_assembly_proxy_model": True,
            "contact_physics_modeled": False,
            "bolt_preload_modeled": False,
            "production_ready": False,
        },
    }
    return problem, _diag(problem), standard


def _empty_problem(
    *,
    status: str,
    diagnostics: list[str],
    warnings: list[str],
    candidates: list[dict[str, Any]],
    selected_part_id: str | None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format": "aieng.assembly_topopt_problem",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "generated_at_utc": _now(),
        "status": status,
        "selected_part_id": selected_part_id,
        "target_part": target,
        "candidate_parts": [
            {"part_id": c.get("part_id") or c.get("id"), "role": c.get("role"), "editable": c.get("editable", True)}
            for c in candidates
        ],
        "standard_problem_emitted": False,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "limitations": list(_LIMITATIONS),
        "provenance": {
            "created_by": "aieng.assembly_topopt_v0",
            "source_artifacts": list(_SOURCE_ARTIFACTS),
            "derived_from_assembly_proxy_model": True,
            "contact_physics_modeled": False,
            "bolt_preload_modeled": False,
            "production_ready": False,
        },
    }


def _diag(problem: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "aieng.assembly_topopt_derivation",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "status": problem.get("status"),
        "selected_part_id": problem.get("selected_part_id"),
        "diagnostics": problem.get("diagnostics") or [],
        "warnings": problem.get("warnings") or [],
        "summary": {
            "candidate_count": len(problem.get("candidate_parts") or []),
            "preserve_region_count": len(problem.get("preserve_regions") or []),
            "standard_problem_emitted": bool(problem.get("standard_problem_emitted")),
        },
        "limitations": list(_LIMITATIONS),
        "provenance": problem.get("provenance") or {},
    }


def _update_manifest(manifest: dict[str, Any], problem: dict[str, Any], standard: dict[str, Any] | None) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else {"format": "aieng.conversion_manifest"}
    asm = manifest.setdefault("assembly", {})
    if not isinstance(asm, dict):
        asm = {}
        manifest["assembly"] = asm
    asm["assembly_topopt_status"] = problem.get("status")
    asm["assembly_topopt_selected_part_id"] = problem.get("selected_part_id")
    asm["assembly_topopt_standard_problem_emitted"] = standard is not None
    asm["assembly_topopt_limitations"] = list(_LIMITATIONS)
    return manifest


def _update_execution_manifest(
    manifest: dict[str, Any],
    execution: dict[str, Any],
    *,
    part_result_path: str | None = None,
    part_shape_path: str | None = None,
) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else {"format": "aieng.conversion_manifest"}
    asm = manifest.setdefault("assembly", {})
    if not isinstance(asm, dict):
        asm = {}
        manifest["assembly"] = asm
    asm["assembly_topopt_execution_status"] = execution.get("status")
    asm["assembly_topopt_execution_selected_part_id"] = execution.get("selected_part_id")
    asm["assembly_topopt_optimizer"] = (execution.get("optimizer") or {}).get("name")
    asm["assembly_topopt_writeback_status"] = (execution.get("writeback") or {}).get("status")
    asm["assembly_topopt_part_result_path"] = part_result_path
    asm["assembly_topopt_part_shape_path"] = part_shape_path
    asm["assembly_topopt_execution_explicit"] = True
    asm["assembly_topopt_execution_limitations"] = list(_LIMITATIONS)
    return manifest


def _part_registry_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(p.get("part_id")): p for p in _as_list(registry.get("parts")) if isinstance(p, dict) and p.get("part_id")}


def _is_part_optimizable(pid: str, assembly_problem: dict[str, Any], registry: dict[str, Any]) -> tuple[bool, str | None]:
    reg = _part_registry_by_id(registry).get(str(pid), {})
    role = reg.get("role") or ((assembly_problem.get("target_part") or {}).get("role"))
    editable = bool(reg.get("editable", (assembly_problem.get("target_part") or {}).get("editable", role == "design_part")))
    if role in _NON_OPTIMIZABLE_ROLES:
        return False, "selected_part_is_reference_or_frozen"
    if not editable:
        return False, "selected_part_not_editable"
    candidates = {str(c.get("part_id")) for c in _as_list(assembly_problem.get("candidate_parts")) if isinstance(c, dict)}
    if candidates and str(pid) not in candidates:
        return False, "selected_part_not_in_candidate_parts"
    return True, None


def _blank_field(shape: tuple[int, ...], value: float = 0.0) -> list[Any]:
    if len(shape) == 3:
        nz, ny, nx = shape
        return [[[value for _i in range(nx)] for _j in range(ny)] for _k in range(nz)]
    nely, nelx = shape
    return [[value for _i in range(nelx)] for _j in range(nely)]


def _field_shape(grid: dict[str, Any]) -> tuple[tuple[int, ...], bool]:
    if "nx" in grid:
        return (int(grid.get("nz", 1)), int(grid.get("ny", 1)), int(grid.get("nx", 1))), True
    return (int(grid.get("nely", 1)), int(grid.get("nelx", 1))), False


def _mark_cell(mask: list[Any], cell: list[int], is3d: bool) -> bool:
    try:
        if is3d:
            i, j, k = int(cell[0]), int(cell[1]), int(cell[2])
            if 0 <= k < len(mask) and 0 <= j < len(mask[k]) and 0 <= i < len(mask[k][j]):
                mask[k][j][i] = 1
                return True
        else:
            i, j = int(cell[0]), int(cell[1])
            if 0 <= j < len(mask) and 0 <= i < len(mask[j]):
                mask[j][i] = 1
                return True
    except Exception:
        return False
    return False


def _count_marked(mask: list[Any], is3d: bool) -> int:
    if is3d:
        return sum(1 for plane in mask for row in plane for v in row if float(v) > 0.5)
    return sum(1 for row in mask for v in row if float(v) > 0.5)


def _merge_numeric_field(base: list[Any], other: Any, *, take_max: bool, is3d: bool) -> None:
    if not isinstance(other, list):
        return
    try:
        if is3d:
            for k, plane in enumerate(other):
                for j, row in enumerate(plane):
                    for i, val in enumerate(row):
                        if k < len(base) and j < len(base[k]) and i < len(base[k][j]):
                            base[k][j][i] = max(float(base[k][j][i]), float(val)) if take_max else float(val)
        else:
            for j, row in enumerate(other):
                for i, val in enumerate(row):
                    if j < len(base) and i < len(base[j]):
                        base[j][i] = max(float(base[j][i]), float(val)) if take_max else float(val)
    except Exception:
        return


def _assembly_preserve_guidance_field(
    standard_problem: dict[str, Any],
    assembly_problem: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build an explicit optimizer guidance field from assembly preserve cells.

    Result guidance from assembly_result_map is merged with interface preserve masks
    when available.  This is still solver-neutral: it only supplies the existing SIMP
    optimizers with guidance fields they already know how to consume.
    """
    warnings: list[str] = []
    grid = standard_problem.get("grid") or assembly_problem.get("grid") or {}
    frame = standard_problem.get("frame") or (standard_problem.get("derivation") or {}).get("frame") or assembly_problem.get("frame") or {}
    shape, is3d = _field_shape(grid)
    preserve_mask = _blank_field(shape, 0.0)
    stiffness_weight = _blank_field(shape, 1.0)
    ignore_mask = _blank_field(shape, 0.0)
    base_field = None
    if (standard_problem.get("result_guidance") or {}).get("available"):
        try:
            base_field = build_guidance_field({**standard_problem, "frame": frame})
            _merge_numeric_field(preserve_mask, base_field.get("preserve_mask"), take_max=True, is3d=is3d)
            _merge_numeric_field(stiffness_weight, base_field.get("stiffness_weight_field"), take_max=True, is3d=is3d)
            _merge_numeric_field(ignore_mask, base_field.get("ignore_mask"), take_max=True, is3d=is3d)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"result_guidance_field_merge_failed:{type(exc).__name__}")

    total = mapped = unmapped = valid_cell_count = 0
    region_summaries: list[dict[str, Any]] = []
    for region in _as_list(assembly_problem.get("preserve_regions")):
        if not isinstance(region, dict):
            continue
        total += 1
        region_valid = 0
        cells = [c for c in _as_list(region.get("cells")) if isinstance(c, list)]
        for cell in cells:
            if _mark_cell(preserve_mask, cell, is3d):
                region_valid += 1
        if region_valid:
            mapped += 1
            valid_cell_count += region_valid
        else:
            unmapped += 1
            warnings.append(f"preserve_region_unmapped:{region.get('region_id')}")
        region_summaries.append({
            "region_id": region.get("region_id"),
            "interface_id": region.get("interface_id"),
            "connection_id": region.get("connection_id"),
            "semantic_role": region.get("semantic_role"),
            "cells_supplied": len(cells),
            "cells_mapped": region_valid,
        })
    cells_preserved = _count_marked(preserve_mask, is3d)
    diagnostics = {
        "preserve_regions_total": total,
        "preserve_regions_mapped": mapped,
        "preserve_regions_unmapped": unmapped,
        "preserve_cells_supplied": valid_cell_count,
        "cells_preserved": cells_preserved,
        "result_guidance_merged": base_field is not None,
        "region_summaries": region_summaries,
    }
    return {
        "format": "aieng.assembly_topopt_guidance_field",
        "schema_version": "0.1",
        "dimension": "3d" if is3d else "2d",
        "grid": dict(grid),
        "frame": frame,
        "options": {
            "use_result_guidance": True,
            "preserve_min_density": float(standard_problem.get("preserve_min_density", 0.95)),
            "source": "assembly_interface_preserve_regions",
        },
        "preserve_mask": preserve_mask,
        "stiffness_weight_field": stiffness_weight,
        "ignore_mask": ignore_mask,
        "diagnostics": diagnostics,
        "provenance": {
            "created_by": "aieng.assembly_topopt_execution_v0",
            "assembly_problem_path": ASSEMBLY_TOPOPT_PROBLEM_PATH,
            "standard_problem_path": STANDARD_TOPOPT_PROBLEM_PATH,
        },
    }, diagnostics, warnings


def _execution_diag(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "aieng.assembly_topopt_execution",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "status": execution.get("status"),
        "selected_part_id": execution.get("selected_part_id"),
        "diagnostics": execution.get("diagnostics") or [],
        "warnings": execution.get("warnings") or [],
        "preserve_constraints": execution.get("preserve_constraints") or {},
        "writeback": execution.get("writeback") or {},
        "summary": {
            "optimizer": (execution.get("optimizer") or {}).get("name"),
            "dimension": execution.get("dimension"),
            "bcs_source": execution.get("bcs_source"),
            "use_result_guidance": execution.get("use_result_guidance"),
            "writeback_status": (execution.get("writeback") or {}).get("status"),
        },
        "limitations": list(_LIMITATIONS),
        "provenance": execution.get("provenance") or {},
    }


def _status_rank(status: str) -> int:
    return {"passed": 0, "warning": 1, "failed": 2, "insufficient_data": 3}.get(str(status), 1)


def _combine_statuses(*statuses: str) -> str:
    valid = [str(status) for status in statuses if status]
    if not valid:
        return "passed"
    return max(valid, key=_status_rank)


def _part_id_from_member(member: str, suffix: str) -> str | None:
    prefix = "parts/"
    if not member.startswith(prefix) or not member.endswith(suffix):
        return None
    middle = member[len(prefix): -len(suffix)]
    if "/" in middle or not middle:
        return None
    return middle


def _scan_unsupported_claims(value: Any, *, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key in _UNSUPPORTED_CLAIM_KEYS and isinstance(item, bool) and item:
                found.append(path)
            found.extend(_scan_unsupported_claims(item, prefix=path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(_scan_unsupported_claims(item, prefix=f"{prefix}[{idx}]"))
    return found


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        token = str(item)
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _verification_summary(verification: dict[str, Any]) -> dict[str, Any]:
    selected = verification.get("selected_part") if isinstance(verification.get("selected_part"), dict) else {}
    non_selected = verification.get("non_selected_parts") if isinstance(verification.get("non_selected_parts"), dict) else {}
    preserve = verification.get("preserve_interfaces") if isinstance(verification.get("preserve_interfaces"), dict) else {}
    provenance = verification.get("provenance") if isinstance(verification.get("provenance"), dict) else {}
    return {
        "format": "aieng.assembly_optimization_summary.v0",
        "format_version": FORMAT_VERSION,
        "generated_at_utc": verification.get("generated_at_utc") or _now(),
        "status": verification.get("status"),
        "selected_part_id": verification.get("selected_part_id"),
        "selected_part_status": selected.get("status"),
        "optimized_artifact_found": bool(selected.get("optimized_artifact_found")),
        "optimization_result_found": bool(selected.get("optimization_result_found")),
        "unexpected_modified_parts": len(_as_list(non_selected.get("unexpected_modified_parts"))),
        "frozen_parts_modified": len(_as_list(non_selected.get("frozen_parts_modified"))),
        "preserve_regions_total": int(preserve.get("preserve_regions_total") or 0),
        "preserve_regions_unmapped": int(preserve.get("preserve_regions_unmapped") or 0),
        "provenance_complete": bool(provenance.get("provenance_complete")),
        "proxy_limitations_preserved": bool(provenance.get("proxy_limitations_preserved")),
        "warnings": _as_list(verification.get("warnings")),
        "errors": _as_list(verification.get("errors")),
    }


def _update_post_optimization_manifest(
    manifest: dict[str, Any],
    verification: dict[str, Any],
    *,
    summary_written: bool,
) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else {"format": "aieng.conversion_manifest"}
    asm = manifest.setdefault("assembly", {})
    if not isinstance(asm, dict):
        asm = {}
        manifest["assembly"] = asm
    asm["assembly_post_optimization_verification_status"] = verification.get("status")
    asm["assembly_post_optimization_selected_part_id"] = verification.get("selected_part_id")
    asm["assembly_post_optimization_verification_path"] = ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH
    asm["assembly_post_optimization_summary_path"] = ASSEMBLY_OPTIMIZATION_SUMMARY_PATH if summary_written else None
    asm["assembly_post_optimization_proxy_limitations_preserved"] = (
        (verification.get("provenance") or {}).get("proxy_limitations_preserved")
        if isinstance(verification.get("provenance"), dict)
        else None
    )
    return manifest


def _update_design_recommendation_manifest(
    manifest: dict[str, Any],
    recommendations: dict[str, Any],
    report: dict[str, Any],
    *,
    next_actions_written: bool,
) -> dict[str, Any]:
    manifest = manifest if isinstance(manifest, dict) else {"format": "aieng.conversion_manifest"}
    asm = manifest.setdefault("assembly", {})
    if not isinstance(asm, dict):
        asm = {}
        manifest["assembly"] = asm
    asm["assembly_design_recommendations_status"] = recommendations.get("status")
    asm["assembly_design_recommendations_path"] = ASSEMBLY_DESIGN_RECOMMENDATIONS_PATH
    asm["assembly_postprocess_report_path"] = ASSEMBLY_POSTPROCESS_REPORT_PATH
    asm["assembly_postprocess_selected_part_id"] = recommendations.get("selected_part_id")
    asm["assembly_postprocess_recommendation_count"] = len(_as_list(recommendations.get("recommendations")))
    asm["assembly_postprocess_next_actions_path"] = ASSEMBLY_NEXT_ACTIONS_PATH if next_actions_written else None
    asm["assembly_postprocess_report_status"] = report.get("status")
    return manifest


def _needs_input_execution(
    *,
    reason: str,
    diagnostics: list[str],
    warnings: list[str] | None = None,
    selected_part_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    execution = {
        "format": "aieng.assembly_topology_optimization",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "generated_at_utc": _now(),
        "status": "needs_user_input",
        "selected_part_id": selected_part_id,
        "diagnostics": diagnostics or [reason],
        "warnings": warnings or [],
        "writeback": {"status": "not_attempted", "reason": reason},
        "limitations": list(_LIMITATIONS),
        "provenance": {
            "created_by": "aieng.assembly_topopt_execution_v0",
            "explicit_execution_required": True,
            "optimizer_executed": False,
            "source_artifacts": [ASSEMBLY_TOPOPT_PROBLEM_PATH, STANDARD_TOPOPT_PROBLEM_PATH],
            "assembly_connections_are_proxy_derived": True,
            "contact_physics_modeled": False,
            "bolt_preload_modeled": False,
            "multi_part_simultaneous_optimization": False,
            "production_ready": False,
        },
    }
    return execution, _execution_diag(execution)


def _recommendation_entry(
    *,
    rec_id: str,
    rec_type: str,
    severity: str,
    reason: str,
    source_artifacts: list[str],
    confidence: str,
    source_part_id: str | None = None,
    source_interface_id: str | None = None,
    source_connection_id: str | None = None,
    source_ir_node: str | None = None,
    load_case_id: str | None = None,
    result_type: str | None = None,
    suggested_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "id": rec_id,
        "type": rec_type,
        "severity": severity,
        "reason": reason,
        "source_artifacts": sorted(set(str(x) for x in source_artifacts if x)),
        "confidence": confidence,
    }
    if source_part_id is not None:
        out["source_part_id"] = source_part_id
    if source_interface_id is not None:
        out["source_interface_id"] = source_interface_id
    if source_connection_id is not None:
        out["source_connection_id"] = source_connection_id
    if source_ir_node is not None:
        out["source_ir_node"] = source_ir_node
    if load_case_id is not None:
        out["load_case_id"] = load_case_id
    if result_type is not None:
        out["result_type"] = result_type
    if suggested_parameters:
        out["suggested_parameters"] = suggested_parameters
    return out


def _severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"info": 0, "warning": 0, "critical": 0}
    for item in items:
        sev = str(item.get("severity") or "warning")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _next_actions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"critical": 0, "warning": 1, "info": 2}
    ranked = sorted(items, key=lambda item: (order.get(str(item.get("severity")), 3), str(item.get("id") or "")))
    return [
        {
            "recommendation_id": item.get("id"),
            "type": item.get("type"),
            "severity": item.get("severity"),
            "reason": item.get("reason"),
        }
        for item in ranked
    ]


def _report_status(
    *,
    missing_inputs: list[str],
    recommendations: list[dict[str, Any]],
    verification_status: str | None = None,
) -> str:
    if missing_inputs:
        return "insufficient_data"
    if verification_status == "insufficient_data":
        return "insufficient_data"
    if verification_status == "needs_user_input":
        return "needs_user_input"
    if any(str(item.get("severity")) == "critical" for item in recommendations):
        return "failed"
    if any(str(item.get("type")) in {"rerun_topopt", "increase_preserve", "increase_stiffness_weight"} for item in recommendations):
        return "rerun_recommended"
    if any(str(item.get("type")) == "request_user_input" for item in recommendations):
        return "needs_user_input"
    if recommendations:
        return "accept"
    return "warning"


def _shape_writeback_payload(
    topo_result: dict[str, Any],
    *,
    selected_part_id: str,
    target_part: dict[str, Any],
    method: str,
    representation: str,
    boundary: str,
) -> dict[str, Any]:
    payload = topology_result_to_shape_ir(
        topo_result,
        representation=representation,
        method=method,
        boundary=boundary,
        node_id=f"optimized_{selected_part_id}",
    )
    payload.setdefault("provenance", {})
    payload["provenance"].update({
        "assembly_aware_topopt": True,
        "selected_part_id": selected_part_id,
        "source_ir_node": target_part.get("source_ir_node"),
        "source_geometry_ref": target_part.get("geometry_ref"),
        "derived_part_artifact": True,
        "does_not_overwrite_assembly_geometry": True,
    })
    payload["assembly_writeback"] = {
        "selected_part_id": selected_part_id,
        "source_ir_node": target_part.get("source_ir_node"),
        "design_space_node": target_part.get("design_space_node"),
        "writeback_kind": "derived_selected_part_artifact",
        "original_geometry_ref_preserved": target_part.get("geometry_ref"),
    }
    return payload


def verify_assembly_post_optimization(
    package_path: str | Path,
    *,
    emit_summary: bool = True,
) -> dict[str, Any]:
    """Verify selected-part-only assembly topopt writeback and record diagnostics.

    This is a conservative post-writeback audit. It checks artifact presence,
    non-selected part immutability, preserve-region traceability, and honesty /
    provenance fields. It does not certify physical interface equivalence.
    """
    package_path = Path(package_path)
    generated_at = _now()
    if not package_path.exists():
        verification = {
            "format": "aieng.assembly.post_optimization_verification.v0",
            "format_version": FORMAT_VERSION,
            "generated_at_utc": generated_at,
            "status": "insufficient_data",
            "selected_part_id": None,
            "selected_part": {"status": "insufficient_data", "errors": ["package_not_found"], "warnings": []},
            "non_selected_parts": {"status": "insufficient_data", "non_selected_parts_checked": 0, "unexpected_modified_parts": [], "frozen_parts_modified": []},
            "preserve_interfaces": {"status": "insufficient_data", "preserve_regions_total": 0, "preserve_regions_mapped": 0, "preserve_regions_unmapped": 0, "preserve_interface_ids": [], "warnings": ["package_not_found"]},
            "provenance": {"status": "insufficient_data", "provenance_complete": False, "proxy_limitations_preserved": False, "unsupported_claims_detected": [], "warnings": ["package_not_found"]},
            "honesty": {
                "contact_physics_modeled": False,
                "bolt_preload_modeled": False,
                "multi_part_optimization": False,
                "proxy_connection_model": False,
            },
            "errors": ["package_not_found"],
            "warnings": [],
            "source_artifacts": [],
        }
        return {"status": "insufficient_data", "verification": verification, "summary": _verification_summary(verification), "artifacts": []}

    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            assembly = _read_json(zf, names, ASSEMBLY_IR_PATH) or {}
            part_registry = _read_json(zf, names, PART_REGISTRY_PATH) or {}
            connection_graph = _read_json(zf, names, CONNECTION_GRAPH_PATH) or {}
            interface_resolution = _read_json(zf, names, INTERFACE_RESOLUTION_PATH) or {}
            connection_geometry = _read_json(zf, names, ASSEMBLY_CONNECTION_GEOMETRY_PATH) or {}
            assembly_problem = _read_json(zf, names, ASSEMBLY_TOPOPT_PROBLEM_PATH) or {}
            execution = _read_json(zf, names, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH) or {}
            execution_diag = _read_json(zf, names, ASSEMBLY_TOPOPT_EXECUTION_PATH) or {}
            manifest = _read_json(zf, names, CONVERSION_MANIFEST_PATH) or {"format": "aieng.conversion_manifest"}
    except Exception as exc:  # noqa: BLE001
        verification = {
            "format": "aieng.assembly.post_optimization_verification.v0",
            "format_version": FORMAT_VERSION,
            "generated_at_utc": generated_at,
            "status": "insufficient_data",
            "selected_part_id": None,
            "selected_part": {"status": "insufficient_data", "errors": [f"package_read_failed:{type(exc).__name__}"], "warnings": []},
            "non_selected_parts": {"status": "insufficient_data", "non_selected_parts_checked": 0, "unexpected_modified_parts": [], "frozen_parts_modified": []},
            "preserve_interfaces": {"status": "insufficient_data", "preserve_regions_total": 0, "preserve_regions_mapped": 0, "preserve_regions_unmapped": 0, "preserve_interface_ids": [], "warnings": [f"package_read_failed:{type(exc).__name__}"]},
            "provenance": {"status": "insufficient_data", "provenance_complete": False, "proxy_limitations_preserved": False, "unsupported_claims_detected": [], "warnings": [f"package_read_failed:{type(exc).__name__}"]},
            "honesty": {
                "contact_physics_modeled": False,
                "bolt_preload_modeled": False,
                "multi_part_optimization": False,
                "proxy_connection_model": False,
            },
            "errors": [f"package_read_failed:{type(exc).__name__}: {exc}"],
            "warnings": [],
            "source_artifacts": [],
        }
        return {"status": "insufficient_data", "verification": verification, "summary": _verification_summary(verification), "artifacts": []}

    required_inputs = [
        ASSEMBLY_IR_PATH,
        PART_REGISTRY_PATH,
        CONNECTION_GRAPH_PATH,
        INTERFACE_RESOLUTION_PATH,
        ASSEMBLY_CONNECTION_GEOMETRY_PATH,
        ASSEMBLY_TOPOPT_PROBLEM_PATH,
        STANDARD_TOPOPT_PROBLEM_PATH,
        ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH,
        ASSEMBLY_TOPOPT_EXECUTION_PATH,
    ]
    missing_inputs = [member for member in required_inputs if member not in names]
    errors: list[str] = []
    warnings: list[str] = []
    if missing_inputs:
        errors.extend(f"missing_input:{member}" for member in missing_inputs)

    selected_part_id = str(assembly_problem.get("selected_part_id") or execution.get("selected_part_id") or "").strip() or None
    registry_parts = [part for part in _as_list(part_registry.get("parts")) if isinstance(part, dict) and part.get("part_id")]
    registry_by_id = {str(part["part_id"]): part for part in registry_parts}
    selected_part = registry_by_id.get(str(selected_part_id)) if selected_part_id else None

    manifest_asm = manifest.get("assembly") if isinstance(manifest.get("assembly"), dict) else {}
    writeback = execution.get("writeback") if isinstance(execution.get("writeback"), dict) else {}
    part_shape_path = str(
        writeback.get("part_shape_ir_path")
        or manifest_asm.get("assembly_topopt_part_shape_path")
        or (PART_OPTIMIZED_SHAPE_IR_TEMPLATE.format(part_id=selected_part_id) if selected_part_id else "")
    )
    part_result_path = str(
        manifest_asm.get("assembly_topopt_part_result_path")
        or (PART_TOPOLOGY_OPTIMIZATION_TEMPLATE.format(part_id=selected_part_id) if selected_part_id else "")
    )
    optimized_artifact_found = bool(part_shape_path and part_shape_path in names)
    optimization_result_found = bool(part_result_path and part_result_path in names)
    part_shape = None
    part_result = None
    with zipfile.ZipFile(package_path, "r") as zf:
        if optimized_artifact_found:
            part_shape = _read_json(zf, names, part_shape_path) or {}
        if optimization_result_found:
            part_result = _read_json(zf, names, part_result_path) or {}

    selected_errors: list[str] = []
    selected_warnings: list[str] = []
    role = (selected_part or {}).get("role")
    editable = bool((selected_part or {}).get("editable"))
    optimizer_executed = bool((execution.get("provenance") or {}).get("optimizer_executed"))
    writeback_status = str(writeback.get("status") or execution.get("status") or "")
    if not selected_part_id:
        selected_errors.append("selected_part_id_missing")
    if selected_part_id and selected_part is None:
        selected_errors.append("selected_part_missing_from_registry")
    if selected_part is not None and role != "design_part" and not editable:
        selected_errors.append("selected_part_not_design_or_editable")
    if optimizer_executed and writeback_status == "derived_part_artifact_written" and not optimized_artifact_found:
        selected_errors.append("selected_optimized_artifact_missing")
    elif not optimized_artifact_found and writeback_status in {"needs_user_input", "not_attempted", "optimized_writeback_ready", "not_requested"}:
        selected_warnings.append("optimized_artifact_not_available")
    if optimizer_executed and not optimization_result_found:
        selected_errors.append("selected_optimization_result_missing")
    elif not optimizer_executed and not optimization_result_found:
        selected_warnings.append("optimization_result_not_available")
    writeback_method = writeback.get("writeback_method") or writeback.get("method")
    if execution.get("status") == "derived_part_artifact_written" and not writeback_method:
        selected_warnings.append("writeback_method_missing")
    source_artifacts = _dedupe(_as_list((execution.get("provenance") or {}).get("source_artifacts")))
    if not source_artifacts:
        selected_warnings.append("source_artifacts_missing")
    selected_status = "failed" if selected_errors else ("insufficient_data" if not optimizer_executed else ("warning" if selected_warnings else "passed"))
    selected_block = {
        "selected_part_id": selected_part_id,
        "role": role,
        "editable": editable,
        "optimized_artifact_found": optimized_artifact_found,
        "optimization_result_found": optimization_result_found,
        "optimized_artifact_path": part_shape_path or None,
        "optimization_result_path": part_result_path or None,
        "writeback_method": writeback_method,
        "source_artifacts_recorded": source_artifacts,
        "source_geometry_ref": writeback.get("source_geometry_ref") or (selected_part or {}).get("geometry_ref"),
        "status": selected_status,
        "errors": selected_errors,
        "warnings": selected_warnings,
    }

    non_selected_checked = 0
    unexpected_modified_parts: list[dict[str, Any]] = []
    frozen_parts_modified: list[dict[str, Any]] = []
    for part in registry_parts:
        pid = str(part.get("part_id"))
        if pid == str(selected_part_id):
            continue
        non_selected_checked += 1
        artifact_paths = []
        shape_path = PART_OPTIMIZED_SHAPE_IR_TEMPLATE.format(part_id=pid)
        result_path = PART_TOPOLOGY_OPTIMIZATION_TEMPLATE.format(part_id=pid)
        if shape_path in names:
            artifact_paths.append(shape_path)
        if result_path in names:
            artifact_paths.append(result_path)
        if not artifact_paths:
            continue
        item = {
            "part_id": pid,
            "role": part.get("role"),
            "editable": bool(part.get("editable")),
            "artifact_paths": artifact_paths,
        }
        unexpected_modified_parts.append(item)
        if part.get("role") in _NON_OPTIMIZABLE_ROLES or not bool(part.get("editable")):
            frozen_parts_modified.append(item)

    for member in names:
        pid = _part_id_from_member(member, "/geometry/optimized_shape_ir.json") or _part_id_from_member(member, "/analysis/topology_optimization.json")
        if not pid or pid == str(selected_part_id) or pid in registry_by_id:
            continue
        unexpected_modified_parts.append({
            "part_id": pid,
            "role": "unknown",
            "editable": False,
            "artifact_paths": [member],
        })

    non_selected_status = "failed" if frozen_parts_modified or unexpected_modified_parts else "passed"
    non_selected_block = {
        "non_selected_parts_checked": non_selected_checked,
        "unexpected_modified_parts": unexpected_modified_parts,
        "frozen_parts_modified": frozen_parts_modified,
        "status": non_selected_status,
    }

    edges = {
        str(edge.get("id")): edge
        for edge in _as_list(connection_graph.get("edges"))
        if isinstance(edge, dict) and edge.get("id")
    }
    preserve_regions = [region for region in _as_list(assembly_problem.get("preserve_regions")) if isinstance(region, dict)]
    preserve_interface_ids: list[str] = []
    preserve_warnings: list[str] = []
    preserve_errors: list[str] = []
    preserve_diag = execution.get("preserve_constraints") if isinstance(execution.get("preserve_constraints"), dict) else {}
    region_summaries = {
        str(item.get("region_id")): item
        for item in _as_list(preserve_diag.get("region_summaries"))
        if isinstance(item, dict) and item.get("region_id")
    }
    preserve_mapped = 0
    preserve_unmapped = 0
    for region in preserve_regions:
        region_id = str(region.get("region_id") or "")
        interface_id = region.get("interface_id")
        if interface_id:
            preserve_interface_ids.append(str(interface_id))
        else:
            preserve_warnings.append(f"preserve_region_missing_interface:{region_id or 'unknown'}")
        region_part_id = region.get("part_id")
        if region_part_id and selected_part_id and str(region_part_id) != str(selected_part_id):
            connection_id = region.get("connection_id")
            edge = edges.get(str(connection_id)) if connection_id else None
            if not edge or str(selected_part_id) not in {str(edge.get("part_a")), str(edge.get("part_b"))}:
                preserve_warnings.append(f"preserve_region_not_traceable_to_selected:{region_id or interface_id}")
        cells = [cell for cell in _as_list(region.get("cells")) if isinstance(cell, list)]
        summary = region_summaries.get(region_id)
        mapped_cells = int(summary.get("cells_mapped") or 0) if isinstance(summary, dict) else len(cells)
        if mapped_cells > 0:
            preserve_mapped += 1
        else:
            preserve_unmapped += 1
            preserve_warnings.append(f"preserve_region_unmapped:{region_id or interface_id}")
    if not preserve_regions:
        preserve_warnings.append("no_preserve_regions_recorded")
    preserve_status = "failed" if preserve_errors else ("warning" if preserve_warnings else "passed")
    preserve_block = {
        "preserve_regions_total": len(preserve_regions),
        "preserve_regions_mapped": preserve_mapped,
        "preserve_regions_unmapped": preserve_unmapped,
        "preserve_interface_ids": _dedupe(preserve_interface_ids),
        "warnings": preserve_warnings,
        "status": preserve_status,
    }

    provenance_warnings: list[str] = []
    execution_provenance = execution.get("provenance") if isinstance(execution.get("provenance"), dict) else {}
    required_source_artifacts = {
        ASSEMBLY_IR_PATH,
        PART_REGISTRY_PATH,
        CONNECTION_GRAPH_PATH,
        INTERFACE_RESOLUTION_PATH,
        ASSEMBLY_TOPOPT_PROBLEM_PATH,
        STANDARD_TOPOPT_PROBLEM_PATH,
    }
    if (execution.get("assembly_result_guidance") or {}).get("available"):
        required_source_artifacts.add(ASSEMBLY_RESULT_MAP_PATH)
    missing_sources = sorted(required_source_artifacts - set(source_artifacts))
    if missing_sources:
        provenance_warnings.extend(f"missing_source_artifact:{member}" for member in missing_sources)
    proxy_connection_model = bool(
        execution_provenance.get("assembly_connections_are_proxy_derived")
        or (assembly_problem.get("provenance") or {}).get("derived_from_assembly_proxy_model")
        or (part_result.get("problem") or {}).get("assembly_preserve_constraints")
    )
    if not proxy_connection_model:
        provenance_warnings.append("proxy_connection_model_not_recorded")
    unsupported_claims = _dedupe(
        _scan_unsupported_claims(execution, prefix="execution")
        + _scan_unsupported_claims(execution_diag, prefix="execution_diag")
        + _scan_unsupported_claims(part_result, prefix="part_result")
        + _scan_unsupported_claims(part_shape, prefix="part_shape")
    )
    provenance_complete = bool(selected_part_id and source_artifacts and execution_provenance)
    proxy_limitations_preserved = bool(
        execution_provenance.get("contact_physics_modeled") is False
        and execution_provenance.get("bolt_preload_modeled") is False
        and execution_provenance.get("multi_part_simultaneous_optimization") is False
        and proxy_connection_model
        and not unsupported_claims
    )
    provenance_status = "failed" if unsupported_claims else ("warning" if provenance_warnings or not provenance_complete or not proxy_limitations_preserved else "passed")
    provenance_block = {
        "provenance_complete": provenance_complete,
        "proxy_limitations_preserved": proxy_limitations_preserved,
        "unsupported_claims_detected": unsupported_claims,
        "source_artifacts_recorded": source_artifacts,
        "warnings": provenance_warnings,
        "status": provenance_status,
    }

    errors.extend(selected_errors)
    errors.extend(preserve_errors)
    warnings.extend(selected_warnings)
    warnings.extend(preserve_warnings)
    warnings.extend(provenance_warnings)
    if unexpected_modified_parts:
        warnings.extend(f"unexpected_modified_part:{item['part_id']}" for item in unexpected_modified_parts)
    if frozen_parts_modified:
        errors.extend(f"frozen_part_modified:{item['part_id']}" for item in frozen_parts_modified)
    if unsupported_claims:
        errors.extend(f"unsupported_claim_detected:{claim}" for claim in unsupported_claims)

    honesty = {
        "contact_physics_modeled": bool(execution_provenance.get("contact_physics_modeled")),
        "bolt_preload_modeled": bool(execution_provenance.get("bolt_preload_modeled") or execution_provenance.get("bolt_preload_modelled")),
        "multi_part_optimization": bool(execution_provenance.get("multi_part_simultaneous_optimization") or execution_provenance.get("multi_part_optimization")),
        "proxy_connection_model": proxy_connection_model,
    }

    overall_status = _combine_statuses(selected_status, non_selected_status, preserve_status, provenance_status)
    if missing_inputs:
        overall_status = "insufficient_data"

    verification = {
        "format": "aieng.assembly.post_optimization_verification.v0",
        "format_version": FORMAT_VERSION,
        "generated_at_utc": generated_at,
        "status": overall_status,
        "selected_part_id": selected_part_id,
        "selected_part": selected_block,
        "non_selected_parts": non_selected_block,
        "preserve_interfaces": preserve_block,
        "provenance": provenance_block,
        "honesty": honesty,
        "errors": _dedupe(errors),
        "warnings": _dedupe(warnings),
        "source_artifacts": _dedupe(source_artifacts + required_inputs),
    }
    summary = _verification_summary(verification)
    members = {
        ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH: _dumps(verification),
        CONVERSION_MANIFEST_PATH: _dumps(_update_post_optimization_manifest(manifest, verification, summary_written=emit_summary)),
    }
    if emit_summary:
        members[ASSEMBLY_OPTIMIZATION_SUMMARY_PATH] = _dumps(summary)
    _replace_members(package_path, members)
    return {
        "status": overall_status,
        "verification": verification,
        "summary": summary,
        "artifacts": sorted(members.keys()),
    }


def write_assembly_design_recommendations(
    package_path: str | Path,
    *,
    emit_next_actions: bool = True,
) -> dict[str, Any]:
    """Build rule-based assembly design recommendations from existing execution artifacts.

    This is post-processing only. It never re-runs optimization or modifies geometry.
    Missing inputs degrade honestly to insufficient_data / needs_user_input artifacts.
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"assembly_present": False, "reason": "package not found"}

    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            if ASSEMBLY_IR_PATH not in names:
                return {"assembly_present": False}
            manifest = _read_json(zf, names, CONVERSION_MANIFEST_PATH) or {"format": "aieng.conversion_manifest"}
            part_registry = _read_json(zf, names, PART_REGISTRY_PATH) or {}
            connection_graph = _read_json(zf, names, CONNECTION_GRAPH_PATH) or {}
            interface_resolution = _read_json(zf, names, INTERFACE_RESOLUTION_PATH) or {}
            connection_geometry = _read_json(zf, names, ASSEMBLY_CONNECTION_GEOMETRY_PATH) or {}
            result_map = _read_json(zf, names, ASSEMBLY_RESULT_MAP_PATH) or {}
            assembly_problem = _read_json(zf, names, ASSEMBLY_TOPOPT_PROBLEM_PATH) or {}
            execution = _read_json(zf, names, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH) or {}
            execution_diag = _read_json(zf, names, ASSEMBLY_TOPOPT_EXECUTION_PATH) or {}
            verification = _read_json(zf, names, ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH) or {}
            optimization_summary = _read_json(zf, names, ASSEMBLY_OPTIMIZATION_SUMMARY_PATH) or {}
            selected_part_id = str(
                assembly_problem.get("selected_part_id")
                or execution.get("selected_part_id")
                or verification.get("selected_part_id")
                or ""
            ).strip() or None
            selected_shape_path = (
                PART_OPTIMIZED_SHAPE_IR_TEMPLATE.format(part_id=selected_part_id)
                if selected_part_id
                else None
            )
            selected_part_shape = _read_json(zf, names, selected_shape_path) if selected_shape_path in names else {}
    except Exception as exc:  # noqa: BLE001
        return {
            "assembly_present": True,
            "status": "insufficient_data",
            "error": f"{type(exc).__name__}: {exc}",
        }

    required_inputs = [
        PART_REGISTRY_PATH,
        CONNECTION_GRAPH_PATH,
        INTERFACE_RESOLUTION_PATH,
        ASSEMBLY_CONNECTION_GEOMETRY_PATH,
        ASSEMBLY_TOPOPT_PROBLEM_PATH,
        ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH,
        ASSEMBLY_TOPOPT_EXECUTION_PATH,
        ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH,
    ]
    optional_inputs = [
        ASSEMBLY_RESULT_MAP_PATH,
        ASSEMBLY_OPTIMIZATION_SUMMARY_PATH,
    ]
    present_inputs = [member for member in required_inputs + optional_inputs if member in names]
    missing_inputs = [member for member in required_inputs if member not in names]

    registry_by_id = {
        str(part.get("part_id")): part
        for part in _as_list(part_registry.get("parts"))
        if isinstance(part, dict) and part.get("part_id")
    }
    selected_part = registry_by_id.get(str(selected_part_id)) if selected_part_id else None
    selected_part_block = verification.get("selected_part") if isinstance(verification.get("selected_part"), dict) else {}
    non_selected_block = verification.get("non_selected_parts") if isinstance(verification.get("non_selected_parts"), dict) else {}
    preserve_block = verification.get("preserve_interfaces") if isinstance(verification.get("preserve_interfaces"), dict) else {}
    provenance_block = verification.get("provenance") if isinstance(verification.get("provenance"), dict) else {}
    topo_result = ((execution.get("topology_optimization") or {}).get("result") if isinstance(execution.get("topology_optimization"), dict) else {}) or {}
    mapped_results = [item for item in _as_list(result_map.get("mapped_results")) if isinstance(item, dict)]
    selected_results = [item for item in mapped_results if str(item.get("part_id")) == str(selected_part_id)]
    connection_edges = {
        str(edge.get("id")): edge
        for edge in _as_list(connection_graph.get("edges"))
        if isinstance(edge, dict) and edge.get("id")
    }
    interface_records = interface_resolution.get("interfaces") if isinstance(interface_resolution.get("interfaces"), dict) else {}
    geometry_records = [item for item in _as_list(connection_geometry.get("connections")) if isinstance(item, dict)]
    geometry_by_conn = {str(item.get("connection_id")): item for item in geometry_records if item.get("connection_id")}

    unresolved_interface_count = sum(
        1
        for record in interface_records.values()
        if isinstance(record, dict) and str(record.get("resolution_status")) != "resolved"
    )
    proxy_connection_count = sum(
        1
        for edge in connection_edges.values()
        if bool(edge.get("is_proxy")) or "proxy" in str(edge.get("type") or "")
    )
    low_confidence_results = [
        item for item in selected_results if str(item.get("confidence") or "low").lower() not in _ENFORCEABLE_CONFIDENCE
    ]
    low_confidence_mapping_count = len(low_confidence_results)
    unmapped_region_count = len(_as_list(result_map.get("unmapped_regions")))
    preserve_regions_unmapped = int(preserve_block.get("preserve_regions_unmapped") or 0)
    unexpected_modified_parts = _as_list(non_selected_block.get("unexpected_modified_parts"))
    frozen_parts_modified = _as_list(non_selected_block.get("frozen_parts_modified"))
    verification_status = str(verification.get("status") or "insufficient_data")
    guidance_consumed = bool(topo_result.get("result_guidance_consumed"))
    achieved_volume_fraction = topo_result.get("achieved_volume_fraction")
    target_volume_fraction = topo_result.get("target_volume_fraction") or ((execution.get("topology_optimization") or {}).get("problem") or {}).get("volfrac")
    representation = str(((execution.get("writeback") or {}).get("representation") or (selected_part_shape or {}).get("representation") or "")).lower()

    rules_evaluated = [
        "accept_candidate",
        "review_interface_refs",
        "stress_hotspot_near_connection",
        "deflection_hotspot",
        "low_confidence_or_unmapped_mapping",
        "unsafe_writeback",
        "proceed_to_dimension_optimization",
        "proceed_to_mesh_to_cad_reconstruction",
    ]
    rules_triggered: list[str] = []
    recommendations: list[dict[str, Any]] = []
    status_reasoning: list[str] = []
    inconsistencies: list[str] = []

    if selected_part_id and isinstance(execution_diag, dict) and execution_diag.get("selected_part_id") not in {None, selected_part_id}:
        inconsistencies.append("selected_part_id_mismatch:execution_diag")
    if selected_part_id and isinstance(verification, dict) and verification.get("selected_part_id") not in {None, selected_part_id}:
        inconsistencies.append("selected_part_id_mismatch:verification")
    if selected_part_id and selected_part is None:
        inconsistencies.append("selected_part_missing_from_registry")
    if unexpected_modified_parts and not frozen_parts_modified:
        inconsistencies.append("unexpected_non_selected_artifacts_present")

    rec_index = 1

    def add_rec(**kwargs: Any) -> None:
        nonlocal rec_index
        recommendations.append(_recommendation_entry(rec_id=f"rec_{rec_index:03d}", **kwargs))
        rec_index += 1

    critical_missing = [
        member
        for member in missing_inputs
        if member in {
            ASSEMBLY_TOPOPT_PROBLEM_PATH,
            ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH,
            ASSEMBLY_TOPOPT_EXECUTION_PATH,
            ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH,
            PART_REGISTRY_PATH,
        }
    ]
    if critical_missing:
        rules_triggered.append("missing_inputs")
        status_reasoning.append("Critical recommendation inputs are missing.")
        add_rec(
            rec_type="request_user_input",
            severity="critical",
            reason="Missing required assembly postprocess artifacts prevents a trustworthy recommendation pass.",
            source_artifacts=critical_missing,
            confidence="high",
            source_part_id=selected_part_id,
        )
    else:
        verification_failed = verification_status == "failed"
        if verification_failed:
            rules_triggered.append("unsafe_writeback")
            status_reasoning.append("Post-optimization verification failed; downstream export is blocked.")
            add_rec(
                rec_type="block_downstream_export",
                severity="critical",
                reason="Post-optimization verification failed; selected-part-only writeback or honesty checks did not pass.",
                source_artifacts=[ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH],
                confidence="high",
                source_part_id=selected_part_id,
                source_ir_node=(selected_part or {}).get("source_ir_node"),
            )
            add_rec(
                rec_type="request_user_input",
                severity="critical",
                reason="A human should review the failed assembly post-optimization verification before continuing.",
                source_artifacts=[ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH],
                confidence="high",
                source_part_id=selected_part_id,
            )
        elif verification_status == "insufficient_data":
            rules_triggered.append("insufficient_verification_data")
            status_reasoning.append("Post-optimization verification is still insufficient, so only advisory next steps are safe.")
            add_rec(
                rec_type="request_user_input",
                severity="warning",
                reason="The package does not yet contain enough post-optimization evidence to trust downstream export or rerun advice.",
                source_artifacts=[ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH, ASSEMBLY_TOPOPT_EXECUTION_PATH],
                confidence="medium",
                source_part_id=selected_part_id,
            )

        preserve_issue = preserve_regions_unmapped > 0 or unresolved_interface_count > 0
        if preserve_issue:
            rules_triggered.append("review_interface_refs")
            status_reasoning.append("Preserve regions or interface references are not fully mapped.")
            add_rec(
                rec_type="review_interface_refs",
                severity="critical" if preserve_regions_unmapped > 1 else "warning",
                reason="One or more preserve interfaces are unmapped or unresolved; review topology references before trusting the optimized candidate.",
                source_artifacts=[ASSEMBLY_TOPOPT_PROBLEM_PATH, ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH, INTERFACE_RESOLUTION_PATH],
                confidence="high" if preserve_regions_unmapped else "medium",
                source_part_id=selected_part_id,
                suggested_parameters={"review_preserve_interfaces": True},
            )

        stress_hotspots = []
        deflection_hotspots = []
        high_confidence_results = 0
        for item in selected_results:
            conf = str(item.get("confidence") or "low").lower()
            if conf in _ENFORCEABLE_CONFIDENCE:
                high_confidence_results += 1
            connection = connection_edges.get(str(item.get("connection_id"))) if item.get("connection_id") else None
            iface = interface_records.get(str(item.get("interface_id"))) if item.get("interface_id") else None
            geom = geometry_by_conn.get(str(item.get("connection_id"))) if item.get("connection_id") else None
            role = (iface or {}).get("semantic_role")
            ctype = str((connection or {}).get("type") or "")
            geom_status = str((geom or {}).get("geometry_status") or (geom or {}).get("status") or "")
            critical_interface = ctype in {"bolted_proxy", "welded_proxy", "rigid_tie"} or role in {"mounting_face", "bolt_hole", "support_face"}
            if not critical_interface:
                continue
            rtype = str(item.get("result_type") or "").lower()
            if rtype in _STRESS_TYPES:
                stress_hotspots.append((item, ctype, role, geom_status))
            elif rtype in _DEFLECTION_TYPES:
                deflection_hotspots.append((item, ctype, role, geom_status))

        enforceable_stress_hotspots = [
            item for item in stress_hotspots if str(item[0].get("confidence") or "low").lower() in _ENFORCEABLE_CONFIDENCE
        ]
        should_advise_rerun = bool(enforceable_stress_hotspots) and (preserve_issue or not guidance_consumed or verification_status == "warning")
        if should_advise_rerun:
            rules_triggered.append("stress_hotspot_near_connection")
            hotspot, ctype, role, geom_status = enforceable_stress_hotspots[0]
            status_reasoning.append("A stress hotspot remains associated with a critical assembly interface/connection and the current candidate lacks fully trusted preservation evidence.")
            add_rec(
                rec_type="rerun_topopt",
                severity="warning" if str(hotspot.get("confidence") or "low").lower() in _ENFORCEABLE_CONFIDENCE else "critical",
                reason="Re-run assembly-aware topopt with stronger preserve guidance around the hotspot-linked connection/interface.",
                source_artifacts=[ASSEMBLY_RESULT_MAP_PATH, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH, ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH],
                confidence=str(hotspot.get("confidence") or "low").lower(),
                source_part_id=str(hotspot.get("part_id") or selected_part_id),
                source_interface_id=hotspot.get("interface_id"),
                source_connection_id=hotspot.get("connection_id"),
                source_ir_node=hotspot.get("source_ir_node"),
                load_case_id=hotspot.get("load_case_id"),
                result_type=hotspot.get("result_type"),
                suggested_parameters={
                    "use_result_guidance": True,
                    "preserve_min_density": 0.98,
                    "guidance_radius_cells": 2,
                    "focus_connection_type": ctype or role or geom_status,
                },
            )

        enforceable_deflection_hotspots = [
            item for item in deflection_hotspots if str(item[0].get("confidence") or "low").lower() in _ENFORCEABLE_CONFIDENCE
        ]
        should_increase_stiffness = bool(enforceable_deflection_hotspots) and (not guidance_consumed or verification_status == "warning")
        if should_increase_stiffness:
            rules_triggered.append("deflection_hotspot")
            hotspot, ctype, role, _geom_status = enforceable_deflection_hotspots[0]
            status_reasoning.append("A deflection hotspot remains on the selected part without fully trusted stiffness guidance consumption.")
            params = {
                "use_result_guidance": True,
                "stiffness_weight_multiplier": 1.25,
            }
            if ctype in {"bolted_proxy", "rigid_tie", "welded_proxy"} or role in {"mounting_face", "bolt_hole"}:
                params["preserve_min_density"] = 0.97
            add_rec(
                rec_type="increase_stiffness_weight",
                severity="warning",
                reason="Increase stiffness weighting around the deflection-sensitive region before re-running the selected-part optimization.",
                source_artifacts=[ASSEMBLY_RESULT_MAP_PATH, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH],
                confidence=str(hotspot.get("confidence") or "low").lower(),
                source_part_id=str(hotspot.get("part_id") or selected_part_id),
                source_interface_id=hotspot.get("interface_id"),
                source_connection_id=hotspot.get("connection_id"),
                source_ir_node=hotspot.get("source_ir_node"),
                load_case_id=hotspot.get("load_case_id"),
                result_type=hotspot.get("result_type"),
                suggested_parameters=params,
            )

        low_confidence_issue = low_confidence_mapping_count > 0 or (unmapped_region_count > 1) or (unmapped_region_count > 0 and high_confidence_results == 0)
        if low_confidence_issue:
            rules_triggered.append("low_confidence_or_unmapped_mapping")
            status_reasoning.append("Assembly result mappings are low-confidence or too incomplete for an automatic next-step decision.")
            add_rec(
                rec_type="request_user_input",
                severity="warning",
                reason="Result mapping confidence is too low or too incomplete to recommend an automatic rerun/export path confidently.",
                source_artifacts=[ASSEMBLY_RESULT_MAP_PATH, INTERFACE_RESOLUTION_PATH, ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH],
                confidence="low",
                source_part_id=selected_part_id,
                suggested_parameters={
                    "review_mapping_confidence": True,
                    "review_unmapped_regions": unmapped_region_count,
                    "review_low_confidence_regions": low_confidence_mapping_count,
                },
            )

        candidate_ok = bool(
            verification_status in {"passed", "warning"}
            and bool(selected_part_block.get("optimized_artifact_found"))
            and bool(selected_part_block.get("optimization_result_found"))
            and not frozen_parts_modified
            and not unexpected_modified_parts
            and preserve_regions_unmapped == 0
            and bool(provenance_block.get("proxy_limitations_preserved"))
        )
        if candidate_ok:
            rules_triggered.append("accept_candidate")
            status_reasoning.append("The selected part was optimized and verified without non-selected part modifications or preserve-region failures.")
            add_rec(
                rec_type="accept_candidate",
                severity="info",
                reason="The current optimized assembly candidate is acceptable for downstream review under the proxy-model limitations.",
                source_artifacts=[ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH, ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH, ASSEMBLY_OPTIMIZATION_SUMMARY_PATH],
                confidence="high" if verification_status == "passed" else "medium",
                source_part_id=selected_part_id,
                source_ir_node=(selected_part or {}).get("source_ir_node"),
            )
            if isinstance(achieved_volume_fraction, (int, float)) and achieved_volume_fraction < 0.98:
                rules_triggered.append("proceed_to_dimension_optimization")
                add_rec(
                    rec_type="proceed_to_dimension_optimization",
                    severity="info",
                    reason="The candidate appears stable enough for a later dimension-optimization pass; do not treat this as an automatic next step.",
                    source_artifacts=[ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH, ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH],
                    confidence="medium",
                    source_part_id=selected_part_id,
                    source_ir_node=(selected_part or {}).get("source_ir_node"),
                    suggested_parameters={
                        "achieved_volume_fraction": achieved_volume_fraction,
                        "target_volume_fraction": target_volume_fraction,
                    },
                )
            if representation in {"manifold_mesh", "smooth_mesh"}:
                rules_triggered.append("proceed_to_mesh_to_cad_reconstruction")
                add_rec(
                    rec_type="proceed_to_mesh_to_cad_reconstruction",
                    severity="info",
                    reason="The optimized part is a mesh-like derived artifact and can be evaluated by the existing mesh-to-CAD reconstruction ladder, with partial/failure outcomes still possible.",
                    source_artifacts=[selected_shape_path or PART_OPTIMIZED_SHAPE_IR_TEMPLATE.format(part_id=selected_part_id), ASSEMBLY_POST_OPTIMIZATION_VERIFICATION_PATH],
                    confidence="medium",
                    source_part_id=selected_part_id,
                    source_ir_node=(selected_part or {}).get("source_ir_node"),
                )

    recommendation_status = _report_status(
        missing_inputs=missing_inputs,
        recommendations=recommendations,
        verification_status=verification_status,
    )
    severity_count = _severity_counts(recommendations)
    report = {
        "format": "aieng.assembly.postprocess_report.v0",
        "format_version": FORMAT_VERSION,
        "generated_at_utc": _now(),
        "status": recommendation_status,
        "selected_part_id": selected_part_id,
        "input_artifacts_present": present_inputs,
        "input_artifacts_missing": missing_inputs,
        "rules_evaluated": rules_evaluated,
        "rules_triggered": rules_triggered,
        "recommendation_count": len(recommendations),
        "severity_count": severity_count,
        "proxy_connection_count": proxy_connection_count,
        "unresolved_interface_count": unresolved_interface_count,
        "low_confidence_mapping_count": low_confidence_mapping_count,
        "unmapped_region_count": unmapped_region_count,
        "status_reasoning": status_reasoning or ["No recommendation rules triggered."],
        "inconsistencies": inconsistencies,
        "limitations": list(_POSTPROCESS_LIMITATIONS),
    }
    recommendations_doc = {
        "format": "aieng.assembly.design_recommendations.v0",
        "format_version": FORMAT_VERSION,
        "generated_at_utc": report["generated_at_utc"],
        "status": recommendation_status,
        "candidate_id": f"assembly_topopt:{selected_part_id or 'unknown'}",
        "selected_part_id": selected_part_id,
        "recommendations": recommendations,
        "summary": {
            "recommendation_count": len(recommendations),
            "severity_count": severity_count,
            "proxy_connection_count": proxy_connection_count,
            "unresolved_interface_count": unresolved_interface_count,
            "low_confidence_mapping_count": low_confidence_mapping_count,
            "unmapped_region_count": unmapped_region_count,
            "verification_status": verification_status,
            "optimized_artifact_found": bool(selected_part_block.get("optimized_artifact_found")),
            "optimization_result_found": bool(selected_part_block.get("optimization_result_found")),
            "optimization_summary_status": optimization_summary.get("status") if isinstance(optimization_summary, dict) else None,
        },
        "limitations": list(_POSTPROCESS_LIMITATIONS),
        "source_artifacts": _dedupe(present_inputs + missing_inputs),
    }
    next_actions = {
        "format": "aieng.assembly.next_actions.v0",
        "format_version": FORMAT_VERSION,
        "generated_at_utc": report["generated_at_utc"],
        "status": recommendation_status,
        "selected_part_id": selected_part_id,
        "actions": _next_actions(recommendations),
        "limitations": list(_POSTPROCESS_LIMITATIONS),
    }
    members = {
        ASSEMBLY_DESIGN_RECOMMENDATIONS_PATH: _dumps(recommendations_doc),
        ASSEMBLY_POSTPROCESS_REPORT_PATH: _dumps(report),
        CONVERSION_MANIFEST_PATH: _dumps(
            _update_design_recommendation_manifest(
                manifest,
                recommendations_doc,
                report,
                next_actions_written=emit_next_actions,
            )
        ),
    }
    if emit_next_actions:
        members[ASSEMBLY_NEXT_ACTIONS_PATH] = _dumps(next_actions)
    _replace_members(package_path, members)
    return {
        "assembly_present": True,
        "status": recommendation_status,
        "selected_part_id": selected_part_id,
        "recommendations": recommendations_doc,
        "report": report,
        "next_actions": next_actions if emit_next_actions else None,
        "artifacts": sorted(members.keys()),
    }


def derive_multipart_topopt_problem(
    *,
    assembly: dict[str, Any],
    part_registry: dict[str, Any],
    interface_resolution: dict[str, Any],
    connection_geometry: dict[str, Any],
    topology_by_part: dict[str, dict[str, dict[str, Any]]],
    selected_part_ids: list[str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive an ADVISORY multi-part topology/size optimization *problem* (#203).

    Coordinates design spaces across **explicitly selected** design parts while
    preserving frozen / reference / fastener / load-source / fixture parts.
    Advisory only: it derives and records a reviewable problem artifact — it never
    runs an optimizer, accepts a candidate, or overwrites baseline geometry.

    Refuses honestly (status ``needs_user_input``) when: no parts are selected; a
    selected part is unknown / non-optimizable / both design-and-frozen (ambiguous
    ownership); or two selected design parts are coupled by a connection whose
    interface constraints are missing/unresolved.
    """
    assembly = assembly if isinstance(assembly, dict) else {}
    part_registry = part_registry if isinstance(part_registry, dict) else {}
    interface_resolution = interface_resolution if isinstance(interface_resolution, dict) else {}
    connection_geometry = connection_geometry if isinstance(connection_geometry, dict) else {}
    topology_by_part = topology_by_part or {}

    parts_by_id = {str(p.get("id")): p for p in _as_list(assembly.get("parts")) if isinstance(p, dict) and p.get("id")}
    reg_by_id = {str(p.get("part_id")): p for p in _as_list(part_registry.get("parts")) if isinstance(p, dict)}
    intent = assembly.get("analysis_intent") if isinstance(assembly.get("analysis_intent"), dict) else {}
    frozen = {str(x) for x in _as_list(intent.get("frozen_parts"))}
    allowed = {str(x) for x in _as_list(intent.get("allowed_optimization_parts"))}
    resolved = interface_resolution.get("interfaces") if isinstance(interface_resolution.get("interfaces"), dict) else {}
    interfaces = _collect_interfaces(assembly)

    refusals: list[dict[str, Any]] = []
    warnings: list[str] = []

    def _role(pid: str) -> str | None:
        part = parts_by_id.get(pid) or {}
        return part.get("role") or (reg_by_id.get(pid) or {}).get("role")

    def _editable(pid: str) -> bool:
        part = parts_by_id.get(pid) or {}
        reg = reg_by_id.get(pid) or {}
        return bool(reg.get("editable", part.get("editable", _role(pid) == "design_part")))

    selected = [str(x) for x in _as_list(selected_part_ids)]
    deduped: list[str] = []
    for pid in selected:
        if pid in deduped:
            warnings.append(f"duplicate_selected_part:{pid}")
        else:
            deduped.append(pid)

    design_part_ids: list[str] = []
    for pid in deduped:
        if pid not in parts_by_id:
            refusals.append({"part_id": pid, "reason": "unknown_selected_part"})
            continue
        role = _role(pid)
        if pid in frozen:
            refusals.append({"part_id": pid, "reason": "ambiguous_ownership_design_and_frozen", "role": role})
            continue
        if role in _NON_OPTIMIZABLE_ROLES:
            refusals.append({"part_id": pid, "reason": f"non_optimizable_role:{role}", "role": role})
            continue
        if role not in _OPTIMIZABLE_ROLES:
            refusals.append({"part_id": pid, "reason": "not_a_design_part", "role": role})
            continue
        if not _editable(pid):
            refusals.append({"part_id": pid, "reason": "not_editable", "role": role})
            continue
        if allowed and pid not in allowed:
            refusals.append({"part_id": pid, "reason": "not_in_allowed_optimization_parts", "role": role})
            continue
        design_part_ids.append(pid)

    design_set = set(design_part_ids)

    # ── couplings + missing-interface-constraint detection ───────────────────
    geo_by_id = {g.get("connection_id"): g for g in _as_list(connection_geometry.get("connections")) if isinstance(g, dict)}
    couplings: list[dict[str, Any]] = []
    missing_constraints: list[str] = []
    for idx, conn in enumerate(_as_list(assembly.get("connections"))):
        if not isinstance(conn, dict):
            continue
        pa, pb = str(conn.get("part_a")), str(conn.get("part_b"))
        if pa not in design_set and pb not in design_set:
            continue  # connection unrelated to any selected design part
        cid = str(conn.get("id") or f"connection_{idx:03d}")
        ia, ib = conn.get("interface_a"), conn.get("interface_b")
        kind = "design_design" if (pa in design_set and pb in design_set) else "design_nondesign"

        def _iface_ok(iid: Any) -> bool:
            if not iid:
                return False
            rec = resolved.get(str(iid)) if isinstance(resolved, dict) else None
            return isinstance(rec, dict) and rec.get("resolution_status") != "unresolved" and bool(
                (rec.get("world") or {}).get("bbox")
            )

        a_ok, b_ok = _iface_ok(ia), _iface_ok(ib)
        if kind == "design_design" and not (a_ok and b_ok):
            missing_constraints.append(cid)
        elif kind == "design_nondesign":
            design_side_iface = ia if pa in design_set else ib
            if not _iface_ok(design_side_iface):
                warnings.append(f"unresolved_interface_warning:{cid}")
        couplings.append({
            "connection_id": cid,
            "type": conn.get("type"),
            "kind": kind,
            "part_a": pa, "part_b": pb,
            "interface_a": ia, "interface_b": ib,
            "interface_a_resolved": a_ok,
            "interface_b_resolved": b_ok,
            "geometry_status": (geo_by_id.get(cid) or {}).get("geometry_status"),
        })
    for cid in missing_constraints:
        refusals.append({"connection_id": cid, "reason": "missing_interface_constraint"})

    # ── design spaces + advisory variables for valid design parts ────────────
    design_parts: list[dict[str, Any]] = []
    for pid in design_part_ids:
        bbox = _part_bbox_from_topology(topology_by_part.get(pid) or {})
        if bbox is None:
            refusals.append({"part_id": pid, "reason": "no_topology_for_design_space"})
            continue
        preserve = [
            {
                "connection_id": c["connection_id"],
                "interface_id": c["interface_a"] if c["part_a"] == pid else c["interface_b"],
                "semantic_role": (interfaces.get(str(c["interface_a"] if c["part_a"] == pid else c["interface_b"])) or {}).get("semantic_role"),
                "preserve_min_density": 0.95,
                "reason": "shared interface region preserved across coupling",
            }
            for c in couplings if pid in (c["part_a"], c["part_b"])
        ]
        design_parts.append({
            "part_id": pid,
            "role": _role(pid),
            "design_space": {"bbox": bbox, "diag": round(_bbox_diag(bbox), 6)},
            "variables": [
                {"id": f"{pid}_topology", "type": "topology_density", "design_space_bbox": bbox,
                 "volume_fraction_init": 0.5, "bounds": [0.0, 1.0], "safe_to_modify": True},
                {"id": f"{pid}_size_scale", "type": "sizing", "semantic_role": "uniform_scale",
                 "value_init": 1.0, "bounds": [0.5, 1.5], "safe_to_modify": True,
                 "note": "advisory sizing variable; not executed"},
            ],
            "preserve_regions": preserve,
        })

    # ── non-design (preserved) parts ─────────────────────────────────────────
    non_design_parts = []
    for pid, part in parts_by_id.items():
        if pid in design_set and any(dp["part_id"] == pid for dp in design_parts):
            continue
        role = _role(pid)
        reason = (
            "frozen" if pid in frozen
            else f"non_optimizable_role:{role}" if role in _NON_OPTIMIZABLE_ROLES
            else "not_selected"
        )
        non_design_parts.append({"part_id": pid, "role": role, "reason": reason, "preserved": True})

    has_valid = len(design_parts) > 0
    status = "ready" if (has_valid and not refusals and not missing_constraints) else "needs_user_input"
    if not selected:
        status = "needs_user_input"
        refusals.append({"reason": "no_parts_selected"})
    elif not has_valid and not refusals:
        refusals.append({"reason": "no_valid_design_part_with_design_space"})
        status = "needs_user_input"

    problem = {
        "format": "aieng.assembly_multipart_topopt_problem",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "schema_version": "0.1",
        "generated_at_utc": _now(),
        "status": status,
        "selected_part_ids": deduped,
        "design_parts": design_parts,
        "non_design_parts": non_design_parts,
        "couplings": couplings,
        "objective": {
            "recorded": True,
            "executed": False,
            "sense": "minimize",
            "metric": "total_compliance",
            "subject_to": ["per_part_volume_fraction", "preserve_interface_regions"],
            "note": "objective is recorded for review; no optimizer is run in v0",
        },
        "refusals": refusals,
        "warnings": sorted(set(warnings)),
        "honesty": {
            "advisory_only": True,
            "optimizer_executed": False,
            "baseline_modified": False,
            "auto_accepted": False,
            "production_grade_simultaneous_optimization": False,
            "note": "Advisory multi-part problem derivation only — no optimizer execution, "
                    "no auto-acceptance, no baseline promotion. Frozen/reference/fastener parts "
                    "are preserved and marked non-design.",
        },
    }
    derivation = {
        "format": "aieng.assembly_multipart_topopt_derivation",
        "format_version": FORMAT_VERSION,
        "schema_version": "0.1",
        "status": status,
        "summary": {
            "selected": len(deduped),
            "design_parts": len(design_parts),
            "non_design_parts": len(non_design_parts),
            "couplings": len(couplings),
            "refusals": len(refusals),
            "missing_interface_constraints": len(missing_constraints),
        },
        "refusals": refusals,
        "warnings": sorted(set(warnings)),
    }
    return problem, derivation


def write_multipart_topopt_problem(
    package_path: str | Path,
    *,
    selected_part_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read package assembly artifacts and write the advisory multi-part problem.

    Advisory only — writes ``analysis/assembly_multipart_topopt_problem.json`` +
    ``diagnostics/assembly_multipart_topopt_derivation.json``; runs no optimizer
    and never modifies baseline geometry.
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"assembly_present": False, "reason": "package not found"}
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            if ASSEMBLY_IR_PATH not in names:
                return {"assembly_present": False}
            assembly = _read_json(zf, names, ASSEMBLY_IR_PATH) or {}
            part_registry = _read_json(zf, names, PART_REGISTRY_PATH) or {}
            interface_resolution = _read_json(zf, names, INTERFACE_RESOLUTION_PATH) or {}
            connection_geometry = _read_json(zf, names, ASSEMBLY_CONNECTION_GEOMETRY_PATH) or {}
            topology_by_part = _build_topology_by_part(zf, names, assembly)
    except Exception as exc:  # noqa: BLE001
        return {"assembly_present": False, "error": f"{type(exc).__name__}: {exc}"}

    problem, derivation = derive_multipart_topopt_problem(
        assembly=assembly,
        part_registry=part_registry,
        interface_resolution=interface_resolution,
        connection_geometry=connection_geometry,
        topology_by_part=topology_by_part,
        selected_part_ids=selected_part_ids,
    )
    _replace_members(package_path, {
        ASSEMBLY_MULTIPART_TOPOPT_PROBLEM_PATH: _dumps(problem),
        ASSEMBLY_MULTIPART_TOPOPT_DERIVATION_PATH: _dumps(derivation),
    })
    return {
        "assembly_present": True,
        "status": problem["status"],
        "design_parts": [dp["part_id"] for dp in problem["design_parts"]],
        "refusals": problem["refusals"],
        "baseline_modified": False,
        "artifacts": [ASSEMBLY_MULTIPART_TOPOPT_PROBLEM_PATH, ASSEMBLY_MULTIPART_TOPOPT_DERIVATION_PATH],
    }


def write_assembly_topopt_problem(
    package_path: str | Path,
    *,
    selected_part_id: str | None = None,
    dimension: str = "2d",
    resolution: int = 48,
    resolution_3d: int = 16,
    volfrac: float = 0.5,
    penalty: float = 3.0,
    rmin: float = 1.5,
    max_iters: int = DEFAULT_MAX_ITERS_2D,
    use_result_guidance: bool = False,
    emit_standard: bool = True,
) -> dict[str, Any]:
    """Read package assembly artifacts, derive/write assembly topopt setup."""
    package_path = Path(package_path)
    if not package_path.exists():
        return {"assembly_present": False, "reason": "package not found"}
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            if ASSEMBLY_IR_PATH not in names:
                return {"assembly_present": False}
            assembly = _read_json(zf, names, ASSEMBLY_IR_PATH) or {}
            part_registry = _read_json(zf, names, PART_REGISTRY_PATH) or {}
            connection_graph = _read_json(zf, names, CONNECTION_GRAPH_PATH) or {}
            interface_resolution = _read_json(zf, names, INTERFACE_RESOLUTION_PATH) or {}
            connection_geometry = _read_json(zf, names, ASSEMBLY_CONNECTION_GEOMETRY_PATH) or {}
            assembly_cae_model = _read_json(zf, names, ASSEMBLY_CAE_MODEL_PATH) or {}
            assembly_result_map = _read_json(zf, names, ASSEMBLY_RESULT_MAP_PATH) or {}
            manifest = _read_json(zf, names, CONVERSION_MANIFEST_PATH) or {"format": "aieng.conversion_manifest"}
            topology_by_part = _build_topology_by_part(zf, names, assembly)
    except Exception as exc:  # noqa: BLE001
        return {"assembly_present": False, "error": f"{type(exc).__name__}: {exc}"}

    problem, diag, standard = derive_assembly_topopt_problem(
        assembly=assembly,
        part_registry=part_registry,
        connection_graph=connection_graph,
        interface_resolution=interface_resolution,
        connection_geometry=connection_geometry,
        assembly_cae_model=assembly_cae_model,
        assembly_result_map=assembly_result_map,
        topology_by_part=topology_by_part,
        selected_part_id=selected_part_id,
        dimension=dimension,
        resolution=resolution,
        resolution_3d=resolution_3d,
        volfrac=volfrac,
        penalty=penalty,
        rmin=rmin,
        max_iters=max_iters,
        use_result_guidance=use_result_guidance,
    )
    members = {
        ASSEMBLY_TOPOPT_PROBLEM_PATH: _dumps(problem),
        ASSEMBLY_TOPOPT_DERIVATION_PATH: _dumps(diag),
        CONVERSION_MANIFEST_PATH: _dumps(_update_manifest(manifest, problem, standard if emit_standard else None)),
    }
    if standard is not None and emit_standard:
        members[STANDARD_TOPOPT_PROBLEM_PATH] = _dumps(standard)
    _replace_members(package_path, members)
    return {
        "assembly_present": True,
        "status": problem.get("status"),
        "selected_part_id": problem.get("selected_part_id"),
        "standard_problem_emitted": bool(standard is not None and emit_standard),
        "diagnostics": problem.get("diagnostics") or [],
        "warnings": problem.get("warnings") or [],
        "artifacts": sorted(members.keys()),
        "problem": problem,
        "standard_problem": standard if emit_standard else None,
    }


def run_assembly_topology_optimization(
    package_path: str | Path,
    *,
    optimizer: str | None = None,
    writeback: bool = True,
    method: str | None = None,
    representation: str | None = None,
    boundary: str = "spline",
) -> dict[str, Any]:
    """Explicitly run assembly-aware topology optimization for one selected part.

    This is intentionally not called by assembly validation/CAE processing.  It
    consumes the already-derived assembly and standard topopt problem artifacts,
    calls the existing topology optimizer unchanged, and writes only assembly
    diagnostics plus selected-part derived artifacts.  Package-level geometry is
    not overwritten.
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"assembly_present": False, "reason": "package not found"}
    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            if ASSEMBLY_IR_PATH not in names:
                return {"assembly_present": False}
            assembly_problem = _read_json(zf, names, ASSEMBLY_TOPOPT_PROBLEM_PATH)
            standard_problem = _read_json(zf, names, STANDARD_TOPOPT_PROBLEM_PATH)
            part_registry = _read_json(zf, names, PART_REGISTRY_PATH) or {}
            manifest = _read_json(zf, names, CONVERSION_MANIFEST_PATH) or {"format": "aieng.conversion_manifest"}
    except Exception as exc:  # noqa: BLE001
        return {"assembly_present": False, "error": f"{type(exc).__name__}: {exc}"}

    diagnostics: list[str] = []
    warnings: list[str] = []
    selected_part_id = None
    if not isinstance(assembly_problem, dict):
        execution, diag = _needs_input_execution(
            reason="missing_assembly_topopt_problem",
            diagnostics=["missing_assembly_topopt_problem"],
        )
        members = {
            ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH: _dumps(execution),
            ASSEMBLY_TOPOPT_EXECUTION_PATH: _dumps(diag),
            CONVERSION_MANIFEST_PATH: _dumps(_update_execution_manifest(manifest, execution)),
        }
        _replace_members(package_path, members)
        verification_result = verify_assembly_post_optimization(package_path)
        recommendation_result = write_assembly_design_recommendations(package_path)
        return {
            "assembly_present": True,
            **execution,
            "verification_status": verification_result.get("status"),
            "post_optimization_verification": verification_result.get("verification"),
            "recommendation_status": recommendation_result.get("status"),
            "design_recommendations": recommendation_result.get("recommendations"),
            "artifacts": sorted(
                set(members.keys())
                | set(_as_list(verification_result.get("artifacts")))
                | set(_as_list(recommendation_result.get("artifacts")))
            ),
        }

    selected_part_id = assembly_problem.get("selected_part_id")
    if not selected_part_id:
        diagnostics.append("missing_selected_part")
    candidates = [c for c in _as_list(assembly_problem.get("candidate_parts")) if isinstance(c, dict)]
    if len([c for c in candidates if str(c.get("part_id")) == str(selected_part_id)]) != 1:
        diagnostics.append("selected_part_not_unique")
    ok_part, reason = _is_part_optimizable(str(selected_part_id), assembly_problem, part_registry) if selected_part_id else (False, "missing_selected_part")
    if not ok_part and reason:
        diagnostics.append(reason)
    if assembly_problem.get("status") != "ready":
        diagnostics.append("assembly_topopt_problem_not_ready")
    if not isinstance(standard_problem, dict):
        diagnostics.append("missing_topology_optimization_problem")
    elif not (standard_problem.get("bcs") or {}).get("supports") or not (standard_problem.get("bcs") or {}).get("loads"):
        diagnostics.append("topology_optimization_problem_not_runnable")
    target_part = assembly_problem.get("target_part") if isinstance(assembly_problem.get("target_part"), dict) else {}
    if standard_problem and str((standard_problem.get("derivation") or {}).get("selected_part_id") or selected_part_id) != str(selected_part_id):
        diagnostics.append("standard_problem_selected_part_mismatch")

    if diagnostics:
        execution, diag = _needs_input_execution(
            reason=";".join(diagnostics),
            diagnostics=diagnostics,
            warnings=warnings,
            selected_part_id=selected_part_id,
        )
        members = {
            ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH: _dumps(execution),
            ASSEMBLY_TOPOPT_EXECUTION_PATH: _dumps(diag),
            CONVERSION_MANIFEST_PATH: _dumps(_update_execution_manifest(manifest, execution)),
        }
        _replace_members(package_path, members)
        verification_result = verify_assembly_post_optimization(package_path)
        recommendation_result = write_assembly_design_recommendations(package_path)
        return {
            "assembly_present": True,
            **execution,
            "verification_status": verification_result.get("status"),
            "post_optimization_verification": verification_result.get("verification"),
            "recommendation_status": recommendation_result.get("status"),
            "design_recommendations": recommendation_result.get("recommendations"),
            "artifacts": sorted(
                set(members.keys())
                | set(_as_list(verification_result.get("artifacts")))
                | set(_as_list(recommendation_result.get("artifacts")))
            ),
        }

    assert isinstance(standard_problem, dict)
    assert selected_part_id is not None
    guidance_field, preserve_diag, preserve_warnings = _assembly_preserve_guidance_field(standard_problem, assembly_problem)
    warnings.extend(preserve_warnings)
    problem_for_run = {
        **standard_problem,
        "guidance_field": guidance_field,
        "use_result_guidance": True,
        "assembly_preserve_constraints": {
            "selected_part_id": selected_part_id,
            "preserve_regions": assembly_problem.get("preserve_regions") or [],
            "diagnostics": preserve_diag,
        },
    }
    dim = "3d" if "nx" in (standard_problem.get("grid") or {}) else "2d"
    opt_name = optimizer or ("simp_3d" if dim == "3d" else "simp_2d")
    topo_result = run_topology_optimization(problem_for_run, optimizer=opt_name)
    topo_result.setdefault("provenance", {})
    topo_result["provenance"].update({
        "assembly_aware_topopt": True,
        "selected_part_id": selected_part_id,
        "assembly_problem_path": ASSEMBLY_TOPOPT_PROBLEM_PATH,
        "standard_problem_path": STANDARD_TOPOPT_PROBLEM_PATH,
        "interface_preserve_constraints_applied": True,
    })
    topo_result.setdefault("problem", {})
    topo_result["problem"]["assembly_preserve_constraints"] = preserve_diag

    writeback_block: dict[str, Any] = {"status": "not_requested" if not writeback else "not_attempted"}
    part_result_path = PART_TOPOLOGY_OPTIMIZATION_TEMPLATE.format(part_id=selected_part_id)
    part_shape_path: str | None = None
    if writeback:
        if not (target_part.get("geometry_ref") or target_part.get("source_ir_node") or target_part.get("design_space_node")):
            diagnostics.append("safe_writeback_target_missing")
            writeback_block = {
                "status": "needs_user_input",
                "reason": "safe_writeback_target_missing",
                "part_geometry_modified": False,
                "derived_optimized_part_created": False,
            }
        else:
            method_chosen = method or ("smooth_mesh" if dim == "3d" else "contour")
            representation_chosen = representation or ("manifold_mesh" if dim == "3d" else "brep_build123d")
            shape_payload = _shape_writeback_payload(
                topo_result,
                selected_part_id=str(selected_part_id),
                target_part=target_part,
                method=method_chosen,
                representation=representation_chosen,
                boundary=boundary,
            )
            part_shape_path = PART_OPTIMIZED_SHAPE_IR_TEMPLATE.format(part_id=selected_part_id)
            members[part_shape_path] = _dumps(shape_payload)
            writeback_block = {
                "status": "derived_part_artifact_written",
                "selected_part_id": selected_part_id,
                "source_ir_node": target_part.get("source_ir_node"),
                "design_space_node": target_part.get("design_space_node"),
                "source_geometry_ref": target_part.get("geometry_ref"),
                "writeback_method": method_chosen,
                "representation": representation_chosen,
                "part_geometry_modified": False,
                "derived_optimized_part_created": True,
                "part_shape_ir_path": part_shape_path,
                "reference_parts_modified": False,
            }

    status = "needs_user_input" if diagnostics else ("optimized_writeback_ready" if not writeback else writeback_block["status"])
    execution = {
        "format": "aieng.assembly_topology_optimization",
        "format_version": FORMAT_VERSION,
        "contract_version": "0.1",
        "generated_at_utc": _now(),
        "status": status,
        "selected_part_id": selected_part_id,
        "target_part": target_part,
        "optimizer": topo_result.get("optimizer"),
        "dimension": topo_result.get("dimension") or dim,
        "bcs_source": (topo_result.get("problem") or {}).get("bcs_source"),
        "use_result_guidance": True,
        "preserve_constraints": preserve_diag,
        "assembly_result_guidance": standard_problem.get("result_guidance"),
        "topology_optimization": topo_result,
        "writeback": writeback_block,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "limitations": list(_LIMITATIONS),
        "provenance": {
            "created_by": "aieng.assembly_topopt_execution_v0",
            "explicit_execution_required": True,
            "optimizer_executed": True,
            "source_artifacts": [
                ASSEMBLY_TOPOPT_PROBLEM_PATH,
                STANDARD_TOPOPT_PROBLEM_PATH,
                ASSEMBLY_IR_PATH,
                PART_REGISTRY_PATH,
                CONNECTION_GRAPH_PATH,
                INTERFACE_RESOLUTION_PATH,
                ASSEMBLY_RESULT_MAP_PATH,
            ],
            "assembly_connections_are_proxy_derived": True,
            "contact_physics_modeled": False,
            "bolt_preload_modeled": False,
            "multi_part_simultaneous_optimization": False,
            "production_ready": False,
        },
    }
    members[ASSEMBLY_TOPOLOGY_OPTIMIZATION_PATH] = _dumps(execution)
    members[ASSEMBLY_TOPOPT_EXECUTION_PATH] = _dumps(_execution_diag(execution))
    members[part_result_path] = _dumps(topo_result)
    members[CONVERSION_MANIFEST_PATH] = _dumps(
        _update_execution_manifest(
            manifest,
            execution,
            part_result_path=part_result_path,
            part_shape_path=part_shape_path,
        )
    )
    _replace_members(package_path, members)
    verification_result = verify_assembly_post_optimization(package_path)
    recommendation_result = write_assembly_design_recommendations(package_path)
    return {
        "assembly_present": True,
        "status": execution.get("status"),
        "selected_part_id": selected_part_id,
        "optimizer": opt_name,
        "dimension": execution.get("dimension"),
        "bcs_source": execution.get("bcs_source"),
        "writeback": writeback_block,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "preserve_constraints": preserve_diag,
        "verification_status": verification_result.get("status"),
        "post_optimization_verification": verification_result.get("verification"),
        "recommendation_status": recommendation_result.get("status"),
        "design_recommendations": recommendation_result.get("recommendations"),
        "artifacts": sorted(
            set(members.keys())
            | set(_as_list(verification_result.get("artifacts")))
            | set(_as_list(recommendation_result.get("artifacts")))
        ),
        "execution": execution,
    }
