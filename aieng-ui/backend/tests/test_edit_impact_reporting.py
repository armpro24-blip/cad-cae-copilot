"""A geometry change must reach the tool that promises to report it.

AGENTS.md: "After a geometry edit, `aieng.agent_context` includes an EDIT IMPACT
section listing `@artifact:` references needing revalidation. Treat these as hard
blockers before running a simulation." The tool's own description says the same.
It was never implemented there — the EDIT IMPACT text lived in
`geometry_providers`, which built prompt context for the LLM path removed in the
MCP-first cutover.

Measured while dogfooding topology optimization: `opt.writeback_to_shape_ir`
replaced the whole body (30 faces -> 1, `base_plate`/`rib_main` -> a mesh proxy)
and afterwards the package still said `geometry_modified: false` from the
previous solver run, while `agent_context` reported `warnings: []`. The solver
was blocked only because the old face ids happened to vanish — safety by
accident rather than by rule.
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


def _project_with_status(tmp_path: Path, status: dict | None) -> tuple[TestClient, str]:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    from app.main import default_project, project_dir, save_project

    project = save_project(settings, default_project("edit-impact"))
    project["aieng_file"] = "p.aieng"
    save_project(settings, project)

    pkg = project_dir(settings, project["id"]) / "p.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "edit-impact-test"}))
        if status is not None:
            zf.writestr("state/revalidation_status.json", json.dumps(status))
    return client, project["id"]


def _context(client: TestClient, project_id: str) -> dict:
    resp = client.get(f"/api/projects/{project_id}/agent-context")
    assert resp.status_code == 200, resp.text
    return resp.json()


_STALE = {
    "schema_version": "0.2",
    "geometry_modified": True,
    "requires_revalidation": True,
    "reason": "geometry_edit",
    "triggering_tool": "opt.writeback_to_shape_ir",
    "current_geometry_revision": 2,
    "last_validated_geometry_revision": 1,
    "affected_artifacts": ["results/computed_metrics.json", "simulation/cae_mapping.json"],
    "affected_domains": ["result_summary", "solver_outputs"],
}

_CLEAN = {
    "schema_version": "0.2",
    "geometry_modified": False,
    "requires_revalidation": False,
    "reason": "solver_rerun_completed",
    "triggering_tool": "cae.run_solver",
    "current_geometry_revision": 2,
    "last_validated_geometry_revision": 2,
    "validated_by_run_id": "run_007",
    "affected_artifacts": [],
}


def test_stale_evidence_is_reported_with_its_artifacts(tmp_path: Path) -> None:
    client, pid = _project_with_status(tmp_path, _STALE)
    block = _context(client, pid)["edit_impact"]

    assert block["stale"] is True
    assert block["geometry_revision"] == 2
    assert block["triggering_tool"] == "opt.writeback_to_shape_ir"
    assert block["affected_artifacts"] == [
        "@artifact:results/computed_metrics.json",
        "@artifact:simulation/cae_mapping.json",
    ], "artifacts must be pointer-formatted, as the guide describes them"
    assert "do not cite these artifacts" in block["guidance"]


def test_stale_evidence_reaches_the_warnings_and_the_next_focus(tmp_path: Path) -> None:
    """A hard blocker buried in a sub-block is not a blocker."""
    client, pid = _project_with_status(tmp_path, _STALE)
    ctx = _context(client, pid)

    assert any("EDIT IMPACT" in w and "opt.writeback_to_shape_ir" in w
               for w in ctx["warnings"]), ctx["warnings"]
    assert any("revalidate" in f for f in ctx["agent_brief"]["next_decision_focus"]), \
        ctx["agent_brief"]["next_decision_focus"]


def test_a_validated_package_says_so_without_crying_wolf(tmp_path: Path) -> None:
    client, pid = _project_with_status(tmp_path, _CLEAN)
    ctx = _context(client, pid)

    assert ctx["edit_impact"]["stale"] is False
    assert ctx["edit_impact"]["validated_by_run_id"] == "run_007"
    assert not any("EDIT IMPACT" in w for w in ctx["warnings"])


def test_a_package_without_the_state_file_is_not_reported_as_stale(tmp_path: Path) -> None:
    client, pid = _project_with_status(tmp_path, None)
    block = _context(client, pid)["edit_impact"]
    assert block == {"stale": False, "available": False}


def test_the_writeback_records_itself_as_the_trigger() -> None:
    """The wrapper hard-coded `cad.edit_parameter` for every caller."""
    import inspect

    from app.project_io import _record_geometry_edit_in_package
    from app.runtime_registry import opt as opt_tools

    signature = inspect.signature(_record_geometry_edit_in_package)
    assert "triggering_tool" in signature.parameters, "callers must be able to name themselves"
    source = inspect.getsource(opt_tools)
    assert 'triggering_tool="opt.writeback_to_shape_ir"' in source


def test_the_writeback_response_omits_bulk_arrays_but_keeps_their_size() -> None:
    """~7.5k tokens per call, 91% of it coordinates an agent cannot act on."""
    from app.runtime_registry.opt import _summarize_shape_ir_payload

    payload = {
        "format": "aieng.shape_ir",
        "representation": "manifold_mesh",
        "parts": [{
            "id": "optimized", "vertex_count": 350, "triangle_count": 696,
            "bbox": [0, 0, 0, 1, 1, 1], "lossy": True, "not_production_cad": True,
            "vertices": [[0.0, 0.0, 0.0]] * 350,
            "faces": [[0, 1, 2]] * 696,
        }],
    }
    part = _summarize_shape_ir_payload(payload)["parts"][0]

    assert "vertices" not in part and "faces" not in part
    assert part["omitted_arrays"] == {"vertices": 350, "faces": 696}
    assert "geometry/shape_ir.json" in part["omitted_arrays_note"]
    # everything an agent can actually act on survives
    assert part["vertex_count"] == 350 and part["triangle_count"] == 696
    assert part["lossy"] is True and part["not_production_cad"] is True
    assert len(json.dumps(part)) < 600, "the summary must not itself be a payload"


def test_an_unexpected_payload_shape_is_returned_untouched() -> None:
    """Summarizing must never be the thing that loses the caller's data."""
    from app.runtime_registry.opt import _summarize_shape_ir_payload

    for payload in (
        {"format": "aieng.shape_ir", "parts": "not-a-list"},
        {"format": "aieng.shape_ir"},                       # no parts at all
        {"format": "aieng.shape_ir", "parts": None},
        "not-a-dict",
    ):
        assert _summarize_shape_ir_payload(payload) == payload
