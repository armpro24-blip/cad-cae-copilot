"""Constants dimensioned inside a `with BuildPart() as bp:` block bind to that part.

Measured failure this guards (dogfood, 2026-08-10): the same-line usage scan
only sees `<var> = ...CONST...` assignments, so for the dominant build123d
idiom — and the exact shape of the Engineering Mode example in AGENTS.md —
the primary body's dimensions had NO usage binding:

    with BuildPart() as bp:
        Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS)   # not an assignment
    base_plate = bp.part
    base_plate.label = "base_plate"

An incidental use by another part then captured the constant outright:

    rib_main = rib_main.moved(Location((0, 0, PLATE_THICKNESS)))

so PLATE_THICKNESS was exposed as rib_main's `thickness_mm`. Editing that
"rib thickness" resized the PLATE (regression_diff verdict
`collateral_change`), and a sizing sweep on it would have swept the wrong body.
"""
from __future__ import annotations

from app.cad_generation import _constants_to_part_labels, _topology_to_feature_graph


_BRACKET_SOURCE = """\
from build123d import *

PLATE_LENGTH = 120.0
PLATE_WIDTH = 80.0
PLATE_THICKNESS = 8.0
RIB_HEIGHT = 25.0
RIB_THICKNESS = 5.0

with BuildPart() as bp:
    Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((10, 10, PLATE_THICKNESS)):
        Hole(radius=2.5, depth=PLATE_THICKNESS)
base_plate = bp.part
base_plate.label = "base_plate"

rib_main = rib(RIB_HEIGHT * 1.6, RIB_HEIGHT, RIB_THICKNESS)
rib_main = rib_main.moved(Location((0, 0, PLATE_THICKNESS)))
rib_main.label = "rib_main"

result = Compound(children=[base_plate, rib_main])
"""


def test_builder_block_constants_bind_to_their_part() -> None:
    mapping = _constants_to_part_labels(
        _BRACKET_SOURCE,
        ["PLATE_LENGTH", "PLATE_WIDTH", "PLATE_THICKNESS", "RIB_HEIGHT", "RIB_THICKNESS"],
    )

    # Dimensions of the builder-built body belong to that body.
    assert mapping["PLATE_LENGTH"] == {"base_plate"}
    assert mapping["PLATE_WIDTH"] == {"base_plate"}
    # Genuinely used by both (plate thickness + rib placement) -> shared, which
    # routes it to Global Parameters rather than mis-attributing it to the rib.
    assert mapping["PLATE_THICKNESS"] == {"base_plate", "rib_main"}
    # The rib's own dimensions stay on the rib.
    assert mapping["RIB_HEIGHT"] == {"rib_main"}
    assert mapping["RIB_THICKNESS"] == {"rib_main"}


def test_plate_thickness_is_not_exposed_as_the_ribs_thickness() -> None:
    """The user-visible payoff: no `thickness_mm` on the rib that resizes the plate."""
    topology = {
        "entities": [
            {"id": "body_001", "type": "solid", "name": "base_plate",
             "bounding_box": [-60, -40, 0, 60, 40, 8], "volume": 76800.0},
            {"id": "body_002", "type": "solid", "name": "rib_main",
             "bounding_box": [-20, -2.5, 8, 20, 2.5, 33], "volume": 5000.0},
        ],
    }
    graph = _topology_to_feature_graph(topology, source_code=_BRACKET_SOURCE, model_kind="mechanical")
    by_name = {f.get("name"): f for f in graph["features"]}

    rib_params = by_name["rib_main"].get("parameters") or {}
    rib_constants = {
        p.get("cad_parameter_name") for p in rib_params.values() if isinstance(p, dict)
    }
    assert "PLATE_THICKNESS" not in rib_constants, rib_params
    assert {"RIB_HEIGHT", "RIB_THICKNESS"} <= rib_constants

    plate_params = by_name["base_plate"].get("parameters") or {}
    plate_constants = {
        p.get("cad_parameter_name") for p in plate_params.values() if isinstance(p, dict)
    }
    assert {"PLATE_LENGTH", "PLATE_WIDTH"} <= plate_constants

    # Every declared constant stays addressable somewhere in the graph.
    all_constants: set[str] = set()
    for feature in graph["features"]:
        for param in (feature.get("parameters") or {}).values():
            if isinstance(param, dict) and param.get("cad_parameter_name"):
                all_constants.add(param["cad_parameter_name"])
    assert "PLATE_THICKNESS" in all_constants


def test_unlabelled_builder_block_is_ignored() -> None:
    """No label to bind to -> no usage binding invented (name matching still applies)."""
    source = (
        "from build123d import *\n"
        "BEAM_LENGTH = 100.0\n"
        "with BuildPart() as bp:\n"
        "    Box(BEAM_LENGTH, 20, 10)\n"
        "result = bp.part\n"
    )
    assert _constants_to_part_labels(source, ["BEAM_LENGTH"]) == {}
