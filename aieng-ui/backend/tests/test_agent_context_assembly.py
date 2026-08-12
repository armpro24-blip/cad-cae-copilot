"""agent_context must show that a project IS an assembly, and which joints are refused (#496).

Measured on the dogfood gearbox package: three parts, four interfaces and three
mates were authored through the documented `cad.define_*` chain, one of them
deliberately wrong. `aieng.agent_context` — the tool an agent reads every session
to learn the project's state — returned no assembly information at all, so the
refused connection was invisible on the path an agent actually walks.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.config import Settings

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _make_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def _make_project(settings: Settings, name: str) -> tuple[str, Path]:
    from app.main import default_project, project_dir, save_project

    project = save_project(settings, default_project(name))
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)
    return project["id"], project_dir(settings, project["id"]) / "p.aieng"


_ASSEMBLY = {
    "format": "aieng.assembly_ir",
    "unit": "mm",
    "parts": [
        {"id": "housing", "role": "design_part", "geometry_ref": "housing"},
        {"id": "cover", "role": "design_part", "geometry_ref": "cover"},
    ],
    "interfaces": [
        {"id": "if_a", "part_id": "housing", "semantic_role": "mounting_face"},
        {"id": "if_b", "part_id": "cover", "semantic_role": "support_face"},
    ],
    "connections": [
        {"id": "good", "type": "bolted_proxy", "part_a": "housing", "part_b": "cover"},
        {"id": "gapped", "type": "bonded", "part_a": "housing", "part_b": "cover"},
    ],
}

_GEOMETRY = {
    "connections": [
        {"connection_id": "good", "type": "bolted_proxy",
         "geometry_status": "plausible", "reasons": []},
        {"connection_id": "gapped", "type": "bonded", "geometry_status": "invalid",
         "reasons": ["joint_across_gap", "no_overlap"]},
    ],
    "provenance": {"contact_physics_modeled": False, "bolt_preload_modeled": False},
}

_DRAFT = {
    "status": "needs_user_input",
    "connections": [{"connection_id": "gapped", "disabled": True}],
    "needs_user_input": ["connection 'gapped' geometry invalid: joint_across_gap, no_overlap"],
}


_MESH_DIAG_OK = {
    "safe_for_solver": True,
    "summary": {"ok": 2, "warning": 0, "blocking": 0},
    "blocking_interfaces": [],
}

_MESH_DIAG_BLOCKED = {
    "safe_for_solver": False,
    "summary": {"ok": 1, "warning": 0, "blocking": 1},
    "blocking_interfaces": ["if_b"],
}


def _write_assembly_package(pkg: Path, *, mesh_diag: dict | None = None) -> None:
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "asm-context-test"}))
        zf.writestr("assembly/assembly_ir.json", json.dumps(_ASSEMBLY))
        zf.writestr("diagnostics/assembly_connection_geometry.json", json.dumps(_GEOMETRY))
        zf.writestr("simulation/assembly_cae_setup_draft.json", json.dumps(_DRAFT))
        zf.writestr(
            "diagnostics/assembly_mesh_interface_diagnostics.json",
            json.dumps(mesh_diag if mesh_diag is not None else _MESH_DIAG_OK),
        )


def _context(tmp_path: Path, *, assembly: bool, mesh_diag: dict | None = None) -> dict:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id, pkg = _make_project(settings, "assembly-context")
    if assembly:
        _write_assembly_package(pkg, mesh_diag=mesh_diag)
    else:
        pkg.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"model_id": "single-part"}))
    resp = client.get(f"/api/projects/{project_id}/agent-context")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_assembly_is_visible_with_counts(tmp_path: Path) -> None:
    block = _context(tmp_path, assembly=True)["assembly"]
    assert block["present"] is True
    assert block["part_count"] == 2
    assert block["interface_count"] == 2
    assert block["connection_count"] == 2
    assert block["connection_status"] == {"plausible": 1, "invalid": 1}


def test_only_non_plausible_connections_are_listed(tmp_path: Path) -> None:
    """Compact by design — a healthy joint costs no tokens."""
    block = _context(tmp_path, assembly=True)["assembly"]
    listed = {c["connection_id"] for c in block["attention"]}
    assert listed == {"gapped"}
    assert "joint_across_gap" in block["attention"][0]["reasons"]


def test_refused_joint_reaches_warnings_and_next_focus(tmp_path: Path) -> None:
    """A refused connection must be unmissable, not merely present in a sub-block."""
    ctx = _context(tmp_path, assembly=True)
    assert any("gapped" in w and "NOT solver-enabled" in w for w in ctx["warnings"]), \
        ctx["warnings"]
    focus = ctx["agent_brief"]["next_decision_focus"]
    assert any("gapped" in f for f in focus), focus
    assert "2 part(s)" in ctx["agent_brief"]["part_summary"]


def test_draft_status_and_honesty_flags_are_carried(tmp_path: Path) -> None:
    block = _context(tmp_path, assembly=True)["assembly"]
    assert block["cae_draft_status"] == "needs_user_input"
    assert block["needs_user_input"]
    assert block["honesty"]["contact_physics_modeled"] is False
    assert block["honesty"]["bolt_preload_modeled"] is False


def test_interface_coverage_is_reported(tmp_path: Path) -> None:
    block = _context(tmp_path, assembly=True)["assembly"]
    assert block["interfaces"]["safe_for_solver"] is True
    assert block["interfaces"]["summary"]["ok"] == 2


def test_an_empty_interface_makes_the_assembly_unsafe_to_solve(tmp_path: Path) -> None:
    """`empty_interface` is the one BLOCKING finding — it must reach warnings."""
    ctx = _context(tmp_path, assembly=True, mesh_diag=_MESH_DIAG_BLOCKED)
    assert ctx["assembly"]["interfaces"]["safe_for_solver"] is False
    assert any("if_b" in w and "not safe to solve" in w for w in ctx["warnings"]), \
        ctx["warnings"]


def test_single_part_project_is_unaffected(tmp_path: Path) -> None:
    ctx = _context(tmp_path, assembly=False)
    assert ctx["assembly"] == {"present": False}
    assert not any("assembly connection" in w for w in ctx["warnings"])
