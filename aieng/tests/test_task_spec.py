from __future__ import annotations

from pathlib import Path

import json
import zipfile

import pytest
import yaml
from jsonschema import Draft202012Validator

from aieng.cli import main
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.feature_graph import recognize_features_package
from aieng.mcp.server import tool_get_task_spec
from aieng.package import read_manifest
from aieng.task.task_spec_writer import TASK_SPEC_PATH, write_task_spec_package
from aieng.validate import validate_package

FAKE_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def _make_package(tmp_path):
    step = tmp_path / "bracket.step"
    step.write_bytes(FAKE_STEP)
    pkg = tmp_path / "bracket.aieng"
    import_step_package(step, pkg)
    extract_topology_package(pkg)
    recognize_features_package(pkg)
    return pkg


def _read_task_spec(pkg):
    with zipfile.ZipFile(pkg) as zf:
        return yaml.safe_load(zf.read(TASK_SPEC_PATH))


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def test_task_spec_schema_is_valid_json_schema():
    import pathlib
    schema_path = (Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json")
    assert schema_path.exists(), "schemas/task_spec.schema.json missing"
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)


def test_minimal_valid_task_spec_conforms_to_schema():
    import pathlib
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "task_id": "task_001",
        "format_version": "0.1.0",
        "intent": "Reduce mass by 15%.",
        "mode": "proposal_only",
        "required_outputs": ["patch_proposal"],
        "forbidden_claims": ["solver_validated"],
        "claim_policy": {
            "no_solver_run_claim": True,
            "no_mesh_generation_claim": True,
            "no_geometry_modification_claim": True,
            "external_tools_execute": True,
        },
    }
    errors = list(validator.iter_errors(spec))
    assert errors == []


def test_schema_rejects_false_no_solver_run_claim():
    import pathlib
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "task_id": "task_001",
        "format_version": "0.1.0",
        "intent": "Test.",
        "mode": "proposal_only",
        "required_outputs": ["patch_proposal"],
        "forbidden_claims": ["solver_validated"],
        "claim_policy": {
            "no_solver_run_claim": False,
            "no_mesh_generation_claim": True,
            "no_geometry_modification_claim": True,
            "external_tools_execute": True,
        },
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject no_solver_run_claim=false"


def test_schema_rejects_false_external_tools_execute():
    import pathlib
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "task_id": "task_001",
        "format_version": "0.1.0",
        "intent": "Test.",
        "mode": "proposal_only",
        "required_outputs": ["patch_proposal"],
        "forbidden_claims": ["solver_validated"],
        "claim_policy": {
            "no_solver_run_claim": True,
            "no_mesh_generation_claim": True,
            "no_geometry_modification_claim": True,
            "external_tools_execute": False,
        },
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject external_tools_execute=false"


def test_schema_rejects_unrecognized_mode():
    import pathlib
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "task_id": "task_001",
        "format_version": "0.1.0",
        "intent": "Test.",
        "mode": "full_cad_execution",
        "required_outputs": ["patch_proposal"],
        "forbidden_claims": ["solver_validated"],
        "claim_policy": {
            "no_solver_run_claim": True,
            "no_mesh_generation_claim": True,
            "no_geometry_modification_claim": True,
            "external_tools_execute": True,
        },
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject unrecognized mode"


def test_schema_rejects_empty_forbidden_claims():
    import pathlib
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "task_id": "task_001",
        "format_version": "0.1.0",
        "intent": "Test.",
        "mode": "proposal_only",
        "required_outputs": ["patch_proposal"],
        "forbidden_claims": [],
        "claim_policy": {
            "no_solver_run_claim": True,
            "no_mesh_generation_claim": True,
            "no_geometry_modification_claim": True,
            "external_tools_execute": True,
        },
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject empty forbidden_claims"


# ---------------------------------------------------------------------------
# Writer happy path
# ---------------------------------------------------------------------------

def test_write_task_spec_creates_file(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15%.")
    with zipfile.ZipFile(pkg) as zf:
        assert TASK_SPEC_PATH in zf.namelist()


def test_write_task_spec_has_required_fields(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15%.")
    spec = _read_task_spec(pkg)
    for field in ("task_id", "format_version", "intent", "mode", "required_outputs",
                  "forbidden_claims", "claim_policy"):
        assert field in spec, f"missing field: {field}"


def test_write_task_spec_intent_is_preserved(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15% while keeping mounting holes unchanged.")
    spec = _read_task_spec(pkg)
    assert spec["intent"] == "Reduce mass by 15% while keeping mounting holes unchanged."


def test_write_task_spec_default_mode_is_proposal_only(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    assert spec["mode"] == "proposal_only"


def test_write_task_spec_custom_task_id(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", task_id="task_007")
    spec = _read_task_spec(pkg)
    assert spec["task_id"] == "task_007"


def test_write_task_spec_custom_mode(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", mode="analysis_ready")
    spec = _read_task_spec(pkg)
    assert spec["mode"] == "analysis_ready"


def test_write_task_spec_claim_policy_booleans_are_true(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    cp = spec["claim_policy"]
    assert cp["no_solver_run_claim"] is True
    assert cp["no_mesh_generation_claim"] is True
    assert cp["no_geometry_modification_claim"] is True
    assert cp["external_tools_execute"] is True


def test_write_task_spec_forbidden_claims_non_empty(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    assert isinstance(spec["forbidden_claims"], list)
    assert len(spec["forbidden_claims"]) > 0


def test_write_task_spec_forbidden_claims_include_solver_and_mesh(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    assert "solver_validated" in spec["forbidden_claims"]
    assert "mesh_validated" in spec["forbidden_claims"]


def test_write_task_spec_conforms_to_schema(tmp_path):
    import pathlib
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15%.")
    spec = _read_task_spec(pkg)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "task_spec.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(spec))
    assert errors == [], f"schema errors: {errors}"


# ---------------------------------------------------------------------------
# Writer manifest update
# ---------------------------------------------------------------------------

def test_write_task_spec_updates_manifest(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    manifest = read_manifest(pkg)
    assert manifest["resources"]["task"]["task_spec"] == TASK_SPEC_PATH


def test_write_task_spec_manifest_still_has_other_resources(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    manifest = read_manifest(pkg)
    assert "graph" in manifest["resources"]
    assert manifest["resources"]["graph"]["feature_graph"] == "graph/feature_graph.json"


# ---------------------------------------------------------------------------
# Writer overwrite guard
# ---------------------------------------------------------------------------

def test_write_task_spec_refuses_overwrite_without_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    with pytest.raises(FileExistsError, match="--overwrite"):
        write_task_spec_package(pkg, "Different intent.")


def test_write_task_spec_overwrites_with_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    write_task_spec_package(pkg, "New intent.", overwrite=True)
    spec = _read_task_spec(pkg)
    assert spec["intent"] == "New intent."


# ---------------------------------------------------------------------------
# Writer error paths
# ---------------------------------------------------------------------------

def test_write_task_spec_raises_for_missing_package(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        write_task_spec_package(tmp_path / "missing.aieng", "intent")


def test_write_task_spec_raises_for_empty_intent(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(ValueError, match="intent"):
        write_task_spec_package(pkg, "   ")


def test_write_task_spec_raises_for_unknown_mode(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        write_task_spec_package(pkg, "intent", mode="full_execution")


# ---------------------------------------------------------------------------
# Validate: passes with valid task spec
# ---------------------------------------------------------------------------

def test_validate_passes_with_valid_task_spec(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    report = validate_package(pkg)
    rendered = report.render()
    assert report.ok, rendered
    assert "PASS task/task_spec.yaml is valid YAML" in rendered
    assert "PASS task/task_spec.yaml conforms to task_spec.schema.json" in rendered
    assert "PASS task/task_spec.yaml claim_policy correctly declares no solver, mesh, or geometry-modification claims" in rendered


def test_validate_passes_mode_proposal_only(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", mode="proposal_only")
    report = validate_package(pkg)
    rendered = report.render()
    assert "PASS task/task_spec.yaml mode 'proposal_only' is the conservative default" in rendered


def test_validate_warns_for_non_proposal_only_mode(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", mode="analysis_ready")
    report = validate_package(pkg)
    rendered = report.render()
    assert report.ok, rendered
    assert "WARN task/task_spec.yaml mode 'analysis_ready' implies external tool execution" in rendered


# ---------------------------------------------------------------------------
# Validate: fails on invalid task spec
# ---------------------------------------------------------------------------

def _inject_task_spec(pkg, spec_dict):
    with zipfile.ZipFile(pkg) as zf:
        members = [(i, zf.read(i.filename) if not i.is_dir() else b"") for i in zf.infolist()]
    import shutil, tempfile
    from pathlib import Path as P
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=pkg.parent) as fh:
        temp = P(fh.name)
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for info, data in members:
            if info.filename != TASK_SPEC_PATH:
                zf.writestr(info, data)
        zf.writestr(TASK_SPEC_PATH, yaml.dump(spec_dict))
    shutil.move(str(temp), pkg)


def test_validate_fails_for_empty_forbidden_claims(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    spec["forbidden_claims"] = []
    _inject_task_spec(pkg, spec)
    report = validate_package(pkg)
    assert not report.ok
    assert any("forbidden_claims" in m.text for m in report.messages)


def test_validate_fails_for_false_no_solver_claim(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    spec["claim_policy"]["no_solver_run_claim"] = False
    _inject_task_spec(pkg, spec)
    report = validate_package(pkg)
    assert not report.ok
    assert any("claim_policy" in m.text for m in report.messages)


def test_validate_fails_for_unrecognized_required_output(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    spec["required_outputs"] = ["solver_result"]
    _inject_task_spec(pkg, spec)
    report = validate_package(pkg)
    assert not report.ok
    assert any("required_outputs" in m.text for m in report.messages)


def test_validate_fails_for_unrecognized_mode(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    spec["mode"] = "cad_execution"
    _inject_task_spec(pkg, spec)
    report = validate_package(pkg)
    assert not report.ok
    assert any("mode" in m.text for m in report.messages)


# ---------------------------------------------------------------------------
# MCP get_task_spec returns structured content
# ---------------------------------------------------------------------------

def test_mcp_get_task_spec_returns_structured_content(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15%.", task_id="task_042")
    result = tool_get_task_spec(pkg)
    assert result.get("task_id") == "task_042"
    assert result.get("intent") == "Reduce mass by 15%."
    assert result.get("mode") == "proposal_only"
    assert "claim_policy" in result


def test_mcp_get_task_spec_returns_not_found_when_absent(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_task_spec(pkg)
    assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# CLI happy path
# ---------------------------------------------------------------------------

def test_cli_write_task_spec_happy_path(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    rc = main(["write-task-spec", str(pkg), "--intent", "Reduce mass by 15%."])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS wrote task specification" in out
    assert "PASS task/task_spec.yaml written" in out


def test_cli_write_task_spec_with_task_id(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    rc = main(["write-task-spec", str(pkg), "--intent", "Reduce mass.", "--task-id", "task_999"])
    assert rc == 0
    spec = _read_task_spec(pkg)
    assert spec["task_id"] == "task_999"


def test_cli_write_task_spec_refuses_overwrite_without_flag(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    assert main(["write-task-spec", str(pkg), "--intent", "First intent."]) == 0
    capsys.readouterr()
    rc = main(["write-task-spec", str(pkg), "--intent", "Second intent."])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err


def test_cli_write_task_spec_overwrites_with_flag(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    assert main(["write-task-spec", str(pkg), "--intent", "First intent."]) == 0
    capsys.readouterr()
    rc = main(["write-task-spec", str(pkg), "--intent", "Second intent.", "--overwrite"])
    assert rc == 0
    spec = _read_task_spec(pkg)
    assert spec["intent"] == "Second intent."


def test_cli_write_task_spec_fails_for_missing_package(tmp_path, capsys):
    rc = main(["write-task-spec", str(tmp_path / "missing.aieng"), "--intent", "intent"])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# No execution boundary violation
# ---------------------------------------------------------------------------

def test_write_task_spec_does_not_introduce_cad_execution(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    cp = spec["claim_policy"]
    assert cp["no_solver_run_claim"] is True
    assert cp["no_mesh_generation_claim"] is True
    assert cp["no_geometry_modification_claim"] is True
    assert cp["external_tools_execute"] is True
    assert "geometry_modified" in spec["forbidden_claims"]
    assert "solver_validated" in spec["forbidden_claims"]
    assert "mesh_validated" in spec["forbidden_claims"]
    assert "safe_to_manufacture" in spec["forbidden_claims"]


def test_validate_fails_for_false_external_tools_execute(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.")
    spec = _read_task_spec(pkg)
    spec["claim_policy"]["external_tools_execute"] = False
    _inject_task_spec(pkg, spec)
    report = validate_package(pkg)
    assert not report.ok
    assert any("claim_policy" in m.text for m in report.messages)
