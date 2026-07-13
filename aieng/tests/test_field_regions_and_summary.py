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
) -> Path:
    """Build a package with source deck + cae_mapping so feature_ref can be resolved."""
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
    new.replace(pkg_path)
    return pkg_path


def test_feature_ref_resolved_when_mapping_evidence_exists(tmp_path: Path) -> None:
    """When source deck NSETs + cae_mapping evidence exists, cluster feature_ref is filled."""
    source_deck = (
        "*NODE\n"
        "1, 0.0, 0.0, 0.0\n"
        "2, 0.1, 0.0, 0.0\n"
        "3, 10.0, 0.0, 0.0\n"
        "*ELEMENT, TYPE=C3D4, ELSET=E_ALL\n"
        "1, 1, 2, 3\n"
        "*NSET, NSET=N_FILLET\n"
        "1, 2\n"
        "*NSET, NSET=N_BASE\n"
        "3\n"
    )
    mappings = [
        {
            "cae_entity": "N_FILLET",
            "cae_type": "boundary_condition_target",
            "maps_to": {"feature_id": "feat_fillet_001"},
            "mapping_status": "mapped",
            "mapping_method": "named_selection",
            "confidence": 1.0,
        },
        {
            "cae_entity": "N_BASE",
            "cae_type": "load_target",
            "maps_to": {"feature_id": "feat_base_001"},
            "mapping_status": "mapped",
            "mapping_method": "named_selection",
            "confidence": 1.0,
        },
    ]
    pkg = _package_with_cae_mapping(tmp_path, source_deck=source_deck, mappings=mappings)

    frd = tmp_path / "job.frd"
    coords = {1: (0, 0, 0), 2: (0.1, 0, 0), 3: (10, 0, 0)}
    stress = {1: 100.0, 2: 99.0, 3: 1.0}  # cluster forms around peak nodes 1 & 2
    frd.write_text(_make_frd(coords, stress), encoding="utf-8")

    result = extract_field_regions_package(pkg, frd, threshold_percentile=80)
    assert result["cluster_count"] == 1
    with zipfile.ZipFile(pkg) as zf:
        regions = json.loads(zf.read("results/field_regions.json"))
    cluster = regions["clusters"][0]
    assert cluster["feature_ref"] == "feat_fillet_001", (
        "peak node 1 belongs to N_FILLET → feat_fillet_001"
    )


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
