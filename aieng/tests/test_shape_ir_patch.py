"""Tests for the Shape IR patch format + apply."""
from __future__ import annotations

import copy
import json

from aieng.converters.shape_ir_patch import (
    apply_shape_ir_patch,
    build_patch_report,
    validate_shape_ir,
)


def _box_payload():
    return {"parts": [
        {"id": "plate", "type": "box", "dimensions": [40, 30, 6], "parameters": {"RADIUS": 4}},
        {"id": "post", "type": "cylinder", "radius": 5, "height": 20},
    ]}


def _nurbs_payload():
    return {"representation": "nurbs_brep", "parts": [
        {"id": "patch", "type": "nurbs_surface", "control_net": [
            [[0, 0, 0], [10, 0, 0]],
            [[0, 10, 0], [10, 10, 0]],
        ]},
    ]}


def test_set_parameter():
    payload = _box_payload()
    original = copy.deepcopy(payload)
    patch = {"operations": [{"op": "set_parameter", "target": "plate", "parameter": "RADIUS",
                             "value": 12, "reason": "stiffen corner"}]}
    res = apply_shape_ir_patch(payload, patch)
    assert res["ok"] is True and not res["failed"]
    assert res["new_payload"]["parts"][0]["parameters"]["RADIUS"] == 12
    assert payload == original  # input never mutated
    assert res["operations"][0]["status"] == "applied"
    assert res["operations"][0]["reason"] == "stiffen corner"


def test_move_control_point_delta_and_value():
    res = apply_shape_ir_patch(_nurbs_payload(), {"operations": [
        {"op": "move_control_point", "target": "patch", "path": [0, 1], "delta": [0, 0, 5]},
    ]})
    assert res["ok"] is True
    assert res["new_payload"]["parts"][0]["control_net"][0][1] == [10.0, 0.0, 5.0]

    res2 = apply_shape_ir_patch(_nurbs_payload(), {"operations": [
        {"op": "move_control_point", "target": "patch", "path": [1, 1], "value": [10, 10, 7]},
    ]})
    assert res2["new_payload"]["parts"][0]["control_net"][1][1] == [10.0, 10.0, 7.0]

    # out-of-range index fails atomically (original untouched)
    bad = apply_shape_ir_patch(_nurbs_payload(), {"operations": [
        {"op": "move_control_point", "target": "patch", "path": [9, 9], "delta": [0, 0, 1]},
    ]})
    assert bad["ok"] is False and bad["failed"][0]["op"] == "move_control_point"


def test_add_node_and_duplicate():
    res = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "add_node", "node": {"id": "rib", "type": "box", "dimensions": [30, 4, 20]}},
    ]})
    assert res["ok"] is True
    ids = [p["id"] for p in res["new_payload"]["parts"]]
    assert ids == ["plate", "post", "rib"]

    dup = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "add_node", "node": {"id": "plate", "type": "box"}},
    ]})
    assert dup["ok"] is False and "already exists" in dup["failed"][0]["message"]


def test_invalid_patch_target_and_atomicity():
    payload = _box_payload()
    original = copy.deepcopy(payload)
    # second op targets a missing node; first op must NOT be committed (atomic)
    res = apply_shape_ir_patch(payload, {"operations": [
        {"op": "set_parameter", "target": "plate", "parameter": "RADIUS", "value": 99},
        {"op": "set_parameter", "target": "ghost", "parameter": "X", "value": 1},
    ]})
    assert res["ok"] is False
    statuses = [o["status"] for o in res["operations"]]
    assert statuses == ["applied", "failed"]
    assert payload == original  # nothing written back


def test_remove_only_node_fails_validation():
    # removing every node yields an empty 'parts' -> invalid Shape IR -> reject
    res = apply_shape_ir_patch({"parts": [{"id": "only", "type": "box"}]}, {"operations": [
        {"op": "remove_node", "target": "only"},
    ]})
    assert res["ok"] is False
    assert res["validation"]["ok"] is False


def test_change_representation_backend():
    ok = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "change_representation_backend", "value": "manifold_mesh"},
    ]})
    assert ok["ok"] is True and ok["new_payload"]["representation"] == "manifold_mesh"
    bad = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "change_representation_backend", "value": "not_a_backend"},
    ]})
    assert bad["ok"] is False


def test_connect_disconnect():
    res = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "connect", "connection": {"source": "plate", "target": "post", "type": "joined_to"}},
    ]})
    assert res["ok"] is True
    assert {"source": "plate", "target": "post", "type": "joined_to"} in res["new_payload"]["adjacency"]
    # disconnect a non-existent edge fails
    bad = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "disconnect", "connection": {"source": "plate", "target": "post"}},
    ]})
    assert bad["ok"] is False


def test_dry_run_does_not_imply_commit():
    res = apply_shape_ir_patch(_box_payload(), {"operations": [
        {"op": "set_parameter", "target": "plate", "parameter": "RADIUS", "value": 20},
    ]}, dry_run=True)
    assert res["ok"] is True and res["dry_run"] is True
    assert res["new_payload"]["parts"][0]["parameters"]["RADIUS"] == 20
    report = build_patch_report({"author": "tester", "tool": "pytest"}, res)
    assert report["dry_run"] is True
    assert report["provenance"]["committed"] is False  # dry-run never commits
    assert report["applied_count"] == 1


def test_validate_shape_ir():
    assert validate_shape_ir({"parts": [{"id": "a"}]})[0] is True
    assert validate_shape_ir({"parts": []})[0] is False
    assert validate_shape_ir({"parts": [{"id": "a"}, {"id": "a"}]})[0] is False
    assert validate_shape_ir({})[0] is False


# ── the shapes the product actually writes ───────────────────────────────────
#
# The fixture above gives its node an explicit `parameters` map and no
# same-named top-level field — precisely the input shape where writing to
# `parameters` was correct. A real Shape IR part from the topology-optimization
# writeback looks nothing like it: `extruded_region` with `thickness` and
# `polygons` as top-level fields and no `parameters` at all.

_EXTRUDED_IR = {
    "format": "aieng.shape_ir",
    "model_id": "probe",
    "representation": "brep_build123d",
    "parts": [{
        "id": "region_001",
        "type": "extruded_region",
        "label": "region_001",
        "thickness": 24.0,
        "origin": [0.0, 0.0, 0.0],
        "u_axis": "x",
        "v_axis": "y",
        "polygons": [[[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0]]],
    }],
}


def _patch(ops, payload=None):
    from aieng.converters.shape_ir_patch import apply_shape_ir_patch

    import copy as _copy
    return apply_shape_ir_patch(_copy.deepcopy(payload or _EXTRUDED_IR),
                                {"format_version": "0.1", "operations": ops})


def test_set_parameter_changes_the_field_the_compilers_read() -> None:
    """It reported success and changed nothing.

    `_compile_node` merges as `{**parameters, **node}` — the node's own field
    wins — and an `extruded_region` reads `thickness` off the node directly,
    never consulting `parameters`. So writing to `parameters` returned ok, said
    "set region_001.parameters.thickness = 30.0", and recompiled the SAME
    geometry: an approval-gated edit that did nothing.
    """
    result = _patch([{"op": "set_parameter", "target": "region_001",
                      "parameter": "thickness", "value": 30.0}])

    assert result["ok"], result.get("error")
    node = result["new_payload"]["parts"][0]
    assert node["thickness"] == 30.0
    assert "parameters" not in node, "the shadow copy would be inert"

    # …and the compiler agrees, which is the claim that actually matters.
    from aieng.converters.shape_ir import extruded_region_geometry

    assert extruded_region_geometry(node)["thickness"] == 30.0


def test_set_parameter_still_uses_the_parameters_map_when_there_is_no_field() -> None:
    """A primitive node keeps its `parameters` home."""
    payload = {**_EXTRUDED_IR, "parts": [
        {"id": "plate", "type": "box", "dimensions": [40, 30, 6], "parameters": {"RADIUS": 4}}]}
    result = _patch([{"op": "set_parameter", "target": "plate",
                      "parameter": "RADIUS", "value": 12}], payload)

    assert result["ok"], result.get("error")
    assert result["new_payload"]["parts"][0]["parameters"]["RADIUS"] == 12


def test_set_parameter_refuses_to_rewrite_what_identifies_the_node() -> None:
    """Writing `id`/`type`/`label` as a parameter is inert at best."""
    for field in ("id", "type", "label"):
        result = _patch([{"op": "set_parameter", "target": "region_001",
                          "parameter": field, "value": "x"}])
        assert not result["ok"]
        assert "replace_node" in json.dumps(result["failed"]), field


def test_move_control_point_moves_an_extruded_regions_polygon_vertex() -> None:
    """A polygon vertex IS the control point of the node kind the product writes.

    `_control_net` looked only for `control_net`/`control_points`/`points`/`net`,
    so the operation refused with "node has no control_net" on every
    extruded_region — the shape the topology-optimization writeback produces.
    """
    result = _patch([{"op": "move_control_point", "target": "region_001",
                      "path": [0, 1], "delta": [2.0, 0.5]}])

    assert result["ok"], result.get("error")
    assert result["new_payload"]["parts"][0]["polygons"][0][1] == [12.0, 0.5]


def test_move_control_point_works_in_the_points_own_dimension() -> None:
    """Hard-coding 3 rejected every planar control point."""
    absolute = _patch([{"op": "move_control_point", "target": "region_001",
                        "path": [0, 0], "value": [1.0, 2.0]}])
    assert absolute["ok"], absolute.get("error")
    assert absolute["new_payload"]["parts"][0]["polygons"][0][0] == [1.0, 2.0]

    wrong_dims = _patch([{"op": "move_control_point", "target": "region_001",
                          "path": [0, 0], "value": [1.0, 2.0, 3.0]}])
    assert not wrong_dims["ok"], "a 3D point in a planar polygon must be refused"
    assert "component" in json.dumps(wrong_dims["failed"])


def test_a_failed_operation_leaves_the_payload_untouched() -> None:
    """Atomicity, checked on the object actually passed in.

    The first version asserted against the module-level fixture, which `_patch`
    deep-copies — so it would have passed even if the input were mutated. A test
    that cannot observe the thing it names proves nothing.
    """
    from aieng.converters.shape_ir_patch import apply_shape_ir_patch

    payload = copy.deepcopy(_EXTRUDED_IR)
    result = apply_shape_ir_patch(payload, {"format_version": "0.1", "operations": [
        {"op": "set_parameter", "target": "region_001", "parameter": "thickness", "value": 30.0},
        {"op": "remove_node", "target": "no_such_node"},
    ]})

    assert not result["ok"]
    assert payload["parts"][0]["thickness"] == 24.0, "the input payload was mutated"


def test_set_parameter_refuses_to_change_a_numeric_fields_type() -> None:
    """Otherwise it fails later, inside the compiler, far from the patch."""
    result = _patch([{"op": "set_parameter", "target": "region_001",
                      "parameter": "thickness", "value": "thick"}])
    assert not result["ok"]
    assert "numeric" in json.dumps(result["failed"])


def test_set_parameter_refuses_to_rewrite_the_name_it_is_targeted_by() -> None:
    """`_node_id` falls back to `name`, so rewriting it moves the target."""
    payload = {**_EXTRUDED_IR, "parts": [
        {"name": "region_by_name", "type": "extruded_region", "thickness": 4.0,
         "polygons": [[[0, 0], [1, 0], [1, 1]]]}]}
    result = _patch([{"op": "set_parameter", "target": "region_by_name",
                      "parameter": "name", "value": "renamed"}], payload)
    assert not result["ok"]
    assert "replace_node" in json.dumps(result["failed"])


def test_a_delta_with_the_wrong_component_count_is_refused() -> None:
    """`>=` silently ignored the extras — the mistake it should have caught."""
    result = _patch([{"op": "move_control_point", "target": "region_001",
                      "path": [0, 0], "delta": [1.0, 2.0, 3.0]}])
    assert not result["ok"]
    assert "exactly 2" in json.dumps(result["failed"])
