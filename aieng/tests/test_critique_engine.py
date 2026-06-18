"""Tests for the deterministic critique engine shared with cad.critique."""
from __future__ import annotations

from aieng.converters.critique_engine import critique_geometry


def _topo(*bodies):
    return {"entities": list(bodies)}


def _body(bid, name, bbox, *, body_type="solid"):
    return {"id": bid, "type": body_type, "name": name, "bounding_box": bbox}


def _feature_graph(*features):
    return {"features": list(features)}


def _named_part(name, body_id):
    return {"id": f"feat_{body_id}", "type": "named_part", "name": name,
            "geometry_refs": {"body": body_id}}


def test_empty_model_skipped():
    result = critique_geometry(_topo(), _feature_graph())
    assert result["status"] == "ok"
    assert result["verdict"] == "skipped"
    assert result["findings"] == []


def test_no_engineering_features_in_auto_mode():
    topo = _topo(_body("b1", "arm_L", [0, 0, 0, 10, 10, 100]))
    fg = _feature_graph(_named_part("arm_L", "b1"))
    result = critique_geometry(topo, fg, mode="auto")
    assert result["status"] == "ok"
    assert result["verdict"] == "passes"
    assert result["summary"]["engineering_audit_run"] is False


def test_thin_wall_fails_audit():
    # back_plate is one of the labels that elevates a thin-wall finding to high severity.
    topo = _topo(_body("b1", "back_plate", [0, 0, 0, 100, 80, 1.5]))
    fg = _feature_graph(_named_part("back_plate", "b1"))
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "fails_audit"
    finding = next(f for f in result["findings"] if f["rule"] == "min_wall_thickness")
    assert finding["severity"] == "high"
    assert finding["category"] == "manufacturing_rule"
    assert "1.50mm" in finding["observation"]


def test_thin_rib_is_medium_severity():
    topo = _topo(_body("b1", "rib_main", [0, 0, 0, 50, 1.0, 25]))
    fg = _feature_graph(_named_part("rib_main", "b1"))
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "passes_with_warnings"
    assert result["findings"][0]["severity"] == "medium"


def test_floating_part_flagged_high():
    b1 = _body("b1", "base_plate", [0, 0, 0, 50, 50, 5])
    b2 = _body("b2", "floater", [500, 500, 500, 520, 520, 520])
    topo = _topo(b1, b2)
    fg = _feature_graph(_named_part("base_plate", "b1"), _named_part("floater", "b2"))
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "fails_audit"
    finding = next(f for f in result["findings"] if f["rule"] == "floating_component")
    assert finding["severity"] == "high"


def test_nonstandard_hole_is_low_severity():
    topo = _topo(_body("b1", "base_plate", [0, 0, 0, 50, 50, 5]))
    fg = _feature_graph(
        _named_part("base_plate", "b1"),
        {"id": "mh1", "type": "mounting_hole", "name": "hole_1",
         "parameters": {"hole_diameter_mm": 7.0}, "geometry_refs": {"body": "b1"}},
    )
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "passes_with_notes"
    finding = result["findings"][0]
    assert finding["rule"] == "standard_hole_size"
    assert finding["severity"] == "low"


def test_nonstandard_hole_detected_with_core_recognizer_diameter_field():
    """The core RuleBasedFeatureRecognizer emits hole diameter as `diameter_mm`
    (the runtime path uses `hole_diameter_mm`). The standard-hole DfM check must
    read either, so it fires on imported / CLI-recognized geometry too."""
    topo = _topo(_body("b1", "base_plate", [0, 0, 0, 50, 50, 5]))
    fg = _feature_graph(
        _named_part("base_plate", "b1"),
        {"id": "mh1", "type": "mounting_hole", "name": "hole_1",
         "parameters": {"diameter_mm": 7.3, "radius_mm": 3.65},
         "geometry_refs": {"faces": ["f_hole_1"]}},
    )
    result = critique_geometry(topo, fg, mode="engineering")
    finding = next(f for f in result["findings"] if f["rule"] == "standard_hole_size")
    assert finding["severity"] == "low"


def test_tapped_hole_at_tap_drill_diameter_is_not_flagged_nonstandard():
    """A hole recognized as a thread (tap-drill diameter) is intentionally a
    non-standard *final* diameter — flagging it 'non-standard hole' is a false
    positive, so the standard-hole check must skip threaded holes."""
    topo = _topo(_body("b1", "base_plate", [0, 0, 0, 50, 50, 5]))
    fg = _feature_graph(
        _named_part("base_plate", "b1"),
        # 6.8mm == M8 coarse tap-drill: non-standard as a final drill size.
        {"id": "mh1", "type": "mounting_hole", "name": "hole_1",
         "parameters": {"diameter_mm": 6.8, "radius_mm": 3.4},
         "geometry_refs": {"faces": ["f_hole_1"]}},
        {"id": "th1", "type": "thread", "name": "Thread candidate (M8)",
         "parameters": {"nominal_size": "M8"}, "geometry_refs": {"faces": ["f_hole_1"]}},
    )
    result = critique_geometry(topo, fg, mode="engineering")
    assert not any(f["rule"] == "standard_hole_size" for f in result["findings"])


def test_bracket_without_mounting_warns():
    topo = _topo(_body("b1", "bracket", [0, 0, 0, 50, 50, 5]))
    fg = _feature_graph(_named_part("bracket", "b1"))
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "passes_with_warnings"
    assert any(f["rule"] == "missing_mounting_interface" for f in result["findings"])


def test_passes_when_thick_enough():
    # A non-plate/non-bracket part in forced engineering mode has no blocking
    # findings and no advisory warnings.
    topo = _topo(_body("b1", "arm_L", [0, 0, 0, 10, 10, 100]))
    fg = _feature_graph(_named_part("arm_L", "b1"))
    result = critique_geometry(topo, fg, mode="engineering")
    assert result["status"] == "ok"
    assert result["verdict"] == "passes"
    assert result["findings"] == []


def test_custom_min_wall_respected():
    topo = _topo(_body("b1", "arm_L", [0, 0, 0, 10, 10, 2.0]))
    fg = _feature_graph(_named_part("arm_L", "b1"))
    result = critique_geometry(topo, fg, mode="engineering", min_wall_mm=1.5)
    assert result["status"] == "ok"
    assert result["verdict"] == "passes"


def test_rules_applied_returned():
    result = critique_geometry(_topo(), _feature_graph())
    assert result["rules_applied"]["min_wall_mm"] == 3.0
    assert result["rules_applied"]["min_corner_radius_mm"] == 2.0
    assert 3.0 in result["rules_applied"]["standard_hole_diameters_mm"]
