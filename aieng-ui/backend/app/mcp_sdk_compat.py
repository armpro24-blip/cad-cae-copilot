"""One import surface for the MCP SDK, whichever major version is installed.

``mcp`` 2.0 (2026-07-28) renamed ``FastMCP`` to ``MCPServer`` and moved every
module this server touches from ``mcp.server.fastmcp`` to
``mcp.server.mcpserver`` — importing the old path raises a guidance stub. The
day it shipped, a fresh install of ``aieng-workbench-mcp`` stopped importing and
CI went red only via a scheduled job (#462 pinned ``<2`` as the stopgap; #463 is
this migration).

Probed on 1.27.1 and 2.1.1 before writing this: every internal the server relies
on — ``_tool_manager._tools``, ``Tool.parameters`` / ``Tool.fn_metadata``,
``FuncMetadata(arg_model=...)``, ``ArgModelBase.model_dump_one_level``,
``run("stdio")`` / ``sse_app()``, ``Context.elicit`` /
``session.check_client_capability``, ``Image(data=..., format=...)``,
``Prompt.from_function`` — exists with the same shape on both sides of the
rename. The one genuine API break is ``FastMCP.get_context()``, which 2.x
removed: the request context now travels down the call chain instead of living
in a contextvar. Both majors inject it into a handler parameter annotated with
``Context`` (verified: ``Tool.context_kwarg`` is detected identically), so the
server uses that instead — this module exists so it can do so spelled one way.

Try 2.x first: on 2.x the OLD path raises the stub's ModuleNotFoundError, while
on 1.x the NEW path raises a plain ModuleNotFoundError. Trying 1.x first would
therefore need to distinguish the stub from a missing module; this order needs
no such care.
"""
from __future__ import annotations

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.context import Context
    from mcp.server.mcpserver.prompts import Prompt
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase, FuncMetadata
    from mcp.server.mcpserver.utilities.types import Image

    MCP_SDK_MAJOR = 2
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP, Image  # type: ignore[no-redef]
    from mcp.server.fastmcp.utilities.func_metadata import (  # type: ignore[no-redef]
        ArgModelBase,
        FuncMetadata,
    )

    try:
        from mcp.server.fastmcp.prompts import Prompt  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - very old 1.x layouts
        from mcp.server.fastmcp.prompts.base import Prompt  # type: ignore[no-redef]

    MCP_SDK_MAJOR = 1

__all__ = [
    "ArgModelBase",
    "Context",
    "FastMCP",
    "FuncMetadata",
    "Image",
    "MCP_SDK_MAJOR",
    "Prompt",
]
