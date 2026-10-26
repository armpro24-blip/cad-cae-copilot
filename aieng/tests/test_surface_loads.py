"""A total force on a face is a traction, not an equal share per node.

Measured through the product path on a uniform-tension bar — an exact patch
test, sigma_xx = F/A everywhere — the old equal-per-node `*CLOAD` on the
quadratic (C3D10) end face put the model's peak von Mises stress at the loaded
face, +295 % of the exact value, and the tip displacement +11.4 %. With the
consistent split below the same run is exact to 0.001 %.
"""
from __future__ import annotations

import pytest

from aieng.simulation import deck_generator as dg
from aieng.simulation import surface_loads as sl

# One quadratic tet: corners 1-4, mid-sides 5-10 in Abaqus/CalculiX order
# 5:(1-2) 6:(2-3) 7:(3-1) 8:(1-4) 9:(2-4) 10:(3-4).
_TET10 = """*NODE
1, 0, 0, 0
2, 2, 0, 0
3, 0, 2, 0
4, 0, 0, 2
5, 1, 0, 0
6, 1, 1, 0
7, 0, 1, 0
8, 0, 0, 1
9, 1, 0, 1
10, 0, 1, 1
*ELEMENT, TYPE=C3D10, ELSET=EALL
1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
*NSET, NSET=BOTTOM
1, 2, 3, 5, 6, 7
"""


def _bottom():
    nodes, elements, nsets = sl.parse_mesh(_TET10)
    facets = sl.facets_within(sl.free_facets(elements), nsets["BOTTOM"])
    return nodes, facets


def test_quadratic_face_puts_the_traction_on_the_midside_nodes() -> None:
    nodes, facets = _bottom()
    assert len(facets) == 1
    weights = sl.consistent_weights(facets, nodes)
    # Zienkiewicz & Taylor: uniform traction on a 6-node triangle -> corners 0,
    # mid-sides A/3 each. The equal split gave every node 1/6.
    assert weights == pytest.approx({5: 1 / 3, 6: 1 / 3, 7: 1 / 3})


def test_the_face_number_and_outward_normal_follow_calculix() -> None:
    nodes, facets = _bottom()
    (facet,) = facets
    assert facet.face_number == 1  # ccx tet face 1 = corners 1-2-3
    assert sl.outward_normal(facet, nodes) == pytest.approx((0.0, 0.0, -1.0))
    assert sl.facet_area(facet, nodes) == pytest.approx(2.0)


def test_a_face_shared_by_two_elements_is_not_a_surface() -> None:
    two = """*NODE
1, 0, 0, 0
2, 1, 0, 0
3, 0, 1, 0
4, 0, 0, 1
5, 0, 0, -1
*ELEMENT, TYPE=C3D4
1, 1, 2, 3, 4
2, 1, 3, 2, 5
"""
    _nodes, elements, _ = sl.parse_mesh(two)
    keys = {frozenset(f.corners) for f in sl.free_facets(elements)}
    assert frozenset((1, 2, 3)) not in keys  # the glued interface
    assert len(keys) == 6


def test_the_deck_emits_per_node_consistent_loads_and_pressure() -> None:
    surfaces = dg._LoadSurfaces(_TET10)
    warnings: list[str] = []
    block = dg._step_block(
        "static", None,
        [{"target": "BOTTOM", "dof": 3, "value": -300.0},
         {"target": "BOTTOM", "pressure": 2.5}],
        warnings, surfaces,
    )
    text = "\n".join(block)
    assert "5, 3, -100" in text and "6, 3, -100" in text and "7, 3, -100" in text
    assert "1, 3," not in text  # corners carry nothing
    assert "*DLOAD" in text and "1, P1, 2.5" in text
    assert warnings == []


def test_a_node_set_with_no_face_keeps_the_equal_split_and_says_so() -> None:
    surfaces = dg._LoadSurfaces(_TET10 + "*NSET, NSET=EDGE\n1, 2\n")
    warnings: list[str] = []
    block = dg._step_block(
        "static", None, [{"target": "EDGE", "dof": 1, "value": 10.0}], warnings, surfaces
    )
    assert "EDGE, 1, 5.000000" in block
    assert any("covers no tetrahedral element face" in w for w in warnings)


def test_a_pressure_with_no_face_is_refused_not_dropped() -> None:
    surfaces = dg._LoadSurfaces(_TET10 + "*NSET, NSET=EDGE\n1, 2\n")
    with pytest.raises(dg.DeckGenerationError, match="no surface to apply a pressure"):
        dg._step_block("static", None, [{"target": "EDGE", "pressure": 1.0}], [], surfaces)


def test_parsed_pressure_loads_reach_the_emitter() -> None:
    out = dg._normalize_parsed_loads(
        [{"id": "p", "type": "pressure", "target": "S", "pressure_mpa": -10}]
    )
    assert out == [{"id": "p", "target": "S", "pressure": -10.0}]
