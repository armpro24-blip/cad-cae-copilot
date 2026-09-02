from __future__ import annotations

from pathlib import Path

import json
import zipfile

from jsonschema import Draft202012Validator

from aieng.cli import main
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import (
    TOPOLOGY_MAP_PATH,
    MockTopologyExtractor,
    OCCBasedTopologyExtractor,
    extract_topology_package,
)
from aieng.package import create_package, read_manifest
from aieng.validate import validate_package


FAKE_STEP_CONTENT = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def write_fake_step(path):
    path.write_bytes(FAKE_STEP_CONTENT)
    return path


def imported_package(tmp_path):
    step_path = write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)
    return package_path


def read_topology_map(package_path):
    with zipfile.ZipFile(package_path) as package:
        return json.loads(package.read(TOPOLOGY_MAP_PATH))


def test_extract_topology_happy_path_after_import_step(tmp_path):
    package_path = imported_package(tmp_path)

    extract_topology_package(package_path)

    topology_map = read_topology_map(package_path)
    assert topology_map["format_version"] == "0.1.0"
    assert topology_map["metadata"]["extraction_backend"] == "mock"
    entity_ids = {entity["id"] for entity in topology_map["entities"]}
    assert "body_001" in entity_ids
    assert "face_base_top" in entity_ids
    assert "face_hole_001_cyl" in entity_ids


def test_extract_topology_fails_if_package_does_not_exist(tmp_path):
    try:
        extract_topology_package(tmp_path / "missing.aieng")
    except FileNotFoundError as exc:
        assert "package does not exist" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_extract_topology_fails_if_normalized_step_missing(tmp_path):
    package_path = tmp_path / "empty.aieng"
    create_package("empty", package_path)

    try:
        extract_topology_package(package_path)
    except FileNotFoundError as exc:
        assert "geometry/normalized.step missing" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_extract_topology_writes_topology_map(tmp_path):
    package_path = imported_package(tmp_path)

    extract_topology_package(package_path)

    with zipfile.ZipFile(package_path) as package:
        assert TOPOLOGY_MAP_PATH in set(package.namelist())


def test_extract_topology_manifest_references_topology_map(tmp_path):
    package_path = imported_package(tmp_path)

    extract_topology_package(package_path)

    manifest = read_manifest(package_path)
    assert manifest["resources"]["geometry"]["topology_map"] == TOPOLOGY_MAP_PATH
    assert manifest["resources"]["geometry"]["source"] == "geometry/source.step"
    assert manifest["resources"]["geometry"]["normalized"] == "geometry/normalized.step"


def test_topology_map_conforms_to_schema(tmp_path):
    package_path = imported_package(tmp_path)
    extract_topology_package(package_path)
    topology_map = read_topology_map(package_path)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "topology_map.schema.json").read_text())

    errors = list(Draft202012Validator(schema).iter_errors(topology_map))

    assert errors == []


def test_topology_entity_ids_are_unique(tmp_path):
    package_path = imported_package(tmp_path)
    extract_topology_package(package_path)
    topology_map = read_topology_map(package_path)

    entity_ids = [entity["id"] for entity in topology_map["entities"]]

    assert len(entity_ids) == len(set(entity_ids))


def test_extract_topology_does_not_overwrite_existing_topology_map_by_default(tmp_path):
    package_path = imported_package(tmp_path)
    extract_topology_package(package_path)
    with zipfile.ZipFile(package_path) as package:
        original_topology = package.read(TOPOLOGY_MAP_PATH)

    try:
        extract_topology_package(package_path)
    except FileExistsError as exc:
        assert "use --overwrite" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")

    with zipfile.ZipFile(package_path) as package:
        assert package.read(TOPOLOGY_MAP_PATH) == original_topology


def test_extract_topology_overwrites_only_with_overwrite(tmp_path):
    package_path = imported_package(tmp_path)
    extract_topology_package(package_path)

    extract_topology_package(package_path, overwrite=True)

    topology_map = read_topology_map(package_path)
    assert topology_map["metadata"]["extraction_backend"] == "mock"


def test_cli_extract_topology_happy_path_and_validate(tmp_path, capsys):
    package_path = imported_package(tmp_path)

    assert main(["extract-topology", str(package_path), "--backend", "mock"]) == 0
    output = capsys.readouterr().out
    assert "PASS extracted mock topology" in output
    assert "PASS geometry/topology_map.json written" in output

    assert main(["validate", str(package_path)]) == 0
    validate_output = capsys.readouterr().out
    assert "PASS geometry/topology_map.json exists" in validate_output
    assert "PASS geometry/topology_map.json conforms to topology_map.schema.json" in validate_output
    assert "PASS topology entity IDs are unique" in validate_output


def test_cli_extract_topology_does_not_overwrite_by_default(tmp_path, capsys):
    package_path = imported_package(tmp_path)
    assert main(["extract-topology", str(package_path), "--backend", "mock"]) == 0
    capsys.readouterr()

    assert main(["extract-topology", str(package_path), "--backend", "mock"]) == 2
    captured = capsys.readouterr()
    assert "FAIL geometry/topology_map.json already exists" in captured.err


def test_cli_extract_topology_overwrites_with_overwrite(tmp_path, capsys):
    package_path = imported_package(tmp_path)
    assert main(["extract-topology", str(package_path), "--backend", "mock"]) == 0
    capsys.readouterr()

    assert main(["extract-topology", str(package_path), "--overwrite", "--backend", "mock"]) == 0
    output = capsys.readouterr().out
    assert "PASS geometry/topology_map.json written" in output


def test_validate_passes_after_topology_extraction(tmp_path):
    package_path = imported_package(tmp_path)
    extract_topology_package(package_path)

    report = validate_package(package_path)
    rendered = report.render()

    assert report.ok
    assert "PASS geometry/topology_map.json exists" in rendered
    assert "PASS topology entity type-specific fields are present" in rendered


def test_mock_topology_contains_planar_and_cylindrical_faces_and_edges():
    topology_map = MockTopologyExtractor().extract(b"not parsed")
    entities = topology_map["entities"]
    plane_faces = [entity for entity in entities if entity.get("surface_type") == "plane"]
    cylinder_faces = [entity for entity in entities if entity.get("surface_type") == "cylinder"]
    edges = [entity for entity in entities if entity.get("type") == "edge"]

    assert len(plane_faces) >= 4
    assert len(cylinder_faces) >= 4
    assert edges
    assert all("normal" in entity for entity in plane_faces)
    assert all("radius" in entity and "axis" in entity for entity in cylinder_faces)


def test_occ_based_topology_extractor_raises_not_implemented():
    # When OCP is unavailable: NotImplementedError. When available but bytes are invalid STEP: ValueError.
    try:
        OCCBasedTopologyExtractor().extract(b"unused")
    except (NotImplementedError, ValueError):
        pass  # both are valid outcomes depending on OCP availability
    else:
        raise AssertionError("expected NotImplementedError or ValueError")
