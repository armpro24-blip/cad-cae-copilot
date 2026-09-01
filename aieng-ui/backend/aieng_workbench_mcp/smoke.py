"""Deterministic smoke check for the packaged AIENG Workbench MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


_ENV_KEYS = (
    "AIENG_BACKEND_URL",
    "AIENG_MCP_MANAGED_APPROVAL",
    "AIENG_MCP_BLOCK_APPROVAL_TOOLS",
    "AIENG_MCP_REQUIRE_GUIDES",
    "AIENG_PLATFORM_DATA",
)


def _tool_text(call_result: Any) -> str:
    """Text payload of a tool call, on either SDK major.

    mcp 1.x returned a bare list of content blocks; 2.x wraps them in a
    ``CallToolResult`` under ``.content``. This is the packaged entry point an
    external agent runs first, so it must read both.
    """
    content = getattr(call_result, "content", call_result)
    if isinstance(content, (list, tuple)) and content:
        first = content[0]
        return getattr(first, "text", str(first))
    return str(content)


def _json_tool_result(call_result: Any) -> dict[str, Any]:
    return json.loads(_tool_text(call_result))


def _smoke_cad_code() -> str:
    return """\
from build123d import *

PLATE_LENGTH = 80
PLATE_WIDTH = 40
PLATE_THICKNESS = 8
HOLE_RADIUS = 3.3

with BuildPart() as smoke_bracket:
    Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS)
    with Locations((-28, -12, 0), (-28, 12, 0), (28, -12, 0), (28, 12, 0)):
        Hole(HOLE_RADIUS, depth=PLATE_THICKNESS + 2)

result = smoke_bracket.part
result.label = "smoke_base_plate"
result.color = Color(0.45, 0.58, 0.70)
"""


def _install_stub_cad_handler(project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from app import runtime as _rt

    original = dict(_rt._REGISTRY["cad.execute_build123d"])
    calls: list[dict[str, Any]] = []

    def _stub_execute_build123d(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(inp))
        return {
            "status": "ok",
            "project_id": project_id,
            "mode": inp.get("mode", "replace"),
            "used_base": False,
            "cache_hit": False,
            "named_parts": ["smoke_base_plate"],
            "parts_added": ["smoke_base_plate"],
            "topology_summary": {"solid_count": 1, "face_count": 6},
            "geometry_report": {
                "summary": "stubbed smoke bracket: one named base plate with coherent topology metadata"
            },
            "artifacts": [
                {"path": "geometry/generated.step", "kind": "step"},
                {"path": "geometry/preview.glb", "kind": "glb"},
            ],
        }

    _rt._REGISTRY["cad.execute_build123d"] = {
        **original,
        "handler": _stub_execute_build123d,
    }
    return original, calls


async def _run_smoke_async(*, data_dir: Path, real_cad: bool) -> dict[str, Any]:
    from app import mcp_server as ms
    from app import runtime as _rt

    old_backend_url = ms._BACKEND_URL
    original_env = {key: os.environ.get(key) for key in _ENV_KEYS}
    original_cad_tool: dict[str, Any] | None = None
    cad_calls: list[dict[str, Any]] = []
    try:
        ms._apply_cli_runtime_options(
            backend_url="",
            approval_mode="client",
            data_dir=str(data_dir),
            require_guides=True,
        )
        mcp = ms._build_mcp_server()

        tools = {tool.name for tool in await mcp.list_tools()}
        prompts = {prompt.name for prompt in await mcp.list_prompts()}
        resources = {str(resource.uri) for resource in await mcp.list_resources()}
        assert "aieng_agent_readme" in tools
        assert "aieng_create_project" in tools
        assert "cad_execute_build123d" in tools
        assert "aieng_mcp_first_onboarding" in prompts
        assert "aieng_cad_build_workflow" in prompts
        assert "aieng://guides/mcp-first-discipline" in resources

        prompt = await mcp.get_prompt("aieng_mcp_first_onboarding")
        prompt_text = "\n".join(str(message.content) for message in prompt.messages)
        assert "aieng.agent_readme" in prompt_text

        resource_chunks = list(await mcp.read_resource("aieng://guides/mcp-first-discipline"))
        resource_text = "\n".join(str(getattr(chunk, "content", chunk)) for chunk in resource_chunks)
        assert "AIENG_MCP_BLOCK_APPROVAL_TOOLS=1" in resource_text
        assert "solver" in resource_text.lower()

        readme = _json_tool_result(await mcp.call_tool("aieng_agent_readme", {}))
        assert readme["mode"] == "quickstart"
        _json_tool_result(await mcp.call_tool("aieng_guide", {"topic": "cad"}))

        created = _json_tool_result(await mcp.call_tool("aieng_create_project", {"name": "MCP packaged smoke"}))
        project_id = str(created["id"])

        if not real_cad:
            original_cad_tool, cad_calls = _install_stub_cad_handler(project_id)

        cad_result = _json_tool_result(
            await mcp.call_tool(
                "cad_execute_build123d",
                {
                    "project_id": project_id,
                    "code": _smoke_cad_code(),
                    "mode": "replace",
                    "response_detail": "compact",
                    "thumbnail": False,
                    "timeout": 60,
                },
            )
        )
        if cad_result.get("status") != "ok":
            raise RuntimeError("CAD smoke failed: " + json.dumps(cad_result, ensure_ascii=False, default=str))
        assert "smoke_base_plate" in cad_result.get("named_parts", [])
        assert cad_result.get("topology_summary") or cad_result.get("geometry_report")

        os.environ["AIENG_MCP_BLOCK_APPROVAL_TOOLS"] = "1"
        before_block_count = len(cad_calls)
        blocked = _json_tool_result(
            await mcp.call_tool(
                "cad_execute_build123d",
                {"project_id": project_id, "code": "result = None", "mode": "replace"},
            )
        )
        assert blocked["code"] == "approval_blocked"
        if not real_cad:
            assert len(cad_calls) == before_block_count

        return {
            "status": "ok",
            "mode": "real-cad" if real_cad else "stubbed-cad",
            "data_dir": str(data_dir),
            "project_id": project_id,
            "tool_count": len(tools),
            "prompt_count": len(prompts),
            "resource_count": len(resources),
            "cad_named_parts": cad_result.get("named_parts", []),
            "approval_block_code": blocked["code"],
        }
    finally:
        if original_cad_tool is not None:
            _rt._REGISTRY["cad.execute_build123d"] = original_cad_tool
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ms._BACKEND_URL = old_backend_url


def run_smoke(*, data_dir: str | Path | None = None, real_cad: bool = False) -> dict[str, Any]:
    if data_dir is not None:
        root = Path(data_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(_run_smoke_async(data_dir=root, real_cad=real_cad))

    with tempfile.TemporaryDirectory(prefix="aieng-mcp-smoke-", ignore_cleanup_errors=True) as tmp:
        return asyncio.run(_run_smoke_async(data_dir=Path(tmp), real_cad=real_cad))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aieng-workbench-mcp-smoke")
    parser.add_argument("--data-dir", help="Optional data directory to keep smoke artifacts.")
    parser.add_argument(
        "--real-cad",
        action="store_true",
        help="Run real build123d/OCP CAD execution instead of the CI-safe stub.",
    )
    args = parser.parse_args(argv)

    result = run_smoke(data_dir=args.data_dir, real_cad=args.real_cad)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
