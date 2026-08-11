"""Engineering words → face ids, with honest refusal instead of guessing.

The pre-processing step is where an engineer's intent ("fix the bottom, 500 N
down on the rib") had no expression: it had to be hand-translated into face ids,
NSET names, DOF ranges and direction vectors. These tests pin the vocabulary and,
more importantly, the refusals — a wrong face silently becomes a wrong answer.
"""
from __future__ import annotations

from app.cae_setup_intent import describe_face, normalize_direction, resolve_face_intent


def _face(fid, surface="plane", normal=None, area=100.0, body="body_001", **extra):
    face = {"id": fid, "type": "face", "surface_type": surface, "area": area, "body_id": body}
    if normal is not None:
        face["normal"] = list(normal)
    face.update(extra)
    return face


# A plate (body_001) carrying a rib (body_002), like the dogfood bracket.
_BRACKET = {
    "entities": [
        {"id": "body_001", "type": "solid", "name": "base_plate"},
        {"id": "body_002", "type": "solid", "name": "rib_main"},
        _face("face_001", normal=(0, 0, -1), area=9600.0),                    # plate bottom
        _face("face_002", normal=(0, 0, 1), area=9500.0),                     # plate top
        _face("face_003", normal=(1, 0, 0), area=430.0),                      # plate +X
        _face("face_004", normal=(-1, 0, 0), area=430.0),                     # plate -X
        _face("face_010", surface="cylinder", area=94.0, radius=2.5,
              roles=["mounting_candidate"]),
        _face("face_011", surface="cylinder", area=94.0, radius=2.5,
              roles=["mounting_candidate"]),
        _face("face_020", normal=(0, 0, 1), area=180.0, body="body_002"),     # rib top
        _face("face_021", normal=(0, 0, -1), area=150.0, body="body_002"),    # rib bottom
    ]
}


def test_bottom_resolves_to_the_downward_face_with_a_reason() -> None:
    hit = resolve_face_intent(_BRACKET, "bottom")
    assert hit["status"] == "ok"
    assert hit["face_ids"] == ["face_001"]
    assert "bottom" in hit["reason"] and "9600" in hit["reason"]


def test_chinese_words_work() -> None:
    assert resolve_face_intent(_BRACKET, "底面")["face_ids"] == ["face_001"]
    assert resolve_face_intent(_BRACKET, "顶面")["face_ids"] == ["face_002"]


def test_part_scope_disambiguates_top() -> None:
    """Both plate and rib have a +Z face; naming the part picks the right one."""
    assert resolve_face_intent(_BRACKET, "rib_main top")["face_ids"] == ["face_020"]
    assert resolve_face_intent(_BRACKET, "base_plate top")["face_ids"] == ["face_002"]


def test_bolt_holes_return_the_whole_pattern() -> None:
    hit = resolve_face_intent(_BRACKET, "bolt holes")
    assert hit["status"] == "ok"
    assert sorted(hit["face_ids"]) == ["face_010", "face_011"]
    assert "r=2.50" in hit["reason"]


def test_explicit_pointer_is_taken_verbatim() -> None:
    hit = resolve_face_intent(_BRACKET, "@face:face_021")
    assert hit["status"] == "ok" and hit["face_ids"] == ["face_021"]


def test_unknown_face_id_is_refused_not_substituted() -> None:
    hit = resolve_face_intent(_BRACKET, "@face:face_999")
    assert hit["status"] == "not_found"
    assert hit["face_ids"] == []


def test_equally_sized_opposite_faces_are_ambiguous_not_guessed() -> None:
    """The two ±X faces have identical area — refuse and show the candidates."""
    symmetric = {"entities": [
        {"id": "body_001", "type": "solid", "name": "block"},
        _face("f1", normal=(1, 0, 0), area=500.0),
        _face("f2", normal=(1, 0, 0), area=500.0),
    ]}
    hit = resolve_face_intent(symmetric, "+X")
    assert hit["status"] == "ambiguous"
    assert hit["face_ids"] == []
    assert len(hit["candidates"]) == 2


def test_unparseable_wording_lists_what_is_understood() -> None:
    hit = resolve_face_intent(_BRACKET, "the shiny bit near the middle")
    assert hit["status"] == "not_found"
    assert "Understood" in hit["reason"]
    assert hit["candidates"], "should show real faces to choose from"


def test_sloped_gusset_top_resolves_and_says_it_is_inclined() -> None:
    """The top of a triangular rib is its hypotenuse, not an axis-aligned face.

    Measured on the real bracket: the rib's upward face has normal
    [0.53, 0, 0.848] — just under the strict alignment bar. Refusing it would
    reject the face every engineer calls "the top", so it resolves at medium
    confidence with the tilt stated.
    """
    gusset = {"entities": [
        {"id": "body_002", "type": "solid", "name": "rib_main"},
        _face("f_slope", normal=(0.53, 0.0, 0.848), area=235.8, body="body_002"),
        _face("f_bottom", normal=(0, 0, -1), area=200.0, body="body_002"),
        _face("f_sideA", normal=(0, 1, 0), area=500.0, body="body_002"),
        _face("f_sideB", normal=(0, -1, 0), area=500.0, body="body_002"),
    ]}
    hit = resolve_face_intent(gusset, "rib_main top")
    assert hit["status"] == "ok"
    assert hit["face_ids"] == ["f_slope"]
    assert hit["confidence"] == "medium"
    assert "inclined" in hit["reason"] and "°" in hit["reason"]


def test_curved_face_cannot_be_selected_by_direction() -> None:
    curved = {"entities": [
        {"id": "body_001", "type": "solid", "name": "shaft"},
        _face("f1", surface="cylinder", area=300.0, radius=5.0),
    ]}
    hit = resolve_face_intent(curved, "top")
    assert hit["status"] in ("not_found", "ambiguous")
    assert hit["face_ids"] == []


def test_multiple_intents_merge() -> None:
    hit = resolve_face_intent(_BRACKET, ["bottom", "@face:face_020"])
    assert hit["status"] == "ok"
    assert hit["face_ids"] == ["face_001", "face_020"]


def test_direction_words_and_vectors() -> None:
    assert normalize_direction([0, 0, -1]) == [0.0, 0.0, -1.0]
    assert normalize_direction("-Z") == [0.0, 0.0, -1.0]
    assert normalize_direction("down") == [0.0, 0.0, -1.0]
    assert normalize_direction("向下") == [0.0, 0.0, -1.0]
    assert normalize_direction("+X") == [1.0, 0.0, 0.0]
    assert normalize_direction("sideways-ish") is None


def test_describe_face_is_readable_by_a_human() -> None:
    line = describe_face(_BRACKET["entities"][2], {"body_001": "base_plate"})
    assert "@face:face_001" in line and "plane" in line
    assert "9600.0 mm²" in line and "on base_plate" in line
