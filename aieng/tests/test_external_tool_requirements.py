from __future__ import annotations

import importlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from aieng.ai.summary_writer import AI_SUMMARY_PATH, README_FOR_AI_PATH, summarize_package
from aieng.cli import main
from aieng.context.apply_context import apply_context_package
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.feature_graph import recognize_features_package
from aieng.mcp.server import tool_get_external_tool_requirements
from aieng.package import read_manifest
from aieng.task.external_tool_requirements_writer import (
    EXTERNAL_TOOL_REQUIREMENTS_PATH,
    write_external_tool_requirements_package,
)
from aieng.task.task_spec_writer import write_task_spec_package
from aieng.validate import validate_package

FAKE_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
HAS_MCP = importlib.util.find_spec("mcp") is not None


def _make_package(tmp_path):
    step = tmp_path / "bracket.step"
    step.write_bytes(FAKE_STEP)
    pkg = tmp_path / "bracket.aieng"
    import_step_package(step, pkg)
    extract_topology_package(pkg)
    recognize_features_package(pkg)
    return pkg


def _read_requirements(pkg):
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read(EXTERNAL_TOOL_REQUIREMENTS_PATH))


def _inject_requirements(pkg, data_dict):
    with zipfile.ZipFile(pkg) as zf:
        members = [(i, zf.read(i.filename) if not i.is_dir() else b"") for i in zf.infolist()]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=pkg.parent) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for info, data in members:
            if info.filename != EXTERNAL_TOOL_REQUIREMENTS_PATH:
                zf.writestr(info, data)
        zf.writestr(EXTERNAL_TOOL_REQUIREMENTS_PATH, json.dumps(data_dict, indent=2))
    shutil.move(str(temp), pkg)


# ---------------------------------------------------------------------------
# Schema validity
# ---------------------------------------------------------------------------

def test_schema_is_valid_json_schema():
    schema_path = (Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json")
    assert schema_path.exists()
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)


def test_minimal_valid_requirements_conforms_to_schema():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "format_version": "0.1.0",
        "handoff_id": "handoff_001",
        "required_capabilities": [
            {"capability": "inspect_current_state", "tool_role": "agent_runtime", "required": True}
        ],
        "candidate_tools": [
            {"tool_id": "freecad", "tool_role": "cad_runtime", "status": "candidate", "capabilities": ["modify_cad_geometry"]}
        ],
        "handoff_policy": {
            "bounded_steps_only": True,
            "inspect_before_execution": True,
            "reinspect_after_external_change": True,
            "record_artifacts": True,
            "record_tool_trace": True,
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": False,
        },
        "writeback_requirements": ["validation_status_update"],
        "forbidden_core_actions": ["run_solver"],
    }
    errors = list(validator.iter_errors(spec))
    assert errors == [], f"unexpected schema errors: {errors}"


def test_schema_rejects_external_tools_execute_false():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "format_version": "0.1.0",
        "handoff_id": "handoff_001",
        "required_capabilities": [],
        "candidate_tools": [],
        "handoff_policy": {
            "bounded_steps_only": True,
            "inspect_before_execution": True,
            "reinspect_after_external_change": True,
            "record_artifacts": True,
            "record_tool_trace": True,
            "external_tools_execute": False,
            "aieng_core_executes_external_tools": False,
        },
        "writeback_requirements": ["v"],
        "forbidden_core_actions": ["run_solver"],
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject external_tools_execute=false"


def test_schema_rejects_aieng_core_executes_true():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "format_version": "0.1.0",
        "handoff_id": "handoff_001",
        "required_capabilities": [],
        "candidate_tools": [],
        "handoff_policy": {
            "bounded_steps_only": True,
            "inspect_before_execution": True,
            "reinspect_after_external_change": True,
            "record_artifacts": True,
            "record_tool_trace": True,
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": True,
        },
        "writeback_requirements": ["v"],
        "forbidden_core_actions": ["run_solver"],
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject aieng_core_executes_external_tools=true"


def test_schema_rejects_empty_writeback_requirements():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "format_version": "0.1.0",
        "handoff_id": "handoff_001",
        "required_capabilities": [],
        "candidate_tools": [],
        "handoff_policy": {
            "bounded_steps_only": True,
            "inspect_before_execution": True,
            "reinspect_after_external_change": True,
            "record_artifacts": True,
            "record_tool_trace": True,
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": False,
        },
        "writeback_requirements": [],
        "forbidden_core_actions": ["run_solver"],
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject empty writeback_requirements"


def test_schema_rejects_empty_forbidden_core_actions():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    validator = Draft202012Validator(schema)
    spec = {
        "format_version": "0.1.0",
        "handoff_id": "handoff_001",
        "required_capabilities": [],
        "candidate_tools": [],
        "handoff_policy": {
            "bounded_steps_only": True,
            "inspect_before_execution": True,
            "reinspect_after_external_change": True,
            "record_artifacts": True,
            "record_tool_trace": True,
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": False,
        },
        "writeback_requirements": ["v"],
        "forbidden_core_actions": [],
    }
    errors = list(validator.iter_errors(spec))
    assert errors, "schema should reject empty forbidden_core_actions"


# ---------------------------------------------------------------------------
# Writer happy path
# ---------------------------------------------------------------------------

def test_writer_creates_file(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        assert EXTERNAL_TOOL_REQUIREMENTS_PATH in zf.namelist()


def test_writer_has_required_fields(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    for field in ("format_version", "handoff_id", "required_capabilities",
                  "candidate_tools", "handoff_policy", "writeback_requirements",
                  "forbidden_core_actions"):
        assert field in req, f"missing field: {field}"


def test_writer_default_handoff_id(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert req["handoff_id"] == "handoff_001"


def test_writer_custom_handoff_id(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg, handoff_id="handoff_007")
    req = _read_requirements(pkg)
    assert req["handoff_id"] == "handoff_007"


def test_writer_handoff_policy_external_tools_execute_true(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert req["handoff_policy"]["external_tools_execute"] is True


def test_writer_handoff_policy_aieng_core_executes_false(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert req["handoff_policy"]["aieng_core_executes_external_tools"] is False


def test_writer_forbidden_core_actions_non_empty(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert isinstance(req["forbidden_core_actions"], list)
    assert len(req["forbidden_core_actions"]) > 0


def test_writer_forbidden_core_actions_include_expected(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert "run_solver" in req["forbidden_core_actions"]
    assert "generate_mesh" in req["forbidden_core_actions"]
    assert "modify_cad_geometry" in req["forbidden_core_actions"]


def test_writer_conforms_to_schema(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "external_tool_requirements.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(req))
    assert errors == [], f"schema errors: {errors}"


def test_writer_picks_up_source_task_id_when_task_spec_present(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", task_id="task_042")
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert req.get("source_task_id") == "task_042"


def test_writer_no_source_task_id_when_task_spec_absent(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    assert "source_task_id" not in req


# ---------------------------------------------------------------------------
# Overwrite guard
# ---------------------------------------------------------------------------

def test_writer_refuses_overwrite_without_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    with pytest.raises(FileExistsError, match="--overwrite"):
        write_external_tool_requirements_package(pkg)


def test_writer_overwrites_with_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg, handoff_id="handoff_001")
    write_external_tool_requirements_package(pkg, handoff_id="handoff_002", overwrite=True)
    req = _read_requirements(pkg)
    assert req["handoff_id"] == "handoff_002"


# ---------------------------------------------------------------------------
# Manifest update
# ---------------------------------------------------------------------------

def test_writer_updates_manifest(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    manifest = read_manifest(pkg)
    assert manifest["resources"]["task"]["external_tool_requirements"] == EXTERNAL_TOOL_REQUIREMENTS_PATH


def test_writer_manifest_preserves_other_resources(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    manifest = read_manifest(pkg)
    assert "graph" in manifest["resources"]


# ---------------------------------------------------------------------------
# Writer error paths
# ---------------------------------------------------------------------------

def test_writer_raises_for_missing_package(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        write_external_tool_requirements_package(tmp_path / "missing.aieng")


def test_writer_raises_for_wrong_extension(tmp_path):
    wrong = tmp_path / "pkg.zip"
    wrong.write_bytes(b"")
    with pytest.raises(ValueError, match=".aieng"):
        write_external_tool_requirements_package(wrong)


def test_writer_raises_for_empty_handoff_id(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(ValueError, match="handoff_id"):
        write_external_tool_requirements_package(pkg, handoff_id="   ")


# ---------------------------------------------------------------------------
# Validator: passes with valid resource
# ---------------------------------------------------------------------------

def test_validate_passes_with_valid_requirements(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    report = validate_package(pkg)
    rendered = report.render()
    assert report.ok, rendered
    assert "PASS task/external_tool_requirements.json is valid JSON" in rendered
    assert "PASS task/external_tool_requirements.json conforms to external_tool_requirements.schema.json" in rendered
    assert "PASS task/external_tool_requirements.json handoff_policy correctly sets execution-boundary true flags" in rendered
    assert "PASS task/external_tool_requirements.json handoff_policy.aieng_core_executes_external_tools is false" in rendered


# ---------------------------------------------------------------------------
# Validator: fails on execution-boundary violations
# ---------------------------------------------------------------------------

def test_validate_fails_when_aieng_core_executes_true(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    req["handoff_policy"]["aieng_core_executes_external_tools"] = True
    _inject_requirements(pkg, req)
    report = validate_package(pkg)
    assert not report.ok
    assert any("aieng_core_executes_external_tools" in m.text for m in report.messages)


def test_validate_fails_when_external_tools_execute_false(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    req["handoff_policy"]["external_tools_execute"] = False
    _inject_requirements(pkg, req)
    report = validate_package(pkg)
    assert not report.ok
    assert any("external_tools_execute" in m.text or "handoff_policy" in m.text for m in report.messages)


def test_validate_fails_when_forbidden_core_actions_empty(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    req["forbidden_core_actions"] = []
    _inject_requirements(pkg, req)
    report = validate_package(pkg)
    assert not report.ok
    assert any("forbidden_core_actions" in m.text for m in report.messages)


def test_validate_warns_when_source_task_id_present_but_no_task_spec(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    req["source_task_id"] = "task_orphan"
    _inject_requirements(pkg, req)
    report = validate_package(pkg)
    assert report.ok
    assert any("source_task_id" in m.text for m in report.messages)


def test_validate_no_warn_when_source_task_id_matches_present_task_spec(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", task_id="task_001")
    write_external_tool_requirements_package(pkg)
    report = validate_package(pkg)
    assert report.ok
    assert not any("source_task_id" in m.text and "not present" in m.text for m in report.messages)


# ---------------------------------------------------------------------------
# CLI happy path
# ---------------------------------------------------------------------------

def test_cli_happy_path(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    rc = main(["write-external-tool-requirements", str(pkg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS wrote external tool requirements" in out
    assert "PASS task/external_tool_requirements.json written" in out


def test_cli_custom_handoff_id(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    rc = main(["write-external-tool-requirements", str(pkg), "--handoff-id", "handoff_999"])
    assert rc == 0
    req = _read_requirements(pkg)
    assert req["handoff_id"] == "handoff_999"


def test_cli_refuses_overwrite_without_flag(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    assert main(["write-external-tool-requirements", str(pkg)]) == 0
    capsys.readouterr()
    rc = main(["write-external-tool-requirements", str(pkg)])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err


def test_cli_overwrites_with_flag(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    assert main(["write-external-tool-requirements", str(pkg), "--handoff-id", "handoff_001"]) == 0
    capsys.readouterr()
    rc = main(["write-external-tool-requirements", str(pkg), "--handoff-id", "handoff_002", "--overwrite"])
    assert rc == 0
    req = _read_requirements(pkg)
    assert req["handoff_id"] == "handoff_002"


def test_cli_fails_for_missing_package(tmp_path, capsys):
    rc = main(["write-external-tool-requirements", str(tmp_path / "missing.aieng")])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

def test_mcp_tool_returns_structured_content(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg, handoff_id="handoff_007")
    result = tool_get_external_tool_requirements(pkg)
    assert result.get("handoff_id") == "handoff_007"
    assert "handoff_policy" in result
    assert result["handoff_policy"]["external_tools_execute"] is True
    assert result["handoff_policy"]["aieng_core_executes_external_tools"] is False


def test_mcp_tool_returns_not_found_when_absent(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_external_tool_requirements(pkg)
    assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# Summary visibility
# ---------------------------------------------------------------------------

def test_summary_includes_external_tool_section_when_present(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    summarize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        readme = zf.read(README_FOR_AI_PATH).decode()
        summary = zf.read(AI_SUMMARY_PATH).decode()
    assert "## External tool handoff contract" in readme
    assert "external_tools_execute: true" in readme
    assert "aieng_core_executes_external_tools: false" in readme
    assert "## External tool handoff contract" in summary


def test_summary_shows_absent_message_when_missing(tmp_path):
    pkg = _make_package(tmp_path)
    summarize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        readme = zf.read(README_FOR_AI_PATH).decode()
    assert "## External tool handoff contract" in readme
    assert "task/external_tool_requirements.json` is absent" in readme


# ---------------------------------------------------------------------------
# No execution boundary violation
# ---------------------------------------------------------------------------

def test_writer_does_not_call_cad_cae_tools(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    req = _read_requirements(pkg)
    policy = req["handoff_policy"]
    assert policy["external_tools_execute"] is True
    assert policy["aieng_core_executes_external_tools"] is False
    assert "run_solver" in req["forbidden_core_actions"]
    assert "generate_mesh" in req["forbidden_core_actions"]
    assert "modify_cad_geometry" in req["forbidden_core_actions"]
    all_statuses = {t.get("status") for t in req.get("candidate_tools", []) if isinstance(t, dict)}
    assert all_statuses <= {"candidate", "active", "unavailable"}
    assert "active" not in all_statuses, "no candidate tool should be 'active' — none are confirmed available"
