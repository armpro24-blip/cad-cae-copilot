"""Tests for assembly interface resolution + geometric connection validation.

Geometry validation only — no contact, no bolt preload, no solver execution.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from aieng.converters.assembly_interface_resolution import (
    ASSEMBLY_CONNECTION_GEOMETRY_PATH,
    INTERFACE_RESOLUTION_PATH,
    build_topology_by_part,
    resolve_and_validate_assembly_geometry,
    resolve_assembly_interfaces,
    resolve_interface,
    validate_connection_geometry,
)

# bbox = [xmin,ymin,zmin,xmax,ymax,zmax]


def _face(fid, bbox, normal, area=1.0, body=None):
    e = {"id": fid, "type": "face", "bounding_box": bbox, "normal": normal, "area": area}
    if body:
        e["body_id"] = body
    return e


def _two_part_assembly(conn_type="rigid_tie", behavior=None, **conn_extra):
    return {
        "format": "aieng.assembly_ir", "unit": "mm",
        "parts": [
            {"id": "a", "role": "design_part", "geometry_ref": "geometry/a.step",
             "transform": {"translation": [0, 0, 0], "unit": "mm"}, "material": "Al"},
            {"id": "b", "role": "reference_part", "geometry_ref": "geometry/b.step",
             # b is translated +10 in z so its mating face meets a's mating face
             "transform": {"translation": [0, 0, 10], "unit": "mm"}, "material": "Steel"},
        ],
        "interfaces": [
            {"id": "if_a", "part_id": "a", "semantic_role": "mounting_face",
             "topology_refs": {"face_ids": ["a_top"]}},
            {"id": "if_b", "part_id": "b", "semantic_role": "support_face",
             "topology_refs": {"face_ids": ["b_bot"]}},
        ],
        "connections": [
            {"id": "c1", "type": conn_type, "part_a": "a", "part_b": "b",
             "interface_a": "if_a", "interface_b": "if_b",
             "behavior": behavior or ["load_transfer"], **conn_extra},
        ],
        "analysis_intent": {"design_parts": ["a"]},
    }


def _topology():
    # part a: top face at z=10 (local), normal +z
    # part b: bottom face at z=0 (local), normal -z; after +10 translate it lands at z=10
    return {
        "a": {"a_top": _face("a_top", [0, 0, 10, 5, 5, 10], [0, 0, 1.0], area=25.0)},
        "b": {"b_bot": _face("b_bot", [0, 0, 0, 5, 5, 0], [0, 0, -1.0], area=25.0)},
    }


# ── resolution + transforms ──────────────────────────────────────────────────

def test_transform_moves_bbox_and_centroid():
    iface = {"id": "if_b", "part_id": "b", "semantic_role": "support_face",
             "topology_refs": {"face_ids": ["b_bot"]}}
    rec = resolve_interface(iface, _topology()["b"], {"translation": [0, 0, 10]})
    assert rec["resolution_status"] == "resolved"
    # local bottom face centroid z = 0 -> world z = 10
    assert rec["local"]["centroid"][2] == 0.0
    assert rec["world"]["centroid"][2] == 10.0
    assert rec["world"]["bbox"][2] == 10.0 and rec["world"]["bbox"][5] == 10.0


def test_unresolved_face_reported_honestly():
    iface = {"id": "x", "part_id": "a", "topology_refs": {"face_ids": ["ghost"]}}
    rec = resolve_interface(iface, _topology()["a"], {"translation": [0, 0, 0]})
    assert rec["resolution_status"] == "unresolved"
    assert rec["unresolved_refs"] == ["ghost"]
    assert rec["world"] == {}


def test_partial_resolution():
    iface = {"id": "x", "part_id": "a", "topology_refs": {"face_ids": ["a_top", "ghost"]}}
    rec = resolve_interface(iface, _topology()["a"], {"translation": [0, 0, 0]})
    assert rec["resolution_status"] == "partially_resolved"
    assert rec["unresolved_refs"] == ["ghost"] and rec["resolved_entity_count"] == 1


def test_missing_transform_defaults_to_identity():
    iface = {"id": "if_a", "part_id": "a", "topology_refs": {"face_ids": ["a_top"]}}
    rec = resolve_interface(iface, _topology()["a"], None)
    assert rec["transform_applied"] is True
    assert rec["transform_note"] is None
    assert rec["world"]["centroid"] == rec["local"]["centroid"]


# ── connection geometry validation ───────────────────────────────────────────

def test_matching_translated_faces_plausible_rigid_tie():
    asm = _two_part_assembly("rigid_tie")
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert c["geometry_status"] == "plausible", c["reasons"]
    assert c["metrics"]["bbox_overlap"] is True
    # opposing faces: +z (a) vs -z (b) -> dot ~ -1, NOT same direction
    assert c["metrics"]["normal_alignment"] < 0


def test_far_apart_interfaces_invalid():
    asm = _two_part_assembly("rigid_tie")
    asm["parts"][1]["transform"]["translation"] = [0, 0, 1000]  # b way out in z
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert c["geometry_status"] == "invalid"
    assert "far_apart" in c["reasons"]


def test_same_direction_normals_warn():
    asm = _two_part_assembly("rigid_tie")
    topo = _topology()
    topo["b"]["b_bot"]["normal"] = [0, 0, 1.0]  # both faces point +z (suspicious for a mate)
    res = resolve_assembly_interfaces(asm, topo)
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert "normals_same_direction" in c["reasons"]
    assert c["geometry_status"] == "warning"


def test_unresolved_interface_insufficient_data():
    asm = _two_part_assembly("rigid_tie")
    res = resolve_assembly_interfaces(asm, {"a": _topology()["a"]})  # no topology for b
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert c["geometry_status"] == "insufficient_data"
    assert "unresolved_interface" in c["reasons"]


def test_bolted_proxy_without_bolt_hole_warns():
    # roles are mounting_face/support_face; bolted_proxy prefers bolt_hole/mounting_face.
    # mounting_face is present on one side, so to force the warning drop both to contact_face.
    asm = _two_part_assembly("bolted_proxy")
    asm["interfaces"][0]["semantic_role"] = "contact_face"
    asm["interfaces"][1]["semantic_role"] = "contact_face"
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert "no_bolt_hole_evidence" in c["reasons"]
    assert c["geometry_status"] in ("warning", "invalid")


def test_bolted_proxy_with_mounting_face_no_evidence_warning():
    asm = _two_part_assembly("bolted_proxy")  # if_a is mounting_face
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    assert "no_bolt_hole_evidence" not in geo["connections"][0]["reasons"]


def test_contact_proxy_draft_only():
    asm = _two_part_assembly("contact_proxy")
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert "contact_draft_only" in c["reasons"]
    assert c["geometry_status"] == "warning"   # never "plausible"


def _separated(conn_type, gap_mm=2.0):
    """Interfaces `gap_mm` apart: too far to touch, too near to be `far_apart`.

    Interface diagonal ~7.07 mm, so touch_tol ~0.14 and near_tol ~3.53.
    """
    asm = _two_part_assembly(conn_type)
    asm["parts"][1]["transform"]["translation"] = [0, 0, 10 + gap_mm]
    res = resolve_assembly_interfaces(asm, _topology())
    return validate_connection_geometry(asm, res)["connections"][0]


def test_join_across_a_gap_is_invalid_not_merely_a_warning():
    """A tie/weld/bolt across empty space cannot exist at any scale (#496).

    Measured on the dogfood gearbox: a `bonded` tie between interfaces 20 mm
    apart scored `warning` (20 mm is small next to their 162 mm diagonal), so it
    stayed solver-enabled and transferred load across the gap — stiffer than
    reality, in the non-conservative direction, with `needs_user_input: []`.
    """
    for ctype in ("rigid_tie", "bonded", "welded_proxy", "bolted_proxy"):
        c = _separated(ctype)
        assert c["geometry_status"] == "invalid", f"{ctype}: {c['reasons']} {c['metrics']}"
        assert "joint_across_gap" in c["reasons"], ctype
        assert c["metrics"]["bbox_overlap"] is False


def test_a_spring_across_a_gap_stays_a_warning():
    """Distance is what a spring is for — it must not inherit the joint rule."""
    c = _separated("spring_proxy")
    assert c["geometry_status"] == "warning", c["reasons"]
    assert "joint_across_gap" not in c["reasons"]
    assert "no_overlap" in c["reasons"]


def test_touching_joints_are_unaffected():
    """The escalation must not disturb correctly-mated joints."""
    for ctype in ("rigid_tie", "bonded"):
        asm = _two_part_assembly(ctype)
        res = resolve_assembly_interfaces(asm, _topology())
        c = validate_connection_geometry(asm, res)["connections"][0]
        assert c["geometry_status"] == "plausible", f"{ctype}: {c['reasons']}"
        assert "joint_across_gap" not in c["reasons"]


def test_spring_proxy_warning_even_when_aligned():
    asm = _two_part_assembly("spring_proxy")
    res = resolve_assembly_interfaces(asm, _topology())
    geo = validate_connection_geometry(asm, res)
    c = geo["connections"][0]
    assert "centroid_based_proxy" in c["reasons"]
    assert c["geometry_status"] == "warning"


# ── package integration ──────────────────────────────────────────────────────

def _write_assembly_package(tmp_path: Path, assembly, per_part_topology=True) -> Path:
    pkg = tmp_path / "asm.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", json.dumps({"name": "Asm"}))
        zf.writestr("provenance/conversion_manifest.json",
                    json.dumps({"format": "aieng.conversion_manifest"}))
        zf.writestr("assembly/assembly_ir.json", json.dumps(assembly))
        if per_part_topology:
            topo = _topology()
            for pid, ents in topo.items():
                zf.writestr(f"parts/{pid}/topology_map.json",
                            json.dumps({"format_version": "0.1.0",
                                        "entities": list(ents.values())}))
    return pkg


def test_package_resolves_and_validates(tmp_path: Path):
    pkg = _write_assembly_package(tmp_path, _two_part_assembly("rigid_tie"))
    result = resolve_and_validate_assembly_geometry(pkg)
    assert result["assembly_present"] is True
    assert result["resolution_summary"]["resolved"] == 2
    assert result["geometry_summary"].get("plausible") == 1
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        assert INTERFACE_RESOLUTION_PATH in names
        assert ASSEMBLY_CONNECTION_GEOMETRY_PATH in names
        # honesty + no solver deck
        assert not any(n.lower().endswith((".inp", ".frd", ".step", ".stp")) for n in names)
        geo = json.loads(zf.read(ASSEMBLY_CONNECTION_GEOMETRY_PATH))
        assert geo["provenance"]["geometry_validation_only"] is True
        assert geo["provenance"]["contact_physics_modeled"] is False
        graph = json.loads(zf.read("assembly/connection_graph.json"))
        assert graph["edges"][0]["geometry_status"] == "plausible"


def test_package_invalid_connection_disables_cae_draft(tmp_path: Path):
    asm = _two_part_assembly("rigid_tie")
    asm["parts"][1]["transform"]["translation"] = [0, 0, 1000]  # far apart -> invalid
    pkg = _write_assembly_package(tmp_path, asm)
    resolve_and_validate_assembly_geometry(pkg)
    with zipfile.ZipFile(pkg) as zf:
        draft = json.loads(zf.read("simulation/assembly_cae_setup_draft.json"))
        assert draft["status"] == "needs_user_input"
        cdraft = draft["connections"][0]
        assert cdraft["geometry_status"] == "invalid" and cdraft.get("disabled") is True


def test_package_gapped_tie_is_disabled_end_to_end(tmp_path: Path):
    """The gap case must reach the draft, not just the classifier (#496)."""
    asm = _two_part_assembly("bonded")
    asm["parts"][1]["transform"]["translation"] = [0, 0, 12]  # 2 mm gap, not far_apart
    pkg = _write_assembly_package(tmp_path, asm)
    resolve_and_validate_assembly_geometry(pkg)
    with zipfile.ZipFile(pkg) as zf:
        draft = json.loads(zf.read("simulation/assembly_cae_setup_draft.json"))
    assert draft["status"] == "needs_user_input"
    cdraft = draft["connections"][0]
    assert cdraft["geometry_status"] == "invalid" and cdraft.get("disabled") is True
    assert any("joint_across_gap" in m for m in draft["needs_user_input"]), \
        draft["needs_user_input"]


def test_package_without_assembly_unaffected(tmp_path: Path):
    pkg = tmp_path / "plain.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/shape_ir.json", json.dumps({"representation": "manifold_mesh"}))
    before = set(zipfile.ZipFile(pkg).namelist())
    result = resolve_and_validate_assembly_geometry(pkg)
    after = set(zipfile.ZipFile(pkg).namelist())
    assert result["assembly_present"] is False and before == after


def test_shared_package_topology_fallback(tmp_path: Path):
    # No per-part maps; a single shared geometry/topology_map.json with body_id scoping.
    asm = _two_part_assembly("rigid_tie")
    pkg = tmp_path / "shared.aieng"
    a = _face("a_top", [0, 0, 10, 5, 5, 10], [0, 0, 1.0], area=25.0, body="a")
    b = _face("b_bot", [0, 0, 0, 5, 5, 0], [0, 0, -1.0], area=25.0, body="b")
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("assembly/assembly_ir.json", json.dumps(asm))
        zf.writestr("geometry/topology_map.json",
                    json.dumps({"format_version": "0.1.0", "entities": [a, b]}))
    result = resolve_and_validate_assembly_geometry(pkg)
    assert result["resolution_summary"]["resolved"] == 2


def test_shared_topology_scopes_named_part_via_feature_graph_body_ref(tmp_path: Path):
    """Compound children can be named parts while topology faces use body_00N ids."""
    asm = {
        "format": "aieng.assembly_ir",
        "unit": "mm",
        "parts": [
            {"id": "base_plate", "role": "design_part", "geometry_ref": "base_plate"},
            {"id": "load_wall", "role": "reference_part", "geometry_ref": "load_wall"},
        ],
        "interfaces": [
            {"id": "if_base", "part_id": "base_plate", "semantic_role": "mounting_face",
             "topology_refs": {"face_ids": ["face_003"]}},
        ],
        "connections": [],
    }
    topology = {
        "format_version": "0.1.0",
        "entities": [
            {"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 2]},
            {"id": "face_003", "type": "face", "body_id": "body_001",
             "bounding_box": [0, 0, 2, 10, 10, 2], "normal": [0, 0, 1], "area": 100.0},
            {"id": "body_002", "type": "solid", "bounding_box": [0, 0, 2, 10, 2, 12]},
            {"id": "face_015", "type": "face", "body_id": "body_002",
             "bounding_box": [0, 0, 2, 10, 2, 12], "normal": [0, -1, 0], "area": 100.0},
        ],
    }
    feature_graph = {
        "features": [
            {"id": "feat_body_001", "type": "named_part", "name": "base_plate",
             "geometry_refs": {"body": "body_001"}},
            {"id": "feat_body_002", "type": "named_part", "name": "load_wall",
             "geometry_refs": {"body": "body_002"}},
        ]
    }
    pkg = tmp_path / "compound_named_parts.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("assembly/assembly_ir.json", json.dumps(asm))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))

    with zipfile.ZipFile(pkg) as zf:
        topo_by_part = build_topology_by_part(zf, set(zf.namelist()), asm)

    assert "face_003" in topo_by_part["base_plate"]
    assert "face_015" not in topo_by_part["base_plate"]

    result = resolve_and_validate_assembly_geometry(pkg)
    assert result["resolution_summary"]["resolved"] == 1
    with zipfile.ZipFile(pkg) as zf:
        resolution = json.loads(zf.read(INTERFACE_RESOLUTION_PATH))
    rec = resolution["interfaces"]["if_base"]
    assert rec["resolution_status"] == "resolved"
    assert rec["transform_applied"] is True
