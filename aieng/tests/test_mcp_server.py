from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml

from aieng.cli import main
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.aag import build_aag_package
from aieng.graph.feature_graph import recognize_features_package
from aieng.objects.interface_graph_writer import build_interface_graph_package
from aieng.ai.summary_writer import summarize_package
from aieng.validation.status_writer import update_validation_status_package
from aieng.mcp.server import (
    OperationForbidden,
    PackageNotReadable,
    create_server,
    tool_get_aag_neighbors,
    tool_get_feature,
    tool_get_interfaces,
    tool_get_manifest,
    tool_get_summary,
    tool_get_task_spec,
    tool_get_topology,
    tool_get_validation_status,
    tool_propose_patch,
    tool_resolve_ref,
)

FAKE_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
HAS_MCP = importlib.util.find_spec("mcp") is not None


def _make_package(tmp_path: Path) -> Path:
    step = tmp_path / "bracket.step"
    step.write_bytes(FAKE_STEP)
    pkg = tmp_path / "bracket.aieng"
    import_step_package(step, pkg)
    extract_topology_package(pkg)
    recognize_features_package(pkg)
    return pkg


def _make_full_package(tmp_path: Path) -> Path:
    pkg = _make_package(tmp_path)
    build_aag_package(pkg)
    summarize_package(pkg)
    update_validation_status_package(pkg)
    return pkg


def _inject_status_yaml(pkg: Path, status: dict) -> None:
    with zipfile.ZipFile(pkg) as zf:
        members = [(i, zf.read(i.filename) if not i.is_dir() else b"") for i in zf.infolist()]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=pkg.parent) as fh:
        temp = Path(fh.name)
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for info, data in members:
            if info.filename != "validation/status.yaml":
                zf.writestr(info, data)
        zf.writestr("validation/status.yaml", yaml.dump(status))
    shutil.move(str(temp), pkg)


# ---------------------------------------------------------------------------
# get_manifest
# ---------------------------------------------------------------------------

def test_get_manifest_returns_model_id(tmp_path):
    pkg = _make_package(tmp_path)
    manifest = tool_get_manifest(pkg)
    assert "model_id" in manifest


def test_get_manifest_contains_format_version(tmp_path):
    pkg = _make_package(tmp_path)
    manifest = tool_get_manifest(pkg)
    assert "format_version" in manifest


def test_get_manifest_reads_member_without_enumerating_package(tmp_path, monkeypatch):
    pkg = tmp_path / "minimal.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "m1", "format_version": "0.1"}))

    def fail_namelist(self):
        raise AssertionError("namelist should not be needed for direct member reads")

    monkeypatch.setattr(zipfile.ZipFile, "namelist", fail_namelist)

    assert tool_get_manifest(pkg) == {"model_id": "m1", "format_version": "0.1"}


# ---------------------------------------------------------------------------
# get_feature
# ---------------------------------------------------------------------------

def test_get_feature_returns_correct_feature(tmp_path):
    pkg = _make_package(tmp_path)
    feature = tool_get_feature(pkg, "feat_hole_001")
    assert feature["id"] == "feat_hole_001"
    assert feature["type"] == "mounting_hole"
    assert feature["ref"] == "@aieng[graph/feature_graph.json#feat_hole_001]"


def test_get_feature_returns_editable_flag(tmp_path):
    pkg = _make_package(tmp_path)
    feature = tool_get_feature(pkg, "feat_hole_001")
    assert "editable" in feature


def test_tool_resolve_ref_returns_inspected_record(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_resolve_ref(pkg, "@aieng[graph/feature_graph.json#feat_hole_001]")
    assert result["id"] == "feat_hole_001"
    assert result["kind"] == "feature"


def test_get_feature_raises_for_unknown_id(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(PackageNotReadable, match="feat_unknown_999"):
        tool_get_feature(pkg, "feat_unknown_999")


def test_get_feature_raises_when_feature_graph_missing(tmp_path):
    step = tmp_path / "bracket.step"
    step.write_bytes(FAKE_STEP)
    pkg = tmp_path / "bracket.aieng"
    import_step_package(step, pkg)
    with pytest.raises(PackageNotReadable, match="not found in package"):
        tool_get_feature(pkg, "feat_hole_001")


# ---------------------------------------------------------------------------
# get_topology
# ---------------------------------------------------------------------------

def test_get_topology_returns_all_entities(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_topology(pkg)
    assert "entities" in result
    assert len(result["entities"]) > 0


def test_get_topology_no_filter_key_when_unfiltered(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_topology(pkg)
    assert "_filter_applied" not in result


def test_get_topology_filters_by_type_face(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_topology(pkg, entity_type="face")
    assert all(e["type"] == "face" for e in result["entities"])
    assert result["_filter_applied"]["type"] == "face"


def test_get_topology_filters_by_type_edge(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_topology(pkg, entity_type="edge")
    assert all(e["type"] == "edge" for e in result["entities"])


# ---------------------------------------------------------------------------
# get_interfaces
# ---------------------------------------------------------------------------

def test_get_interfaces_returns_interface_graph(tmp_path):
    pkg = _make_package(tmp_path)
    build_interface_graph_package(pkg)
    result = tool_get_interfaces(pkg)
    assert "interfaces" in result
    assert "format" in result


def test_get_interfaces_filters_by_role(tmp_path):
    pkg = _make_package(tmp_path)
    build_interface_graph_package(pkg)
    result = tool_get_interfaces(pkg, role="mounting_interface_candidate")
    for iface in result["interfaces"]:
        assert "mounting_interface_candidate" in iface["roles"]


def test_get_interfaces_filter_applied_key_present(tmp_path):
    pkg = _make_package(tmp_path)
    build_interface_graph_package(pkg)
    result = tool_get_interfaces(pkg, role="mounting_interface_candidate")
    assert result["_filter_applied"]["role"] == "mounting_interface_candidate"


def test_get_interfaces_raises_when_interface_graph_missing(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(PackageNotReadable, match="not found in package"):
        tool_get_interfaces(pkg)


# ---------------------------------------------------------------------------
# get_validation_status
# ---------------------------------------------------------------------------

def test_get_validation_status_returns_dict(tmp_path):
    pkg = _make_full_package(tmp_path)
    status = tool_get_validation_status(pkg)
    assert isinstance(status, dict)


def test_get_validation_status_contains_claim_policy(tmp_path):
    pkg = _make_full_package(tmp_path)
    status = tool_get_validation_status(pkg)
    assert "claim_policy" in status


def test_get_validation_status_raises_when_missing(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(PackageNotReadable, match="not found in package"):
        tool_get_validation_status(pkg)


# ---------------------------------------------------------------------------
# get_aag_neighbors
# ---------------------------------------------------------------------------

def test_get_aag_neighbors_returns_result_for_known_face(tmp_path):
    pkg = _make_package(tmp_path)
    build_aag_package(pkg)
    result = tool_get_aag_neighbors(pkg, "face_base_bottom")
    assert result["query_face_id"] == "face_base_bottom"
    assert isinstance(result["adjacency_arcs"], list)
    assert isinstance(result["neighbor_count"], int)
    assert isinstance(result["neighbor_nodes"], list)


def test_get_aag_neighbors_returns_empty_for_unknown_face(tmp_path):
    pkg = _make_package(tmp_path)
    build_aag_package(pkg)
    result = tool_get_aag_neighbors(pkg, "face_nonexistent_xyz")
    assert result["neighbor_count"] == 0
    assert result["adjacency_arcs"] == []


def test_get_aag_neighbors_raises_when_aag_missing(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(PackageNotReadable, match="not found in package"):
        tool_get_aag_neighbors(pkg, "face_base_bottom")


# ---------------------------------------------------------------------------
# get_task_spec
# ---------------------------------------------------------------------------

def test_get_task_spec_returns_not_found_when_absent(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_get_task_spec(pkg)
    assert result["status"] == "not_found"
    assert "task_spec.yaml" in result["member"]


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------

def test_get_summary_returns_markdown_string(tmp_path):
    pkg = _make_package(tmp_path)
    summarize_package(pkg)
    result = tool_get_summary(pkg)
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_summary_raises_when_absent(tmp_path):
    pkg = _make_package(tmp_path)
    with pytest.raises(PackageNotReadable, match="not found in package"):
        tool_get_summary(pkg)


# ---------------------------------------------------------------------------
# propose_patch
# ---------------------------------------------------------------------------

def test_propose_patch_returns_patch_proposal(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_propose_patch(pkg, "increase hole radius to 8mm")
    assert "patch_id" in result
    assert "operations" in result
    assert result["status"] in {"proposed", "needs_review", "ready_for_validation"}


def test_propose_patch_proposal_has_created_from(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_propose_patch(pkg, "increase hole radius to 8mm")
    assert result["created_from"]["llm_used"] is False


# ---------------------------------------------------------------------------
# claim_policy enforcement
# ---------------------------------------------------------------------------

def test_claim_policy_blocks_forbidden_operation(tmp_path):
    pkg = _make_full_package(tmp_path)
    _inject_status_yaml(pkg, {
        "claim_policy": {
            "forbidden_operations": ["propose_patch"],
            "rationale": "package locked for test",
        }
    })
    with pytest.raises(OperationForbidden, match="propose_patch"):
        tool_propose_patch(pkg, "some intent")


def test_claim_policy_allows_when_operation_not_in_list(tmp_path):
    pkg = _make_full_package(tmp_path)
    _inject_status_yaml(pkg, {
        "claim_policy": {
            "forbidden_operations": ["other_operation"],
            "rationale": "only other_operation is locked",
        }
    })
    result = tool_propose_patch(pkg, "increase hole radius to 8mm")
    assert "patch_id" in result


def test_claim_policy_allows_when_status_file_absent(tmp_path):
    pkg = _make_package(tmp_path)
    result = tool_propose_patch(pkg, "increase hole radius to 8mm")
    assert "patch_id" in result


# ---------------------------------------------------------------------------
# create_server — guards (no MCP runtime needed)
# ---------------------------------------------------------------------------

def test_create_server_raises_for_missing_package(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        create_server(tmp_path / "missing.aieng")


def test_create_server_raises_for_wrong_extension(tmp_path):
    wrong = tmp_path / "model.step"
    wrong.touch()
    with pytest.raises(ValueError, match=".aieng"):
        create_server(wrong)


# ---------------------------------------------------------------------------
# create_server — FastMCP integration (skipped if mcp not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_MCP, reason="mcp package not installed")
def test_create_server_returns_server_instance(tmp_path):
    """The server type, on whichever SDK major is installed.

    `mcp` 2.0 renamed `FastMCP` to `MCPServer` and made the old import path raise
    a guidance stub, so asserting against the 1.x name pinned this test — and the
    core library — to a superseded SDK line (#463).
    """
    try:
        from mcp.server.mcpserver import MCPServer as ServerType  # mcp >= 2.0
    except ModuleNotFoundError:
        from mcp.server.fastmcp import FastMCP as ServerType  # mcp 1.x

    pkg = _make_package(tmp_path)
    server = create_server(pkg)
    assert isinstance(server, ServerType)


@pytest.mark.skipif(not HAS_MCP, reason="mcp package not installed")
def test_create_server_name_contains_package_stem(tmp_path):
    pkg = _make_package(tmp_path)
    server = create_server(pkg)
    assert "bracket" in server.name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_serve_fails_gracefully_for_missing_package(tmp_path, capsys):
    rc = main(["serve", str(tmp_path / "missing.aieng")])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err


def test_cli_serve_fails_gracefully_for_wrong_extension(tmp_path, capsys):
    wrong = tmp_path / "model.step"
    wrong.touch()
    rc = main(["serve", str(wrong)])
    assert rc == 2
    assert "FAIL" in capsys.readouterr().err
