from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from aieng.cli import main
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.aag import AAG_PATH, build_aag_package
from aieng.graph.feature_graph import FEATURE_GRAPH_PATH, recognize_features_package
from aieng.validate import validate_package

FAKE_STEP_CONTENT = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def _write_fake_step(path: Path) -> Path:
    path.write_bytes(FAKE_STEP_CONTENT)
    return path


def _package_with_topology(tmp_path: Path) -> Path:
    step_path = _write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)
    extract_topology_package(package_path)
    return package_path


def _read_json_member(package_path: Path, member: str) -> dict:
    with zipfile.ZipFile(package_path) as package:
        return json.loads(package.read(member))


def _rewrite_member(package_path: Path, member: str, payload: dict) -> None:
    with zipfile.ZipFile(package_path, "r") as src:
        infos = src.infolist()
        blobs = {info.filename: (info, b"" if info.is_dir() else src.read(info.filename)) for info in infos}

    blobs[member] = (None, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for filename, (info, data) in blobs.items():
            if filename == member:
                dst.writestr(member, data)
            elif info is not None:
                dst.writestr(info, data)


def test_build_aag_happy_path_on_mock_topology(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)

    with zipfile.ZipFile(package_path) as package:
        names = set(package.namelist())
        assert AAG_PATH in names
        manifest = json.loads(package.read("manifest.json"))

    assert manifest["resources"]["graph"]["aag"] == AAG_PATH
    aag = _read_json_member(package_path, AAG_PATH)
    assert len(aag["nodes"]) > 0
    assert isinstance(aag["arcs"], list)


def test_build_aag_fails_when_topology_missing(tmp_path):
    step_path = _write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)

    with pytest.raises(FileNotFoundError, match="geometry/topology_map.json missing"):
        build_aag_package(package_path)


def test_build_aag_overwrite_behavior(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)

    with pytest.raises(FileExistsError, match="use --overwrite"):
        build_aag_package(package_path)

    build_aag_package(package_path, overwrite=True)
    aag = _read_json_member(package_path, AAG_PATH)
    assert aag["source_topology_map"] == "geometry/topology_map.json"


def test_aag_conforms_to_schema(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)
    aag = _read_json_member(package_path, AAG_PATH)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "aag.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(aag))
    assert errors == []


def test_validator_catches_unresolved_face_refs(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)
    aag = _read_json_member(package_path, AAG_PATH)
    aag["nodes"][0]["topology_entity_id"] = "face_does_not_exist"
    _rewrite_member(package_path, AAG_PATH, aag)

    report = validate_package(package_path)
    rendered = report.render()
    assert "unknown or non-face topology entity" in rendered


def test_validator_catches_unresolved_arc_node_refs(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)
    aag = _read_json_member(package_path, AAG_PATH)
    if not aag["arcs"]:
        pytest.skip("mock topology produced no arcs")
    aag["arcs"][0]["source_node"] = "node_missing"
    _rewrite_member(package_path, AAG_PATH, aag)

    report = validate_package(package_path)
    rendered = report.render()
    assert "unknown source_node" in rendered


def test_validator_catches_unresolved_shared_edge_ids(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)
    aag = _read_json_member(package_path, AAG_PATH)
    if not aag["arcs"]:
        pytest.skip("mock topology produced no arcs")
    aag["arcs"][0]["shared_edge_ids"] = ["edge_missing"]
    _rewrite_member(package_path, AAG_PATH, aag)

    report = validate_package(package_path)
    rendered = report.render()
    assert "unknown shared_edge_ids" in rendered


def test_feature_recognition_still_works_without_aag(tmp_path):
    package_path = _package_with_topology(tmp_path)
    recognize_features_package(package_path)
    feature_graph = _read_json_member(package_path, FEATURE_GRAPH_PATH)
    hole = next(feature for feature in feature_graph["features"] if feature["id"] == "feat_hole_001")
    assert "aag_node_ids" not in hole["recognition"]


def test_feature_recognition_works_with_aag_present(tmp_path):
    package_path = _package_with_topology(tmp_path)
    build_aag_package(package_path)
    recognize_features_package(package_path)
    feature_graph = _read_json_member(package_path, FEATURE_GRAPH_PATH)
    hole = next(feature for feature in feature_graph["features"] if feature["id"] == "feat_hole_001")
    assert isinstance(hole["recognition"].get("aag_node_ids"), list)


def test_cli_build_aag_happy_path_and_validate(tmp_path, capsys):
    package_path = _package_with_topology(tmp_path)

    assert main(["build-aag", str(package_path)]) == 0
    output = capsys.readouterr().out
    assert "PASS built attributed adjacency graph" in output
    assert "PASS graph/aag.json written" in output

    assert main(["validate", str(package_path)]) == 0
    rendered = capsys.readouterr().out
    assert "graph/aag.json conforms to aag.schema.json" in rendered


def test_cli_build_aag_fails_without_topology(tmp_path, capsys):
    step_path = _write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)

    assert main(["build-aag", str(package_path)]) == 2
    captured = capsys.readouterr()
    assert "geometry/topology_map.json missing" in captured.err


def test_occ_topology_contains_edge_face_refs_when_available(tmp_path):
    pytest.importorskip("OCP.STEPControl", reason="OCP/CadQuery not installed")

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer

    try:
        from OCP.STEPControl import STEPControl_AsIs
    except ImportError:
        from OCP.STEPControl import STEPControl_StepModelType
        STEPControl_AsIs = STEPControl_StepModelType.STEPControl_AsIs

    step_path = tmp_path / "box.step"
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape(), STEPControl_AsIs)
    assert writer.Write(str(step_path)) == IFSelect_RetDone

    package_path = tmp_path / "box.aieng"
    import_step_package(step_path, package_path)
    extract_topology_package(package_path, backend="occ")
    topology = _read_json_member(package_path, "geometry/topology_map.json")
    edges = [entity for entity in topology["entities"] if entity.get("type") == "edge"]
    assert any(isinstance(edge.get("face_ids"), list) and len(edge["face_ids"]) >= 1 for edge in edges)
