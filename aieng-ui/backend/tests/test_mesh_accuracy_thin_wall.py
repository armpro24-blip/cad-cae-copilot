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

from app.simulation_runner import assess_mesh_accuracy, thinnest_body_extent

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
