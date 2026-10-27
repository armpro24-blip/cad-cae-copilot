"""Tests for the geometry_verification block returned by CAD edit flows.

The verification block reports topology survival (faces/edges/solids),
B-Rep validity (honestly marked unknown), and export sanity after a
parameter edit or part removal/replacement.
"""

from __future__ import annotations

import pytest

from app.cad_generation import (
    _entity_survival_summary,
    _feature_reference_ids,
    _geometry_verification,
)


def _make_topo(entities: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Build a minimal topology_map from entity dicts."""
    return {"entities": entities}


# Faces carry the geometry a real topology map records: the write re-identifies
# faces by it, and the verification reports the ids the write will persist. A
# face with no geometry cannot be re-identified and would be given a fresh id.
_BOTTOM = {"surface_type": "plane", "normal": [0.0, 0.0, -1.0], "area": 100.0,
           "bounding_box": [0.0, 0.0, 0.0, 10.0, 10.0, 0.0]}
_TOP = {"surface_type": "plane", "normal": [0.0, 0.0, 1.0], "area": 100.0,
        "bounding_box": [0.0, 0.0, 10.0, 10.0, 10.0, 10.0]}
_BORE = {"surface_type": "cylinder", "axis": [0.0, 0.0, 1.0], "area": 157.1,
         "bounding_box": [4.5, 4.5, 0.0, 5.5, 5.5, 10.0]}


@pytest.fixture
def unchanged_topology() -> dict[str, list[dict[str, str]]]:
    """A topology where all entity ids survive unchanged."""
    return _make_topo([
        {"id": "solid_001", "type": "solid"},
        {"id": "face_001", "type": "face", **_BOTTOM},
        {"id": "face_002", "type": "face", **_TOP},
        {"id": "edge_001", "type": "edge"},
    ])


@pytest.fixture
def changed_topology() -> dict[str, list[dict[str, str]]]:
    """A topology where face_002 and edge_001 are replaced by new ids."""
    return _make_topo([
        {"id": "solid_001", "type": "solid"},
        {"id": "face_001", "type": "face", **_BOTTOM},
        {"id": "face_003", "type": "face", **_BORE},
        {"id": "edge_002", "type": "edge"},
    ])


def test_unchanged_topology_reports_all_survived(
    unchanged_topology: dict[str, list[dict[str, str]]],
) -> None:
    """When topology ids are preserved, the verification block is green."""
    result = _geometry_verification(
        unchanged_topology,
        unchanged_topology,
        step_bytes=b"step",
        stl_bytes=b"stl",
        glb_bytes=b"glb",
    )

    assert result["topology_preserved"] is True
    assert result["stale_reference_risk"] is False
    assert result["face_edge_survival"]["face"]["survived_count"] == 2
    assert result["face_edge_survival"]["edge"]["survived_count"] == 1
    assert result["export_sanity"]["status"] == "pass"


def test_changed_topology_reports_removed_and_added_ids(
    unchanged_topology: dict[str, list[dict[str, str]]],
    changed_topology: dict[str, list[dict[str, str]]],
) -> None:
    """When topology ids change, removed sample ids are surfaced."""
    result = _geometry_verification(
        unchanged_topology,
        changed_topology,
        step_bytes=b"step",
        stl_bytes=b"stl",
    )

    assert result["topology_preserved"] is False
    assert result["stale_reference_risk"] is True
    face_summary = result["face_edge_survival"]["face"]
    assert face_summary["before_count"] == 2
    assert face_summary["after_count"] == 2
    assert face_summary["removed_count"] == 1
    assert "face_002" in face_summary["sample_removed"]
    edge_summary = result["face_edge_survival"]["edge"]
    assert edge_summary["removed_count"] == 1
    assert "edge_001" in edge_summary["sample_removed"]


def test_referenced_face_id_statuses(
    unchanged_topology: dict[str, list[dict[str, str]]],
    changed_topology: dict[str, list[dict[str, str]]],
) -> None:
    """Referenced ids are reported individually as survived, lost, new, or unknown."""
    result = _geometry_verification(
        unchanged_topology,
        changed_topology,
        referenced_face_ids=["face_001", "face_002", "face_003"],
    )

    referenced = {r["id"]: r["status"] for r in result["face_edge_survival"]["face"]["referenced"]}
    assert referenced["face_001"] == "survived"
    assert referenced["face_002"] == "lost"
    assert referenced["face_003"] == "new"


def test_unknown_reference_index_is_not_false(
    unchanged_topology: dict[str, list[dict[str, str]]],
) -> None:
    """A referenced id not present in either topology is reported unknown."""
    result = _geometry_verification(
        unchanged_topology,
        unchanged_topology,
        referenced_face_ids=["ghost_face_999"],
    )

    referenced = result["face_edge_survival"]["face"]["referenced"]
    assert len(referenced) == 1
    assert referenced[0]["status"] == "unknown"


def test_export_sanity_fail_when_no_exports() -> None:
    """Missing exports produce a failing export sanity status."""
    topo = _make_topo([{"id": "face_001", "type": "face"}])
    result = _geometry_verification(topo, topo)
    assert result["export_sanity"]["status"] == "fail"
    assert result["export_sanity"]["step_exported"] is False


def test_export_sanity_warn_when_partial_export() -> None:
    """Only STEP or only STL produces a warning."""
    topo = _make_topo([{"id": "face_001", "type": "face"}])
    result = _geometry_verification(topo, topo, step_bytes=b"step")
    assert result["export_sanity"]["status"] == "warn"


def test_brep_validity_is_honestly_unknown() -> None:
    """The block does not claim full B-Rep validity."""
    topo = _make_topo([{"id": "face_001", "type": "face"}])
    result = _geometry_verification(topo, topo, step_bytes=b"step", stl_bytes=b"stl")
    assert result["brep_validity"]["status"] == "unknown"
    assert "BRepCheck" in result["brep_validity"]["detail"]


def test_entity_survival_summary_with_referenced_ids() -> None:
    """The low-level summary helper correctly classifies each referenced id."""
    before = _make_topo([
        {"id": "face_001", "type": "face"},
        {"id": "face_002", "type": "face"},
    ])
    after = _make_topo([
        {"id": "face_001", "type": "face"},
        {"id": "face_003", "type": "face"},
    ])
    summary = _entity_survival_summary(
        before, after, "face", referenced_ids=["face_001", "face_002", "face_003", "face_999"]
    )

    assert summary["before_count"] == 2
    assert summary["after_count"] == 2
    assert summary["removed_count"] == 1
    statuses = {r["id"]: r["status"] for r in summary["referenced"]}
    assert statuses == {
        "face_001": "survived",
        "face_002": "lost",
        "face_003": "new",
        "face_999": "unknown",
    }


def test_feature_reference_ids_reads_geometry_refs() -> None:
    """Generated feature graphs store face references under geometry_refs."""
    feature = {
        "id": "feat_hole",
        "geometry_refs": {"faces": ["face_001", "face_002"]},
    }

    assert _feature_reference_ids(feature, "face") == ["face_001", "face_002"]


def test_feature_reference_ids_supports_direct_keys_and_deduplicates() -> None:
    """Legacy direct face_ids keys remain supported without duplicate output."""
    feature = {
        "id": "feat_legacy",
        "face_ids": ["face_001", "face_002"],
        "geometry_refs": {"faces": ["face_002", "face_003"]},
    }

    assert _feature_reference_ids(feature, "face") == ["face_001", "face_002", "face_003"]


def test_feature_reference_ids_reads_edge_refs() -> None:
    """Edge references are extracted from geometry_refs.edges."""
    feature = {
        "id": "feat_edge",
        "edge_ids": ["edge_001"],
        "geometry_refs": {"edges": ["edge_001", "edge_002"]},
    }

    assert _feature_reference_ids(feature, "edge") == ["edge_001", "edge_002"]


def test_feature_reference_ids_reads_entities_face_fallback() -> None:
    """Faces stored in geometry_refs.entities are picked up as a fallback."""
    feature = {
        "id": "feat_entities",
        "geometry_refs": {"entities": ["face_010", "edge_777", "face_011"]},
    }

    assert _feature_reference_ids(feature, "face") == ["face_010", "face_011"]


def _plane(fid: str, z: float, area: float) -> dict:
    return {"id": fid, "type": "face", "surface_type": "plane", "normal": [0.0, 0.0, 1.0],
            "area": area, "bounding_box": [0.0, 0.0, z, 10.0, 10.0, z]}


def test_a_thickness_edit_that_keeps_the_face_count_is_not_called_preserved() -> None:
    """Same ids by enumeration, different faces: what the write persists decides.

    Measured in a cold-agent trial: a plate-thickness edit returned "face 16/16
    survived, stale_reference_risk: false", then the package write re-identified
    the faces and retired every bound id (rib top face_016 -> face_030). Two
    look-alike +Z planes that both moved cannot be told apart, so the write
    gives them fresh ids; the receipt must say so.
    """
    before = _make_topo([
        {"id": "solid_001", "type": "solid"},
        _plane("face_001", 8.0, 9600.0),    # plate top
        _plane("face_002", 33.0, 300.0),    # rib top
    ])
    after = _make_topo([
        {"id": "solid_001", "type": "solid"},
        _plane("face_001", 12.0, 9600.0),   # same enumeration ids, moved faces
        _plane("face_002", 37.0, 300.0),
    ])

    result = _geometry_verification(
        before, after, step_bytes=b"step", stl_bytes=b"stl", glb_bytes=b"glb",
        referenced_face_ids=["face_002"],
    )

    assert result["stale_reference_risk"] is True
    faces = result["topology_change"]["entity_id_changes"]["face"]
    assert faces["survived_count"] == 0 and faces["removed_count"] == 2
    # The prediction works on a copy: the caller's topology is untouched.
    assert [e["id"] for e in after["entities"]] == ["solid_001", "face_001", "face_002"]


def test_a_face_that_did_not_move_keeps_its_id_in_the_report() -> None:
    before = _make_topo([_plane("face_001", 0.0, 100.0)])
    after = _make_topo([_plane("face_001", 0.0, 100.0)])

    result = _geometry_verification(before, after, step_bytes=b"s", stl_bytes=b"s")

    assert result["stale_reference_risk"] is False


def test_a_failed_face_id_prediction_reports_unknown_not_preserved(monkeypatch) -> None:
    """If the re-identification cannot run, raw enumeration ids prove nothing."""
    import app.topology_identity as identity

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(identity, "stabilize_topology_face_ids", _boom)
    before = _make_topo([_plane("face_001", 8.0, 9600.0)])
    after = _make_topo([_plane("face_001", 12.0, 9600.0)])

    result = _geometry_verification(before, after, step_bytes=b"s", stl_bytes=b"s")

    assert result["stale_reference_risk"] is None
    assert result["topology_preserved"] is None
    assert result["topology_change"]["face_ids_verified"] is False
