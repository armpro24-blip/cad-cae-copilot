from __future__ import annotations

from pathlib import Path
import json
import zipfile

import yaml
from jsonschema import Draft202012Validator

from aieng.ai.patch_proposer import propose_patch_package
from aieng.ai.summary_writer import summarize_package
from aieng.cli import main
from aieng.context.apply_context import apply_context_package
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.allowed_operations_catalog_writer import build_allowed_operations_catalog_package
from aieng.graph.feature_graph import recognize_features_package
from aieng.package import read_manifest
from aieng.validate import validate_package

FAKE_STEP_CONTENT = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
MASS_REDUCTION_INTENT = "Reduce mass by 15% while keeping mounting holes unchanged."

VALID_CONTEXT = {
    "material": "Al6061-T6",
    "protected_features": ["feat_hole_pattern_001"],
    "simulation": {
        "type": "static_structural",
        "fixed": ["feat_hole_pattern_001"],
        "loads": [
            {"target": "feat_base_plate_001", "type": "force", "value_n": 500, "direction": [1, 0, 0]}
        ],
    },
    "targets": {"max_von_mises_stress_mpa": 120},
    "assumptions": [
        "Mounting hole pattern is treated as fixed support.",
        "Load target is provided by user context.",
    ],
}


def write_fake_step(path):
    path.write_bytes(FAKE_STEP_CONTENT)
    return path


def write_context(path):
    path.write_text(yaml.safe_dump(VALID_CONTEXT, sort_keys=False), encoding="utf-8")
    return path


def phase5a_package(tmp_path):
    step_path = write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    context_path = write_context(tmp_path / "context.yaml")
    import_step_package(step_path, package_path)
    extract_topology_package(package_path, backend="mock")
    recognize_features_package(package_path)
    apply_context_package(package_path, context_path)
    summarize_package(package_path)
    return package_path


def read_json_member(package_path, member):
    with zipfile.ZipFile(package_path) as package:
        return json.loads(package.read(member))


def read_patch(package_path, member="ai/patches/patch_0001.json"):
    return read_json_member(package_path, member)


def write_json_member(package_path, member, payload):
    with zipfile.ZipFile(package_path, mode="r") as package:
        members = [
            (info, b"" if info.is_dir() else package.read(info.filename))
            for info in package.infolist()
            if info.filename != member
        ]

    with zipfile.ZipFile(package_path, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
        for info, data in members:
            package.writestr(info, data)
        package.writestr(member, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))


def test_propose_patch_happy_path_after_phase5a(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    assert patch["patch_id"] == "patch_0001"
    assert patch["status"] == "proposed"
    assert patch["operations"]


def test_propose_patch_fails_if_package_does_not_exist(tmp_path):
    try:
        propose_patch_package(tmp_path / "missing.aieng", MASS_REDUCTION_INTENT)
    except FileNotFoundError as exc:
        assert "package does not exist" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_propose_patch_fails_if_feature_graph_is_missing(tmp_path):
    step_path = write_fake_step(tmp_path / "bracket.step")
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)

    try:
        propose_patch_package(package_path, MASS_REDUCTION_INTENT)
    except FileNotFoundError as exc:
        assert "graph/feature_graph.json missing" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_propose_patch_creates_patch_0001(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    with zipfile.ZipFile(package_path) as package:
        assert "ai/patches/patch_0001.json" in set(package.namelist())


def test_repeated_propose_patch_creates_patch_0002(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)
    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    with zipfile.ZipFile(package_path) as package:
        names = set(package.namelist())
    assert "ai/patches/patch_0001.json" in names
    assert "ai/patches/patch_0002.json" in names
    assert read_patch(package_path, "ai/patches/patch_0002.json")["patch_id"] == "patch_0002"


def test_manifest_references_patch_proposal_files(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    manifest = read_manifest(package_path)
    assert manifest["resources"]["ai"]["patches"] == ["ai/patches/patch_0001.json"]
    assert manifest["resources"]["patches"] == ["ai/patches/patch_0001.json"]


def test_patch_proposal_conforms_to_schema(tmp_path):
    package_path = phase5a_package(tmp_path)
    propose_patch_package(package_path, MASS_REDUCTION_INTENT)
    patch = read_patch(package_path)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "patch_proposal.schema.json").read_text())

    errors = list(Draft202012Validator(schema).iter_errors(patch))

    assert errors == []


def test_patch_proposal_includes_original_user_intent(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    assert read_patch(package_path)["user_intent"] == MASS_REDUCTION_INTENT


def test_patch_proposal_status_is_proposed_for_mass_reduction(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    assert read_patch(package_path)["status"] == "proposed"


def test_patch_proposal_includes_no_geometry_modified_and_no_solver_run(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    assert patch["no_geometry_modified"] is True
    assert patch["no_solver_run"] is True
    assert "No geometry has been modified." in patch["warnings"]
    assert "No solver result has been attached." in patch["warnings"]


def test_patch_proposal_includes_protected_targets_checked(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    assert "feat_hole_pattern_001" in patch["protected_targets_checked"]
    assert patch["protected_target_checks"][0]["feature_id"] == "feat_hole_pattern_001"


def test_patch_proposal_avoids_protected_feature_ids(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    assert "feat_hole_pattern_001" in patch["protected_targets_avoided"]
    assert "feat_hole_pattern_001" in patch["operations"][0]["parameters"]["avoid_protected_features"]


def test_patch_proposal_does_not_target_protected_features_with_geometry_changing_operations(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    protected = set(patch["protected_targets_checked"])
    for operation in patch["operations"]:
        if operation["op"] in {"add_feature", "modify_parameter", "remove_feature"}:
            assert operation.get("target") not in protected
            assert operation.get("target_feature_id") not in protected


def test_patch_proposal_includes_required_validation_steps(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    for step in [
        "geometry_validity",
        "protected_region_integrity",
        "mesh_generation",
        "static_structural_analysis",
        "max_stress_constraint",
        "manufacturing_rule_check",
    ]:
        assert step in patch["requires_validation"]


def test_patch_proposal_expected_effects_include_mass_target_and_unknown_stress_risk(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    effects = read_patch(package_path)["expected_effects"]
    assert effects["mass_change_target_percent"] == -15
    assert effects["stress_risk"] == "unknown_requires_validation"


def test_unrecognized_intent_creates_needs_review_with_no_geometry_changing_operations(tmp_path):
    package_path = phase5a_package(tmp_path)

    propose_patch_package(package_path, "Make it more elegant.")

    patch = read_patch(package_path)
    assert patch["status"] == "needs_review"
    assert patch["operations"] == []
    assert "Intent was not recognized by the rule-based proposer." in patch["warnings"]


def test_cli_propose_patch_happy_path_and_validate(tmp_path, capsys):
    package_path = phase5a_package(tmp_path)

    assert main(["propose-patch", str(package_path), "--intent", MASS_REDUCTION_INTENT]) == 0
    output = capsys.readouterr().out
    assert "PASS generated structured patch proposal" in output
    assert "PASS ai/patches/patch proposal written" in output

    assert main(["validate", str(package_path)]) == 0
    validate_output = capsys.readouterr().out
    assert "PASS ai/patches/patch_0001.json exists" in validate_output
    assert "PASS ai/patches/patch_0001.json conforms to patch_proposal.schema.json" in validate_output
    assert "PASS ai/patches/patch_0001.json references known features and respects protected targets" in validate_output


def test_validate_passes_after_propose_patch(tmp_path):
    package_path = phase5a_package(tmp_path)
    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    report = validate_package(package_path)
    rendered = report.render()

    assert report.ok
    assert "PASS ai/patches/patch_0001.json references known features and respects protected targets" in rendered


def test_propose_patch_includes_allowed_operations_catalog_in_source_files_when_present(tmp_path):
    package_path = phase5a_package(tmp_path)
    build_allowed_operations_catalog_package(package_path)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)

    patch = read_patch(package_path)
    assert "graph/allowed_operations_catalog.json" in patch["source_files_consulted"]


def test_propose_patch_respects_forbidden_catalog_policy(tmp_path):
    package_path = phase5a_package(tmp_path)
    build_allowed_operations_catalog_package(package_path)

    catalog = read_json_member(package_path, "graph/allowed_operations_catalog.json")
    for entry in catalog.get("feature_operations", []):
        if entry.get("feature_id") != "feat_base_plate_001":
            continue
        for op in entry.get("operations", []):
            if op.get("operation_type") == "add_feature":
                op["status"] = "forbidden"
                op["reason"] = "test policy block"
    write_json_member(package_path, "graph/allowed_operations_catalog.json", catalog)

    propose_patch_package(package_path, MASS_REDUCTION_INTENT)
    patch = read_patch(package_path)

    assert patch["status"] == "needs_review"
    assert patch["operations"] == []
    assert any("forbidden by allowed_operations_catalog" in warning for warning in patch["warnings"])


def test_propose_patch_load_assignment_intent_uses_assign_load_operation(tmp_path):
    package_path = phase5a_package(tmp_path)
    assert main(["build-interface-graph", str(package_path)]) == 0
    build_allowed_operations_catalog_package(package_path)

    propose_patch_package(package_path, "Assign load to the load interface.")
    patch = read_patch(package_path)

    assert patch["status"] == "proposed"
    assert patch["operations"]
    assert patch["operations"][0]["op"] == "assign_load"
    assert patch["operations"][0]["target"] == "feat_base_plate_001"


def test_propose_patch_boundary_assignment_intent_uses_assign_boundary_condition_operation(tmp_path):
    package_path = phase5a_package(tmp_path)
    assert main(["build-interface-graph", str(package_path)]) == 0
    build_allowed_operations_catalog_package(package_path)

    propose_patch_package(package_path, "Assign boundary condition as fixed support.")
    patch = read_patch(package_path)

    assert patch["status"] == "proposed"
    assert patch["operations"]
    assert patch["operations"][0]["op"] == "assign_boundary_condition"
    assert patch["operations"][0]["target"] == "feat_hole_pattern_001"


def test_propose_patch_load_assignment_respects_forbidden_policy(tmp_path):
    package_path = phase5a_package(tmp_path)
    assert main(["build-interface-graph", str(package_path)]) == 0
    build_allowed_operations_catalog_package(package_path)

    catalog = read_json_member(package_path, "graph/allowed_operations_catalog.json")
    for entry in catalog.get("feature_operations", []):
        if entry.get("feature_id") != "feat_base_plate_001":
            continue
        for op in entry.get("operations", []):
            if op.get("operation_type") == "assign_load":
                op["status"] = "forbidden"
                op["reason"] = "test policy block"
    write_json_member(package_path, "graph/allowed_operations_catalog.json", catalog)

    propose_patch_package(package_path, "Apply load to the target interface.")
    patch = read_patch(package_path)

    assert patch["status"] == "needs_review"
    assert patch["operations"] == []
    assert any("assign_load" in warning and "forbidden" in warning for warning in patch["warnings"])


def test_validate_fails_when_patch_operation_conflicts_with_forbidden_catalog_policy(tmp_path):
    package_path = phase5a_package(tmp_path)
    assert main(["build-interface-graph", str(package_path)]) == 0
    build_allowed_operations_catalog_package(package_path)

    propose_patch_package(package_path, "Assign load to the load interface.")

    catalog = read_json_member(package_path, "graph/allowed_operations_catalog.json")
    for entry in catalog.get("feature_operations", []):
        if entry.get("feature_id") != "feat_base_plate_001":
            continue
        for op in entry.get("operations", []):
            if op.get("operation_type") == "assign_load":
                op["status"] = "forbidden"
                op["reason"] = "forced forbidden policy for validator conformance test"
    write_json_member(package_path, "graph/allowed_operations_catalog.json", catalog)

    report = validate_package(package_path)
    assert any(
        m.level.value == "FAIL" and "conflicts with allowed_operations_catalog forbidden policy" in m.text
        for m in report.messages
    )
