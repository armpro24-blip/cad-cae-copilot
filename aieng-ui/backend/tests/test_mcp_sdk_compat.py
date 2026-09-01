"""The MCP SDK import surface must stay behind one module (#463).

`mcp` 2.0 renamed `FastMCP` to `MCPServer` and moved every module this server
touches. The break reached us as a red scheduled job and a non-importable fresh
install — not as a failing local test — because the imports were scattered
across the server, the core library and four test modules, and because nothing
exercised the packaged form against an unpinned resolve.

These tests keep the surface consolidated and the version-sensitive spots
honest. They are cheap and static on purpose: the real cross-version proof is
running the whole suite under both majors, which CI does in the `MCP SDK 1.x` /
`MCP SDK 2.x` lanes. The packaging smoke keeps a free `mcp` resolve alongside
them, as the canary for a future major nobody has ported to.
"""
from __future__ import annotations

import os
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


# The core library ships its own tiny MCP server and its own tests; both import
# the SDK. Scanning only the backend left exactly that blind spot — CI caught a
# 1.x-only import in `aieng/tests/test_mcp_server.py` that this guard had walked
# straight past.
_SCANNED_TREES = (
    _BACKEND / "app",
    _BACKEND / "tests",
    _REPO / "aieng" / "src",
    _REPO / "aieng" / "tests",
)

# The core library has no dependency on the backend, so it cannot import the
# compat module; it does the same try/except two-step inline. Exempt those exact
# files by PATH — a bare basename would also pardon the backend's own
# `server.py`, which is a wider exemption than the reason for it.
_ALLOWED_DIRECT_IMPORTERS = {
    _BACKEND / "app" / "mcp_sdk_compat.py",
    _REPO / "aieng" / "src" / "aieng" / "mcp" / "server.py",
    _REPO / "aieng" / "tests" / "test_mcp_server.py",
}


# Every way of reaching a versioned path, not just the one the port happened to
# use. A guard that only knows `from X import Y` is a guard whose own boundary
# was never checked — which is the same shape as the blind spot that let a
# 1.x-only import in the core's tests walk past the first version of this file.
_VERSIONED_IMPORT = re.compile(
    r"""(?:
          \b(?:from|import)\s+mcp\.server\.(?:fastmcp|mcpserver)\b
        | (?:import_module|__import__)\s*\(\s*["']mcp\.server\.(?:fastmcp|mcpserver)
        )""",
    re.VERBOSE,
)


def _versioned_import_offenders(paths: list[Path]) -> list[str]:
    """Lines reaching a version-specific SDK path, outside the exempt files."""
    allowed = {p.resolve() for p in _ALLOWED_DIRECT_IMPORTERS}
    offenders: list[str] = []
    for path in paths:
        if path.resolve() in allowed:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if _VERSIONED_IMPORT.search(line):
                offenders.append(f"{path}: {line.strip()}")
    return offenders


def test_no_module_imports_the_versioned_paths_directly() -> None:
    """One import site, so the next rename is a one-file change.

    `mcp.server.fastmcp` (1.x) and `mcp.server.mcpserver` (2.x) may appear only
    where a fallback is written deliberately — anywhere else and a rename
    silently strands that file on one major.
    """
    paths = [p for tree in _SCANNED_TREES for p in tree.glob("**/*.py")]
    offenders = [
        o.replace(f"{_REPO}{os.sep}", "") for o in _versioned_import_offenders(paths)
    ]
    assert offenders == [], (
        "import these through app.mcp_sdk_compat instead:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_catches_every_way_of_writing_the_import(tmp_path: Path) -> None:
    """The check's own coverage boundary, checked.

    Both defects this file exists to prevent were boundary defects: the port
    scattered imports the guard did not scan, and the guard matched only the
    import spelling the port itself had used. So plant each spelling.
    """
    # Composed rather than written literally: a literal would be flagged by the
    # very guard under test — it scans this tree — and exempting this file to
    # allow the fixtures would pardon a real regression in it too.
    old, new = "mcp.server." + "fastmcp", "mcp.server." + "mcpserver"
    spellings = (
        f"from {old} import FastMCP",
        f"from {new} import MCPServer",
        f"import {old}",
        f"import {new} as srv",
        f'importlib.import_module("{old}")',
        f"__import__('{new}')",
    )
    for index, line in enumerate(spellings):
        planted = tmp_path / f"planted_{index}.py"
        planted.write_text(line + "\n", encoding="utf-8")
        assert _versioned_import_offenders([planted]), f"guard missed: {line}"

    innocent = tmp_path / "innocent.py"
    innocent.write_text(
        f"from app.mcp_sdk_compat import FastMCP\n# from {old} import FastMCP\n",
        encoding="utf-8",
    )
    assert _versioned_import_offenders([innocent]) == []


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
