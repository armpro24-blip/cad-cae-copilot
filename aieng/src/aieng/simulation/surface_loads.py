"""Distributed loads on solid-element faces: traction-consistent nodal forces and pressure.

A load the setup states as a TOTAL force on a face is a uniform traction
``t = F / A`` over that face. Turning it into nodal forces is not a matter of
dividing by the node count. For a uniform traction on a flat triangle of area
``A`` the work-equivalent ("consistent") nodal forces are (Zienkiewicz & Taylor,
*The Finite Element Method*, Vol. 1, 6th ed., 2005, consistent load vector
``f = ∫ N^T t dA`` evaluated for the linear and quadratic triangle):

- 3-node face (C3D4):  ``A/3`` at each corner;
- 6-node face (C3D10): ``0`` at each corner, ``A/3`` at each mid-side node.

Dividing the total equally over every node of a quadratic face puts roughly
four times too much force on each corner and too little on each mid-side node:
the load is still in equilibrium, but it is a lumpy, wrong traction. Measured
through the product path on a uniform-tension bar (an exact patch test whose
stress is ``F/A`` everywhere) that produced the model's peak stress at the
loaded face. This module computes the consistent distribution instead, from the
mesh's own free faces, so it needs no knowledge of how the node set was built.

Pressure (a load that follows the surface normal, e.g. NAFEMS LE1's 10 MPa on
an elliptical edge) cannot be expressed as one force direction at all; it is
emitted as CalculiX ``*DLOAD`` ``P<n>`` face loads, which ccx integrates itself.

Only tetrahedra are handled — the product mesher (gmsh) emits C3D4/C3D10. A node
set on another element type returns no facets, and callers fall back to the
equal split *with a warning*, which is what an imported hex deck always got.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: CalculiX face numbering for tetrahedra (ccx manual, element C3D4/C3D10 face
#: definitions used by *DLOAD/*SURFACE): face 1 = 1-2-3, 2 = 1-4-2, 3 = 2-4-3,
#: 4 = 3-4-1 (1-based corner numbers). Mid-side nodes of C3D10 follow the
#: Abaqus/CalculiX order 5:(1-2) 6:(2-3) 7:(3-1) 8:(1-4) 9:(2-4) 10:(3-4).
_TET_FACE_CORNERS: tuple[tuple[int, int, int], ...] = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))
_TET10_EDGE_MIDSIDE: dict[frozenset[int], int] = {
    frozenset((0, 1)): 4,
    frozenset((1, 2)): 5,
    frozenset((2, 0)): 6,
    frozenset((0, 3)): 7,
    frozenset((1, 3)): 8,
    frozenset((2, 3)): 9,
}
_TET_TYPES = {"C3D4": 4, "C3D10": 10}

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Facet:
    """One free (boundary) face of a solid tetrahedron."""

    element_id: int
    face_number: int  # CalculiX face label, 1..4
    corners: tuple[int, int, int]
    midsides: tuple[int, ...]  # empty for C3D4
    opposite: int  # the element's fourth corner — fixes the outward side


def parse_mesh(text: str) -> tuple[dict[int, Vec], list[tuple[int, str, list[int]]], dict[str, list[int]]]:
    """``(nodes, solid_tets, nsets)`` from CalculiX deck text.

    Tolerates gmsh's comma-attached lowercase headers and Abaqus continuation
    lines (a data line ending in ``,``). Non-tetrahedral element blocks are
    skipped; ``*NSET`` lists are read verbatim (``GENERATE`` ranges expanded).
    """
    nodes: dict[int, Vec] = {}
    elements: list[tuple[int, str, list[int]]] = []
    nsets: dict[str, list[int]] = {}
    section: str | None = None
    etype = ""
    nset_name = ""
    generate = False
    pending: list[str] = []

    def _flush_element(tokens: list[str]) -> None:
        values = [int(t) for t in tokens if t.strip()]
        if len(values) == _TET_TYPES[etype] + 1:
            elements.append((values[0], etype, values[1:]))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            pending = []
            head = line.upper().replace(" ", "")
            params = {
                p.split("=", 1)[0]: p.split("=", 1)[1] if "=" in p else ""
                for p in line.replace(" ", "").split(",")[1:]
            }
            upper_params = {k.upper(): v for k, v in params.items()}
            keyword = head.split(",")[0]
            section = None
            if keyword == "*NODE" and not any(k in head for k in ("FILE", "PRINT", "OUTPUT")):
                section = "node"
            elif keyword == "*ELEMENT":
                etype = upper_params.get("TYPE", "").upper()
                section = "element" if etype in _TET_TYPES else None
            elif keyword == "*NSET":
                nset_name = upper_params.get("NSET", "")
                generate = "GENERATE" in upper_params
                if nset_name:
                    nsets.setdefault(nset_name, [])
                    section = "nset"
            continue
        if section == "node":
            parts = line.split(",")
            if len(parts) >= 4:
                try:
                    nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    pass
        elif section == "element":
            pending.extend(t for t in line.split(",") if t.strip())
            if not line.endswith(","):
                try:
                    _flush_element(pending)
                except ValueError:
                    pass
                pending = []
        elif section == "nset":
            ids = [t.strip() for t in line.split(",") if t.strip()]
            try:
                if generate and len(ids) >= 2:
                    start, stop = int(ids[0]), int(ids[1])
                    step = int(ids[2]) if len(ids) > 2 else 1
                    nsets[nset_name].extend(range(start, stop + 1, step))
                else:
                    nsets[nset_name].extend(int(i) for i in ids)
            except ValueError:
                pass
    return nodes, elements, nsets


def free_facets(elements: Iterable[tuple[int, str, list[int]]]) -> list[Facet]:
    """Tetrahedron faces that belong to exactly one element — the model boundary.

    A face shared by two elements is interior. That includes the glued
    interface between two bodies after gmsh's OCC fragment, so a load can never
    land on a surface that is inside the material.
    """
    seen: dict[frozenset[int], list[Facet]] = {}
    for eid, etype, conn in elements:
        corners4 = conn[:4]
        for number, (a, b, c) in enumerate(_TET_FACE_CORNERS, start=1):
            opposite = ({0, 1, 2, 3} - {a, b, c}).pop()
            mids: tuple[int, ...] = ()
            if etype == "C3D10":
                mids = tuple(
                    conn[_TET10_EDGE_MIDSIDE[frozenset(pair)]] for pair in ((a, b), (b, c), (c, a))
                )
            facet = Facet(
                element_id=eid,
                face_number=number,
                corners=(corners4[a], corners4[b], corners4[c]),
                midsides=mids,
                opposite=corners4[opposite],
            )
            seen.setdefault(frozenset(facet.corners), []).append(facet)
    return [group[0] for group in seen.values() if len(group) == 1]


def facets_within(facets: Iterable[Facet], node_ids: Iterable[int]) -> list[Facet]:
    """Free facets whose every node is in ``node_ids``."""
    members = set(node_ids)
    return [
        f for f in facets
        if all(n in members for n in f.corners) and all(n in members for n in f.midsides)
    ]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def facet_area(facet: Facet, nodes: dict[int, Vec]) -> float:
    """Flat-triangle area through the facet's corners."""
    p0, p1, p2 = (nodes[n] for n in facet.corners)
    cx, cy, cz = _cross(_sub(p1, p0), _sub(p2, p0))
    return 0.5 * (cx * cx + cy * cy + cz * cz) ** 0.5


def outward_normal(facet: Facet, nodes: dict[int, Vec]) -> Vec:
    """Unit normal of the facet pointing away from its element."""
    p0, p1, p2 = (nodes[n] for n in facet.corners)
    n = _cross(_sub(p1, p0), _sub(p2, p0))
    d = _sub(nodes[facet.opposite], p0)
    if n[0] * d[0] + n[1] * d[1] + n[2] * d[2] > 0:
        n = (-n[0], -n[1], -n[2])
    mag = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
    return (n[0] / mag, n[1] / mag, n[2] / mag)


def consistent_weights(facets: Iterable[Facet], nodes: dict[int, Vec]) -> dict[int, float]:
    """Per-node share of a uniform traction over ``facets``; shares sum to 1.

    Empty when the facets carry no area (a node set that is a point or a line
    is a concentrated load, not a traction — the caller keeps the equal split).
    """
    raw: dict[int, float] = {}
    total = 0.0
    for facet in facets:
        area = facet_area(facet, nodes)
        if area <= 0.0:
            continue
        total += area
        carriers = facet.midsides if facet.midsides else facet.corners
        for node in carriers:
            raw[node] = raw.get(node, 0.0) + area / 3.0
    if total <= 0.0:
        return {}
    return {node: share / total for node, share in raw.items()}


def surface_area(facets: Iterable[Facet], nodes: dict[int, Vec]) -> float:
    return sum(facet_area(f, nodes) for f in facets)
