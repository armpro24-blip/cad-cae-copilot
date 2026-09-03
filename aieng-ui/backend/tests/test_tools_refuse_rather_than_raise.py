"""A tool answers an ordinary wrong input; it does not raise at it.

Found while dogfooding `aieng.generate_preview` (7 mentions in AGENTS.md, named
by no test). Given a project id that does not exist, **14 of the 27 read-only
tools that take one raised `HTTPException`** instead of returning the
`{"status": "error", "code": ...}` shape their siblings return — including
`aieng.agent_context`, one of the three calls AGENTS.md says every session starts
with.

It was not silent: both callers of `invoke_tool` catch broadly. But they report
`code: "tool_exception"` with the message `HTTPException: 404: project not
found`, so an agent branching on `code` could not tell "you named a project that
does not exist" from "the tool crashed" — and the MCP path logs a full stack
trace for what is usually a typo.

Fixed at the dispatch boundary rather than in fourteen handlers: an
`HTTPException` is a REPORTED error the handler chose to raise, so
`invoke_tool` returns it in the normal shape. Every other exception still
propagates, because those really are crashes.

The mapping is deliberately coarse — the boundary knows the status code, not why
the handler picked it, and deriving something more specific from the detail
string would be guessing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import runtime  # noqa: E402
from app.runtime_tool_schemas import TOOL_SCHEMAS  # noqa: E402

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

#: Tools that could mutate a package, spawn a solver, or reach the network.
#: They are excluded from the sweep, not from the rule — the rule is about how a
#: tool reports a bad input, and running these to find out is not worth it.
_UNSAFE_MARKERS = (
    "execute", "edit_parameter", "replace_part", "remove_part", "refine", "delete",
    "restore", "run_solver", "apply", "insert", "convert", "reference_image",
    "sizing_sweep", "doe_", "run_topology", "writeback", "generate_mesh",
    "run_simulation", "mesh_convergence", "confirm_modeling_plan", "save_draft",
    "adopt_targets", "generate_cad_fixture", "define_", "import_solver_evidence",
    "run_candidates", "accept_candidate", "propose_", "cae_evaluate",
    "run_assembly", "generate_solver_input", "setup_static", "author_load_case",
    "apply_load_case", "update_validation_status", "write_", "refresh_", "extract_",
    "map_results", "generate_bom", "set_part_material", "generate_computed_metrics",
    "report_generate", "opt_", "value_demo",
)


def _sweepable() -> list[str]:
    return sorted(
        name for name, schema in TOOL_SCHEMAS.items()
        if "project_id" in (schema.get("required") or [])
        and not any(marker in name for marker in _UNSAFE_MARKERS)
    )


@pytest.fixture(scope="module", autouse=True)
def _app(tmp_path_factory: pytest.TempPathFactory):
    from app.app_factory import create_app
    from app.config import Settings

    root = tmp_path_factory.mktemp("refusals")
    workspace = root / "workspace"
    create_app(Settings(
        platform_root=root / "platform",
        workspace_root=workspace,
        data_root=root / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    ))


def test_the_sweep_covers_a_meaningful_number_of_tools() -> None:
    """A filter that excluded everything would make the sweep below vacuous."""
    assert len(_sweepable()) >= 20, _sweepable()
    assert "aieng.agent_context" in _sweepable()
    assert "aieng.generate_preview" in _sweepable()


@pytest.mark.parametrize("tool_name", _sweepable())
def test_an_unknown_project_is_refused_not_raised(tool_name: str) -> None:
    try:
        result = runtime.invoke_tool(tool_name, {"project_id": "definitely_not_a_project"})
    except Exception as exc:  # noqa: BLE001 - the failure this test exists for
        pytest.fail(
            f"{tool_name} raised {type(exc).__name__} for an unknown project id: "
            f"{exc}. Callers report any exception as `tool_exception`, so an "
            "agent cannot tell a bad id from a crash."
        )

    assert isinstance(result, dict), f"{tool_name} returned {type(result).__name__}"
    # `not_found` from the boundary, or something more specific the handler
    # already produced. What must NOT happen is an exception.
    if result.get("status") == "error":
        assert result.get("code"), f"{tool_name} refused without a code: {result}"


def test_a_reported_error_keeps_its_status_code_in_the_message() -> None:
    """The mapping is coarse on purpose; the detail must survive it."""
    result = runtime.invoke_tool("aieng.agent_context", {"project_id": "nope"})
    assert result["status"] == "error"
    assert result["code"] == "not_found"
    assert "project not found" in result["message"]


def test_a_real_crash_still_propagates() -> None:
    """Converting every exception would hide genuine failures.

    Only `HTTPException` — which a handler raises deliberately — is converted.
    """
    runtime.register_tool(
        "test.explodes",
        lambda _inp, _ctx: (_ for _ in ()).throw(RuntimeError("boom")),
        description="deliberately fails",
    )
    try:
        with pytest.raises(RuntimeError, match="boom"):
            runtime.invoke_tool("test.explodes", {})
    finally:
        runtime._REGISTRY.pop("test.explodes", None)


def test_generate_preview_says_what_to_do_about_missing_geometry(tmp_path: Path) -> None:
    """The documented fix for an empty viewer, called on the usual cause.

    "The viewer shows nothing" is most often "there is no geometry yet", so that
    call got the least useful answer: `400: STEP source not found`.
    """
    from app.app_factory import create_app
    from app.config import Settings
    from app.main import default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )
    create_app(settings)
    project_id = save_project(settings, default_project("empty"))["id"]

    result = runtime.invoke_tool("aieng.generate_preview", {"project_id": project_id})
    assert result["status"] == "error"
    assert result["code"] == "no_geometry", result
    assert "cad.execute_build123d" in result["message"], "name the way out"


def test_generate_preview_without_a_project_id_refuses(tmp_path: Path) -> None:
    """It used to raise a bare ValueError."""
    result = runtime.invoke_tool("aieng.generate_preview", {})
    assert result["status"] == "error"
    assert result["code"] == "missing_project_id"
