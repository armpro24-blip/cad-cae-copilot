"""Tests for Phase 14C: evidence ledger and claim-evidence map."""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from aieng.package import create_package
from aieng.task.task_spec_writer import write_task_spec_package
from aieng.task.external_tool_requirements_writer import write_external_tool_requirements_package
from aieng.results.evidence_writer import (
    EVIDENCE_INDEX_PATH,
    record_evidence_package,
    write_evidence_scaffold_package,
)
from aieng.validate import validate_package, Level
from aieng.ai.summary_writer import summarize_package

HAS_MCP = importlib.util.find_spec("mcp") is not None


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


def _write_member(pkg: Path, member: str, data: dict) -> None:
    import shutil, tempfile
    encoded = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(pkg, "r") as zf:
        members = [(info, zf.read(info)) for info in zf.infolist() if info.filename != member]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=pkg.parent) as fh:
        temp = Path(fh.name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for info, content in members:
                zf.writestr(info, content)
            zf.writestr(member, encoded)
        shutil.move(str(temp), pkg)
    finally:
        if temp.exists():
            temp.unlink()


def _names(pkg: Path) -> set[str]:
    with zipfile.ZipFile(pkg) as zf:
        return set(zf.namelist())


# ---------------------------------------------------------------------------
# Schema conformance — evidence_index
# ---------------------------------------------------------------------------

def test_evidence_index_schema_has_required_fields():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "evidence_index.schema.json"
    schema = json.loads(schema_path.read_text())
    required = schema.get("required", [])
    for field in ("format_version", "evidence_index_id", "evidence_items", "claim_policy"):
        assert field in required, f"{field} must be required in evidence_index schema"


def test_evidence_index_schema_claim_policy_consts():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "evidence_index.schema.json"
    schema = json.loads(schema_path.read_text())
    policy_props = schema["properties"]["claim_policy"]["properties"]
    assert policy_props["external_tools_execute"]["const"] is True
    assert policy_props["aieng_core_generates_solver_evidence"]["const"] is False
    assert policy_props["aieng_core_generates_mesh_evidence"]["const"] is False
    assert policy_props["aieng_core_modifies_cad_geometry"]["const"] is False


def test_evidence_index_schema_evidence_type_enum():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "evidence_index.schema.json"
    schema = json.loads(schema_path.read_text())
    item_props = schema["properties"]["evidence_items"]["items"]["properties"]
    enum = item_props["evidence_type"]["enum"]
    for v in ("task_spec", "external_tool_requirements", "solver_result", "mesh_evidence", "geometry_modification", "validation_report"):
        assert v in enum


def test_evidence_index_schema_producer_kind_enum():
    schema_path = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "evidence_index.schema.json"
    schema = json.loads(schema_path.read_text())
    item_props = schema["properties"]["evidence_items"]["items"]["properties"]
    enum = item_props["producer"]["properties"]["kind"]["enum"]
    for v in ("aieng_core", "external_cad", "external_cae", "external_solver", "external_agent"):
        assert v in enum


# ---------------------------------------------------------------------------
# Writer happy path — minimal package (no task spec, no handoff)
# ---------------------------------------------------------------------------

def test_write_evidence_scaffold_creates_evidence_index(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    names = _names(pkg)
    assert EVIDENCE_INDEX_PATH in names


def test_evidence_index_format_version(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert data["format_version"] == "0.1.0"


def test_evidence_index_has_stable_id(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert isinstance(data["evidence_index_id"], str)
    assert data["evidence_index_id"]


def test_evidence_index_claim_policy_flags(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    cp = data["claim_policy"]
    assert cp["external_tools_execute"] is True
    assert cp["aieng_core_generates_solver_evidence"] is False
    assert cp["aieng_core_generates_mesh_evidence"] is False
    assert cp["aieng_core_modifies_cad_geometry"] is False


# ---------------------------------------------------------------------------
# Writer — package with task spec and handoff contract
# ---------------------------------------------------------------------------

def test_task_spec_evidence_added_when_present(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass by 15%.")
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    ev_ids = [item["evidence_id"] for item in data["evidence_items"]]
    assert "ev_task_spec_001" in ev_ids


def test_source_task_id_propagated(tmp_path):
    pkg = _make_package(tmp_path)
    write_task_spec_package(pkg, "Reduce mass.", task_id="task_abc")
    write_evidence_scaffold_package(pkg)
    ev_data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert ev_data.get("source_task_id") == "task_abc"


def test_handoff_evidence_added_when_present(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    ev_ids = [item["evidence_id"] for item in data["evidence_items"]]
    assert "ev_handoff_001" in ev_ids


def test_source_handoff_id_propagated(tmp_path):
    pkg = _make_package(tmp_path)
    write_external_tool_requirements_package(pkg, handoff_id="handoff_xyz")
    write_evidence_scaffold_package(pkg)
    ev_data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert ev_data.get("source_handoff_id") == "handoff_xyz"


# ---------------------------------------------------------------------------
# Overwrite guard
# ---------------------------------------------------------------------------

def test_write_evidence_scaffold_rejects_overwrite_without_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    with pytest.raises(FileExistsError):
        write_evidence_scaffold_package(pkg)


def test_write_evidence_scaffold_allows_overwrite_with_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    write_evidence_scaffold_package(pkg, overwrite=True)
    assert EVIDENCE_INDEX_PATH in _names(pkg)


# ---------------------------------------------------------------------------
# Manifest update
# ---------------------------------------------------------------------------

def test_manifest_updated_with_evidence_paths(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    manifest = _read_member(pkg, "manifest.json")
    results = manifest["resources"]["results"]
    assert results["evidence_index"] == EVIDENCE_INDEX_PATH


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_write_evidence_scaffold_rejects_missing_package(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_evidence_scaffold_package(tmp_path / "nonexistent.aieng")


def test_write_evidence_scaffold_rejects_wrong_extension(tmp_path):
    f = tmp_path / "test.zip"
    f.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError):
        write_evidence_scaffold_package(f)


def test_write_evidence_scaffold_rejects_bad_zip(tmp_path):
    f = tmp_path / "bad.aieng"
    f.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        write_evidence_scaffold_package(f)


# ---------------------------------------------------------------------------
# Validator passes
# ---------------------------------------------------------------------------

def test_validator_passes_for_minimal_evidence_scaffold(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    report = validate_package(pkg)
    fails = [m for m in report.messages if m.level is Level.FAIL]
    assert not fails, f"Unexpected FAIL messages: {[m.render() for m in fails]}"


def test_validator_passes_evidence_unique_id_check(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    report = validate_package(pkg)
    pass_texts = [m.text for m in report.messages if m.level is Level.PASS]
    assert any("evidence IDs are unique" in t for t in pass_texts)


# ---------------------------------------------------------------------------
# Validator rejects invalid data
# ---------------------------------------------------------------------------

def _write_bad_evidence(pkg: Path, data: dict) -> None:
    """Replace results/evidence_index.json with arbitrary data."""
    import shutil, tempfile
    members = []
    with zipfile.ZipFile(pkg) as zf:
        for info in zf.infolist():
            if info.filename == EVIDENCE_INDEX_PATH:
                continue
            raw = b"" if info.is_dir() else zf.read(info.filename)
            members.append((info, raw))
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=pkg.parent) as fh:
        temp = Path(fh.name)
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as zf:
            for info, raw in members:
                zf.writestr(info, raw)
            zf.writestr(EVIDENCE_INDEX_PATH, (json.dumps(data) + "\n").encode())
        shutil.move(str(temp), pkg)
    finally:
        if temp.exists():
            temp.unlink()


def _record_solver_evidence(pkg: Path, *, evidence_id: str = "ev_solver_result_001") -> None:
    record_evidence_package(
        pkg,
        evidence_type="solver_result",
        producer_kind="external_solver",
        producer_tool="solver_x",
        artifact_kind="result_file",
        artifact_path="external/results/solver.out",
        claim_support=["claim_solver_result_001"],
        evidence_id=evidence_id,
        verification_status="available",
    )


def test_validator_fails_for_duplicate_evidence_ids(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    data["evidence_items"] = [
        {
            "evidence_id": "ev_dup",
            "evidence_type": "task_spec",
            "producer": {"kind": "aieng_core"},
            "artifact": {"kind": "yaml", "path": "task/task_spec.yaml"},
            "claim_support": [],
            "verification": {"status": "available"},
        },
        {
            "evidence_id": "ev_dup",
            "evidence_type": "task_spec",
            "producer": {"kind": "aieng_core"},
            "artifact": {"kind": "yaml", "path": "task/task_spec.yaml"},
            "claim_support": [],
            "verification": {"status": "available"},
        },
    ]
    _write_bad_evidence(pkg, data)
    report = validate_package(pkg)
    fail_texts = [m.text for m in report.messages if m.level is Level.FAIL]
    assert any("evidence IDs are not unique" in t for t in fail_texts)


def test_validator_fails_for_wrong_claim_policy_flag(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    data["claim_policy"]["external_tools_execute"] = False
    _write_bad_evidence(pkg, data)
    report = validate_package(pkg)
    fail_texts = [m.text for m in report.messages if m.level is Level.FAIL]
    assert any("claim_policy" in t for t in fail_texts)


def test_record_evidence_adds_to_evidence_index(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    _record_solver_evidence(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert any(item["evidence_id"] == "ev_solver_result_001" for item in data["evidence_items"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_write_evidence_scaffold(tmp_path):
    pkg = _make_package(tmp_path)
    from aieng.cli import main
    result = main(["write-evidence-scaffold", str(pkg)])
    assert result == 0
    assert EVIDENCE_INDEX_PATH in _names(pkg)


def test_cli_write_evidence_scaffold_prints_pass(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    from aieng.cli import main
    main(["write-evidence-scaffold", str(pkg)])
    out = capsys.readouterr().out
    assert "PASS results/evidence_index.json written" in out


def test_cli_write_evidence_scaffold_rejects_missing(tmp_path, capsys):
    from aieng.cli import main
    result = main(["write-evidence-scaffold", str(tmp_path / "no.aieng")])
    assert result == 2


def test_cli_write_evidence_scaffold_rejects_double_write(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    from aieng.cli import main
    main(["write-evidence-scaffold", str(pkg)])
    result = main(["write-evidence-scaffold", str(pkg)])
    assert result == 2


def test_cli_write_evidence_scaffold_overwrite_flag(tmp_path):
    pkg = _make_package(tmp_path)
    from aieng.cli import main
    main(["write-evidence-scaffold", str(pkg)])
    result = main(["write-evidence-scaffold", str(pkg), "--overwrite"])
    assert result == 0


def test_record_evidence_happy_path_after_scaffold(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    from aieng.cli import main
    result = main([
        "record-evidence",
        str(pkg),
        "--kind", "solver_result",
        "--producer-kind", "external_solver",
        "--producer-tool", "ccx",
        "--artifact-kind", "result_file",
        "--artifact-path", "external/ccx/job.dat",
        "--claim-support", "claim_solver_result_001",
    ])
    assert result == 0
    evidence = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert any(item["evidence_type"] == "solver_result" for item in evidence["evidence_items"])


def test_record_evidence_auto_id_generation(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    record_evidence_package(
        pkg,
        evidence_type="solver_result",
        producer_kind="external_solver",
        producer_tool="ccx",
        artifact_kind="result_file",
        artifact_path="external/ccx/job.dat",
        claim_support=["claim_solver_result_001"],
    )
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    ids = [item["evidence_id"] for item in data["evidence_items"]]
    assert "ev_solver_result_001" in ids


def test_record_evidence_duplicate_id_rejected(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    _record_solver_evidence(pkg, evidence_id="ev_solver_result_001")
    with pytest.raises(ValueError):
        _record_solver_evidence(pkg, evidence_id="ev_solver_result_001")


def test_record_evidence_empty_claim_support_rejected(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    from aieng.cli import main
    result = main([
        "record-evidence",
        str(pkg),
        "--kind", "solver_result",
        "--producer-kind", "external_solver",
        "--producer-tool", "ccx",
        "--artifact-kind", "result_file",
        "--artifact-path", "external/ccx/job.dat",
        "--claim-support", ",",
    ])
    assert result == 2


def test_record_evidence_missing_scaffold_error(tmp_path, capsys):
    pkg = _make_package(tmp_path)
    from aieng.cli import main
    result = main([
        "record-evidence",
        str(pkg),
        "--kind", "solver_result",
        "--producer-kind", "external_solver",
        "--producer-tool", "ccx",
        "--artifact-kind", "result_file",
        "--artifact-path", "external/ccx/job.dat",
        "--claim-support", "claim_solver_result_001",
    ])
    assert result == 2
    err = capsys.readouterr().err
    assert "write-evidence-scaffold" in err


@pytest.mark.parametrize("ev_kind", ["solver_result", "mesh_evidence", "geometry_modification"])
def test_record_evidence_rejects_aieng_core_for_external_types(tmp_path, ev_kind):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    with pytest.raises(ValueError):
        record_evidence_package(
            pkg,
            evidence_type=ev_kind,
            producer_kind="aieng_core",
            producer_tool="aieng",
            artifact_kind="json",
            artifact_path="results/tool.json",
            claim_support=["claim_solver_result_001"],
        )


def test_record_evidence_supports_all_four_kinds(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)

    cases = [
        ("solver_result", "external_solver", "claim_solver_result_001"),
        ("mesh_evidence", "external_cae", "claim_mesh_evidence_001"),
        ("geometry_modification", "external_cad", "claim_geometry_modification_001"),
        ("validation_report", "external_agent", "claim_solver_result_001"),
    ]
    for ev_type, producer_kind, claim_id in cases:
        record_evidence_package(
            pkg,
            evidence_type=ev_type,
            producer_kind=producer_kind,
            producer_tool=f"tool_{ev_type}",
            artifact_kind="result_file",
            artifact_path=f"external/{ev_type}.txt",
            claim_support=[claim_id],
        )

    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    kinds = {item["evidence_type"] for item in data["evidence_items"]}
    assert {"solver_result", "mesh_evidence", "geometry_modification", "validation_report"}.issubset(kinds)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

def test_mcp_tool_get_evidence_index_returns_data(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    from aieng.mcp.server import tool_get_evidence_index
    result = tool_get_evidence_index(pkg)
    assert result.get("evidence_index_id") == "evidence_index_001"


def test_mcp_tool_get_evidence_index_returns_not_found(tmp_path):
    pkg = _make_package(tmp_path)
    from aieng.mcp.server import tool_get_evidence_index
    result = tool_get_evidence_index(pkg)
    assert result.get("status") == "not_found"


def test_mcp_tool_returns_updated_evidence_after_record(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    _record_solver_evidence(pkg)

    from aieng.mcp.server import tool_get_evidence_index
    evidence = tool_get_evidence_index(pkg)

    assert any(item["evidence_id"] == "ev_solver_result_001" for item in evidence.get("evidence_items", []))


# ---------------------------------------------------------------------------
# Summary visibility
# ---------------------------------------------------------------------------

def test_summary_includes_evidence_section(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    summarize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        summary = zf.read("ai/summary.md").decode()
        readme = zf.read("README_FOR_AI.md").decode()
    assert "## Evidence ledger" in summary
    assert "## Evidence ledger" in readme
    assert "do not automatically update claim verification status" in summary
    assert "do not automatically update claim verification status" in readme


def test_summary_omits_claim_map_section_in_alpha(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    summarize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        summary = zf.read("ai/summary.md").decode()
        readme = zf.read("README_FOR_AI.md").decode()
    # Alpha contract: no claim maps. The rendered summary must not contain
    # a claim-evidence map section in either output.
    assert "## Claim-evidence map" not in summary
    assert "## Claim-evidence map" not in readme
    assert "claim_map.json" not in summary
    assert "claim_map.json" not in readme


def test_summary_shows_absent_evidence_hint(tmp_path):
    pkg = _make_package(tmp_path)
    summarize_package(pkg)
    with zipfile.ZipFile(pkg) as zf:
        readme = zf.read("README_FOR_AI.md").decode()
    assert "write-evidence-scaffold" in readme


# ---------------------------------------------------------------------------
# Execution boundary enforcement
# ---------------------------------------------------------------------------

def test_evidence_index_never_claims_aieng_generates_solver_evidence(tmp_path):
    pkg = _make_package(tmp_path)
    write_evidence_scaffold_package(pkg)
    data = _read_member(pkg, EVIDENCE_INDEX_PATH)
    assert data["claim_policy"]["aieng_core_generates_solver_evidence"] is False
