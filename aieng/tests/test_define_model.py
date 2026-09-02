from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from aieng.cli import main
from aieng.validate import validate_package


VALID_DEFINITION = {
    "model_id": "definition_simple_bracket",
    "label": "Definition-sourced simple bracket",
    "description": "A semantic bracket definition with no imported STEP geometry.",
    "coordinate_system": {
        "type": "cartesian",
        "handedness": "right",
        "units": {"length": "mm", "angle": "deg"},
        "origin": [0, 0, 0],
        "axes": {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]},
    },
    "material": {
        "name": "Al6061-T6",
        "properties": {
            "youngs_modulus_mpa": 69000,
            "poisson_ratio": 0.33,
            "density_kg_m3": 2700,
            "yield_strength_mpa": 276,
        },
    },
    "features": [
        {
            "feature_id": "feat_base_plate_001",
            "type": "base_plate",
            "name": "Base plate definition",
            "parameters": {"length_mm": 120, "width_mm": 80, "thickness_mm": 8},
            "intent": {"role": "primary_structural_base"},
        },
        {
            "feature_id": "feat_hole_pattern_001",
            "type": "mounting_hole_pattern",
            "name": "Mounting hole pattern definition",
            "parameters": {"count": 4, "diameter_mm": 10, "pitch_x_mm": 90, "pitch_y_mm": 50},
            "intent": {"role": "protected_mounting_interface"},
        },
        {
            "feature_id": "feat_load_interface_001",
            "type": "interface_face",
            "name": "Load interface definition",
            "parameters": {"nominal_area_mm2": 400},
            "intent": {"role": "load_application_region"},
        },
    ],
    "constraints": [
        {
            "id": "con_protect_mounting_001",
            "type": "protect_geometry",
            "target": "feat_hole_pattern_001",
            "reason": "Mounting pattern must remain unchanged.",
        },
        {
            "id": "con_static_target_001",
            "type": "simulation_target",
            "target": "sim_static_001",
            "reason": "Definition includes a target for future static analysis.",
            "metric": "max_von_mises_stress_mpa",
            "operator": "<=",
            "value": 120,
        },
    ],
}


def _write_definition(path: Path, data: dict = VALID_DEFINITION) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _read_json(package_path: Path, member: str) -> dict:
    with zipfile.ZipFile(package_path) as package:
        return json.loads(package.read(member))


def _read_yaml(package_path: Path, member: str) -> dict:
    with zipfile.ZipFile(package_path) as package:
        return yaml.safe_load(package.read(member))


def test_cli_define_creates_definition_sourced_package_without_step(tmp_path, capsys):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    output = capsys.readouterr().out
    assert "PASS created definition-sourced package" in output
    assert "PASS validation/completeness_report.json written" in output
    with zipfile.ZipFile(package_path) as package:
        names = set(package.namelist())

    assert "geometry/source.step" not in names
    assert "geometry/normalized.step" not in names
    assert "geometry/topology_map.json" not in names
    assert "graph/feature_graph.json" in names
    assert "graph/constraints.json" in names
    assert "engineering_context/material.yaml" in names
    assert "validation/status.yaml" in names
    assert "validation/completeness_report.json" in names
    assert "README_FOR_AI.md" in names


def test_define_manifest_records_definition_source_and_resources(tmp_path):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    manifest = _read_json(package_path, "manifest.json")
    assert manifest["source_mode"] == "definition"
    assert manifest["resources"]["graph"]["feature_graph"] == "graph/feature_graph.json"
    assert manifest["resources"]["graph"]["constraints"] == "graph/constraints.json"
    assert manifest["resources"]["engineering_context"]["material"] == "engineering_context/material.yaml"
    assert manifest["resources"]["validation"]["status"] == "validation/status.yaml"
    assert manifest["resources"]["validation"]["completeness_report"] == "validation/completeness_report.json"
    assert manifest["resources"]["ai"]["readme"] == "README_FOR_AI.md"


def test_define_populates_feature_graph_constraints_material_and_status(tmp_path):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    feature_graph = _read_json(package_path, "graph/feature_graph.json")
    constraints = _read_json(package_path, "graph/constraints.json")
    material = _read_yaml(package_path, "engineering_context/material.yaml")
    status = _read_yaml(package_path, "validation/status.yaml")
    completeness = _read_json(package_path, "validation/completeness_report.json")

    feature_ids = {feature["id"] for feature in feature_graph["features"]}
    assert feature_ids == {"feat_base_plate_001", "feat_hole_pattern_001", "feat_load_interface_001"}
    assert all(feature["geometry_refs"] == {} for feature in feature_graph["features"])
    assert all(feature["parameter_source"] == "agent_defined" for feature in feature_graph["features"])
    assert constraints["constraints"][0]["target"] == "feat_hole_pattern_001"
    assert material["name"] == "Al6061-T6"
    assert status["geometry_status"]["step_imported"] is False
    assert status["geometry_status"]["definition_sourced"] is True
    assert completeness["source_mode"] == "definition"
    assert completeness["conversion_mode"] == "best_effort"


def test_define_package_validates_with_geometry_warnings_not_failures(tmp_path, capsys):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0
    assert main(["validate", str(package_path)]) == 0

    output = capsys.readouterr().out
    assert "WARN geometry/source.step missing for definition-sourced package" in output
    assert "WARN geometry/topology_map.json missing for definition-sourced package" in output
    assert "PASS feature graph definition-sourced geometry references are semantic-only" in output
    assert "PASS validation/completeness_report.json semantic checks passed" in output


def test_define_completeness_report_explains_definition_missingness(tmp_path):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    completeness = _read_json(package_path, "validation/completeness_report.json")
    categories = {category["category"]: category for category in completeness["categories"]}

    assert categories["geometry"]["status"] == "missing"
    assert "definition-sourced package intentionally has no step geometry" in categories["geometry"]["notes"][0].lower()
    assert categories["topology"]["status"] == "missing"
    assert "until generated or imported CAD geometry exists" in categories["topology"]["notes"][0]
    assert categories["features"]["status"] == "available"
    assert "semantic-only" in categories["features"]["notes"][0]
    assert categories["simulation_setup"]["status"] == "partial"
    assert "engineering_context/material.yaml" in categories["simulation_setup"]["resources"]


def test_model_definition_schema_accepts_example_file():
    schema_path = (Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "model_definition.schema.json")
    example_path = Path("examples/definition_simple_bracket.yaml")

    assert schema_path.exists()
    assert example_path.exists()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    example = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    errors = list(Draft202012Validator(schema).iter_errors(example))
    assert errors == []


def test_example_definition_contains_mature_design_context():
    example = yaml.safe_load(Path("examples/definition_simple_bracket.yaml").read_text(encoding="utf-8"))

    assert example["provenance"]["source"] == "hand_authored_reference_definition"
    assert example["assumptions"]
    assert example["known_limitations"]
    assert example["design_requirements"]["mass_target"]["objective"] == "reduce_mass_without_changing_mounting_interface"
    assert example["manufacturing"]["process"] == "cnc_machining"
    assert example["simulation"]["type"] == "static_structural"

    hole_pattern = next(feature for feature in example["features"] if feature["feature_id"] == "feat_hole_pattern_001")
    assert hole_pattern["parameters"]["hole_centers_mm"]
    assert hole_pattern["relationships"][0]["target_feature_id"] == "feat_base_plate_001"


def test_define_preserves_mature_context_in_generated_resources(tmp_path):
    definition_path = Path("examples/definition_simple_bracket.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    feature_graph = _read_json(package_path, "graph/feature_graph.json")
    material = _read_yaml(package_path, "engineering_context/material.yaml")
    status = _read_yaml(package_path, "validation/status.yaml")
    constraints = _read_json(package_path, "graph/constraints.json")

    assert feature_graph["metadata"]["provenance"]["source"] == "hand_authored_reference_definition"
    assert "No STEP geometry has been generated from this definition." in feature_graph["metadata"]["known_limitations"]
    assert material["provenance"]["material_data_source"] == "typical_public_datasheet_values"
    assert material["manufacturing"]["process"] == "cnc_machining"
    assert material["design_requirements"]["mass_target"]["target_reduction_percent"] == 15
    assert status["engineering_context_status"]["assumptions_present"] is True
    assert status["engineering_context_status"]["manufacturing_intent_present"] is True
    assert status["engineering_context_status"]["simulation_intent_present"] is True
    assert constraints["assumptions"]


def test_readme_for_ai_explains_absent_step_geometry(tmp_path):
    definition_path = _write_definition(tmp_path / "definition.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    with zipfile.ZipFile(package_path) as package:
        readme = package.read("README_FOR_AI.md").decode("utf-8")

    assert "definition-sourced" in readme
    assert "No STEP geometry is present" in readme
    assert "must not be treated as solver or geometry validation evidence" in readme


def test_readme_for_ai_summarizes_mature_definition_context(tmp_path):
    definition_path = Path("examples/definition_simple_bracket.yaml")
    package_path = tmp_path / "definition_simple_bracket.aieng"

    assert main(["define", str(definition_path), "--out", str(package_path)]) == 0

    with zipfile.ZipFile(package_path) as package:
        readme = package.read("README_FOR_AI.md").decode("utf-8")

    assert "Design requirements" in readme
    assert "Manufacturing intent" in readme
    assert "Simulation intent" in readme
    assert "Known limitations" in readme
