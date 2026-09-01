"""MCP protocol-level smoke (#175).

The Docker smoke proves the MCP server is actually *usable* over the container
endpoint — a real `initialize` / `tools/list` handshake, the canonical tools
present, a read-only call, and the managed-approval fail-safe — not just that
`/sse` is reachable. The container path runs in CI (`docker-smoke.yml`); this
module verifies the same protocol/assertion logic locally and deterministically:

- the real FastMCP server answers `initialize` + `tools/list` and a read-only
  `call_tool` over the in-memory MCP transport,
- the MCP-facing tool names (dots → underscores) include the canonical set, and
- the managed-approval fail-safe precondition holds: with no viewer subscribed,
  `/api/agent/agentic/approval-surface` reports `available: false`, which is what
  makes a gated call return `approval_surface_unavailable` in managed mode.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from pathlib import Path

from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.config import Settings

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

# MCP-facing names: runtime registers dotted names, mcp_server exposes them with
# dots replaced by underscores. These are the canonical tools #175 asserts.
_CANONICAL_MCP_TOOLS = {
    "aieng_agent_readme",
    "aieng_list_projects",
    "cad_execute_build123d",
    "cae_prepare_solver_run",
}


def _make_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def test_mcp_tool_names_use_underscores_and_include_canonical() -> None:
    """The MCP tool surface exposes the canonical tools under underscore names."""
    from app import runtime

    create_app()  # populates runtime._REGISTRY
    dotted = {t["name"] for t in runtime.list_tools_for_mcp()}
    for dotted_name in (
        "aieng.agent_readme",
        "aieng.list_projects",
        "cad.execute_build123d",
        "cae.prepare_solver_run",
    ):
        assert dotted_name in dotted, f"missing registered tool: {dotted_name}"

    mcp_names = {name.replace(".", "_") for name in dotted}
    missing = _CANONICAL_MCP_TOOLS - mcp_names
    assert not missing, f"canonical MCP tool names missing: {missing}"


def test_agent_context_recommendations_resolve_to_registered_tools() -> None:
    """#286: every tool_hint / reference agent_context emits must be a real MCP tool."""
    from app import runtime
    from app.agent_context import _available_actions
    from app.cad_observation import cad_specific_recommendations

    create_app()
    registered = {t["name"] for t in runtime.list_tools_for_mcp()}

    refs: set[str] = set()
    # cad_observation recommendation branches (these feed agent_context).
    for obs in (
        {"status": "missing", "geometry_evidence_level": "none"},
        {"status": "ready", "geometry_evidence_level": "exported_geometry",
         "cae_readiness_hints": {"present_paths": []}},
        {"status": "ready", "geometry_evidence_level": "exported_geometry",
         "floating_parts": ["x"], "cae_readiness_hints": {"present_paths": []}},
    ):
        for rec in cad_specific_recommendations(obs):
            if rec.get("reference"):
                refs.add(rec["reference"])
    # agent_context's own action surface (passes references through as tool_hint).
    actions = _available_actions(
        {"next_recommended_actions": [
            {"kind": "inspect_geometry_readiness", "reference": "aieng.inspect_package",
             "label": "Inspect", "rationale": "r"}]},
        computed_metrics={}, comparison={},
    )
    for a in actions:
        if a.get("tool_hint"):
            refs.add(a["tool_hint"])

    assert refs, "expected recommendations to fire"
    unresolved = sorted(r for r in refs if r not in registered)
    assert not unresolved, f"agent_context emits non-existent tools: {unresolved}"
    # the two previously-dangling names must be gone (regression)
    assert "cad.inspect_geometry" not in refs
    assert "compare_targets" not in refs


@asynccontextmanager
async def _connected_client(mcp: Any):
    """A real client session speaking MCP to `mcp` over in-memory streams.

    mcp 1.x shipped `create_connected_server_and_client_session`; 2.x dropped that
    convenience and keeps only the primitive it was built on
    (`create_client_server_memory_streams`), and renamed the low-level server
    attribute. Both are assembled here so the handshake this test exercises is
    the real protocol on either SDK major.
    """
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    server = getattr(mcp, "_lowlevel_server", None) or mcp._mcp_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                lambda: server.run(
                    server_read, server_write, server.create_initialization_options()
                )
            )
            async with ClientSession(read_stream=client_read, write_stream=client_write) as client:
                await client.initialize()
                yield client
            tg.cancel_scope.cancel()


def test_inproc_mcp_handshake_lists_tools_and_reads_readme() -> None:
    """A real MCP client session against the server completes the handshake,
    lists the canonical tools, and a read-only call returns content."""
    from app.mcp_server import _build_mcp_server

    async def _run() -> None:
        mcp = _build_mcp_server()
        async with _connected_client(mcp) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            missing = _CANONICAL_MCP_TOOLS - names
            assert not missing, f"tools/list missing canonical tools: {missing}"

            result = await client.call_tool("aieng_agent_readme", {})
            assert not getattr(result, "isError", False), "agent_readme call errored"
            text = "".join(getattr(block, "text", "") for block in result.content)
            assert "First three calls" in text or "aieng Workbench" in text, (
                "agent_readme returned no recognizable onboarding content"
            )

    asyncio.run(asyncio.wait_for(_run(), timeout=120))


def test_approval_surface_unavailable_without_viewer(tmp_path: Path) -> None:
    """Managed-approval fail-safe precondition: with no viewer subscribed, the
    approval surface reports unavailable — this is what makes a gated call
    fail-safe with `approval_surface_unavailable` instead of stalling."""
    app = create_app(_make_settings(tmp_path))
    client = TestClient(app)

    resp = client.get("/api/agent/agentic/approval-surface")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["subscriber_count"] == 0
