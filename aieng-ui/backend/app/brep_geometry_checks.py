"""Exact B-Rep geometric checks for validation targets (#296).

Runs heavy OpenCASCADE operations in an isolated subprocess so the main backend
process is never blocked by boolean intersections or surface-surface extrema.
The runner uses OCP directly so it works in build123d/OCP environments without
assuming build123d is importable in the backend interpreter.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_BREP_RUNNER_TEMPLATE = r'''"""Isolated OCP runner for exact B-Rep validation targets."""
import json
import math
import sys


def _import_ocp():
    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
        from OCP.TopoDS import TopoDS
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        from OCP.BRep import BRep_Tool
        from OCP.GeomAdaptor import GeomAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
        from OCP.gp import gp_Pnt, gp_Dir
        return {
            "STEPControl_Reader": STEPControl_Reader,
            "IFSelect_RetDone": IFSelect_RetDone,
            "TopExp_Explorer": TopExp_Explorer,
            "TopAbs_SOLID": TopAbs_SOLID,
            "TopAbs_FACE": TopAbs_FACE,
            "TopoDS": TopoDS,
            "BRepGProp": BRepGProp,
            "GProp_GProps": GProp_GProps,
            "Bnd_Box": Bnd_Box,
            "BRepBndLib": BRepBndLib,
            "BRepAlgoAPI_Common": BRepAlgoAPI_Common,
            "BRepExtrema_DistShapeShape": BRepExtrema_DistShapeShape,
            "BRep_Tool": BRep_Tool,
            "GeomAdaptor_Surface": GeomAdaptor_Surface,
            "GeomAbs_Plane": GeomAbs_Plane,
            "GeomAbs_Cylinder": GeomAbs_Cylinder,
            "gp_Pnt": gp_Pnt,
            "gp_Dir": gp_Dir,
        }
    except Exception as exc:
        raise SystemExit(json.dumps({"error": f"OCP import failed: {exc}"}))


OCP = _import_ocp()


def _bbox_of(shape):
    box = OCP["Bnd_Box"]()
    try:
        OCP["BRepBndLib"].Add_s(shape, box)
    except AttributeError:
        OCP["BRepBndLib"].Add(shape, box)
    return list(box.Get())


def _surface_of(face):
    try:
        return OCP["BRep_Tool"].Surface_s(face)
    except AttributeError:
        return OCP["BRep_Tool"].Surface(face)


def _volume_of(shape):
    props = OCP["GProp_GProps"]()
    try:
        OCP["BRepGProp"].VolumeProperties_s(shape, props)
    except AttributeError:
        OCP["BRepGProp"].VolumeProperties(shape, props)
    return props.Mass()


def _cast_solid(s):
    try:
        return OCP["TopoDS"].Solid_s(s)
    except AttributeError:
        return OCP["TopoDS"].Solid(s)


def _cast_face(s):
    try:
        return OCP["TopoDS"].Face_s(s)
    except AttributeError:
        return OCP["TopoDS"].Face(s)


def _load_solids(step_path):
    reader = OCP["STEPControl_Reader"]()
    status = reader.ReadFile(str(step_path))
    if status != OCP["IFSelect_RetDone"]:
        raise RuntimeError(f"STEP read failed (status={status})")
    reader.TransferRoots()
    shape = reader.Shape()
    solids = []
    exp = OCP["TopExp_Explorer"](shape, OCP["TopAbs_SOLID"])
    while exp.More():
        solids.append(_cast_solid(exp.Current()))
        exp.Next()
    return solids


def _solid_info(solid):
    return {"shape": solid, "volume": _volume_of(solid), "bbox": _bbox_of(solid)}


def _bbox_distance(a, b):
    # sum of center distance and size mismatch
    ca = [(a[i] + a[i + 3]) / 2.0 for i in range(3)]
    cb = [(b[i] + b[i + 3]) / 2.0 for i in range(3)]
    center_dist = math.sqrt(sum((ca[i] - cb[i]) ** 2 for i in range(3)))
    size_a = sum(a[i + 3] - a[i] for i in range(3))
    size_b = sum(b[i + 3] - b[i] for i in range(3))
    return center_dist + abs(size_a - size_b)


def _match_solid_index(part_ref, solids_info, topo_solids):
    """Map a target part reference (name or index) to a STEP solid index."""
    if isinstance(part_ref, int):
        if 0 <= part_ref < len(solids_info):
            return part_ref
        return None
    name = str(part_ref or "").lower()
    if not name:
        return None
    # Match by topology name, then fall back to closest bbox/volume.
    candidates = []
    for idx, ts in enumerate(topo_solids):
        ts_name = str(ts.get("name") or "").lower()
        if ts_name and ts_name == name:
            candidates.append(idx)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # pick the one whose volume best matches the named topology entry
        named_topo = [ts for ts in topo_solids if str(ts.get("name") or "").lower() == name]
        if named_topo and "volume" in named_topo[0]:
            target_vol = float(named_topo[0]["volume"])
            return min(candidates, key=lambda i: abs(solids_info[i]["volume"] - target_vol))
        return candidates[0]
    # No name match: try partial match
    for idx, ts in enumerate(topo_solids):
        ts_name = str(ts.get("name") or "").lower()
        if ts_name and name in ts_name:
            candidates.append(idx)
    if candidates:
        return candidates[0]
    return None


def _vec_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _vec_dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _vec_norm(a):
    return math.sqrt(sum(x * x for x in a))


def _vec_cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _axis_distance(origin_a, dir_a, origin_b, dir_b):
    """Shortest distance between two infinite lines in 3D."""
    cross = _vec_cross(dir_a, dir_b)
    cross_norm = _vec_norm(cross)
    if cross_norm < 1e-9:
        # parallel
        return _vec_norm(_vec_cross(_vec_sub(origin_b, origin_a), dir_a))
    return abs(_vec_dot(_vec_sub(origin_b, origin_a), cross)) / cross_norm


def _axis_angle(dir_a, dir_b):
    dot = abs(_vec_dot(dir_a, dir_b))
    dot = min(1.0, max(0.0, dot))
    return math.degrees(math.acos(dot))


def _cylindrical_faces(shape):
    faces = []
    exp = OCP["TopExp_Explorer"](shape, OCP["TopAbs_FACE"])
    while exp.More():
        face = _cast_face(exp.Current())
        surf = _surface_of(face)
        adaptor = OCP["GeomAdaptor_Surface"](surf)
        if adaptor.GetType() == OCP["GeomAbs_Cylinder"]:
            try:
                cyl = adaptor.Cylinder()
                ax = cyl.Axis()
                origin = [ax.Location().X(), ax.Location().Y(), ax.Location().Z()]
                direction = [ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]
                # normalize direction
                dn = _vec_norm(direction)
                if dn > 0:
                    direction = [d / dn for d in direction]
                faces.append({"face": face, "origin": origin, "direction": direction, "radius": cyl.Radius()})
            except Exception:
                pass
        exp.Next()
    return faces


def _planar_faces(shape):
    faces = []
    shape_center = None
    shape_bbox = _bbox_of(shape)
    if shape_bbox:
        shape_center = [(shape_bbox[i] + shape_bbox[i + 3]) / 2.0 for i in range(3)]
    exp = OCP["TopExp_Explorer"](shape, OCP["TopAbs_FACE"])
    while exp.More():
        face = _cast_face(exp.Current())
        surf = _surface_of(face)
        adaptor = OCP["GeomAdaptor_Surface"](surf)
        if adaptor.GetType() == OCP["GeomAbs_Plane"]:
            try:
                pl = adaptor.Plane()
                ax = pl.Axis()
                normal = [ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]
                nn = _vec_norm(normal)
                if nn > 0:
                    normal = [n / nn for n in normal]
                # Face center from bbox so we can orient the normal outward.
                face_bbox = _bbox_of(face)
                face_center = [(face_bbox[i] + face_bbox[i + 3]) / 2.0 for i in range(3)] if face_bbox else [0, 0, 0]
                if shape_center is not None:
                    to_face = _vec_sub(face_center, shape_center)
                    if _vec_dot(to_face, normal) < 0:
                        normal = [-n for n in normal]
                d = _vec_dot(normal, face_center)
                faces.append({"face": face, "normal": normal, "d": d, "center": face_center})
            except Exception:
                pass
        exp.Next()
    return faces


def _check_no_interference(solid_a, solid_b):
    common = OCP["BRepAlgoAPI_Common"](solid_a, solid_b).Shape()
    vol = _volume_of(common)
    status = "pass" if vol <= 1e-6 else "fail"
    detail = (
        f"no interference (common volume {vol:.6f} mm^3)"
        if status == "pass"
        else f"solids interfere (common volume {vol:.6f} mm^3)"
    )
    return {"status": status, "detail": detail, "measured": round(vol, 6),
            "expected": {"intersection_volume_mm3": 0}}


def _check_clearance(solid_a, solid_b, lo, hi):
    dist_tool = OCP["BRepExtrema_DistShapeShape"](solid_a, solid_b)
    dist_tool.Perform()
    min_dist = dist_tool.Value()
    status = "unknown"
    detail = f"minimum distance {min_dist:.4f}mm"
    measured = round(min_dist, 4)
    expected = {"min_clearance_mm": lo, "max_clearance_mm": hi}
    try:
        val = float(min_dist)
        lo_ok = lo is None or val >= float(lo) - 1e-9
        hi_ok = hi is None or val <= float(hi) + 1e-9
        status = "pass" if lo_ok and hi_ok else "fail"
        detail = (
            f"clearance {val:.4f}mm within [{lo}, {hi}]mm"
            if status == "pass"
            else f"clearance {val:.4f}mm outside [{lo}, {hi}]mm"
        )
    except (TypeError, ValueError):
        pass
    return {"status": status, "detail": detail, "measured": measured, "expected": expected}


def _check_coaxial(solid_a, solid_b, tol_dist, tol_angle):
    cyls_a = _cylindrical_faces(solid_a)
    cyls_b = _cylindrical_faces(solid_b)
    if not cyls_a or not cyls_b:
        return {
            "status": "unknown",
            "detail": "no cylindrical faces found on one or both parts",
            "measured": None,
            "expected": {"max_axis_distance_mm": tol_dist, "max_axis_angle_deg": tol_angle},
        }
    best = None
    for ca in cyls_a:
        for cb in cyls_b:
            dist = _axis_distance(ca["origin"], ca["direction"], cb["origin"], cb["direction"])
            angle = _axis_angle(ca["direction"], cb["direction"])
            score = max(dist / max(tol_dist, 1e-9), angle / max(tol_angle, 1e-9))
            if best is None or score < best["score"]:
                best = {
                    "score": score,
                    "distance": dist,
                    "angle": angle,
                    "radius_a": ca["radius"],
                    "radius_b": cb["radius"],
                }
    assert best is not None
    status = "pass" if best["distance"] <= tol_dist and best["angle"] <= tol_angle else "fail"
    detail = (
        f"best cylinder pair axis distance {best['distance']:.4f}mm, angle {best['angle']:.4f}deg"
        + ("" if status == "pass" else " exceeds tolerance")
    )
    return {
        "status": status,
        "detail": detail,
        "measured": {"axis_distance_mm": round(best["distance"], 4),
                     "axis_angle_deg": round(best["angle"], 4),
                     "radius_a_mm": round(best["radius_a"], 4),
                     "radius_b_mm": round(best["radius_b"], 4)},
        "expected": {"max_axis_distance_mm": tol_dist, "max_axis_angle_deg": tol_angle},
    }


def _check_faces_flush(solid_a, solid_b, tol_dist):
    planes_a = _planar_faces(solid_a)
    planes_b = _planar_faces(solid_b)
    if not planes_a or not planes_b:
        return {
            "status": "unknown",
            "detail": "no planar faces found on one or both parts",
            "measured": None,
            "expected": {"max_plane_distance_mm": tol_dist},
        }
    best = None
    for pa in planes_a:
        for pb in planes_b:
            # Flush faces face each other: normals are approximately opposite.
            dot = _vec_dot(pa["normal"], pb["normal"])
            if dot > -0.98:
                continue
            # Point on plane A: p = normal_a * d_a. Distance to plane B:
            # |normal_b . p - d_b|.
            p_on_a = [pa["normal"][i] * pa["d"] for i in range(3)]
            dist = abs(_vec_dot(pb["normal"], p_on_a) - pb["d"])
            if best is None or dist < best["distance"]:
                best = {"distance": dist, "angle": _axis_angle(pa["normal"], pb["normal"])}
    if best is None:
        return {
            "status": "unknown",
            "detail": "no pair of facing planar faces found",
            "measured": None,
            "expected": {"max_plane_distance_mm": tol_dist},
        }
    status = "pass" if best["distance"] <= tol_dist else "fail"
    detail = (
        f"best facing plane pair distance {best['distance']:.4f}mm"
        + ("" if status == "pass" else " exceeds tolerance")
    )
    return {
        "status": status,
        "detail": detail,
        "measured": {"plane_distance_mm": round(best["distance"], 4),
                     "plane_angle_deg": round(best["angle"], 4)},
        "expected": {"max_plane_distance_mm": tol_dist},
    }


def _run_one(target, solids_info, topo_solids):
    kind = target.get("kind")
    part_a = target.get("part_a")
    part_b = target.get("part_b")
    idx_a = _match_solid_index(part_a, solids_info, topo_solids) if part_a is not None else None
    idx_b = _match_solid_index(part_b, solids_info, topo_solids) if part_b is not None else None

    if idx_a is None:
        return {"status": "unknown", "detail": f"part_a '{part_a}' not resolved in STEP",
                "measured": None, "expected": None}
    if idx_b is None:
        return {"status": "unknown", "detail": f"part_b '{part_b}' not resolved in STEP",
                "measured": None, "expected": None}

    shape_a = solids_info[idx_a]["shape"]
    shape_b = solids_info[idx_b]["shape"]

    tol = float(target.get("tolerance_mm", 1.0))
    if kind == "no_interference":
        return _check_no_interference(shape_a, shape_b)
    if kind == "clearance_within":
        lo = target.get("min_clearance_mm")
        hi = target.get("max_clearance_mm")
        return _check_clearance(shape_a, shape_b, lo, hi)
    if kind == "coaxial_within":
        angle_tol = float(target.get("angle_tolerance_deg", 1.0))
        return _check_coaxial(shape_a, shape_b, tol, angle_tol)
    if kind == "faces_flush_within":
        return _check_faces_flush(shape_a, shape_b, tol)
    return {"status": "unknown", "detail": f"unsupported B-Rep target kind '{kind}'",
            "measured": None, "expected": None}


def main():
    step_path = sys.argv[1]
    targets = json.loads(sys.argv[2])
    topo_map = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    try:
        ocp_solids = _load_solids(step_path)
    except Exception as exc:
        raise SystemExit(json.dumps({"error": f"failed to load STEP: {exc}"}))

    if not ocp_solids:
        raise SystemExit(json.dumps({"error": "STEP file contains no solids"}))

    solids_info = [_solid_info(s) for s in ocp_solids]
    topo_solids = [e for e in topo_map.get("entities", []) if isinstance(e, dict) and e.get("type") == "solid"]

    results = {}
    for idx, target in enumerate(targets):
        tid = target.get("id") or f"target_{idx:03d}"
        try:
            results[tid] = _run_one(target, solids_info, topo_solids)
        except Exception as exc:
            results[tid] = {"status": "unknown", "detail": f"B-Rep check error: {exc}",
                            "measured": None, "expected": None}

    print(json.dumps(results))


if __name__ == "__main__":
    main()
'''


def run_brep_checks(
    step_path: str | Path,
    targets: list[dict[str, Any]],
    topology_map: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run exact B-Rep checks on a STEP file in an isolated subprocess.

    Args:
        step_path: path to the STEP file (e.g. geometry/generated.step).
        targets: list of target specs requiring exact geometry
            (no_interference, coaxial_within, faces_flush_within, clearance_within).
        topology_map: optional topology map used to map part names to STEP solids.
        timeout: subprocess timeout in seconds.

    Returns:
        Dict mapping target id -> result dict with ``status``, ``detail``,
        ``measured``, ``expected``. If the runner fails entirely, an ``error``
        key is included and every target is marked ``unknown``.
    """
    step_path = Path(step_path)
    if not step_path.exists():
        return {
            "error": f"STEP file not found: {step_path}",
            "results": {str(t.get("id") or f"target_{i:03d}"): {
                "status": "unknown",
                "detail": "STEP file not found",
                "measured": None,
                "expected": None,
            } for i, t in enumerate(targets)},
        }

    brep_targets = [t for t in targets if isinstance(t, dict) and t.get("kind") in (
        "no_interference", "coaxial_within", "faces_flush_within", "clearance_within",
    )]
    if not brep_targets:
        return {"results": {}}

    timeout = max(1, min(int(timeout), 600))

    with tempfile.TemporaryDirectory() as tmpdir:
        runner_path = Path(tmpdir) / "brep_runner.py"
        runner_path.write_text(_BREP_RUNNER_TEMPLATE, encoding="utf-8")

        cmd = [
            sys.executable,
            str(runner_path),
            str(step_path),
            json.dumps(brep_targets),
            json.dumps(topology_map or {}),
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return {
                "error": f"B-Rep checks timed out after {timeout}s",
                "results": {str(t.get("id") or f"target_{i:03d}"): {
                    "status": "unknown",
                    "detail": f"B-Rep check timed out after {timeout}s",
                    "measured": None,
                    "expected": None,
                } for i, t in enumerate(brep_targets)},
            }
        except Exception as exc:
            return {
                "error": f"failed to launch B-Rep checker: {exc}",
                "results": {str(t.get("id") or f"target_{i:03d}"): {
                    "status": "unknown",
                    "detail": f"failed to launch B-Rep checker: {exc}",
                    "measured": None,
                    "expected": None,
                } for i, t in enumerate(brep_targets)},
            }

        if proc.returncode != 0:
            err = (proc.stderr or "")[-2000:]
            return {
                "error": f"B-Rep checker failed (exit {proc.returncode}): {err}",
                "results": {str(t.get("id") or f"target_{i:03d}"): {
                    "status": "unknown",
                    "detail": f"B-Rep checker failed: {err}",
                    "measured": None,
                    "expected": None,
                } for i, t in enumerate(brep_targets)},
            }

        try:
            results = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {
                "error": f"B-Rep checker returned invalid JSON: {exc}; stdout={proc.stdout[:500]}",
                "results": {str(t.get("id") or f"target_{i:03d}"): {
                    "status": "unknown",
                    "detail": "B-Rep checker returned invalid output",
                    "measured": None,
                    "expected": None,
                } for i, t in enumerate(brep_targets)},
            }

        if "error" in results:
            return {
                "error": results["error"],
                "results": {str(t.get("id") or f"target_{i:03d}"): {
                    "status": "unknown",
                    "detail": f"B-Rep checker error: {results['error']}",
                    "measured": None,
                    "expected": None,
                } for i, t in enumerate(brep_targets)},
            }

        return {"results": results}
