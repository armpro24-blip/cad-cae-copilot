"""Tests for the deterministic modeling-fidelity assessment (crude vs designed)."""
from __future__ import annotations

from aieng.converters.critique_engine import assess_modeling_fidelity, critique_geometry


def _topo(solids: list[tuple[str, str, list[float]]]) -> dict:
    return {"entities": [
        {"id": sid, "type": "solid", "name": nm, "bounding_box": bb} for sid, nm, bb in solids
    ]}


def _fg(named: list[tuple[str, int, str]], adv: tuple[str, ...] = ()) -> dict:
    feats: list[dict] = []
    for name, face_count, body in named:
        feats.append({
            "id": f"feat_{name}", "type": "named_part", "name": name,
            "geometry_refs": {"body": body, "face_count": face_count},
        })
    for t in adv:
        feats.append({"id": f"feat_{t}", "type": t, "name": t, "parameters": {}})
    return {"features": feats}


def test_crude_gearbox_is_flagged_crude() -> None:
    # enclosure box + two gears buried inside it, no fillet/loft anywhere.
    # Use a non-mechanical label ("case") so the generic fidelity heuristic still
    # fires on the sharp edges; a true mechanical housing with model_kind set is
    # treated differently (see test_mechanical_bracket_is_not_flagged_crude).
    topo = _topo([
        ("body_001", "case", [0, 0, 0, 120, 80, 60]),
        ("body_005", "gear_input", [55, 20, 25, 63, 35, 55]),
        ("body_006", "gear_output", [55, -30, 5, 63, 20, 55]),
    ])
    fid = assess_modeling_fidelity(topo, _fg([
        ("case", 15, "body_001"), ("gear_input", 3, "body_005"), ("gear_output", 3, "body_006"),
    ]))
    assert fid["level"] == "crude"
    rules = {f["rule"] for f in fid["findings"]}
    assert "no_edge_breaking" in rules
    assert "possibly_hidden_part" in rules
    assert fid["signals"]["has_edge_breaking"] is False
    assert all(f["category"] == "modeling_fidelity" and f["suggested_fix"] for f in fid["findings"])


def test_filleted_shaped_model_is_designed() -> None:
    topo = _topo([("body_001", "housing", [0, 0, 0, 120, 80, 60])])
    fid = assess_modeling_fidelity(topo, _fg([("housing", 18, "body_001")], adv=("fillet", "loft")))
    assert fid["signals"]["has_edge_breaking"] is True
    assert fid["signals"]["has_shaped_bodies"] is True
    assert fid["level"] == "designed"
    assert not any(f["rule"] == "no_edge_breaking" for f in fid["findings"])


def test_cylinder_part_is_not_flagged_featureless() -> None:
    # a 3-face cylinder (shaft) is legitimately primitive — must NOT be a featureless_box
    topo = _topo([("body_001", "shaft", [0, 0, 0, 140, 12, 12])])
    fid = assess_modeling_fidelity(topo, _fg([("shaft", 3, "body_001")], adv=("fillet",)))
    assert not any(f["rule"] == "featureless_box" for f in fid["findings"])


def test_bare_box_part_is_flagged_featureless() -> None:
    topo = _topo([("body_001", "plate", [0, 0, 0, 100, 60, 8])])
    fid = assess_modeling_fidelity(topo, _fg([("plate", 6, "body_001")], adv=("fillet",)))
    assert any(f["rule"] == "featureless_box" for f in fid["findings"])


def test_hidden_part_detected_by_bbox_containment() -> None:
    topo = _topo([("b1", "outer", [0, 0, 0, 100, 100, 100]), ("b2", "inner", [40, 40, 40, 60, 60, 60])])
    fid = assess_modeling_fidelity(topo, _fg([("outer", 6, "b1"), ("inner", 3, "b2")]))
    assert any(f["rule"] == "possibly_hidden_part" and "inner" in f["feature"] for f in fid["findings"])


def test_part_inside_hollow_enclosure_is_not_flagged_hidden() -> None:
    # housing bbox 100x80x60 = 480k; actual volume 40k -> hollow shell.
    # the bearing seat inside it is legitimately internal, not "hidden".
    topo = {"entities": [
        {"id": "b1", "type": "solid", "name": "housing", "bounding_box": [0, 0, 0, 100, 80, 60], "volume": 40000.0},
        {"id": "b2", "type": "solid", "name": "bearing_seat", "bounding_box": [40, 30, 20, 60, 50, 40], "volume": 6000.0},
    ]}
    fid = assess_modeling_fidelity(topo, _fg([("housing", 18, "b1"), ("bearing_seat", 4, "b2")], adv=("fillet",)))
    assert fid["signals"]["possibly_hidden_parts"] == 0
    assert not any(f["rule"] == "possibly_hidden_part" for f in fid["findings"])


def test_part_inside_solid_block_is_still_flagged_hidden() -> None:
    # same containment, but the container is a SOLID block (fill ~1.0) -> burial is a real flag
    topo = {"entities": [
        {"id": "b1", "type": "solid", "name": "block", "bounding_box": [0, 0, 0, 100, 80, 60], "volume": 470000.0},
        {"id": "b2", "type": "solid", "name": "insert", "bounding_box": [40, 30, 20, 60, 50, 40], "volume": 6000.0},
    ]}
    fid = assess_modeling_fidelity(topo, _fg([("block", 6, "b1"), ("insert", 3, "b2")], adv=("fillet",)))
    assert any(f["rule"] == "possibly_hidden_part" and "insert" in f["feature"] for f in fid["findings"])


def test_finished_boxy_model_is_designed_not_penalized_for_no_loft() -> None:
    # a filleted product body (no loft) should be 'designed' — boxy massing with
    # broken edges is only a mild note, not a heavy penalty. Use a non-engineering
    # label so the generic heuristic still notes the lack of loft/sweep/revolve.
    topo = {"entities": [
        {"id": "b1", "type": "solid", "name": "product_body", "bounding_box": [0, 0, 0, 120, 80, 60], "volume": 140000.0},
    ]}
    fid = assess_modeling_fidelity(topo, _fg([("product_body", 18, "b1")], adv=("fillet",)))
    assert fid["level"] == "designed"
    assert fid["score"] >= 90  # mild -5 for no-loft, nothing else
    assert any(f["rule"] == "primitive_stacking_only" for f in fid["findings"])  # still noted, just mild


def test_critique_geometry_includes_fidelity_block() -> None:
    topo = _topo([("body_001", "housing", [0, 0, 0, 120, 80, 60])])
    out = critique_geometry(topo, _fg([("housing", 15, "body_001")]))
    assert "fidelity" in out
    assert out["fidelity"]["level"] in {"designed", "basic", "crude"}
    # DfM verdict stays a separate axis from fidelity (a crude box still "passes" DfM)
    assert out["verdict"] in {"passes", "passes_with_notes", "passes_with_warnings", "fails_audit"}


def test_mechanical_bracket_is_not_flagged_crude() -> None:
    # A sharp-edged CNC L-bracket (base_plate + rib_main) is a legitimate
    # engineering part. Without fillets/loft it must NOT score 'crude'.
    topo = {"entities": [
        {"id": "b1", "type": "solid", "name": "base_plate", "bounding_box": [0, 0, 0, 100, 60, 8], "volume": 48000.0},
        {"id": "b2", "type": "solid", "name": "rib_main", "bounding_box": [0, 10, 8, 80, 12, 40], "volume": 22000.0},
    ]}
    fg = _fg([
        ("base_plate", 10, "b1"),
        ("rib_main", 8, "b2"),
    ])
    fid = assess_modeling_fidelity(topo, fg)
    assert fid["level"] == "designed"
    assert fid["score"] == 100
    rules = {f["rule"] for f in fid["findings"]}
    assert "no_edge_breaking" not in rules
    assert "primitive_stacking_only" not in rules


def test_mechanical_intent_by_model_kind_suppresses_fidelity_noise() -> None:
    # Even with generic labels, an explicit model_kind='mechanical' should stop
    # the organic-ID heuristic from penalizing sharp, prismatic geometry.
    topo = _topo([("body_001", "block", [0, 0, 0, 80, 60, 10])])
    fid = assess_modeling_fidelity(topo, {"features": [], "model_kind": "mechanical"})
    assert fid["level"] == "designed"
    assert fid["score"] == 100
    rules = {f["rule"] for f in fid["findings"]}
    assert "no_edge_breaking" not in rules
    assert "primitive_stacking_only" not in rules


def test_empty_model_fidelity_skipped() -> None:
    out = critique_geometry({"entities": []}, {"features": []})
    assert out["fidelity"]["level"] == "skipped"
