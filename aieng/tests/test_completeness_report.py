"""Tests for Phase 16A completeness/missingness report."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from aieng.ai.summary_writer import AI_SUMMARY_PATH, README_FOR_AI_PATH, summarize_package
from aieng.cli import main
from aieng.context.apply_context import apply_context_package
from aieng.geometry.step_importer import import_step_package
from aieng.geometry.topology_extractor import extract_topology_package
from aieng.graph.feature_graph import recognize_features_package
from aieng.package import create_package
from aieng.results.evidence_writer import write_evidence_scaffold_package
from aieng.simulation.mesh_evidence_importer import import_mesh_evidence_package
from aieng.validate import Level, validate_package
from aieng.validation.completeness_writer import (
    COMPLETENESS_REPORT_PATH,
    write_completeness_report_package,
)

FAKE_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def _read_json(pkg: Path, member: str) -> dict:
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read(member))


def _read_text(pkg: Path, member: str) -> str:
    with zipfile.ZipFile(pkg) as zf:
        return zf.read(member).decode("utf-8")


def _names(pkg: Path) -> set[str]:
    with zipfile.ZipFile(pkg) as zf:
        return set(zf.namelist())


def _empty_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "empty.aieng"
    create_package("empty_model", pkg)
    return pkg


def _phase4_pkg(tmp_path: Path) -> Path:
    step = tmp_path / "bracket.step"
    step.write_bytes(FAKE_STEP)
    pkg = tmp_path / "bracket.aieng"
    context = tmp_path / "context.yaml"
    context.write_text(
        "material: Al6061-T6\n"
        "protected_features:\n"
        "  - feat_hole_pattern_001\n"
        "simulation:\n"
        "  type: static_structural\n"
        "  fixed:\n"
        "    - feat_hole_pattern_001\n"
        "  loads:\n"
        "    - target: feat_base_plate_001\n"
        "      type: force\n"
        "      value_n: 500\n"
        "      direction: [1, 0, 0]\n"
        "targets:\n"
        "  max_von_mises_stress_mpa: 120\n",
        encoding="utf-8",
    )
    import_step_package(step, pkg)
    extract_topology_package(pkg)
    recognize_features_package(pkg)
    apply_context_package(pkg, context)
    return pkg


def _category(report: dict, name: str) -> dict:
    return next(c for c in report["categories"] if c["category"] == name)


def test_completeness_schema_has_claim_policy_consts():
    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "completeness_report.schema.json").read_text(encoding="utf-8"))
    policy = schema["$defs"]["ClaimPolicy"]["properties"]
    assert policy["best_effort_conversion"]["const"] is True
    assert policy["missingness_explicit"]["const"] is True
    assert policy["do_not_infer_missing_information"]["const"] is True
    assert policy["unsupported_is_not_false"]["const"] is True
    assert policy["external_tools_execute"]["const"] is True
    assert policy["aieng_core_executes_external_tools"]["const"] is False
    assert "source_mode" in schema["properties"]


def test_write_completeness_report_creates_resource_and_manifest_entry(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_completeness_report_package(pkg)
    assert COMPLETENESS_REPORT_PATH in _names(pkg)
    manifest = _read_json(pkg, "manifest.json")
    assert manifest["resources"]["validation"]["completeness_report"] == COMPLETENESS_REPORT_PATH


def test_empty_package_marks_missing_information_explicitly(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_completeness_report_package(pkg)
    report = _read_json(pkg, COMPLETENESS_REPORT_PATH)
    assert report["conversion_mode"] == "best_effort"
    assert _category(report, "topology")["status"] == "missing"
    assert "geometry/topology_map.json" in _category(report, "topology")["missing_items"]
    assert _category(report, "simulation_setup")["status"] == "missing"
    assert _category(report, "mesh_artifacts")["status"] == "missing"
    assert "results/evidence_index.json:mesh_evidence" in _category(report, "mesh_artifacts")["missing_items"]
    assert _category(report, "patch_proposals")["status"] == "not_applicable"


def test_phase4_package_marks_available_and_partial_categories(tmp_path):
    pkg = _phase4_pkg(tmp_path)
    write_completeness_report_package(pkg)
    report = _read_json(pkg, COMPLETENESS_REPORT_PATH)
    assert _category(report, "geometry")["status"] == "available"
    assert _category(report, "topology")["status"] == "available"
    assert _category(report, "features")["status"] == "partial"
    assert _category(report, "protected_regions")["status"] == "available"
    assert _category(report, "simulation_setup")["status"] == "available"
    assert _category(report, "evidence_ledger")["status"] == "missing"


def test_completeness_report_recommends_next_actions(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_completeness_report_package(pkg)
    report = _read_json(pkg, COMPLETENESS_REPORT_PATH)
    actions = [a["action"] for a in report["next_recommended_actions"]]
    assert "run_extract_topology_or_emit_topology_map" in actions
    assert "provide_simulation_setup" in actions
    assert "write_evidence_scaffold" in actions
    assert "import_mesh_artifact_or_record_reference" in actions


def test_completeness_report_marks_mesh_artifacts_available_after_import(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_evidence_scaffold_package(pkg)
    mesh_file = tmp_path / "mesh.msh"
    mesh_file.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n", encoding="utf-8")
    import_mesh_evidence_package(
        pkg,
        mesh_file=mesh_file,
        mesh_format="gmsh_msh",
        producer_tool="gmsh",
        claim_support=["claim_mesh_evidence_001"],
    )

    write_completeness_report_package(pkg)

    report = _read_json(pkg, COMPLETENESS_REPORT_PATH)
    mesh_category = _category(report, "mesh_artifacts")
    assert mesh_category["status"] == "available"
    assert "results/mesh_artifacts/ev_mesh_evidence_001.msh" in mesh_category["resources"]
    assert any("Mesh evidence is present" in note for note in mesh_category["notes"])


def test_cli_write_completeness_report(tmp_path, capsys):
    pkg = _empty_pkg(tmp_path)
    rc = main(["write-completeness-report", str(pkg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS wrote completeness report" in out
    assert COMPLETENESS_REPORT_PATH in _names(pkg)


def test_cli_write_completeness_report_refuses_overwrite_by_default(tmp_path, capsys):
    pkg = _empty_pkg(tmp_path)
    assert main(["write-completeness-report", str(pkg)]) == 0
    capsys.readouterr()
    assert main(["write-completeness-report", str(pkg)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_validate_passes_valid_completeness_report(tmp_path):
    pkg = _phase4_pkg(tmp_path)
    write_completeness_report_package(pkg)
    report = validate_package(pkg)
    failures = [m for m in report.messages if m.level is Level.FAIL and "completeness" in m.text.lower()]
    assert not failures
    assert any("explicit missingness" in m.text for m in report.messages)


def test_validate_fails_available_category_without_resource(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_completeness_report_package(pkg)
    report = _read_json(pkg, COMPLETENESS_REPORT_PATH)
    _category(report, "topology")["status"] = "available"
    _category(report, "topology")["resources"] = []
    with zipfile.ZipFile(pkg, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != COMPLETENESS_REPORT_PATH]
    import shutil, tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=tmp_path) as fh:
        tmp = Path(fh.name)
    with zipfile.ZipFile(tmp, "w") as zf:
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr(COMPLETENESS_REPORT_PATH, json.dumps(report, indent=2).encode())
    shutil.move(str(tmp), str(pkg))
    validation = validate_package(pkg)
    assert any(m.level is Level.FAIL and "requires at least one present resource" in m.text for m in validation.messages)


def test_summary_mentions_completeness_report_when_present(tmp_path):
    pkg = _phase4_pkg(tmp_path)
    write_completeness_report_package(pkg)
    summarize_package(pkg)
    summary = _read_text(pkg, AI_SUMMARY_PATH)
    readme = _read_text(pkg, README_FOR_AI_PATH)
    assert "Completeness and missingness" in summary
    assert "Completeness and missingness" in readme
    assert "best_effort_conversion" in summary
    assert "missingness_explicit" in readme


def test_summary_mentions_absent_completeness_report(tmp_path):
    pkg = _phase4_pkg(tmp_path)
    summarize_package(pkg)
    summary = _read_text(pkg, AI_SUMMARY_PATH)
    assert "validation/completeness_report.json` is absent" in summary


def test_mcp_get_completeness_report_returns_data(tmp_path):
    pkg = _empty_pkg(tmp_path)
    write_completeness_report_package(pkg)
    from aieng.mcp.server import tool_get_completeness_report
    result = tool_get_completeness_report(pkg)
    assert result["conversion_mode"] == "best_effort"
    assert any(c.get("category") == "topology" for c in result["categories"])


def test_mcp_get_completeness_report_returns_not_found(tmp_path):
    pkg = _empty_pkg(tmp_path)
    from aieng.mcp.server import tool_get_completeness_report
    result = tool_get_completeness_report(pkg)
    assert result["status"] == "not_found"
    assert result["member"] == COMPLETENESS_REPORT_PATH


def test_writer_source_does_not_execute_external_tools():
    text = Path("src/aieng/validation/completeness_writer.py").read_text(encoding="utf-8")
    forbidden = ["subprocess", "FreeCAD", "cadquery", "gmsh", "calculix", "os.system"]
    for keyword in forbidden:
        assert keyword not in text

