"""Stable face identity across CAD rebuilds.

Face ids are assigned by enumeration order (``face_001``, ``face_002``, …).
Measured behaviour of that scheme under the three kinds of edit:

- rebuild with identical geometry: ids stable (6/6 on the reference beam)
- dimensional edit:                ids stable (6/6)
- **topology-changing edit** (e.g. cutting a hole in the same body): OCCT
  re-enumerates and the ids are silently SHUFFLED — measured on the beam,
  ``face_002`` (the +X load face) came back denoting the −Y side face.

A CAE load bound to ``face_002`` would then be applied to the wrong face. The
character re-verification from PR #475 *refuses* that run (normal mismatch),
which is safe but still loses the binding. This module preserves the identity
instead: when new topology is about to be persisted, previously-known faces are
re-identified and keep their ids; only genuinely new faces get new ids.

Matching is deliberately conservative, in two tiers:

1. **exact** — same surface type, same rounded normal/axis, same rounded
   bounding box, same rounded area. Re-identifies untouched faces (the common
   case: an appended part leaves every existing face byte-identical).
2. **character** — same surface type and same orientation (normal/axis), when
   that combination is UNIQUE among the unmatched faces on both sides.
   Re-identifies faces that an edit legitimately moved or resized (a thickness
   change resizes the end faces; a through-hole pierces a face and shrinks its
   area) without ever guessing between lookalikes.

Anything ambiguous or unmatched gets a fresh id, and **retired ids are never
reused** — a stale binding to a removed face must fail honestly, not silently
hit whatever new face inherited the number.

Edges are NOT stabilized (nothing binds to edges today); their adjacency lists
are remapped so they keep pointing at the right faces.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ["stabilize_topology_face_ids"]

_FACE_ID_RE = re.compile(r"^face_(\d+)$")


def _faces(topology: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e for e in (topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "face" and e.get("id")
    ]


def _rounded(value: Any, digits: int) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    if isinstance(value, list):
        return tuple(_rounded(v, digits) for v in value)
    return None


def _orientation(face: dict[str, Any], digits: int) -> Any:
    """The direction a face points: plane normal, or cylinder/cone axis."""
    normal = face.get("normal")
    if isinstance(normal, list):
        return _rounded(normal, digits)
    axis = face.get("axis")
    if isinstance(axis, dict) and isinstance(axis.get("direction"), list):
        return _rounded(axis["direction"], digits)
    if isinstance(axis, list):
        return _rounded(axis, digits)
    return None


def _exact_key(face: dict[str, Any]) -> tuple | None:
    bbox = face.get("bounding_box")
    if not isinstance(bbox, list) or len(bbox) != 6:
        return None
    return (
        str(face.get("surface_type") or ""),
        _orientation(face, 6),
        _rounded(bbox, 4),
        _rounded(face.get("area"), 3),
    )


def _character_key(face: dict[str, Any]) -> tuple | None:
    orientation = _orientation(face, 3)
    if orientation is None:
        return None
    return (str(face.get("surface_type") or ""), orientation)


def _unique_matches(
    old_faces: list[dict[str, Any]],
    new_faces: list[dict[str, Any]],
    key_fn,
) -> dict[str, str]:
    """new_id -> old_id for keys that appear EXACTLY once on each side."""
    old_by_key: dict[tuple, list[str]] = {}
    for f in old_faces:
        k = key_fn(f)
        if k is not None:
            old_by_key.setdefault(k, []).append(str(f["id"]))
    new_by_key: dict[tuple, list[str]] = {}
    for f in new_faces:
        k = key_fn(f)
        if k is not None:
            new_by_key.setdefault(k, []).append(str(f["id"]))
    out: dict[str, str] = {}
    for k, old_ids in old_by_key.items():
        new_ids = new_by_key.get(k)
        if new_ids and len(old_ids) == 1 and len(new_ids) == 1:
            out[new_ids[0]] = old_ids[0]
    return out


def _max_face_index(*topologies: dict[str, Any]) -> int:
    top = 0
    for topo in topologies:
        for f in _faces(topo):
            m = _FACE_ID_RE.match(str(f["id"]))
            if m:
                top = max(top, int(m.group(1)))
    return top


def stabilize_topology_face_ids(
    previous_topology: dict[str, Any] | None,
    new_topology: dict[str, Any],
    feature_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-identify previously-known faces in ``new_topology`` and keep their ids.

    MUTATES ``new_topology`` (and ``feature_graph`` if given) IN PLACE, so the
    caller's in-memory objects stay identical to what gets persisted — the
    response an agent reads and the package on disk must never disagree about
    what ``face_002`` means.

    Returns a report::

        {applied, preserved, renamed: {before: after}, fresh_ids, retired_ids,
         ambiguous_unmatched_new}
    """
    report: dict[str, Any] = {
        "applied": False,
        "preserved": 0,
        "renamed": {},
        "fresh_ids": [],
        "retired_ids": [],
    }
    if not isinstance(previous_topology, dict) or not isinstance(new_topology, dict):
        return report
    old_faces = _faces(previous_topology)
    new_faces = _faces(new_topology)
    if not old_faces or not new_faces:
        return report

    # Tier 1: exact geometric identity.
    match = _unique_matches(old_faces, new_faces, _exact_key)

    # Tier 2: unique character among what is still unmatched on BOTH sides.
    matched_old = set(match.values())
    rest_old = [f for f in old_faces if str(f["id"]) not in matched_old]
    rest_new = [f for f in new_faces if str(f["id"]) not in match]
    match.update(_unique_matches(rest_old, rest_new, _character_key))

    # Assign fresh ids to unmatched new faces — beyond every id ever seen, so a
    # retired id can never be silently inherited by a different face.
    next_index = _max_face_index(previous_topology, new_topology) + 1
    remap: dict[str, str] = {}
    for f in new_faces:
        nid = str(f["id"])
        if nid in match:
            remap[nid] = match[nid]
        else:
            old_ids = {str(o["id"]) for o in old_faces}
            if nid in old_ids:
                # This number belonged to some previous face we could NOT
                # re-identify with this one — do not let it keep the number.
                remap[nid] = f"face_{next_index:03d}"
                next_index += 1
            else:
                remap[nid] = nid  # brand-new number, no history to collide with

    if all(k == v for k, v in remap.items()):
        # Enumeration already agrees with history (identical rebuild, pure
        # dimensional edit, or an append that only added faces). Nothing to do.
        report["preserved"] = len(match)
        return report

    # Apply in place: face ids, edge adjacency, feature_graph face refs.
    for e in new_topology.get("entities") or []:
        if not isinstance(e, dict):
            continue
        if e.get("type") == "face" and str(e.get("id")) in remap:
            e["id"] = remap[str(e["id"])]
        if e.get("type") == "edge":
            faces_list = e.get("faces")
            if isinstance(faces_list, list):
                e["faces"] = [remap.get(str(fid), fid) for fid in faces_list]
    if isinstance(feature_graph, dict):
        for feat in feature_graph.get("features") or []:
            if not isinstance(feat, dict):
                continue
            geo = feat.get("geometry_refs")
            if isinstance(geo, dict) and isinstance(geo.get("faces"), list):
                geo["faces"] = [remap.get(str(fid), fid) for fid in geo["faces"]]

    old_all = {str(f["id"]) for f in old_faces}
    surviving = set(match.values())
    report.update(
        applied=True,
        preserved=len(match),
        renamed={n: o for n, o in remap.items() if n != o},
        # New relative to the PREVIOUS topology — whether or not the id happened
        # to need renaming (a brand-new face keeping its fresh number is still new).
        fresh_ids=sorted(v for v in remap.values() if v not in old_all),
        retired_ids=sorted(old_all - surviving),
    )
    return report