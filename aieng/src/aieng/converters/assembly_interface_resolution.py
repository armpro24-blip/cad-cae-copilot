"""Assembly interface resolution + geometric connection validation (v0.1).

Resolves Assembly IR interface ``topology_refs`` against per-part / package topology maps,
transforms the resolved geometry from part-local into assembly/world coordinates, and validates
whether each simplified connection is GEOMETRICALLY PLAUSIBLE (centroid distance, bbox overlap,
normal alignment, semantic-role fit).

This is GEOMETRY VALIDATION ONLY. It does NOT model contact, bolt preload, or run a solver.
Connections remain proxies. Unresolved topology refs are reported honestly, never invented.

Outputs:
  - assembly/interface_resolution.json
  - diagnostics/assembly_connection_geometry.json
  - updated assembly/connection_graph.json (per-edge geometry_status fields)
  - updated simulation/assembly_cae_setup_draft.json (geometry_status; invalid -> needs_user_input)
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION
from aieng.converters.assembly_ir import (
    ASSEMBLY_CAE_DRAFT_PATH,
    ASSEMBLY_IR_PATH,
    CONNECTION_GRAPH_PATH,
    PROXY_CONNECTION_TYPES,
    _as_list,
    _collect_interfaces,
    build_assembly_cae_setup_draft,
    build_connection_graph,
)

INTERFACE_RESOLUTION_PATH = "assembly/interface_resolution.json"
ASSEMBLY_CONNECTION_GEOMETRY_PATH = "diagnostics/assembly_connection_geometry.json"
ASSEMBLY_MESH_INTERFACE_DIAGNOSTICS_PATH = "diagnostics/assembly_mesh_interface_diagnostics.json"
PACKAGE_TOPOLOGY_PATH = "geometry/topology_map.json"
FEATURE_GRAPH_PATH = "graph/feature_graph.json"

# Pre-solver interface-NSET quality thresholds (deterministic, conservative).
# An interface that maps onto zero faces yields an EMPTY node set; one mapping
# onto a single tiny face yields a SPARSE/undersampled one — both produce
# misleading assembly solver results, so they are surfaced before any run.
# `sparse` and `over_broad` are judged by AREA against the part's largest
# cross-section, not by face count or bbox diagonal. Measured on the dogfood
# gearbox, the earlier proxies fired on 4 of 4 correctly-authored interfaces
# (`ok: 0`), because both are true by construction rather than by defect:
#   - one planar face is exactly how `cad.define_interface` is meant to be used,
#     so `face_count <= 1` flags the normal case;
#   - a ring-shaped housing rim has the part's own footprint diagonal while
#     covering 22% of it, so a diagonal ratio calls it over-broad.
# A diagnostic that fires on every correct input teaches the engineer to ignore
# it — which is how the genuinely blocking `empty_interface` gets missed.
# Both are measured as `coverage_fraction` = interface area / the part's bbox
# SURFACE area — "how much of the part's boundary does this selection tie down".
# One quantity, no shape assumptions: it needs neither a curvature signal nor a
# per-axis span test, so a flat plate, a ring, a long shaft and a thin disc are
# all judged by the same number. A single planar face can never exceed ~0.5 of a
# bbox's surface, so 0.4 means "nearly the largest single-face selection there
# is"; on a cube one face is 0.167 and correctly stays clean.
_SPARSE_MAX_FACES = 1          # a single face is only sparse if it is also small
_SPARSE_SURFACE_FRAC = 0.01    # iface-area / part-bbox-surface < this -> sliver
_DISCONNECT_GAP_FRAC = 0.05    # member-face gap > this*iface-diag -> separate region
_OVER_BROAD_SURFACE_FRAC = 0.4  # iface-area / part-bbox-surface > this -> spans the part

# proximity thresholds expressed as a fraction of the larger interface bbox diagonal
_TOUCH_FRAC = 0.02     # gap <= this*scale -> touching/overlapping
_NEAR_FRAC = 0.5       # gap >  this*scale -> far apart
_NORMAL_SAME_DOT = 0.5  # mating faces expected anti-parallel; dot > this is suspicious

# Connection types whose whole meaning is "these two surfaces are JOINED". A
# relative-scale threshold answers "are these plausibly near each other", which
# is the wrong question for a joint: you cannot tie, weld or bolt across a gap
# at any scale. Measured on the dogfood gearbox — a `bonded` tie between a cover
# and a shaft 20 mm apart scored `warning` (20 mm is small next to the 162 mm
# interface diagonal), stayed `supported: true` with `load_transfer: true`, and
# was carried into assembly_cae_model.json as a tie constraint welding nodes
# across empty space: stiffer than reality, in the non-conservative direction,
# with `needs_user_input: []`.
# `spring_proxy` is deliberately excluded — a spring connecting distant parts is
# exactly what it is for — and `contact_proxy` already never claims better than
# `warning`.
_JOIN_CONNECTION_TYPES = {"rigid_tie", "bonded", "welded_proxy", "bolted_proxy"}

# semantic roles preferred by each proxy type (for evidence checks)
_PREFERRED_ROLES = {
    "bolted_proxy": {"bolt_hole", "mounting_face"},
    "welded_proxy": {"weld_face", "contact_face", "mounting_face"},
}


# ── small geometry helpers (numpy-free where possible) ────────────────────────

def _bbox_center(bb: list[float]) -> list[float]:
    return [(float(bb[0]) + float(bb[3])) / 2.0,
            (float(bb[1]) + float(bb[4])) / 2.0,
            (float(bb[2]) + float(bb[5])) / 2.0]


def _bbox_corners(bb: list[float]) -> list[list[float]]:
    xs, ys, zs = (bb[0], bb[3]), (bb[1], bb[4]), (bb[2], bb[5])
    return [[float(x), float(y), float(z)] for x in xs for y in ys for z in zs]


def _bbox_union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [b for b in boxes if isinstance(b, (list, tuple)) and len(b) == 6]
    if not boxes:
        return None
    lo = [min(float(b[i]) for b in boxes) for i in range(3)]
    hi = [max(float(b[i + 3]) for b in boxes) for i in range(3)]
    return lo + hi


def _bbox_from_points(pts: list[list[float]]) -> list[float]:
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return lo + hi


def _bbox_diag(bb: list[float]) -> float:
    return math.sqrt(sum((float(bb[i + 3]) - float(bb[i])) ** 2 for i in range(3)))


def _bbox_surface_area(bb: list[float]) -> float:
    """Surface area of an AABB — the scale an interface area is measured against.

    Area is the honest yardstick for "how much of this part does the selection
    tie down": a ring and a full disc of the same outer diameter share a bbox
    diagonal but not an area, and it is area that decides how many nodes a
    constraint captures. Measuring against the whole boundary rather than one
    cross-section also makes the number shape-agnostic — a curved face that wraps
    the body needs no special case.
    """
    ex = [max(float(bb[i + 3]) - float(bb[i]), 0.0) for i in range(3)]
    return 2.0 * (ex[0] * ex[1] + ex[1] * ex[2] + ex[0] * ex[2])


def _bbox_gap(a: list[float], b: list[float]) -> float:
    """Euclidean gap between two AABBs (0 if they overlap on every axis)."""
    sep = []
    for i in range(3):
        d = max(a[i] - b[i + 3], b[i] - a[i + 3], 0.0)
        sep.append(d)
    return math.sqrt(sum(d * d for d in sep))


def _bbox_overlap(a: list[float], b: list[float]) -> bool:
    return all(a[i] <= b[i + 3] and b[i] <= a[i + 3] for i in range(3))


def _dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(float(c) ** 2 for c in v))


# ── transforms (translation + euler / matrix) ─────────────────────────────────

def _rotation_from_euler_deg(euler: list[float]) -> list[list[float]]:
    rx, ry, rz = (math.radians(float(a)) for a in euler)
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    # R = Rz @ Ry @ Rx
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def _resolve_transform(transform: Any) -> tuple[list[list[float]], list[float], bool, str | None]:
    """Return (R 3x3, t 3, ok, note). Identity + note when missing/invalid."""
    identity = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    if transform is None:
        return identity, [0.0, 0.0, 0.0], True, None
    if not isinstance(transform, dict):
        return identity, [0.0, 0.0, 0.0], False, "invalid_transform"
    t = transform.get("translation") or [0.0, 0.0, 0.0]
    try:
        tvec = [float(t[0]), float(t[1]), float(t[2])]
    except Exception:
        return identity, [0.0, 0.0, 0.0], False, "invalid_transform"
    matrix = transform.get("matrix")
    if matrix is not None:
        try:
            if len(matrix) == 4:  # 4x4 row-major
                R = [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
                tvec = [float(matrix[r][3]) for r in range(3)]
            elif len(matrix) == 3:
                R = [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
            else:
                return identity, tvec, False, "invalid_transform"
            return R, tvec, True, None
        except Exception:
            return identity, [0.0, 0.0, 0.0], False, "invalid_transform"
    euler = transform.get("rotation_euler_deg")
    if euler is not None:
        try:
            return _rotation_from_euler_deg(euler), tvec, True, None
        except Exception:
            return identity, tvec, False, "invalid_transform"
    return identity, tvec, True, None  # translation only


def _apply_point(R: list[list[float]], t: list[float], p: list[float]) -> list[float]:
    return [R[r][0] * p[0] + R[r][1] * p[1] + R[r][2] * p[2] + t[r] for r in range(3)]


def _apply_vector(R: list[list[float]], v: list[float]) -> list[float]:
    return [R[r][0] * v[0] + R[r][1] * v[1] + R[r][2] * v[2] for r in range(3)]


# ── interface resolution ──────────────────────────────────────────────────────

def _ref_ids(iface: dict[str, Any]) -> dict[str, list[str]]:
    refs = iface.get("topology_refs") or {}
    return {k: [str(x) for x in _as_list(refs.get(k))]
            for k in ("face_ids", "edge_ids", "vertex_ids")}


def resolve_interface(iface: dict[str, Any], topo_index: dict[str, dict[str, Any]] | None,
                      transform: Any) -> dict[str, Any]:
    """Resolve one interface against a part topology index, then transform to world coords."""
    refs = _ref_ids(iface)
    all_ids = refs["face_ids"] + refs["edge_ids"] + refs["vertex_ids"]
    topo_index = topo_index or {}
    R, tvec, tok, tnote = _resolve_transform(transform)

    found, unresolved = [], []
    for rid in all_ids:
        ent = topo_index.get(rid)
        if ent is not None:
            found.append(ent)
        else:
            unresolved.append(rid)

    boxes, centers, normals, areas, face_count = [], [], [], [], 0
    cyl_best: tuple[float, float, list[float], list[float]] | None = None  # (area, radius, axis_dir, axis_point)
    for ent in found:
        bb = ent.get("bounding_box")
        if isinstance(bb, (list, tuple)) and len(bb) == 6:
            boxes.append([float(x) for x in bb])
            c = ent.get("centroid")
            centers.append([float(x) for x in c] if isinstance(c, (list, tuple)) and len(c) == 3
                           else _bbox_center(bb))
        if ent.get("type") == "face":
            face_count += 1
            n = ent.get("normal")
            a = float(ent.get("area") or 0.0)
            if isinstance(n, (list, tuple)) and len(n) == 3 and _norm(n) > 1e-9:
                normals.append(([float(x) for x in n], a if a > 0 else 1.0))
            if a > 0:
                areas.append(a)
            # Capture the dominant cylindrical face (radius + axis) for concentric /
            # tangent mate predicates.
            if ent.get("surface_type") == "cylinder" and isinstance(ent.get("radius"), (int, float)):
                ax = ent.get("axis")
                if isinstance(ax, (list, tuple)) and len(ax) == 3 and _norm(ax) > 1e-9:
                    ap = ent.get("axis_point")
                    apt = ([float(x) for x in ap] if isinstance(ap, (list, tuple)) and len(ap) == 3
                           else (_bbox_center(bb) if isinstance(bb, (list, tuple)) and len(bb) == 6 else [0.0, 0.0, 0.0]))
                    aw = a if a > 0 else 1.0
                    if cyl_best is None or aw > cyl_best[0]:
                        cyl_best = (aw, float(ent["radius"]), [float(x) for x in ax], apt)

    local: dict[str, Any] = {}
    world: dict[str, Any] = {}
    if boxes:
        lbb = _bbox_union(boxes)
        # area-weighted centroid where areas known, else mean of centers
        if areas and len(areas) == len(centers):
            tot = sum(areas) or 1.0
            lcen = [sum(centers[i][k] * areas[i] for i in range(len(centers))) / tot for k in range(3)]
        else:
            lcen = [sum(c[k] for c in centers) / len(centers) for k in range(3)]
        lnorm = None
        if normals:
            wsum = [sum(n[0][k] * n[1] for n in normals) for k in range(3)]
            mag = _norm(wsum)
            if mag > 1e-9:
                lnorm = [wsum[k] / mag for k in range(3)]
        local = {"bbox": lbb, "centroid": lcen, "normal": lnorm,
                 "area": round(sum(areas), 6) if areas else None}
        # world geometry: transform corners, recompute AABB; transform centroid; rotate normal
        wcorners = [_apply_point(R, tvec, c) for c in _bbox_corners(lbb)]
        wbb = _bbox_from_points(wcorners)
        wcen = _apply_point(R, tvec, lcen)
        wnorm = None
        if lnorm is not None:
            wn = _apply_vector(R, lnorm)
            m = _norm(wn)
            wnorm = [wn[k] / m for k in range(3)] if m > 1e-9 else None
        world = {"bbox": [round(x, 6) for x in wbb], "centroid": [round(x, 6) for x in wcen],
                 "normal": [round(x, 6) for x in wnorm] if wnorm else None,
                 "area": local["area"]}
        if cyl_best is not None:
            _, radius, axdir, axpt = cyl_best
            local["radius"] = round(radius, 4)
            local["axis"] = [round(x, 6) for x in axdir]
            local["axis_point"] = [round(x, 4) for x in axpt]
            wax = _apply_vector(R, axdir)
            wm = _norm(wax)
            world["radius"] = local["radius"]
            world["axis"] = [round(wax[k] / wm, 6) for k in range(3)] if wm > 1e-9 else None
            world["axis_point"] = [round(x, 4) for x in _apply_point(R, tvec, axpt)]

    if not all_ids:
        status = "unresolved"
    elif not found:
        status = "unresolved"
    elif unresolved:
        status = "partially_resolved"
    else:
        status = "resolved"

    return {
        "interface_id": iface.get("id"),
        "part_id": iface.get("part_id"),
        "semantic_role": iface.get("semantic_role"),
        "requested_ref_count": len(all_ids),
        "resolved_entity_count": len(found),
        "topology_entity_count": len(found),
        "face_count": face_count,
        "face_like": face_count > 0 and (world.get("normal") is not None),
        "unresolved_refs": unresolved,
        "resolution_status": status,
        "transform_applied": tok,
        "transform_note": tnote,
        "local": local,
        "world": world,
    }


def resolve_assembly_interfaces(assembly: Any,
                                topology_by_part: dict[str, dict[str, dict[str, Any]]] | None) -> dict[str, Any]:
    """Resolve every interface in the Assembly IR. ``topology_by_part`` maps part_id ->
    {entity_id: entity}. Missing topology degrades to unresolved (reported honestly)."""
    assembly = assembly if isinstance(assembly, dict) else {}
    topology_by_part = topology_by_part or {}
    transforms = {p.get("id"): p.get("transform") for p in _as_list(assembly.get("parts"))
                  if isinstance(p, dict)}
    interfaces = _collect_interfaces(assembly)

    records = {}
    for iid, iface in interfaces.items():
        pid = iface.get("part_id")
        records[iid] = resolve_interface(iface, topology_by_part.get(pid), transforms.get(pid))

    counts = {"resolved": 0, "partially_resolved": 0, "unresolved": 0}
    for r in records.values():
        counts[r["resolution_status"]] = counts.get(r["resolution_status"], 0) + 1

    return {
        "format": "aieng.assembly_interface_resolution", "format_version": FORMAT_VERSION,
        "schema_version": "0.1",
        "interfaces": records,
        "summary": {"interface_count": len(records), **counts,
                    "parts_with_topology": sorted(topology_by_part.keys())},
        "provenance": {"created_by": "aieng.assembly_interface_resolution",
                       "geometry_validation_only": True, "solver_executed": False},
    }


# ── connection geometry validation ────────────────────────────────────────────

def _status_from_reasons(reasons: list[str], insufficient: bool) -> str:
    if insufficient:
        return "insufficient_data"
    if any(r in {"far_apart", "joint_across_gap", "missing_transform_blocks_validation",
                 "mate_predicate_violated"} for r in reasons):
        return "invalid"
    if reasons:
        return "warning"
    return "plausible"


# ── domain-aware mate predicates ───────────────────────────────────────────────
# Beyond generic AABB/centroid plausibility, verify the specific ENGINEERING
# relationship a connection intends: concentric (shaft-in-bore), tangent (gear
# mesh), coincident (faces flush), or a clearance gap. Evaluated from the resolved
# WORLD geometry (axis / radius / normal / centroid). Geometry-only; no physics.

_MATE_PREDICATES = ("concentric", "tangent", "coincident", "clearance")
_MATE_DEFAULT_TOL = {"concentric": 0.5, "tangent": 1.0, "coincident": 0.5, "clearance": 1.0}
_PARALLEL_DOT = 0.985  # |a·b| above this ⇒ axes/normals (anti)parallel (~10°)


def _dot3(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub3(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross3(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _axis_line_distance(pa: list[float], da: list[float], pb: list[float], db: list[float]) -> float:
    """Shortest distance between two infinite lines (pa,da) and (pb,db) (da/db unit)."""
    n = _cross3(da, db)
    nn = _norm(n)
    if nn < 1e-6:  # parallel → perpendicular distance from pb to line a
        v = _sub3(pb, pa)
        proj = _dot3(v, da)
        perp = [v[k] - proj * da[k] for k in range(3)]
        return _norm(perp)
    return abs(_dot3(_sub3(pb, pa), [n[k] / nn for k in range(3)]))


def evaluate_mate_predicate(
    predicate: str,
    wa: dict[str, Any],
    wb: dict[str, Any],
    *,
    tolerance_mm: Any = None,
    expected_clearance_mm: Any = None,
) -> dict[str, Any]:
    """Evaluate an engineering mate predicate from two interfaces' WORLD geometry.

    Returns ``{predicate, satisfied(True/False/None), measured_mm, expected_mm,
    tolerance_mm, detail}``. ``satisfied=None`` means insufficient geometry
    (reported honestly, never guessed). Geometry-only — implies no contact/preload.
    """
    pred = str(predicate or "").strip().lower()
    tol = float(tolerance_mm) if isinstance(tolerance_mm, (int, float)) else _MATE_DEFAULT_TOL.get(pred, 0.5)
    base = {"predicate": pred, "tolerance_mm": tol, "satisfied": None,
            "measured_mm": None, "expected_mm": None}

    def _insufficient(msg: str) -> dict[str, Any]:
        return {**base, "satisfied": None, "detail": f"insufficient geometry: {msg}"}

    if pred in ("concentric", "tangent"):
        aa, ab = wa.get("axis"), wb.get("axis")
        pa, pb = wa.get("axis_point"), wb.get("axis_point")
        if not (aa and ab and pa and pb):
            return _insufficient("both interfaces need a resolved cylindrical axis (use cylindrical @face refs)")
        parallel = abs(_dot3(aa, ab)) > _PARALLEL_DOT
        dist = _axis_line_distance(pa, aa, pb, ab)
        if pred == "concentric":
            measured = round(dist, 4)
            sat = bool(parallel and dist <= tol)
            return {**base, "satisfied": sat, "measured_mm": measured, "expected_mm": 0.0,
                    "detail": f"axis offset {measured}mm (tol {tol}); axes {'parallel' if parallel else 'NOT parallel'}"}
        ra, rb = wa.get("radius"), wb.get("radius")
        if not (isinstance(ra, (int, float)) and isinstance(rb, (int, float))):
            return _insufficient("tangent needs a radius on both interfaces")
        measured, expected = round(dist, 4), round(float(ra) + float(rb), 4)
        sat = bool(parallel and abs(dist - (ra + rb)) <= tol)
        return {**base, "satisfied": sat, "measured_mm": measured, "expected_mm": expected,
                "detail": f"axis center-distance {measured}mm vs r1+r2={expected}mm (tol {tol}); "
                          f"axes {'parallel' if parallel else 'NOT parallel'}"}

    if pred == "coincident":
        na, nb, ca, cb = wa.get("normal"), wb.get("normal"), wa.get("centroid"), wb.get("centroid")
        if not (na and nb and ca and cb):
            return _insufficient("coincident needs a planar normal + centroid on both interfaces")
        facing = _dot3(na, nb) < -_PARALLEL_DOT
        dist = round(_dist(ca, cb), 4)
        return {**base, "satisfied": bool(facing and dist <= tol), "measured_mm": dist, "expected_mm": 0.0,
                "detail": f"face centroid gap {dist}mm (tol {tol}); normals {'opposed (mating)' if facing else 'NOT opposed'}"}

    if pred == "clearance":
        ca, cb = wa.get("centroid"), wb.get("centroid")
        if not (ca and cb):
            return _insufficient("clearance needs a centroid on both interfaces")
        dist = round(_dist(ca, cb), 4)
        if not isinstance(expected_clearance_mm, (int, float)):
            return {**base, "measured_mm": dist,
                    "detail": f"measured centroid gap {dist}mm (pass expected_clearance_mm to check it)"}
        exp = round(float(expected_clearance_mm), 4)
        return {**base, "satisfied": bool(abs(dist - exp) <= tol), "measured_mm": dist, "expected_mm": exp,
                "detail": f"gap {dist}mm vs expected {exp}mm (tol {tol})"}

    return {**base, "detail": f"unknown predicate '{pred}' (expected one of {list(_MATE_PREDICATES)})"}


def validate_connection_geometry(assembly: Any, resolution: dict[str, Any]) -> dict[str, Any]:
    """Validate each connection's geometry from resolved interface records."""
    assembly = assembly if isinstance(assembly, dict) else {}
    recs = (resolution or {}).get("interfaces") or {}
    out = []
    for idx, conn in enumerate(_as_list(assembly.get("connections"))):
        if not isinstance(conn, dict):
            continue
        cid = conn.get("id") or f"connection_{idx:03d}"
        ctype = conn.get("type")
        ia, ib = conn.get("interface_a"), conn.get("interface_b")
        ra, rb = recs.get(ia), recs.get(ib)
        reasons: list[str] = []
        metrics: dict[str, Any] = {}

        # need both interfaces resolved with world geometry to validate
        insufficient = False
        if ia is None or ib is None:
            insufficient = True
            reasons.append("unresolved_interface")
        elif ra is None or rb is None or ra["resolution_status"] == "unresolved" \
                or rb["resolution_status"] == "unresolved":
            insufficient = True
            reasons.append("unresolved_interface")
        else:
            wa, wb = ra.get("world") or {}, rb.get("world") or {}
            if not (wa.get("bbox") and wb.get("bbox")):
                insufficient = True
                reasons.append("unresolved_interface")
            else:
                if ra.get("transform_note") in {"missing_transform", "invalid_transform"} \
                        or rb.get("transform_note") in {"missing_transform", "invalid_transform"}:
                    reasons.append("missing_transform")
                ca, cb = wa["centroid"], wb["centroid"]
                centroid_distance = _dist(ca, cb)
                gap = _bbox_gap(wa["bbox"], wb["bbox"])
                overlap = _bbox_overlap(wa["bbox"], wb["bbox"])
                scale = max(_bbox_diag(wa["bbox"]), _bbox_diag(wb["bbox"]), 1e-6)
                touch_tol, near_tol = _TOUCH_FRAC * scale, _NEAR_FRAC * scale
                na, nb = wa.get("normal"), wb.get("normal")
                normal_alignment = None
                if na and nb:
                    normal_alignment = round(sum(na[k] * nb[k] for k in range(3)), 6)
                metrics = {
                    "centroid_distance": round(centroid_distance, 6),
                    "bbox_gap": round(gap, 6), "bbox_overlap": overlap,
                    "normal_alignment": normal_alignment, "scale": round(scale, 6),
                }

                far_apart = gap > near_tol
                touching = gap <= touch_tol or overlap
                if far_apart:
                    reasons.append("far_apart")
                elif not touching:
                    reasons.append("no_overlap")
                    if ctype in _JOIN_CONNECTION_TYPES:
                        # A joint across a gap cannot exist at any scale.
                        reasons.append("joint_across_gap")
                if normal_alignment is not None and normal_alignment > _NORMAL_SAME_DOT:
                    reasons.append("normals_same_direction")

                # type-specific evidence/behavior
                roles = {ra.get("semantic_role"), rb.get("semantic_role")}
                if ctype in _PREFERRED_ROLES and not (roles & _PREFERRED_ROLES[ctype]):
                    reasons.append("no_bolt_hole_evidence" if ctype == "bolted_proxy"
                                   else "no_weld_face_evidence")
                if ctype == "spring_proxy":
                    reasons.append("centroid_based_proxy")
                if ctype == "contact_proxy":
                    reasons.append("contact_draft_only")
                if ctype not in PROXY_CONNECTION_TYPES and ctype not in {"rigid_tie", "bonded"}:
                    reasons.append("unsupported_connection_type")

                # Domain-aware mate predicate (concentric / tangent / coincident /
                # clearance): verify the intended engineering relationship, not just
                # generic proximity. A violated predicate is a real geometric defect.
                pred = conn.get("mate_predicate")
                if pred:
                    mate = evaluate_mate_predicate(
                        str(pred), wa, wb,
                        tolerance_mm=conn.get("mate_tolerance_mm"),
                        expected_clearance_mm=conn.get("expected_clearance_mm"),
                    )
                    metrics["mate_predicate"] = mate
                    if mate.get("satisfied") is False:
                        reasons.append("mate_predicate_violated")

        status = _status_from_reasons(reasons, insufficient)
        # spring/contact proxies never claim better than "warning"
        if ctype in {"spring_proxy", "contact_proxy"} and status == "plausible":
            status = "warning"

        out.append({
            "connection_id": cid, "type": ctype,
            "part_a": conn.get("part_a"), "part_b": conn.get("part_b"),
            "interface_a": ia, "interface_b": ib,
            "geometry_status": status, "reasons": sorted(set(reasons)),
            "metrics": metrics, "is_proxy": ctype in PROXY_CONNECTION_TYPES,
            "mate_predicate": metrics.get("mate_predicate"),
        })

    tally: dict[str, int] = {}
    for c in out:
        tally[c["geometry_status"]] = tally.get(c["geometry_status"], 0) + 1
    return {
        "format": "aieng.assembly_connection_geometry", "format_version": FORMAT_VERSION,
        "schema_version": "0.1",
        "connections": out,
        "summary": {"connection_count": len(out), **tally},
        "limitations": [
            "Geometry validation only — no contact, friction, or bolt preload is modeled.",
            "Proximity/normal checks are approximate AABB+centroid heuristics, not exact surface mating.",
        ],
        "provenance": {"created_by": "aieng.assembly_interface_resolution",
                       "geometry_validation_only": True, "contact_physics_modeled": False,
                       "bolt_preload_modeled": False, "solver_executed": False},
    }


# ── package integration ──────────────────────────────────────────────────────

def _topo_index(topo_doc: Any) -> dict[str, dict[str, Any]]:
    ents = (topo_doc or {}).get("entities") if isinstance(topo_doc, dict) else None
    return {e["id"]: e for e in _as_list(ents) if isinstance(e, dict) and e.get("id")}


def _part_aliases(part: dict[str, Any]) -> set[str]:
    aliases = {str(v) for v in (part.get("id"), part.get("name"), part.get("geometry_ref")) if v}
    geom = part.get("geometry_ref")
    if isinstance(geom, str) and geom:
        try:
            aliases.add(Path(geom).stem)
        except Exception:
            pass
    return aliases


def _feature_graph_body_ids(feature_graph: Any, aliases: set[str]) -> set[str]:
    if not isinstance(feature_graph, dict):
        return set()
    out: set[str] = set()
    for feat in _as_list(feature_graph.get("features")):
        if not isinstance(feat, dict):
            continue
        if feat.get("type") != "named_part":
            continue
        names = {str(v) for v in (feat.get("name"), feat.get("id")) if v}
        if not names.intersection(aliases):
            continue
        refs = feat.get("geometry_refs") if isinstance(feat.get("geometry_refs"), dict) else {}
        body = refs.get("body")
        if body:
            out.add(str(body))
        for ent in _as_list(refs.get("entities")):
            if ent:
                out.add(str(ent))
    return out


def _topology_named_body_ids(shared: dict[str, dict[str, Any]], aliases: set[str]) -> set[str]:
    out: set[str] = set()
    for eid, ent in shared.items():
        if not isinstance(ent, dict) or ent.get("type") != "solid":
            continue
        if str(ent.get("name") or "") in aliases or str(eid) in aliases:
            out.add(str(eid))
    return out


def build_topology_by_part(zf: zipfile.ZipFile, names: set[str],
                           assembly: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Build part_id -> {entity_id: entity}. Preference: explicit part topology_ref ->
    parts/<id>/topology_map.json -> geometry/parts/<id>/topology_map.json -> shared package map."""
    def _read(name: str) -> Any:
        try:
            return json.loads(zf.read(name)) if name in names else None
        except Exception:
            return None

    shared = _topo_index(_read(PACKAGE_TOPOLOGY_PATH))
    feature_graph = _read(FEATURE_GRAPH_PATH)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for part in _as_list(assembly.get("parts")):
        if not isinstance(part, dict) or not part.get("id"):
            continue
        pid = part["id"]
        candidates = [part.get("topology_ref"), f"parts/{pid}/topology_map.json",
                      f"geometry/parts/{pid}/topology_map.json"]
        idx: dict[str, dict[str, Any]] = {}
        for cand in candidates:
            if cand and cand in names:
                idx = _topo_index(_read(cand))
                if idx:
                    break
        if not idx and shared:
            # Shared single-package topology. Live build123d Compounds often label
            # parts as feature_graph named_part entries while topology faces carry
            # kernel body ids (body_001, ...). Resolve that label -> body id before
            # scoping; fall back to legacy global refs only when no mapping exists.
            aliases = _part_aliases(part)
            mapped_body_ids = (
                _feature_graph_body_ids(feature_graph, aliases)
                or _topology_named_body_ids(shared, aliases)
            )
            body_ids = mapped_body_ids or aliases
            scoped = {eid: e for eid, e in shared.items()
                      if str(eid) in body_ids or str(e.get("body_id") or "") in body_ids}
            idx = scoped if mapped_body_ids else (scoped or shared)
        if idx:
            out[pid] = idx
    return out


# ── pre-solver interface-NSET quality diagnostics (#200) ──────────────────────

def _entity_world_box(ent: dict[str, Any], R: list[list[float]], t: list[float]) -> list[float] | None:
    bb = ent.get("bounding_box")
    if not (isinstance(bb, (list, tuple)) and len(bb) == 6):
        return None
    corners = [_apply_point(R, t, c) for c in _bbox_corners([float(x) for x in bb])]
    return _bbox_from_points(corners)


def _part_world_bbox(topo_index: dict[str, dict[str, Any]], R: list[list[float]], t: list[float]) -> list[float] | None:
    boxes = [wb for ent in topo_index.values() if (wb := _entity_world_box(ent, R, t))]
    return _bbox_union(boxes) if boxes else None


def _iface_member_world_boxes(iface: dict[str, Any], topo_index: dict[str, dict[str, Any]],
                              R: list[list[float]], t: list[float]) -> list[list[float]]:
    refs = _ref_ids(iface)
    out: list[list[float]] = []
    for rid in refs["face_ids"] + refs["edge_ids"] + refs["vertex_ids"]:
        ent = topo_index.get(rid)
        if ent is not None and (wb := _entity_world_box(ent, R, t)):
            out.append(wb)
    return out


def _region_count(boxes: list[list[float]], tol: float) -> int:
    """Union-find clustering: boxes that overlap or sit within ``tol`` gap form one region."""
    n = len(boxes)
    if n <= 1:
        return n
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _bbox_overlap(boxes[i], boxes[j]) or _bbox_gap(boxes[i], boxes[j]) <= tol:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    return len({find(i) for i in range(n)})


def diagnose_mesh_interfaces(assembly: Any, topology_by_part: dict[str, dict[str, dict[str, Any]]] | None,
                             resolution: dict[str, Any]) -> dict[str, Any]:
    """Pre-solver interface-NSET quality diagnostics for an assembly.

    Catches interfaces that would map onto **empty / sparse / disconnected /
    over-broad** mesh node sets *before* a solver runs, so misleading assembly
    results are caught early. Derived from the already-resolved interface
    geometry (a mesh proxy); it runs no solver and meshes nothing. Empty
    interfaces are **blocking**; sparse/disconnected/over-broad/partial are
    **warnings**. Complements the connection-geometry plausibility check without
    duplicating it (that judges connection pairs; this judges per-interface node
    coverage).
    """
    assembly = assembly if isinstance(assembly, dict) else {}
    topology_by_part = topology_by_part or {}
    resolution = resolution if isinstance(resolution, dict) else {}
    records = resolution.get("interfaces") if isinstance(resolution.get("interfaces"), dict) else {}
    transforms = {p.get("id"): p.get("transform") for p in _as_list(assembly.get("parts")) if isinstance(p, dict)}
    interfaces = _collect_interfaces(assembly)

    diagnostics: list[dict[str, Any]] = []
    counts = {"ok": 0, "warning": 0, "blocking": 0}
    blocking_interfaces: list[str] = []

    for iid, iface in interfaces.items():
        rec = records.get(iid) if isinstance(records.get(iid), dict) else {}
        pid = iface.get("part_id")
        topo_index = topology_by_part.get(pid) or {}
        R, tvec, _ok, _note = _resolve_transform(transforms.get(pid))
        face_count = int(rec.get("face_count") or 0)
        requested = int(rec.get("requested_ref_count") or 0)
        unresolved = rec.get("unresolved_refs") or []
        status_res = rec.get("resolution_status")
        world = rec.get("world") if isinstance(rec.get("world"), dict) else {}
        area = world.get("area")
        iface_bbox = world.get("bbox")
        iface_diag = _bbox_diag(iface_bbox) if isinstance(iface_bbox, (list, tuple)) and len(iface_bbox) == 6 else 0.0

        member_boxes = _iface_member_world_boxes(iface, topo_index, R, tvec)
        region_count = _region_count(member_boxes, _DISCONNECT_GAP_FRAC * iface_diag) if iface_diag > 0 else len(member_boxes)
        part_bbox = _part_world_bbox(topo_index, R, tvec)
        part_diag = _bbox_diag(part_bbox) if part_bbox else 0.0
        part_surface = _bbox_surface_area(part_bbox) if part_bbox else 0.0
        coverage = (
            (float(area) / part_surface)
            if part_surface > 0 and isinstance(area, (int, float)) and area
            else None
        )

        findings: list[dict[str, Any]] = []
        if status_res == "unresolved" or face_count == 0 or not area:
            findings.append({
                "code": "empty_interface", "severity": "blocking",
                "message": "interface resolves to no usable faces (empty node set)",
                "guidance": "re-pick the interface topology_refs or supply the part topology map; "
                            "the solver would otherwise run with a missing interface.",
            })
        else:
            if unresolved:
                findings.append({
                    "code": "partial_resolution", "severity": "warning",
                    "message": f"{len(unresolved)} of {requested} interface refs did not resolve",
                    "guidance": "refresh topology_refs against the latest part geometry; unresolved refs are dropped from the node set.",
                })
            if face_count <= _SPARSE_MAX_FACES and (coverage is None or coverage < _SPARSE_SURFACE_FRAC):
                extent = (
                    f" covering {coverage * 100:.2f}% of the part surface"
                    if coverage is not None else " (part extent unknown)"
                )
                findings.append({
                    "code": "sparse_interface", "severity": "warning",
                    "message": f"interface maps onto only {face_count} face(s){extent}; "
                               "the node set may be undersampled",
                    "guidance": "refine the mesh on this interface or extend topology_refs so the contact/load region is adequately sampled.",
                })
            if region_count > 1:
                findings.append({
                    "code": "disconnected_interface", "severity": "warning",
                    "message": f"interface faces form {region_count} disconnected regions",
                    "guidance": "split into separate interfaces or confirm the refs belong to one contact patch; a disconnected node set can misrepresent load transfer.",
                })
            if coverage is not None and coverage > _OVER_BROAD_SURFACE_FRAC:
                findings.append({
                    "code": "over_broad_interface", "severity": "warning",
                    "message": f"interface covers {round(coverage * 100)}% of the part surface; "
                               "likely over-broad",
                    "guidance": "narrow the interface to the true mating region; an over-broad node set over-constrains the model.",
                })

        if any(f["severity"] == "blocking" for f in findings):
            status = "blocking"
            blocking_interfaces.append(str(iid))
        elif findings:
            status = "warning"
        else:
            status = "ok"
        counts[status] += 1
        diagnostics.append({
            "interface_id": iid,
            "part_id": pid,
            "semantic_role": iface.get("semantic_role"),
            "resolution_status": status_res,
            "face_count": face_count,
            "requested_ref_count": requested,
            "area": area,
            "region_count": region_count,
            "interface_diag": round(iface_diag, 6) if iface_diag else 0.0,
            "part_diag": round(part_diag, 6) if part_diag else None,
            "part_surface_area": round(part_surface, 6) if part_surface else None,
            "coverage_fraction": round(coverage, 6) if coverage is not None else None,
            "status": status,
            "findings": findings,
        })

    return {
        "format": "aieng.assembly_mesh_interface_diagnostics",
        "format_version": FORMAT_VERSION,
        "schema_version": "0.1",
        "interface_count": len(diagnostics),
        "summary": counts,
        "safe_for_solver": counts["blocking"] == 0,
        "blocking_interfaces": blocking_interfaces,
        "interfaces": diagnostics,
        "honesty": {
            "solver_executed": False,
            "meshing_performed": False,
            "node_sets_proxied_from": "resolved interface topology geometry",
            "note": "Pre-solver geometry-coverage diagnostics; not a mesh-convergence guarantee.",
        },
    }


def resolve_and_validate_assembly_geometry(package_path: str | Path) -> dict[str, Any]:
    """Best-effort: resolve interfaces + validate connection geometry for a package carrying
    assembly/assembly_ir.json. Writes interface_resolution, connection_geometry, refreshes the
    connection_graph + CAE draft with geometry_status. Returns {assembly_present: bool, ...}."""
    package_path = Path(package_path)
    if not package_path.exists():
        return {"assembly_present": False, "reason": "package not found"}
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            if ASSEMBLY_IR_PATH not in names:
                return {"assembly_present": False}
            try:
                assembly = json.loads(zf.read(ASSEMBLY_IR_PATH))
            except Exception:
                assembly = None
            topo_by_part = build_topology_by_part(zf, names, assembly or {})
    except Exception as exc:  # noqa: BLE001
        return {"assembly_present": False, "error": f"{type(exc).__name__}: {exc}"}

    resolution = resolve_assembly_interfaces(assembly, topo_by_part)
    geometry = validate_connection_geometry(assembly, resolution)
    mesh_diag = diagnose_mesh_interfaces(assembly, topo_by_part, resolution)

    # refresh connection graph with geometry status per edge
    graph = build_connection_graph(assembly)
    geo_by_conn = {c["connection_id"]: c for c in geometry["connections"]}
    for edge in graph.get("edges", []):
        g = geo_by_conn.get(edge.get("id"))
        if g:
            edge["geometry_status"] = g["geometry_status"]
            edge["geometry_reasons"] = g["reasons"]
            edge["geometry_metrics"] = g["metrics"]
    graph["geometry_validation"] = {"available": True, "summary": geometry["summary"]}

    # refresh CAE draft with geometry status; invalid connections -> needs_user_input
    draft = build_assembly_cae_setup_draft(assembly)
    draft["geometry_validation_only"] = True
    draft.setdefault("provenance", {})["geometry_validation_only"] = True
    for cdraft in draft.get("connections", []):
        g = geo_by_conn.get(cdraft.get("connection_id"))
        if not g:
            continue
        cdraft["geometry_status"] = g["geometry_status"]
        cdraft["geometry_reasons"] = g["reasons"]
        if g["geometry_status"] == "invalid":
            cdraft["disabled"] = True
            msg = f"connection '{cdraft['connection_id']}' geometry invalid: {', '.join(g['reasons'])}"
            if msg not in draft.get("needs_user_input", []):
                draft.setdefault("needs_user_input", []).append(msg)
    # attach resolved interface geometry to loads/supports where available
    for group in ("loads", "supports"):
        for ref in draft.get(group, []):
            rec = resolution["interfaces"].get(ref.get("interface_id"))
            if rec and rec.get("world", {}).get("centroid") is not None:
                ref["world_centroid"] = rec["world"]["centroid"]
                ref["topology_resolved"] = rec["resolution_status"] != "unresolved"
    # Interfaces that resolve to an empty/unusable node set block the solver via
    # the same needs_user_input gate the geometry-invalid connections use.
    for iid in mesh_diag.get("blocking_interfaces", []):
        msg = f"interface '{iid}' resolves to an empty/unusable node set (mesh-interface diagnostics)"
        if msg not in draft.get("needs_user_input", []):
            draft.setdefault("needs_user_input", []).append(msg)

    if draft.get("needs_user_input"):
        draft["status"] = "needs_user_input"

    def _dumps(o: Any) -> bytes:
        return (json.dumps(o, indent=2, sort_keys=True) + "\n").encode()

    members = {
        INTERFACE_RESOLUTION_PATH: _dumps(resolution),
        ASSEMBLY_CONNECTION_GEOMETRY_PATH: _dumps(geometry),
        ASSEMBLY_MESH_INTERFACE_DIAGNOSTICS_PATH: _dumps(mesh_diag),
        CONNECTION_GRAPH_PATH: _dumps(graph),
        ASSEMBLY_CAE_DRAFT_PATH: _dumps(draft),
    }
    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with (
            zipfile.ZipFile(package_path, "r") as src,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                if item.filename not in members:
                    dst.writestr(item, src.read(item.filename))
            for name, data in members.items():
                dst.writestr(name, data)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    cae_result: dict[str, Any] = {}
    try:
        from aieng.converters.assembly_cae import process_assembly_cae_package
        cae_result = process_assembly_cae_package(package_path)
    except Exception as exc:  # noqa: BLE001 - assembly CAE v0 is best-effort
        cae_result = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "assembly_present": True,
        "resolution_summary": resolution["summary"],
        "geometry_summary": geometry["summary"],
        "mesh_interface_summary": mesh_diag["summary"],
        "mesh_interface_safe_for_solver": mesh_diag["safe_for_solver"],
        "cae_draft_status": draft["status"],
        "assembly_cae_model_status": cae_result.get("assembly_cae_model_status"),
        "solver_deck_status": cae_result.get("solver_deck_status"),
        "solver_execution_status": cae_result.get("solver_execution_status"),
        "assembly_result_mapping_status": cae_result.get("assembly_result_mapping_status"),
        "assembly_topopt_status": cae_result.get("assembly_topopt_status"),
        "assembly_topopt_standard_problem_emitted": cae_result.get("assembly_topopt_standard_problem_emitted"),
        "artifacts": sorted(members.keys()),
    }
