"""Tests for Phase 15B: Provenance Tool Trace."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.package import create_package
from aieng.task.task_spec_writer import write_task_spec_package
from aieng.task.external_tool_requirements_writer import write_external_tool_requirements_package
from aieng.results.evidence_writer import write_evidence_scaffold_package
from aieng.provenance.tool_trace_writer import (
    TOOL_TRACE_PATH,
    record_trace_package,
)
from aieng.validate import validate_package, Level
from aieng.ai.summary_writer import summarize_package
from aieng.cli import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "test.aieng"
    create_package("test_model", pkg)
    return pkg


def _read_member(pkg: Path, member: str) -> dict:
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read(member))


def _names(pkg: Path) -> set[str]:
    with zipfile.ZipFile(pkg) as zf:
        return set(zf.namelist())


def _make_package_with_context(tmp_path: Path) -> Path:
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15% while keeping mounting holes unchanged.", task_id="task_001")
    write_external_tool_requirements_package(pkg, handoff_id="handoff_001")
    write_evidence_scaffold_package(pkg)
    return pkg


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------

def test_tool_trace_schema_valid_json():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "tool_trace.schema.json"
    schema = json.loads(schema_path.read_text())
    required = schema.get("required", [])
    for field in ("format_version", "tool_trace_id", "entries", "claim_policy"):
        assert field in required, f"{field} must be required in tool_trace schema"


def test_tool_trace_schema_format_version_const():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "tool_trace.schema.json"
    schema = json.loads(schema_path.read_text())
    assert schema["properties"]["format_version"]["const"] == "0.1.0"


def test_tool_trace_schema_claim_policy_consts():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "tool_trace.schema.json"
    schema = json.loads(schema_path.read_text())
    policy_props = schema["$defs"]["ClaimPolicy"]["properties"]
    assert policy_props["external_tools_execute"]["const"] is True
    assert policy_props["aieng_core_executes_external_tools"]["const"] is False


def test_tool_trace_schema_tool_role_enum():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "tool_trace.schema.json"
    schema = json.loads(schema_path.read_text())
    role_enum = schema["$defs"]["ToolRef"]["properties"]["tool_role"]["enum"]
    for role in ("agent_runtime", "cad_runtime", "cae_runtime", "cae_preprocessor", "solver", "postprocessor", "manufacturing_checker"):
        assert role in role_enum


def test_tool_trace_schema_exit_status_enum():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "tool_trace.schema.json"
    schema = json.loads(schema_path.read_text())
    es_enum = schema["$defs"]["StepRecord"]["properties"]["exit_status"]["enum"]
    for status in ("success", "failure", "skipped"):
        assert status in es_enum


# ---------------------------------------------------------------------------
# Writer: first call creates provenance/tool_trace.json
# ---------------------------------------------------------------------------

def test_first_record_creates_tool_trace(tmp_path):
    pkg = _make_package(tmp_path)
    assert TOOL_TRACE_PATH not in _names(pkg)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="modify_hole", exit_status="success")
    assert TOOL_TRACE_PATH in _names(pkg)


def test_tool_trace_initial_structure(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="modify_hole", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace["format_version"] == "0.1.0"
    assert isinstance(trace["tool_trace_id"], str)
    assert isinstance(trace["entries"], list)
    assert len(trace["entries"]) == 1
    assert trace["claim_policy"]["external_tools_execute"] is True
    assert trace["claim_policy"]["aieng_core_executes_external_tools"] is False


def test_first_entry_has_correct_id(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="gmsh", tool_role="cae_preprocessor", step_name="mesh_model", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace["entries"][0]["entry_id"] == "trace_0001"


def test_second_call_appends_entry(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step_a", exit_status="success")
    record_trace_package(pkg, tool_id="gmsh", tool_role="solver", step_name="step_b", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert len(trace["entries"]) == 2
    assert trace["entries"][0]["entry_id"] == "trace_0001"
    assert trace["entries"][1]["entry_id"] == "trace_0002"


def test_second_call_does_not_change_first_entry(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step_a", exit_status="success")
    trace_before = _read_member(pkg, TOOL_TRACE_PATH)
    first_entry_before = trace_before["entries"][0]
    record_trace_package(pkg, tool_id="gmsh", tool_role="solver", step_name="step_b", exit_status="failure")
    trace_after = _read_member(pkg, TOOL_TRACE_PATH)
    first_entry_after = trace_after["entries"][0]
    assert first_entry_before == first_entry_after


def test_deterministic_entry_ids(tmp_path):
    pkg = _make_package(tmp_path)
    for i in range(5):
        record_trace_package(pkg, tool_id=f"tool_{i}", tool_role="solver", step_name=f"step_{i}", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    ids = [e["entry_id"] for e in trace["entries"]]
    assert ids == ["trace_0001", "trace_0002", "trace_0003", "trace_0004", "trace_0005"]


def test_manifest_updated_with_tool_trace(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    manifest = _read_member(pkg, "manifest.json")
    assert manifest["resources"]["provenance"]["tool_trace"] == TOOL_TRACE_PATH


# ---------------------------------------------------------------------------
# Writer: source IDs populated from package resources
# ---------------------------------------------------------------------------

def test_source_task_id_populated_when_task_spec_exists(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Some intent", task_id="task_abc")
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace.get("source_task_id") == "task_abc"


def test_source_task_id_absent_when_no_task_spec(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert "source_task_id" not in trace


def test_source_handoff_id_populated_when_ext_req_exists(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Some intent")
    write_external_tool_requirements_package(pkg, handoff_id="handoff_xyz")
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace.get("source_handoff_id") == "handoff_xyz"


def test_source_handoff_id_absent_when_no_ext_req(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert "source_handoff_id" not in trace


# ---------------------------------------------------------------------------
# Writer: optional fields
# ---------------------------------------------------------------------------

def test_tool_version_stored_when_provided(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success", tool_version="0.21.2")
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace["entries"][0]["tool"]["version"] == "0.21.2"


def test_inputs_outputs_stored(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(
        pkg,
        tool_id="freecad",
        tool_role="cad_runtime",
        step_name="step",
        exit_status="success",
        inputs=["geometry/source.step", "graph/feature_graph.json:feat_hole_001"],
        outputs=["geometry/modified_patch_001.step"],
    )
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    step = trace["entries"][0]["step"]
    assert step["inputs"] == ["geometry/source.step", "graph/feature_graph.json:feat_hole_001"]
    assert step["outputs"] == ["geometry/modified_patch_001.step"]


def test_artifacts_and_claims_stored(tmp_path):
    pkg = _make_package_with_context(tmp_path)
    record_trace_package(
        pkg,
        tool_id="freecad",
        tool_role="cad_runtime",
        step_name="step",
        exit_status="success",
        artifacts_recorded=["ev_solver_result_001"],
        claims_advanced=["claim_solver_result_001"],
    )
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    entry = trace["entries"][0]
    assert "ev_solver_result_001" in entry["artifacts_recorded"]
    assert "claim_solver_result_001" in entry["claims_advanced"]


def test_notes_stored(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(
        pkg,
        tool_id="freecad",
        tool_role="cad_runtime",
        step_name="step",
        exit_status="success",
        notes=["External CAD tool reported geometry modification.", "No errors."],
    )
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert trace["entries"][0]["notes"] == ["External CAD tool reported geometry modification.", "No errors."]


# ---------------------------------------------------------------------------
# Writer: validation guards
# ---------------------------------------------------------------------------

def test_invalid_tool_role_raises(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(ValueError, match="tool_role"):
        record_trace_package(pkg, tool_id="tool", tool_role="invalid_role", step_name="step", exit_status="success")


def test_invalid_exit_status_raises(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(ValueError, match="exit_status"):
        record_trace_package(pkg, tool_id="tool", tool_role="solver", step_name="step", exit_status="invalid")


def test_empty_package_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        record_trace_package(tmp_path / "nonexistent.aieng", tool_id="tool", tool_role="solver", step_name="step", exit_status="success")


def test_wrong_extension_raises(tmp_path):
    wrong = tmp_path / "package.zip"
    wrong.write_bytes(b"PK")
    with pytest.raises(ValueError, match=".aieng"):
        record_trace_package(wrong, tool_id="tool", tool_role="solver", step_name="step", exit_status="success")


# ---------------------------------------------------------------------------
# CLI: happy path
# ---------------------------------------------------------------------------

def test_cli_record_trace_creates_file(tmp_path):
    pkg = _make_package(tmp_path)
    rc = main([
        "record-trace", str(pkg),
        "--tool-id", "freecad",
        "--tool-role", "cad_runtime",
        "--step-name", "modify_hole_diameter",
        "--exit-status", "success",
    ])
    assert rc == 0
    assert TOOL_TRACE_PATH in _names(pkg)


def test_cli_record_trace_with_all_options(tmp_path):
    pkg = _make_package(tmp_path)
    rc = main([
        "record-trace", str(pkg),
        "--tool-id", "freecad",
        "--tool-role", "cad_runtime",
        "--step-name", "modify_hole",
        "--exit-status", "success",
        "--tool-version", "0.21.2",
        "--input", "geometry/source.step",
        "--input", "graph/feature_graph.json",
        "--output", "geometry/modified.step",
        "--artifact", "ev_geometry_modification_001",
        "--claim", "claim_geometry_modification_001",
        "--notes", "Step ran without errors.",
    ])
    assert rc == 0
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    entry = trace["entries"][0]
    assert entry["tool"]["version"] == "0.21.2"
    assert "geometry/source.step" in entry["step"]["inputs"]
    assert "geometry/modified.step" in entry["step"]["outputs"]
    assert "ev_geometry_modification_001" in entry["artifacts_recorded"]
    assert "claim_geometry_modification_001" in entry["claims_advanced"]
    assert "Step ran without errors." in entry["notes"]


def test_cli_record_trace_twice_appends(tmp_path):
    pkg = _make_package(tmp_path)
    main(["record-trace", str(pkg), "--tool-id", "freecad", "--tool-role", "cad_runtime", "--step-name", "step_a", "--exit-status", "success"])
    main(["record-trace", str(pkg), "--tool-id", "gmsh", "--tool-role", "solver", "--step-name", "step_b", "--exit-status", "failure"])
    trace = _read_member(pkg, TOOL_TRACE_PATH)
    assert len(trace["entries"]) == 2
    assert trace["entries"][0]["entry_id"] == "trace_0001"
    assert trace["entries"][1]["entry_id"] == "trace_0002"


def test_cli_record_trace_invalid_role_fails(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    # argparse choices validation catches this before the function
    with pytest.raises(SystemExit):
        main(["record-trace", str(pkg), "--tool-id", "freecad", "--tool-role", "invalid", "--step-name", "step", "--exit-status", "success"])


def test_cli_record_trace_nonexistent_package_fails(tmp_path, capsys):
    rc = main(["record-trace", str(tmp_path / "nonexistent.aieng"), "--tool-id", "tool", "--tool-role", "solver", "--step-name", "step", "--exit-status", "success"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Validator: valid trace passes
# ---------------------------------------------------------------------------

def test_validate_passes_valid_trace(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    report = validate_package(pkg)
    failures = [m for m in report.messages if m.level is Level.FAIL and "tool_trace" in m.text.lower()]
    assert not failures


def test_validate_fails_duplicate_entry_ids(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="tool_a", tool_role="solver", step_name="step_a", exit_status="success")
    # Manually inject duplicate entry
    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != TOOL_TRACE_PATH]
        manifest_bytes = zf.read("manifest.json")
        trace = json.loads(zf.read(TOOL_TRACE_PATH))
    # Duplicate the entry
    trace["entries"].append(dict(trace["entries"][0]))
    import shutil, tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=tmp_path) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w") as zf:
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr(TOOL_TRACE_PATH, json.dumps(trace, indent=2).encode())
    shutil.move(str(temp), str(pkg))
    report = validate_package(pkg)
    failures = [m for m in report.messages if m.level is Level.FAIL and "not unique" in m.text and "tool_trace" in m.text.lower()]
    assert failures


def test_validate_fails_bad_claim_policy_flags(tmp_path):
    pkg = _make_package(tmp_path)
    bad_trace = {
        "format_version": "0.1.0",
        "tool_trace_id": "tool_trace_001",
        "entries": [],
        "claim_policy": {
            "external_tools_execute": False,  # wrong
            "aieng_core_executes_external_tools": True,  # wrong
        },
    }
    import shutil, tempfile
    with zipfile.ZipFile(pkg, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != "manifest.json"]
        manifest = json.loads(zf.read("manifest.json"))
    manifest.setdefault("resources", {}).setdefault("provenance", {})["tool_trace"] = TOOL_TRACE_PATH
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=tmp_path) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w") as zf:
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode())
        zf.writestr(TOOL_TRACE_PATH, json.dumps(bad_trace, indent=2).encode())
    shutil.move(str(temp), str(pkg))
    report = validate_package(pkg)
    failures = [m for m in report.messages if m.level is Level.FAIL and "claim_policy" in m.text.lower()]
    assert failures


def test_validate_fails_unknown_artifact_in_evidence_index(tmp_path):
    pkg = _make_package_with_context(tmp_path)
    # Record a trace referencing a non-existent evidence ID
    record_trace_package(
        pkg,
        tool_id="freecad",
        tool_role="cad_runtime",
        step_name="step",
        exit_status="success",
        artifacts_recorded=["ev_nonexistent_999"],
    )
    report = validate_package(pkg)
    failures = [m for m in report.messages if m.level is Level.FAIL and "ev_nonexistent_999" in m.text]
    assert failures



def test_validate_warns_missing_task_spec(tmp_path):
    pkg = _make_package(tmp_path)
    # Write a trace doc with a source_task_id but no task_spec in the package
    import shutil, tempfile
    trace = {
        "format_version": "0.1.0",
        "tool_trace_id": "tool_trace_001",
        "source_task_id": "task_001",
        "entries": [],
        "claim_policy": {
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": False,
        },
    }
    with zipfile.ZipFile(pkg, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != "manifest.json"]
        manifest = json.loads(zf.read("manifest.json"))
    manifest.setdefault("resources", {}).setdefault("provenance", {})["tool_trace"] = TOOL_TRACE_PATH
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=tmp_path) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w") as zf:
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode())
        zf.writestr(TOOL_TRACE_PATH, json.dumps(trace, indent=2).encode())
    shutil.move(str(temp), str(pkg))
    report = validate_package(pkg)
    warnings = [m for m in report.messages if m.level is Level.WARN and "source_task_id" in m.text]
    assert warnings


def test_validate_warns_missing_handoff_contract(tmp_path):
    pkg = _make_package(tmp_path)
    import shutil, tempfile
    trace = {
        "format_version": "0.1.0",
        "tool_trace_id": "tool_trace_001",
        "source_handoff_id": "handoff_001",
        "entries": [],
        "claim_policy": {
            "external_tools_execute": True,
            "aieng_core_executes_external_tools": False,
        },
    }
    with zipfile.ZipFile(pkg, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != "manifest.json"]
        manifest = json.loads(zf.read("manifest.json"))
    manifest.setdefault("resources", {}).setdefault("provenance", {})["tool_trace"] = TOOL_TRACE_PATH
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=tmp_path) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w") as zf:
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode())
        zf.writestr(TOOL_TRACE_PATH, json.dumps(trace, indent=2).encode())
    shutil.move(str(temp), str(pkg))
    report = validate_package(pkg)
    warnings = [m for m in report.messages if m.level is Level.WARN and "source_handoff_id" in m.text]
    assert warnings


# ---------------------------------------------------------------------------
# Summary mentions tool trace when present
# ---------------------------------------------------------------------------

def test_summary_mentions_tool_trace(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step", exit_status="success")
    summarize_package(pkg, overwrite=True)
    with zipfile.ZipFile(pkg) as zf:
        summary = zf.read("ai/summary.md").decode()
        readme = zf.read("README_FOR_AI.md").decode()
    assert "tool_trace" in summary.lower() or "provenance" in summary.lower()
    assert "tool_trace" in readme.lower() or "provenance" in readme.lower()


def test_summary_mentions_tool_count(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step_a", exit_status="success")
    record_trace_package(pkg, tool_id="gmsh", tool_role="solver", step_name="step_b", exit_status="failure")
    summarize_package(pkg, overwrite=True)
    with zipfile.ZipFile(pkg) as zf:
        summary = zf.read("ai/summary.md").decode()
    assert "2" in summary


def test_summary_warns_absent_tool_trace(tmp_path):
    pkg = _make_package(tmp_path)
    summarize_package(pkg, overwrite=True)
    with zipfile.ZipFile(pkg) as zf:
        summary = zf.read("ai/summary.md").decode()
    assert "absent" in summary.lower() or "not present" in summary.lower() or "not present" in summary.lower() or "no external tool" in summary.lower()


# ---------------------------------------------------------------------------
# Boundary guarantee: no CAD/CAE execution introduced
# ---------------------------------------------------------------------------

def test_no_cad_cae_execution_in_writer_source():
    """Verify tool_trace_writer.py does not import forbidden execution modules."""
    source = Path(__file__).parent.parent / "src" / "aieng" / "provenance" / "tool_trace_writer.py"
    text = source.read_text()
    forbidden = ["subprocess", "FreeCAD", "cadquery", "cq.", "gmsh", "calculix", "FreeSimpleGUI"]
    for kw in forbidden:
        assert kw not in text, f"tool_trace_writer.py must not reference '{kw}'"


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

def test_mcp_tool_get_tool_trace_returns_data(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="export_step", exit_status="success")
    from aieng.mcp.server import tool_get_tool_trace
    result = tool_get_tool_trace(pkg)
    assert "entries" in result
    assert any(e.get("tool", {}).get("tool_id") == "freecad" for e in result["entries"])


def test_mcp_tool_get_tool_trace_returns_not_found(tmp_path):
    pkg = _make_package(tmp_path)
    from aieng.mcp.server import tool_get_tool_trace
    result = tool_get_tool_trace(pkg)
    assert result.get("status") == "not_found"
    assert result.get("member") == "provenance/tool_trace.json"


def test_mcp_tool_get_tool_trace_reflects_multiple_entries(tmp_path):
    pkg = _make_package(tmp_path)
    record_trace_package(pkg, tool_id="freecad", tool_role="cad_runtime", step_name="step_a", exit_status="success")
    record_trace_package(pkg, tool_id="gmsh", tool_role="solver", step_name="step_b", exit_status="failure")
    from aieng.mcp.server import tool_get_tool_trace
    result = tool_get_tool_trace(pkg)
    ids = [e.get("tool", {}).get("tool_id") for e in result.get("entries", [])]
    assert "freecad" in ids
    assert "gmsh" in ids
