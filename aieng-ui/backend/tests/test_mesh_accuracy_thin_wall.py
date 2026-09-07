"""The accuracy band must judge the wall that bends, not the bounding box (#487).

Measured on the dogfood bracket (2026-08-11): a 120 x 80 x 6 mm plate carrying a
25 mm rib, meshed at 3 mm, reported

    accuracy: {band: "reliable", thinnest_extent_mm: 29.39,
               elements_through_thinnest: 9.8}

29.39 mm is the bbox Z (plate + rib). The part that actually carries the bending
gradient is the 6 mm plate — 2 elements through it. The band was therefore
optimistic in exactly the canonical thin-plate case, the same non-conservative
direction the C3D10 default exists to prevent.
"""
from __future__ import annotations

import pytest

from app.simulation_runner import (
    _BboxContradicted,
    assess_mesh_accuracy,
    thinnest_body_extent,
)

_BRACKET = {
    "entities": [
        # plate: 120 x 80 x 6 -> thinnest 6
        {"id": "body_001", "type": "solid", "name": "base_plate",
         "bounding_box": [-60, -40, 0, 60, 40, 6]},
        # rib: 40 x 5 x 25 -> thinnest 5
        {"id": "body_002", "type": "solid", "name": "rib_main",
         "bounding_box": [-20, -2.5, 6, 20, 2.5, 31]},
    ]
}

# A cube of nodes spanning the whole bracket — what the bbox rule would see.
_NODES = {
    1: (-60.0, -40.0, 0.0), 2: (60.0, -40.0, 0.0), 3: (60.0, 40.0, 0.0),
    4: (-60.0, 40.0, 0.0), 5: (0.0, 0.0, 31.0),
}


def test_thinnest_body_wins_over_the_bounding_box() -> None:
    found = thinnest_body_extent(_BRACKET)
    assert found is not None
    thickness, name = found
    assert thickness == 5.0 and name == "rib_main", "the rib's 5 mm web is the thinnest wall"


def test_assembly_wrappers_are_ignored() -> None:
    """A labelled Compound is not a physical wall."""
    topo = {"entities": [
        {"id": "body_000", "type": "solid", "name": "assembly", "assembly": True,
         "bounding_box": [0, 0, 0, 1, 1, 1]},
        {"id": "body_001", "type": "solid", "name": "plate",
         "bounding_box": [0, 0, 0, 100, 50, 8]},
    ]}
    assert thinnest_body_extent(topo) == (8.0, "plate")


def test_no_solids_returns_none_so_the_bbox_rule_still_applies() -> None:
    assert thinnest_body_extent({"entities": []}) is None
    assert thinnest_body_extent(None) is None


def test_band_is_measured_through_the_governing_body() -> None:
    accuracy = assess_mesh_accuracy(_NODES, "C3D10", 3.0, thinnest_body_extent(_BRACKET))

    assert accuracy["measured_on"] == "thinnest_body"
    assert accuracy["governing_body"] == "rib_main"
    assert accuracy["thinnest_extent_mm"] == 5.0
    assert accuracy["elements_through_thinnest"] == 1.67
    assert "rib_main (5 mm thick)" in accuracy["reason"]


def test_the_old_bbox_reading_was_four_times_more_optimistic() -> None:
    """Pins the actual regression: same mesh, same model, two rulers."""
    bbox_based = assess_mesh_accuracy(_NODES, "C3D10", 3.0)
    wall_based = assess_mesh_accuracy(_NODES, "C3D10", 3.0, thinnest_body_extent(_BRACKET))

    assert bbox_based["measured_on"] == "model_bounding_box"
    assert bbox_based["elements_through_thinnest"] > wall_based["elements_through_thinnest"] * 4
    assert bbox_based["thinnest_extent_mm"] == 31.0  # the whole bracket height


def test_a_coarse_mesh_on_a_thin_wall_is_now_caught() -> None:
    """The case the bbox rule hid: 8 mm elements on a 5 mm web."""
    accuracy = assess_mesh_accuracy(_NODES, "C3D10", 8.0, thinnest_body_extent(_BRACKET))

    assert accuracy["band"] == "unreliable"
    assert accuracy["reliable_for_bending"] is False
    assert "UNDER-predicted" in accuracy["reason"]
    assert accuracy["recommended_action"]


def test_fallback_keeps_working_without_topology() -> None:
    accuracy = assess_mesh_accuracy(_NODES, "C3D10", 3.0, None)
    assert accuracy["measured_on"] == "model_bounding_box"
    assert "thinnest extent" in accuracy["reason"]


# ── the per-body fix does not reach a HOLLOW body ───────────────────────────
#
# Found by running the promised task across a family of part shapes rather than
# one beam. A 3 mm-walled housing meshed at 6 mm reported
#
#     band: "reliable", thinnest_extent_mm: 40.0, reliable_for_bending: true
#     reason: "~6.7 order-2 element(s) through housing (40 mm thick) ..."
#
# 40 mm is the OUTER envelope of a hollow shell. About 0.5 elements crossed the
# real wall, so the credibility downgrade this band exists to trigger was
# unreachable for every thin-walled part — which is exactly what the `housing()`
# helper builds. Same `by-construction` shape as the bracket case above: the
# proxy is degenerate for the geometry at hand.

_HOUSING = {
    "entities": [
        # 80 x 60 x 40 outer, 3 mm walls, open top: V = 44148, A = 30272.
        # 2V/A = 2.92 mm against a 40 mm box side — the two proxies disagree
        # by 13x, so the box cannot be a wall.
        {"id": "body_001", "type": "solid", "name": "housing",
         "bounding_box": [-40, -30, 0, 40, 30, 40],
         "volume": 44148.0, "area": 30272.0},
    ]
}


def test_a_hollow_body_refuses_to_report_a_band() -> None:
    with pytest.raises(_BboxContradicted) as excinfo:
        thinnest_body_extent(_HOUSING)

    exc = excinfo.value
    assert exc.body == "housing"
    assert exc.bbox_mm == 40.0
    # 2 * 44148 / 30272 = 2.917 mm; the modelled wall is 3.0 mm.
    assert 2.8 < exc.wall_mm < 3.1, exc.wall_mm


def test_a_solid_body_keeps_its_bounding_box_verdict() -> None:
    """The calibrated shapes must not move — the beam is validated at 99% of theory.

    beam 120 x 20 x 10: V = 24000, A = 7600, so 2V/A = 6.32 mm against a 10 mm
    box side — a ratio of 0.63. A tapered gusset lands at the same 0.63. Both
    keep the box, because `2V/A` conflates taper with hollowness and would
    mis-rule them; only a flat contradiction is treated as one.
    """
    beam = {
        "entities": [
            {"id": "body_001", "type": "solid", "name": "beam",
             "bounding_box": [-60, -10, 0, 60, 10, 10],
             "volume": 24000.0, "area": 7600.0},
        ]
    }
    found = thinnest_body_extent(beam)
    assert found == (10.0, "beam")


def test_a_body_with_no_volume_recorded_keeps_the_old_behaviour() -> None:
    """Absent evidence is not evidence of hollowness — same discipline as face_signatures."""
    assert thinnest_body_extent(_BRACKET) == (5.0, "rib_main")
