"""Every backend subprocess spawn must declare what the child's stdin is.

Measured twice (2026-08-10 CAD runners, 2026-08-11 the ccx launcher): on
Windows, a child that INHERITS the parent's stdin blocks before executing any
code when the parent is the MCP stdio server — because that handle is the
JSON-RPC protocol pipe. The child shows one idle thread and zero Python frames
and never runs; `conda run ... ccx` sat at 0 CPU with no children until the MCP
client's 1800s idle timeout fired.

Fixing the sites one at a time did not hold: the CAD runners were fixed first
and the solver launcher in `runtime_registry/cae.py` — a *different* spawn site
reached by the very next tool in the same workflow — still hung. So this test
scans the source instead of trusting review: every `subprocess.run` /
`subprocess.Popen` call in `app/` must pass `stdin=` (DEVNULL when the child
needs no input, PIPE when it is fed) or `input=` (which implies a stdin pipe).
"""
from __future__ import annotations

import ast
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _spawn_calls_missing_stdin(tree: ast.AST) -> list[tuple[int, str]]:
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"run", "Popen"}:
            continue
        # subprocess.run(...) / _subprocess.Popen(...) — match on the module
        # alias ending in "subprocess" so both spellings are covered.
        base = func.value
        if not isinstance(base, ast.Name) or not base.id.endswith("subprocess"):
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        if "stdin" in keywords or "input" in keywords:
            continue
        if any(kw.arg is None for kw in node.keywords):
            continue  # **kwargs passthrough — cannot judge statically
        offenders.append((node.lineno, f"{base.id}.{func.attr}"))
    return offenders


def test_every_subprocess_spawn_declares_stdin() -> None:
    failures: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - unreadable file
            continue
        for lineno, call in _spawn_calls_missing_stdin(tree):
            failures.append(f"{path.relative_to(_APP_ROOT.parent)}:{lineno} {call}(...)")

    assert not failures, (
        "These subprocess spawns inherit the parent's stdin. Under the MCP stdio "
        "server that is the JSON-RPC pipe and the child hangs at startup on "
        "Windows. Pass stdin=subprocess.DEVNULL (no input needed) or "
        "stdin=subprocess.PIPE / input=... (child is fed):\n  "
        + "\n  ".join(failures)
    )
