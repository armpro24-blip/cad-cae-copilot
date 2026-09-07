"""Three advertised tools returned `unavailable` on every provider. They are gone.

`mcp.check`, `mcp.parse_patch` and `mcp.prepare_execution` sat in AGENTS.md's
tool table ("Guardrails, capability gaps, operation policy"), were marked
**Working** in `aieng-ui/README.md`, and were listed as read-only / auto-preview
in two routing tables. Every one of them routed to a provider method, and every
provider stubbed it:

    build123d provider   -> _unavailable("check_mcp_operation")   (the default runtime)
    FreeCAD provider     -> _unavailable("check_mcp_operation")   (the legacy adapter)
    unconfigured stub    -> "no CAD execution provider configured"

There was no configuration on which any of the three did anything. A cold-start
agent trial followed the doc's lead — "mcp.check" for capability gaps — and got
`status: "unavailable"`, `code: "build123d_preview_provider_minimal"`.

They shipped green because the tests that named them asserted the tools were
LISTED (`assert "mcp.check" in tools`), never that they returned anything.
`undocumented-path` from the review lens, with the twist that the docs were the
only thing that ever mentioned them approvingly.

Their stated purposes are served by tools that exist: `aieng.agent_context`
(stale warnings, capability gaps), `cae.prepare_solver_run` (preflight),
`aieng.apply_shape_ir_patch {dry_run: true}` (validate / dry-run a patch), and
`cad.validate_subpart`. The provider protocol methods and the legacy REST/chat
plumbing behind them are deliberately left alone — this removes a promise, not
a subsystem.
"""
from __future__ import annotations

from pathlib import Path

_DEAD = ("mcp.check", "mcp.parse_patch", "mcp.prepare_execution")
_REPO = Path(__file__).resolve().parents[3]


def test_the_dead_bridge_tools_are_not_registered(tmp_path: Path) -> None:
    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings

    # Build the app so the registry is populated. Without this the name list is
    # empty and the assertion below passes vacuously — measured: with the source
    # restored to main and no app built, this test still passed.
    create_app(Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=_REPO / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    ))
    registered = set(runtime.registered_tool_names())
    assert "aieng.list_projects" in registered, "registry not populated; test would be vacuous"

    present = [name for name in _DEAD if name in registered]
    assert not present, f"registered again without an implementation: {present}"


def test_they_publish_no_schema_either() -> None:
    """An orphan schema is how a tool comes back half-way: advertised, not served."""
    from app.runtime_tool_schemas import TOOL_SCHEMAS

    present = [name for name in _DEAD if name in TOOL_SCHEMAS]
    assert not present, present


def test_the_agent_facing_docs_no_longer_promise_them() -> None:
    for doc in ("AGENTS.md", "aieng-ui/README.md", "aieng-ui/docs/runtime_architecture.md"):
        text = (_REPO / doc).read_text(encoding="utf-8")
        promised = [name for name in _DEAD if f"`{name}`" in text or f" {name} " in text]
        assert not promised, f"{doc} still advertises {promised}"


def test_the_planner_no_longer_tells_the_model_to_prefer_them() -> None:
    """A system prompt naming tools that are not in the tool list is `asked-a-got-b`."""
    import inspect

    from app import agent_engine

    source = inspect.getsource(agent_engine)
    assert "prefer mcp.check" not in source
    assert "MCP_BRIDGE_TOOLS" not in source
