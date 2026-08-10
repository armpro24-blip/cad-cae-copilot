"""Tests for stable face identity across CAD rebuilds.

The measured failure this guards (reference beam, cut a through-hole):
OCCT re-enumerates the modified body and the ids silently shuffle —
face_002 (the +X load face) came back denoting the -Y side face. Every
downstream binding (CAE mappings, @face: pointers) rides on these ids.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.topology_identity import stabilize_topology_face_ids


def _plane(fid: str, normal, bbox, area: float) -> dict:
    return {
        "id": fid, "type": "face", "surface_type": "plane",
        "normal": list(normal), "bounding_box": list(bbox), "area": area,
    }


def _cyl(fid: str, direction, bbox, area: float, radius: float) -> dict:
    return {
        "id": fid, "type": "face", "surface_type": "cylinder",
        "axis": {"direction": list(direction)}, "bounding_box": list(bbox),
        "area": area, "radius": radius,
    }


def _topo(*faces, edges=()) -> dict:
    return {"entities": [*faces, *edges]}


_BEAM = [
    _plane("face_001", (-1, 0, 0), (-50, -10, -5, -50, 10, 5), 200.0),
    _plane("face_002", (1, 0, 0), (50, -10, -5, 50, 10, 5), 200.0),
    _plane("face_003", (0, -1, 0), (-50, -10, -5, 50, -10, 5), 1000.0),
    _plane("face_004", (0, 1, 0), (-50, 10, -5, 50, 10, 5), 1000.0),
    _plane("face_005", (0, 0, -1), (-50, -10, -5, 50, 10, -5), 2000.0),
    _plane("face_006", (0, 0, 1), (-50, -10, 5, 50, 10, 5), 2000.0),
]


def test_measured_hole_shuffle_is_undone() -> None:
    """The exact measured failure: same faces, shuffled numbers, pierced tops."""
    old = _topo(*[json.loads(json.dumps(f)) for f in _BEAM])
    # After the hole-cut, OCCT enumerated: 001=-X, 002=-Y, 003=+Z(pierced),
    # 004=+Y, 005=-Z(pierced), 006=+X, 007=hole wall.
    new = _topo(
        _plane("face_001", (-1, 0, 0), (-50, -10, -5, -50, 10, 5), 200.0),
        _plane("face_002", (0, -1, 0), (-50, -10, -5, 50, -10, 5), 1000.0),
        _plane("face_003", (0, 0, 1), (-50, -10, 5, 50, 10, 5), 1971.7),  # pierced
        _plane("face_004", (0, 1, 0), (-50, 10, -5, 50, 10, 5), 1000.0),
        _plane("face_005", (0, 0, -1), (-50, -10, -5, 50, 10, -5), 1971.7),  # pierced
        _plane("face_006", (1, 0, 0), (50, -10, -5, 50, 10, 5), 200.0),
        _cyl("face_007", (0, 0, 1), (27, -3, -5, 33, 3, 5), 188.5, 3.0),
    )
    report = stabilize_topology_face_ids(old, new)

    assert report["applied"] is True
    assert report["preserved"] == 6
    faces = {f["id"]: f for f in new["entities"]}
    # identity restored: each number means what it meant before the edit
    assert faces["face_002"]["normal"] == [1, 0, 0]
    assert faces["face_003"]["normal"] == [0, -1, 0]
    assert faces["face_006"]["normal"] == [0, 0, 1]
    # pierced faces (changed area -> exact tier fails) rescued by character tier
    assert faces["face_005"]["area"] == 1971.7
    # the genuinely new hole wall keeps a fresh number and is reported as such
    assert faces["face_007"]["surface_type"] == "cylinder"
    assert report["fresh_ids"] == ["face_007"]
    assert report["retired_ids"] == []


def test_pure_superset_is_a_noop() -> None:
    """An append that only adds faces must not touch anything."""
    old = _topo(*[json.loads(json.dumps(f)) for f in _BEAM])
    new = _topo(
        *[json.loads(json.dumps(f)) for f in _BEAM],
        _cyl("face_007", (0, 0, 1), (26, -4, 5, 34, 4, 13), 200.0, 4.0),
    )
    report = stabilize_topology_face_ids(old, new)
    assert report["applied"] is False  # enumeration already agrees with history
    assert report["preserved"] == 6
    assert [f["id"] for f in new["entities"]] == [f"face_{i:03d}" for i in range(1, 8)]


def test_ambiguous_lookalikes_get_fresh_ids_not_guesses() -> None:
    """Two identical translated faces cannot be told apart -> no guessing."""
    old = _topo(
        _plane("face_001", (0, 0, 1), (0, 0, 10, 10, 10, 10), 100.0),
        _plane("face_002", (0, 0, 1), (20, 0, 10, 30, 10, 10), 100.0),
    )
    # both moved (exact tier fails for both), same character (tier 2 not unique)
    new = _topo(
        _plane("face_001", (0, 0, 1), (2, 0, 10, 12, 10, 10), 100.0),
        _plane("face_002", (0, 0, 1), (22, 0, 10, 32, 10, 10), 100.0),
    )
    report = stabilize_topology_face_ids(old, new)
    ids = {f["id"] for f in new["entities"]}
    # ids face_001/face_002 belonged to faces we could NOT re-identify — they
    # must be retired, never silently inherited
    assert ids.isdisjoint({"face_001", "face_002"}) or report["applied"] is False
    if report["applied"]:
        assert set(report["retired_ids"]) == {"face_001", "face_002"}
        assert report["preserved"] == 0


def test_retired_id_is_never_reused_for_a_different_face() -> None:
    """A stale binding to a removed face must fail, not hit a stranger."""
    old = _topo(
        _plane("face_001", (0, 0, 1), (0, 0, 0, 10, 10, 0), 100.0),
        _plane("face_002", (1, 0, 0), (10, 0, 0, 10, 10, 10), 50.0),
    )
    # face_002's face is GONE; a brand-new cylinder got enumerated as face_002
    new = _topo(
        _plane("face_001", (0, 0, 1), (0, 0, 0, 10, 10, 0), 100.0),
        _cyl("face_002", (0, 0, 1), (3, 3, 0, 7, 7, 10), 60.0, 2.0),
    )
    report = stabilize_topology_face_ids(old, new)
    assert report["applied"] is True
    ids = {f["id"] for f in new["entities"]}
    assert "face_002" not in ids  # retired, not inherited
    assert "face_002" in report["retired_ids"]
    # the new face got a number beyond everything ever seen
    assert any(fid > "face_002" for fid in report["fresh_ids"])


def test_edge_adjacency_and_feature_graph_are_remapped_together() -> None:
    """Everything that names a face must keep naming the same face."""
    old = _topo(
        _plane("face_001", (1, 0, 0), (5, 0, 0, 5, 5, 5), 25.0),
        _plane("face_002", (0, 1, 0), (0, 5, 0, 5, 5, 5), 25.0),
    )
    new = _topo(
        _plane("face_001", (0, 1, 0), (0, 5, 0, 5, 5, 5), 25.0),   # shuffled
        _plane("face_002", (1, 0, 0), (5, 0, 0, 5, 5, 5), 25.0),
        edges=[{"id": "edge_001", "type": "edge", "faces": ["face_001", "face_002"]}],
    )
    fg = {"features": [{"id": "feat_1", "type": "named_part", "name": "p",
                        "geometry_refs": {"faces": ["face_001", "face_002"]}}]}
    report = stabilize_topology_face_ids(old, new, fg)

    assert report["applied"] is True
    faces = {f["id"]: f for f in new["entities"] if f.get("type") == "face"}
    assert faces["face_001"]["normal"] == [1, 0, 0]   # identity restored
    edge = next(e for e in new["entities"] if e.get("type") == "edge")
    assert sorted(edge["faces"]) == ["face_001", "face_002"]
    assert sorted(fg["features"][0]["geometry_refs"]["faces"]) == ["face_001", "face_002"]


def test_degenerate_inputs_are_safe_noops() -> None:
    assert stabilize_topology_face_ids(None, _topo(*_BEAM))["applied"] is False
    assert stabilize_topology_face_ids(_topo(), _topo(*_BEAM))["applied"] is False
    assert stabilize_topology_face_ids(_topo(*_BEAM), _topo())["applied"] is False


# ── integration: the real kernel, the real write path ────────────────────────

def test_cae_binding_survives_a_real_hole_cut(tmp_path: Path) -> None:
    """End to end with build123d: bind CAE, cut a hole, binding still valid.

    Before this, the hole-cut shuffled face_002 onto the -Y side face — a bound
    load would have silently moved, and the character check (#475) could only
    refuse the run. Now the identity itself is preserved.
    """
    pytest.importorskip("build123d")
    from app.config import Settings
    from app.main import default_project, project_dir, save_project
    from app.cad_generation import execute_build123d_code
    from app.project_io import (
        annotate_cae_mapping_face_character,
        validate_cae_topology_references,
    )

    settings = Settings(
        platform_root=tmp_path / "p", workspace_root=tmp_path / "w",
        data_root=tmp_path / "d",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        sample_step=tmp_path / "w" / "s.step",
    )
    for d in (settings.platform_root, settings.workspace_root, settings.data_root):
        d.mkdir(parents=True, exist_ok=True)
    pid = save_project(settings, default_project("bind-survive"))["id"]

    base = (
        "from build123d import *\n"
        "beam = Box(100.0, 20.0, 10.0)\n"
        "beam.label = 'beam'\n"
        "result = beam\n"
    )
    assert execute_build123d_code(settings, pid, {"code": base, "thumbnail": False})["status"] == "ok"
    pkg = next(Path(project_dir(settings, pid)).glob("*.aieng"))

    mapping = {"schema_version": "0.1", "mappings": [
        {"cae_entity": "FIXED_END", "maps_to": {"role": "fixed_support"}, "face_ids": ["face_001"]},
        {"cae_entity": "LOAD_END", "maps_to": {"role": "load_surface"}, "face_ids": ["face_002"]},
    ]}
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr("simulation/cae_mapping.json", json.dumps(mapping))
    assert annotate_cae_mapping_face_character(pkg)["status"] == "ok"

    hole = (
        "from build123d import *\n"
        "beam = Box(100.0, 20.0, 10.0)\n"
        "beam -= Cylinder(3, 12).moved(Location((30, 0, 0)))\n"
        "beam.label = 'beam'\n"
        "result = beam\n"
    )
    assert execute_build123d_code(settings, pid, {"code": hole, "thumbnail": False})["status"] == "ok"

    with zipfile.ZipFile(pkg) as zf:
        topo = json.loads(zf.read("geometry/topology_map.json"))
        stability = json.loads(zf.read("diagnostics/topology_id_stability.json"))

    # identity: face_002 is still the +X face the load was bound to
    f2 = next(e for e in topo["entities"] if e.get("id") == "face_002")
    assert f2["surface_type"] == "plane"
    assert f2["normal"][0] == pytest.approx(1.0)
    assert stability["preserved"] == 6
    assert stability["retired_ids"] == []

    # payoff: the CAE binding is re-verified, not refused, after a
    # topology-CHANGING edit
    verdict = validate_cae_topology_references(pkg)
    assert verdict["missing_face_ids"] == []
    assert verdict["face_character_mismatches"] == []
    assert verdict["references_reverified"] is True
    assert verdict["valid"] is True
