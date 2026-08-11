"""The server must stay answerable while a tool runs (#481).

Measured 2026-08-11 before the fix: a trivial `aieng.list_projects` sent two
seconds into a 12-second CAD build was answered at +14.12 s — only once the
build finished. Tool bodies ran inline on the JSON-RPC event loop, so a client
pinging for liveness during a long CAD or solver call had every reason to
believe the server had died.

Tool bodies now run in a worker thread. That removes the loop's accidental
mutual exclusion, so mutations on the same project are serialized explicitly.
"""
from __future__ import annotations

import inspect
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import mcp_server

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_same_project_gets_one_lock_different_projects_do_not() -> None:
    a1 = mcp_server._project_mutation_lock("proj_a")
    a2 = mcp_server._project_mutation_lock("proj_a")
    b = mcp_server._project_mutation_lock("proj_b")

    assert a1 is a2, "two writes to one package must contend for the same lock"
    assert a1 is not b, "unrelated projects must not block each other"


def test_concurrent_tool_count_is_bounded() -> None:
    limiter = mcp_server._tool_thread_limiter()
    assert limiter is mcp_server._tool_thread_limiter(), "limiter must be shared"
    assert limiter.total_tokens >= 1


def test_handlers_are_coroutines_so_the_loop_is_never_occupied() -> None:
    """A sync handler would put the tool body back on the event loop."""
    server = mcp_server._build_mcp_server(compact_surface=True)
    tools = server._tool_manager._tools  # type: ignore[attr-defined]
    assert tools, "expected registered tools"
    non_async = [
        name for name, tool in tools.items()
        if not inspect.iscoroutinefunction(getattr(tool, "fn", None))
    ]
    assert non_async == [], f"these handlers still block the loop: {non_async[:5]}"


# ── end to end: a real server, a real long call, a real ping ─────────────────

def test_a_ping_is_answered_while_a_long_build_runs(tmp_path: Path) -> None:
    pytest.importorskip("build123d")

    env = dict(os.environ)
    env["AIENG_BACKEND_URL"] = "http://127.0.0.1:59999"  # force in-process
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server", "--approval-mode", "client",
         "--data-dir", str(tmp_path / "data")],
        cwd=_BACKEND_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env,
    )
    seen: "queue.Queue[tuple[float, dict]]" = queue.Queue()

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("{"):
                try:
                    seen.put((time.monotonic(), json.loads(line)))
                except ValueError:
                    continue

    threading.Thread(target=_pump, daemon=True).start()

    arrivals: dict[int, float] = {}
    payloads: dict[int, dict] = {}

    def send(message: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def drain(until: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ts, msg = seen.get(timeout=1)
            except queue.Empty:
                continue
            rid = msg.get("id")
            if rid is not None:
                arrivals[rid] = ts
                payloads[rid] = msg
                if rid == until:
                    return True
        return False

    def call(rid: int, tool: str, arguments: dict) -> None:
        send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments}})

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "concurrency", "version": "0"}}})
        assert drain(1, 120), "initialize timed out"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        call(2, "aieng_guide", {"topic": "cad"})
        assert drain(2, 60)
        call(3, "aieng_create_project", {"name": "concurrency"})
        assert drain(3, 60)
        project_id = json.loads(
            next(c["text"] for c in payloads[3]["result"]["content"] if c["type"] == "text")
        )["id"]

        # Unique source, or the build cache would make this finish instantly and
        # the test would pass without ever exercising concurrency.
        slow_code = (
            "from build123d import *\n"
            "import time\n"
            f"# cache-bust {time.time()}\n"
            "time.sleep(8)\n"
            "result = Box(10, 10, 10)\n"
        )
        started = time.monotonic()
        call(10, "cad_execute_build123d",
             {"project_id": project_id, "code": slow_code, "thumbnail": False,
              "response_detail": "compact", "timeout": 60})
        time.sleep(1.5)
        pinged = time.monotonic()
        call(11, "aieng_list_projects", {})

        assert drain(11, 30), "the ping was never answered"
        ping_wait = arrivals[11] - pinged
        assert drain(10, 120), "the slow build never finished"
        build_duration = arrivals[10] - started

        assert build_duration > 5, (
            f"the build finished in {build_duration:.1f}s — it was not slow "
            "(cache hit?), so this test proved nothing"
        )
        assert ping_wait < 3.0, (
            f"the ping waited {ping_wait:.1f}s while a {build_duration:.1f}s build ran; "
            "the event loop is still blocked by tool execution"
        )
    finally:
        proc.kill()
        proc.wait(timeout=10)
