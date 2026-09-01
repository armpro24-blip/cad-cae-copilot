"""The MCP SDK import surface must stay behind one module (#463).

`mcp` 2.0 renamed `FastMCP` to `MCPServer` and moved every module this server
touches. The break reached us as a red scheduled job and a non-importable fresh
install — not as a failing local test — because the imports were scattered
across the server, the core library and four test modules, and because nothing
exercised the packaged form against an unpinned resolve.

These tests keep the surface consolidated and the version-sensitive spots
honest. They are cheap and static on purpose: the real cross-version proof is
running the suite under both majors, which CI does via the packaging smoke.
"""
from __future__ import annotations

import re
from pathlib import Path

from app import mcp_sdk_compat

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parents[1]

# Everything the server needs from the SDK, in one place.
_REQUIRED_EXPORTS = ("ArgModelBase", "Context", "FastMCP", "FuncMetadata", "Image", "Prompt")


def test_the_compat_module_exports_the_whole_surface() -> None:
    for name in _REQUIRED_EXPORTS:
        assert hasattr(mcp_sdk_compat, name), f"compat module is missing {name}"
    assert mcp_sdk_compat.MCP_SDK_MAJOR in (1, 2)


def test_no_module_imports_the_versioned_paths_directly() -> None:
    """One import site, so the next rename is a one-file change.

    `mcp.server.fastmcp` (1.x) and `mcp.server.mcpserver` (2.x) may appear only
    inside the compat module — anywhere else and a rename silently strands that
    file on one major.
    """
    offenders: list[str] = []
    for path in list(_BACKEND.glob("app/**/*.py")) + list(_BACKEND.glob("tests/**/*.py")):
        if path.name == "mcp_sdk_compat.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"\bfrom mcp\.server\.(fastmcp|mcpserver)\b", line):
                offenders.append(f"{path.relative_to(_REPO)}: {line.strip()}")
    assert offenders == [], (
        "import these through app.mcp_sdk_compat instead:\n  " + "\n  ".join(offenders)
    )


def test_the_server_no_longer_calls_get_context() -> None:
    """`FastMCP.get_context()` is gone in 2.x — the context is injected instead."""
    source = (_BACKEND / "app" / "mcp_server.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "get_context()" not in code, (
        "get_context() was removed in mcp 2.x; annotate a handler parameter with "
        "Context and let the SDK inject it"
    )
    assert "async def _handler_elicit(ctx: Context | None = None" in code, (
        "the elicit handler must take the injected context"
    )


def test_a_tool_result_is_unwrapped_by_envelope_not_by_type() -> None:
    """1.x returned a list of content blocks; 2.x wraps them in CallToolResult.

    The packaged smoke is the first thing an external agent runs, so it must read
    both — it was the one product-code site with the 1.x assumption.
    """
    from aieng_workbench_mcp.smoke import _tool_text

    class _Block:
        text = "payload"

    class _Result:
        content = [_Block()]

    assert _tool_text([_Block()]) == "payload", "1.x bare-list shape"
    assert _tool_text(_Result()) == "payload", "2.x CallToolResult shape"


def test_the_active_projects_do_not_pin_away_from_2x() -> None:
    """The `<2` stopgap (#462) is what this port exists to lift."""
    for rel in ("aieng-ui/backend/pyproject.toml", "aieng/pyproject.toml"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "mcp>=" in line and "<2" in line:
                raise AssertionError(f"{rel} still pins mcp below 2.x: {line.strip()}")
