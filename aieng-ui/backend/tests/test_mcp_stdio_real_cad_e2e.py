"""End-to-end guard: REAL CAD through the REAL MCP stdio server.

The packaged smoke (`aieng-workbench-mcp-smoke`) exercises the stubbed-CAD
path only, which is how a total failure of the real path shipped unseen:
on Windows, the first lazy import of a heavy C extension (numpy / build123d's
OCP / gmsh) INSIDE the running FastMCP stdio event loop deadlocks in the DLL
loader and `cad.execute_build123d` never returns — even for `Box(10, 10, 10)`.
Measured 2026-08-10 with a minimal repro (bare FastMCP + `import numpy` in a
tool: infinite hang; same import at module scope before `mcp.run()`: instant).

This test drives the actual server subprocess over stdio exactly like an MCP
client and requires a real build123d build to complete. It would have caught
the hang, and it pins the fix (`_preload_native_runtime` + explicit runner
`env=`).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("build123d")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Generous: server import + native-stack preload can take tens of seconds on a
# cold Windows machine. The pre-fix failure mode was an INFINITE hang, so any
# finite bound distinguishes pass from regression.
_CALL_TIMEOUT_S = 120.0


class _StdioClient:
    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._lines: "queue.Queue[str]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)

    def send(self, message: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def recv(self, want_id: int, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self._lines.get(timeout=1.0).strip()
            except queue.Empty:
                continue
            if not line.startswith("{"):
                continue
            message = json.loads(line)
            if message.get("id") == want_id:
                return message
        return None

    def call_tool(self, rid: int, name: str, arguments: dict, timeout: float) -> dict:
        self.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}})
        message = self.recv(rid, timeout)
        assert message is not None, f"{name} produced no response within {timeout}s (hang regression?)"
        text = next(
            (c.get("text", "") for c in message.get("result", {}).get("content", [])
             if c.get("type") == "text"),
            "",
        )
        assert text, f"{name} returned no text content: {message}"
        return json.loads(text)


def test_real_cad_build_completes_over_stdio(tmp_path: Path) -> None:
    env = dict(os.environ)
    # Point at a dead backend so the server takes the in-process fallback —
    # the exact context in which the DLL-loader deadlock lived.
    env["AIENG_BACKEND_URL"] = "http://127.0.0.1:59999"
    env.pop("AIENG_MCP_PRELOAD_NATIVE", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server", "--approval-mode", "client",
         "--data-dir", str(tmp_path / "data")],
        cwd=_BACKEND_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    try:
        client = _StdioClient(proc)
        client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "e2e-test", "version": "0"}}})
        assert client.recv(1, _CALL_TIMEOUT_S) is not None, "initialize timed out"
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        project = client.call_tool(2, "aieng_create_project",
                                   {"name": "stdio-e2e"}, _CALL_TIMEOUT_S)
        project_id = project["id"]

        # Satisfy the guide gate the way a real agent session does.
        client.call_tool(3, "aieng_guide", {"topic": "cad"}, _CALL_TIMEOUT_S)

        started = time.monotonic()
        result = client.call_tool(
            4, "cad_execute_build123d",
            {"project_id": project_id,
             "code": "from build123d import *\nresult = Box(10, 10, 10)",
             "thumbnail": False, "response_detail": "compact", "timeout": 60},
            _CALL_TIMEOUT_S,
        )
        elapsed = time.monotonic() - started

        assert result["status"] == "ok", result
        assert result.get("backend") == "build123d"
        # The pre-fix behavior burned the full subprocess timeout and errored;
        # a healthy call is far under the tool's own 60s budget.
        assert elapsed < _CALL_TIMEOUT_S
    finally:
        proc.kill()
        proc.wait(timeout=10)
