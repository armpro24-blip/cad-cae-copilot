from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.cae_field_summary import generate_field_summary, write_field_summary_package
from aieng.schema_versions import FIELD_REGIONS_SCHEMA, FIELD_SUMMARY_SCHEMA
from aieng.simulation.field_region_extractor import (
    FieldRegionError,
    extract_field_regions_package,
)
from aieng.validate import validate_package


def _frd_value(v: float) -> str:
    return f"{v:12.5E}"


def _coord_line(node_id: int, xyz: tuple[float, float, float]) -> str:
    return "    -1" + f"{node_id:12d}" + "".join(_frd_value(v) for v in xyz)


def _data_line(node_id: int, values: list[float]) -> str:
    return "    -1" + f"{node_id:12d}" + "".join(_frd_value(v) for v in values)


def _make_frd(coords: dict[int, tuple[float, float, float]], stress: dict[int, float] | None) -> str:
    lines = ["    1C"]
    for node_id, xyz in coords.items():
        lines.append(_coord_line(node_id, xyz))
    if stress is not None:
        lines += [
            "    -4  S           6    1",
            "    -5  SXX         1    4    1    1",
            "    -5  SYY         1    4    2    1",
            "    -5  SZZ         1    4    3    1",
            "    -5  SXY         1    4    4    1",
            "    -5  SXZ         1    4    5    1",
            "    -5  SYZ         1    4    6    1",
        ]
        for node_id, value in stress.items():
            lines.append(_data_line(node_id, [value, 0, 0, 0, 0, 0]))
        lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def _package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({
                "model_id": "field_test",
                "format_version": "0.1.0",
                "units": {"length": "mm", "mass": "kg", "force": "N", "stress": "MPa"},
                "resources": {"results": {}},
                "created_by": {"tool": "test", "created_at": "2026-01-01T00:00:00Z"},
            }),
        )
        zf.writestr("results/", b"")
    return path


def test_extracts_one_cluster_and_validates(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)}, {1: 100, 2: 99, 3: 1}), encoding="utf-8")

    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] == 1

    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    assert regions["schema_version"] == FIELD_REGIONS_SCHEMA
    assert regions["clusters"][0]["feature_ref"] is None
    assert validate_package(pkg).ok


def test_extracts_three_clusters(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    coords = {1: (0, 0, 0), 2: (10, 0, 0), 3: (20, 0, 0), 4: (30, 0, 0), 5: (40, 0, 0)}
    stress = {1: 100, 2: 99, 3: 98, 4: 1, 5: 0}
    frd.write_text(_make_frd(coords, stress), encoding="utf-8")

    result = extract_field_regions_package(pkg, frd, max_clusters=3, threshold_percentile=50)
    assert result["cluster_count"] == 3


def test_no_field_raises_honestly(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0)}, None), encoding="utf-8")
    with pytest.raises(FieldRegionError):
        extract_field_regions_package(pkg, frd)


def test_missing_frd_raises(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    with pytest.raises(FileNotFoundError):
        extract_field_regions_package(pkg, tmp_path / "missing.frd")


def test_malformed_frd_raises(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text("not an frd\n", encoding="utf-8")
    with pytest.raises(FieldRegionError):
        extract_field_regions_package(pkg, frd)


def _ccx_value(v: float) -> str:
    return f"{v:12.5E}"


def _make_standard_ccx_frd(
    coords: dict[int, tuple[float, float, float]], stress: dict[int, float]
) -> str:
    """Standard CalculiX output dialect: ' -1' key + I10 node field, with the
    nodal block opened by a 2C header, closed by -3, and followed by a 3C
    element block whose -1 records must NOT be parsed as node coordinates."""
    lines = [
        "    1C",
        "    1UPGM               CalculiX",
        f"    2C                    {len(coords):10d}                                     1",
    ]
    for nid, xyz in coords.items():
        lines.append(" -1" + f"{nid:10d}" + "".join(_ccx_value(v) for v in xyz))
    lines.append(" -3")
    lines += [
        f"    3C                    {1:10d}                                     1",
        " -1" + f"{1:10d}" + f"{4:5d}{0:5d}{1:5d}",
        " -2" + "".join(f"{nid:10d}" for nid in list(coords)[:4]),
        " -3",
    ]
    lines += [
        "  100CL  101 1.00000000         327                     0    1           1",
        " -4  S           6    1",
        " -5  SXX         1    4    1    1",
        " -5  SYY         1    4    2    1",
        " -5  SZZ         1    4    3    1",
        " -5  SXY         1    4    4    1",
        " -5  SXZ         1    4    5    1",
        " -5  SYZ         1    4    6    1",
    ]
    for nid, value in stress.items():
        lines.append(
            " -1" + f"{nid:10d}" + "".join(_ccx_value(v) for v in [value, 0, 0, 0, 0, 0])
        )
    lines.append(" -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def test_standard_ccx_dialect_node_coords_parse(tmp_path: Path) -> None:
    """Real ccx FRD output (short keys, I10 node ids, 2C/3C mesh blocks) parses.

    Regression for the #368 value-demo finding: the viewer field endpoint and
    cae.extract_field_regions fell back to synthetic/mock because this parser
    only understood the wide test dialect and returned zero node coordinates
    for genuine CalculiX output.
    """
    from aieng.simulation.field_region_extractor import _extract_node_coords_from_frd

    coords = {
        1: (0.0, 0.0, 0.0),
        2: (0.1, 0.0, 0.0),
        3: (10.0, 0.0, 0.0),
        4: (10.1, 0.0, 0.0),
    }
    stress = {1: 100.0, 2: 99.0, 3: 5.0, 4: 1.0}
    frd = tmp_path / "job.frd"
    frd.write_text(_make_standard_ccx_frd(coords, stress), encoding="utf-8")

    parsed = _extract_node_coords_from_frd(frd)
    assert set(parsed) == set(coords)  # element-block -1 records not included
    assert parsed[3] == (10.0, 0.0, 0.0)

    pkg = _package(tmp_path / "test.aieng")
    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] == 1


def test_field_summary_writes_json_and_markdown(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (1, 0, 0)}, {1: 100, 2: 1}), encoding="utf-8")
    extract_field_regions_package(pkg, frd)

    write_field_summary_package(pkg)
    summary = generate_field_summary(pkg)

    assert summary["schema_version"] == FIELD_SUMMARY_SCHEMA
    assert summary["llm_summary"]["one_line"]
    with zipfile.ZipFile(pkg) as zf:
        assert "results/field_summary.json" in zf.namelist()
        assert "results/field_summary.md" in zf.namelist()
    assert validate_package(pkg).ok


def _package_with_cae_mapping(
    tmp_path: Path,
    *,
    source_deck: str,
    mappings: list[dict],
    feature_graph: dict | None = None,
) -> Path:
    """Build a package with source deck + cae_mapping (+ optional feature graph)."""
    pkg_path = _package(tmp_path / "mapped.aieng")
    new = tmp_path / "mapped_new.aieng"
    with zipfile.ZipFile(pkg_path, "r") as src, zipfile.ZipFile(
        new, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("simulation/cae_imports/source_solver_deck.inp", source_deck)
        dst.writestr(
            "simulation/cae_mapping.json",
            json.dumps({
                "format": "aieng.cae_mapping",
                "format_version": "0.1.0",
                "source_files": ["simulation/cae_imports/source_solver_deck.inp"],
                "mappings": mappings,
                "notes": [],
            }),
        )
        if feature_graph is not None:
            dst.writestr("graph/feature_graph.json", json.dumps(feature_graph))
    new.replace(pkg_path)
    return pkg_path


_TWO_SET_DECK = (
    "*NODE\n"
    "1, 0.0, 0.0, 0.0\n"
    "2, 0.1, 0.0, 0.0\n"
    "3, 10.0, 0.0, 0.0\n"
    "*ELEMENT, TYPE=C3D4, ELSET=E_ALL\n"
    "1, 1, 2, 3\n"
    "*NSET, NSET=N_FIX\n"
    "1, 2\n"
    "*NSET, NSET=N_LOAD\n"
    "3\n"
)
_PART_GRAPH = {
    "features": [
        {"id": "feat_body_001", "type": "named_part", "name": "base_plate",
         "geometry_refs": {"body": "body_001", "faces": ["face_001", "face_002", "face_003"]}},
        {"id": "feat_body_002", "type": "named_part", "name": "rib_main",
         "geometry_refs": {"body": "body_002", "faces": ["face_010", "face_011"]}},
    ]
}


def _extract_peak_cluster(tmp_path: Path, mappings: list[dict], feature_graph: dict | None) -> dict:
    pkg = _package_with_cae_mapping(
        tmp_path, source_deck=_TWO_SET_DECK, mappings=mappings, feature_graph=feature_graph,
    )
    frd = tmp_path / "job.frd"
    coords = {1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)}
    stress = {1: 100.0, 2: 99.0, 3: 1.0}  # cluster forms around peak nodes 1 & 2 (N_FIX)
    frd.write_text(_make_frd(coords, stress), encoding="utf-8")
    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] == 1
    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    # Only the artifact under test: these minimal fixtures are not full packages.
    import jsonschema
    from importlib.resources import files
    schema = json.loads(files("aieng.schemas").joinpath("field_regions.schema.json").read_text())
    jsonschema.validate(regions, schema)
    return regions["clusters"][0]


def test_feature_ref_resolved_through_mapped_faces_to_their_part(tmp_path: Path) -> None:
    """The shape the workbench writes: maps_to.cae_target_id + face_ids. The target is
    reported as cae_target_ref; the FEATURE is the named part that owns those faces."""
    mappings = [
        {"cae_entity": "N_FIX", "cae_type": "boundary_condition_target",
         "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001"],
         "mapping_status": "mapped", "confidence": 1.0},
        {"cae_entity": "N_LOAD", "cae_type": "load_target",
         "maps_to": {"cae_target_id": "load_001"}, "face_ids": ["face_010"],
         "mapping_status": "mapped", "confidence": 1.0},
    ]
    cluster = _extract_peak_cluster(tmp_path, mappings, _PART_GRAPH)
    assert cluster["cae_target_ref"] == "bc_001"
    assert cluster["feature_ref"] == "feat_body_001"  # face_001 lies on base_plate


def test_a_cae_target_id_is_never_reported_as_a_feature(tmp_path: Path) -> None:
    """The legacy `maps_to.feature_id` spelling held a BC/load id, never a CAD feature.
    It used to be emitted as feature_ref ('near feature bc_001'), which the copilot
    recommendation chain then passed on as a featureId to edit."""
    mappings = [
        {"cae_entity": "N_FIX", "cae_type": "boundary_condition_target",
         "maps_to": {"feature_id": "bc_001"}, "mapping_status": "mapped", "confidence": 1.0},
        {"cae_entity": "N_LOAD", "cae_type": "load_target",
         "maps_to": {"feature_id": "load_001"}, "mapping_status": "mapped", "confidence": 1.0},
    ]
    cluster = _extract_peak_cluster(tmp_path, mappings, _PART_GRAPH)
    assert cluster["cae_target_ref"] == "bc_001"  # legacy spelling still read as a target
    assert cluster["feature_ref"] is None  # no face evidence -> no feature


def test_one_nset_serving_two_targets_is_ambiguous(tmp_path: Path) -> None:
    """A load and a BC on the same faces share an NSET; the last mapping used to
    win silently. The peak cannot name either one."""
    mappings = [
        {"cae_entity": "N_FIX", "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001"]},
        {"cae_entity": "N_FIX", "maps_to": {"cae_target_id": "load_001"}, "face_ids": ["face_001"]},
    ]
    cluster = _extract_peak_cluster(tmp_path, mappings, _PART_GRAPH)
    assert cluster["cae_target_ref"] is None
    assert cluster["feature_ref"] is None


def test_an_unreadable_feature_graph_keeps_the_target(tmp_path: Path) -> None:
    mappings = [{"cae_entity": "N_FIX", "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001"]}]
    pkg = _package_with_cae_mapping(tmp_path, source_deck=_TWO_SET_DECK, mappings=mappings)
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr("graph/feature_graph.json", "{not json")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)},
                             {1: 100.0, 2: 99.0, 3: 1.0}), encoding="utf-8")
    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    with zipfile.ZipFile(pkg) as zf:
        cluster = json.loads(zf.read("results/field_regions.json"))["clusters"][0]
    assert cluster["cae_target_ref"] == "bc_001"
    assert cluster["feature_ref"] is None
    assert any("feature_graph.json is not valid JSON" in w for w in result.get("warnings", []))


def test_array_form_geometry_refs_is_ownership_evidence(tmp_path: Path) -> None:
    """The feature-graph schema keeps a plain list of topology ids as a valid form."""
    graph = {"features": [{"id": "feat_body_001", "type": "named_part", "name": "base_plate",
                           "geometry_refs": ["face_001", "face_002", "edge_007"]}]}
    mappings = [{"cae_entity": "N_FIX", "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001"]}]
    cluster = _extract_peak_cluster(tmp_path, mappings, graph)
    assert cluster["feature_ref"] == "feat_body_001"


def test_faces_spanning_two_parts_resolve_no_feature(tmp_path: Path) -> None:
    mappings = [
        {"cae_entity": "N_FIX", "cae_type": "boundary_condition_target",
         "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001", "face_010"],
         "mapping_status": "mapped", "confidence": 1.0},
    ]
    cluster = _extract_peak_cluster(tmp_path, mappings, _PART_GRAPH)
    assert cluster["cae_target_ref"] == "bc_001"
    assert cluster["feature_ref"] is None


def test_feature_ref_null_when_peak_node_in_no_mapped_nset(tmp_path: Path) -> None:
    """When the peak node has no mapped NSET, feature_ref stays null."""
    source_deck = (
        "*NODE\n"
        "1, 0.0, 0.0, 0.0\n"
        "*NSET, NSET=N_UNMAPPED\n"
        "1\n"
    )
    mappings = [
        {
            "cae_entity": "N_OTHER",  # does not match N_UNMAPPED
            "cae_type": "boundary_condition_target",
            "maps_to": {"feature_id": "feat_other_001"},
            "mapping_status": "mapped",
            "mapping_method": "named_selection",
            "confidence": 1.0,
        }
    ]
    pkg = _package_with_cae_mapping(tmp_path, source_deck=source_deck, mappings=mappings)

    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (1, 0, 0)}, {1: 100.0, 2: 1.0}), encoding="utf-8")
    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] >= 1
    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    assert regions["clusters"][0]["feature_ref"] is None


def test_feature_ref_null_when_peak_node_in_ambiguous_mapped_nsets(tmp_path: Path) -> None:
    """Ambiguous mapping (one node belongs to two distinct-feature NSETs) stays null."""
    source_deck = (
        "*NODE\n"
        "1, 0.0, 0.0, 0.0\n"
        "*NSET, NSET=N_A\n"
        "1\n"
        "*NSET, NSET=N_B\n"
        "1\n"
    )
    mappings = [
        {
            "cae_entity": "N_A",
            "cae_type": "boundary_condition_target",
            "maps_to": {"feature_id": "feat_a"},
            "mapping_status": "mapped",
            "mapping_method": "named_selection",
            "confidence": 1.0,
        },
        {
            "cae_entity": "N_B",
            "cae_type": "load_target",
            "maps_to": {"feature_id": "feat_b"},
            "mapping_status": "mapped",
            "mapping_method": "named_selection",
            "confidence": 1.0,
        },
    ]
    pkg = _package_with_cae_mapping(tmp_path, source_deck=source_deck, mappings=mappings)
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (1, 0, 0)}, {1: 100.0, 2: 1.0}), encoding="utf-8")
    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] >= 1
    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    assert regions["clusters"][0]["feature_ref"] is None


def _add_result_summary(pkg: Path, targets_items: list[dict]) -> None:
    """Insert results/result_summary.json with a targets.items array into the package."""
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(pkg, "r") as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)
    members["results/result_summary.json"] = json.dumps({
        "schema_version": "0.3",
        "targets": {"present": True, "target_count": len(targets_items), "items": targets_items},
    }).encode()
    pkg.unlink()
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_targets_status_filled_when_result_summary_has_targets(tmp_path: Path) -> None:
    """Phase 34 + Phase 35 link: field_summary surfaces targets[*].met state."""
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (1, 0, 0)}, {1: 100, 2: 1}), encoding="utf-8")
    extract_field_regions_package(pkg, frd)
    _add_result_summary(pkg, [
        {
            "id": "stress_limit",
            "metric": "max_von_mises_stress",
            "operator": "<=",
            "target_value": 200.0,
            "actual_value": 180.0,
            "unit": "MPa",
            "met": True,
            "source": "results/computed_metrics.json",
        },
        {
            "id": "mass_target",
            "metric": "total_mass",
            "operator": "<=",
            "target_value": 1.0,
            "actual_value": None,
            "unit": "kg",
            "met": "unknown",
            "source": None,
        },
    ])
    summary = generate_field_summary(pkg)
    targets_status = summary["llm_summary"].get("targets_status")
    assert isinstance(targets_status, list)
    assert {t["id"] for t in targets_status} == {"stress_limit", "mass_target"}
    stress = next(t for t in targets_status if t["id"] == "stress_limit")
    assert stress["met"] is True
    mass = next(t for t in targets_status if t["id"] == "mass_target")
    assert mass["met"] == "unknown"


def test_targets_status_absent_when_result_summary_missing(tmp_path: Path) -> None:
    """Optional field: when result_summary.json is absent, targets_status stays absent."""
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (1, 0, 0)}, {1: 100, 2: 1}), encoding="utf-8")
    extract_field_regions_package(pkg, frd)
    summary = generate_field_summary(pkg)
    assert "targets_status" not in summary["llm_summary"]
    assert validate_package(pkg).ok


def test_uniform_field_is_not_presented_as_a_concentration(tmp_path: Path) -> None:
    # A bar in uniform tension: every node at ~100 MPa. A percentile cut always
    # selects nodes, so "high-stress clusters" came back with no hint that the
    # "high" nodes are within 0.4% of the median.
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 11)}
    stress = {i: 100.0 + 0.04 * i for i in coords}
    frd.write_text(_make_frd(coords, stress), encoding="utf-8")

    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)

    stats = result["field_statistics"]
    assert stats["near_uniform"] is True
    assert stats["peak_to_median_ratio"] == pytest.approx(100.4 / 100.22, abs=1e-4)
    assert any("near-uniform" in w for w in result["warnings"])
    assert result["clusters"][0]["peak_to_median_ratio"] < 1.1
    assert validate_package(pkg).ok


def test_summary_of_a_near_uniform_field_does_not_say_high_magnitude(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 11)}
    frd.write_text(_make_frd(coords, {i: 100.0 + 0.04 * i for i in coords}), encoding="utf-8")
    extract_field_regions_package(pkg, frd, threshold_percentile=80)

    summary = generate_field_summary(pkg)["llm_summary"]

    assert "high-magnitude" not in summary["one_line"]
    assert "near-uniform" in summary["one_line"]
    assert not any("High-magnitude" in r for r in summary["risks"])


def test_field_statistics_keep_small_values(tmp_path: Path) -> None:
    # 1e-7-scale values rounded to 6 decimals read as all zeros beside a
    # non-zero peak/median ratio.
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 11)}
    frd.write_text(_make_frd(coords, {i: 1e-7 * i for i in coords}), encoding="utf-8")

    stats = extract_field_regions_package(pkg, frd, threshold_percentile=80)["field_statistics"]

    assert stats["max"] == pytest.approx(1e-6, rel=1e-4)
    assert stats["median"] == pytest.approx(5.5e-7, rel=1e-4)
    assert stats["min"] == pytest.approx(1e-7, rel=1e-4)
    assert stats["threshold_value"] > 0
    assert validate_package(pkg).ok


def test_concentrated_field_reports_its_ratio(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    coords = {i: (float(i), 0.0, 0.0) for i in range(1, 11)}
    stress = {i: 10.0 for i in coords}
    stress[10] = 50.0  # a 5x hot spot
    frd.write_text(_make_frd(coords, stress), encoding="utf-8")

    result = extract_field_regions_package(pkg, frd, threshold_percentile=90)

    assert result["field_statistics"]["near_uniform"] is False
    assert result["clusters"][0]["peak_to_median_ratio"] == pytest.approx(5.0)
    assert not any("near-uniform" in w for w in result["warnings"])
    assert validate_package(pkg).ok


def test_ccx_field_name_stress_is_accepted(tmp_path: Path) -> None:
    # CalculiX names the block STRESS; field="STRESS" used to fall through to
    # "Unsupported metric" and raise.
    pkg = _package(tmp_path / "test.aieng")
    frd = tmp_path / "job.frd"
    frd.write_text(_make_frd({1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)},
                             {1: 100, 2: 99, 3: 1}), encoding="utf-8")
    result = extract_field_regions_package(pkg, frd, field="STRESS", threshold_percentile=80)
    assert result["clusters"][0]["magnitude"] == {"value": 100.0, "unit": "MPa"}


# The CURRENT mesh after a remesh: the same node ids sit elsewhere, so by id the
# earlier run's peak nodes 1 & 2 would read as members of N_LOAD.
_REMESHED_DECK = (
    "*NODE\n"
    "1, 10.0, 0.0, 0.0\n"
    "2, 9.9, 0.0, 0.0\n"
    "3, 0.0, 0.0, 0.0\n"
    "*ELEMENT, TYPE=C3D4, ELSET=E_ALL\n"
    "1, 1, 2, 3\n"
    "*NSET, NSET=N_FIX\n"
    "3\n"
    "*NSET, NSET=N_LOAD\n"
    "1, 2\n"
)
_TARGET_MAPPINGS = [
    {"cae_entity": "N_FIX", "maps_to": {"cae_target_id": "bc_001"}, "face_ids": ["face_001"]},
    {"cae_entity": "N_LOAD", "maps_to": {"cae_target_id": "load_001"}, "face_ids": ["face_010"]},
]


def _remeshed_package_with_run(tmp_path: Path) -> tuple[Path, Path]:
    """Source deck from the current mesh; run_001's own deck from the old one."""
    pkg = _package_with_cae_mapping(
        tmp_path, source_deck=_REMESHED_DECK, mappings=_TARGET_MAPPINGS,
        feature_graph=_PART_GRAPH,
    )
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr("simulation/runs/run_001/solver_input.inp", _TWO_SET_DECK)
    frd = tmp_path / "tmp_extracted.frd"   # what the tool hands over: a temp file
    coords = {1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)}
    frd.write_text(_make_frd(coords, {1: 100.0, 2: 99.0, 3: 1.0}), encoding="utf-8")
    return pkg, frd


def _regions(pkg: Path) -> dict:
    import jsonschema
    from importlib.resources import files

    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    schema = json.loads(files("aieng.schemas").joinpath("field_regions.schema.json").read_text())
    jsonschema.validate(regions, schema)
    return regions


def test_an_earlier_runs_frd_is_attributed_with_that_runs_node_sets(tmp_path: Path) -> None:
    """Found by a cold-agent trial: after a remesh, run_001's FRD was read against
    the source deck re-synthesised from the NEW mesh, and two different bolt holes
    were both attributed to bc_001."""
    pkg, frd = _remeshed_package_with_run(tmp_path)
    member = "simulation/runs/run_001/outputs/result.frd"

    extract_field_regions_package(pkg, frd, threshold_percentile=80, source_member=member)

    regions = _regions(pkg)
    [cluster] = regions["clusters"]
    assert cluster["cae_target_ref"] == "bc_001"      # by id the new mesh says load_001
    assert cluster["peak_location"] == {"node_id": 1, "x": 0.0, "y": 0.0, "z": 0.0}
    assert regions["source_frd"] == member            # not the temp file's name


def test_node_sets_from_another_mesh_attribute_nothing(tmp_path: Path) -> None:
    """With no run to tie the FRD to, the source deck is checked against the FRD's
    node coordinates; a different mesh gives no attribution rather than a wrong one."""
    pkg, frd = _remeshed_package_with_run(tmp_path)

    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)

    [cluster] = _regions(pkg)["clusters"]
    assert cluster["cae_target_ref"] is None and cluster["feature_ref"] is None
    assert any("different mesh" in w for w in result["warnings"]), result["warnings"]
