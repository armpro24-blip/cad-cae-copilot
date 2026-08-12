"""Agent build123d runner — execute agent-authored code with correct exports.

When an agent does NOT have MCP tools (e.g. Kimi Code CLI without aieng-workbench
MCP), this script provides the same runner environment the backend uses, including:
- automatic step/stl/glb export (with binary=True for GLB)
- topology extraction
- Compound label preservation shim

Usage:
    conda run -n aieng311 python agent_build123d_runner.py my_model.py --out-dir ./output

The input Python file must assign the final model to a variable named ``result``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Re-use the backend's runner ASSEMBLY, not just its template. Substituting the
# placeholders here instead drifted the moment the template grew a second one:
# `__AIENG_SOURCE_LABEL_HINTS__` was left unreplaced, so every fallback run died
# with `NameError: name '__AIENG_SOURCE_LABEL_HINTS__' is not defined` — on the
# very first command AGENTS.md gives a tool-less agent.
from app.cad_generation import (
    _DESIGN_RULE_MARKER,
    _build_build123d_runner_script,
    _runner_subprocess_env,
)


class DesignRuleViolation(RuntimeError):
    """A `require(...)` in the model failed — a design decision, not a crash.

    The MCP path answers a failed `require()` with a structured
    `code: "design_rule_violation"` carrying just the message, and AGENTS.md
    promises exactly that. Fallback mode used to print the internal marker plus a
    traceback through a temp file instead, which is the same information with the
    actionable part buried.
    """


def run(code_text: str, out_dir: Path, timeout: int = 120) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    runner_script = _build_build123d_runner_script(code_text, timeout)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        runner_path = tmp / "runner.py"
        runner_path.write_text(runner_script, encoding="utf-8")
        out_step = tmp / "result.step"
        out_topo = tmp / "topology.json"
        out_stl = tmp / "result.stl"
        out_glb = tmp / "result.glb"

        proc = subprocess.run(
            [
                sys.executable,
                str(runner_path),
                str(out_step),
                str(out_topo),
                str(out_stl),
                str(out_glb),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Same launch contract as the backend: an explicit environment, and
            # never the parent's stdin (a child that inherits a pipe blocks at
            # interpreter startup on Windows).
            env=_runner_subprocess_env(),
            stdin=subprocess.DEVNULL,
        )

        if proc.returncode != 0:
            stderr = proc.stderr or ""
            for line in stderr.splitlines():
                if line.startswith(_DESIGN_RULE_MARKER):
                    raise DesignRuleViolation(
                        line[len(_DESIGN_RULE_MARKER):].strip()
                    )
            stderr_excerpt = stderr[-2000:] if stderr else "(no stderr)"
            raise RuntimeError(
                f"build123d execution failed (exit {proc.returncode}):\n{stderr_excerpt}"
            )

        step_path = out_dir / "result.step"
        stl_path = out_dir / "result.stl"
        glb_path = out_dir / "result.glb"
        topo_path = out_dir / "topology.json"

        step_bytes = out_step.read_bytes() if out_step.exists() else b""
        stl_bytes = out_stl.read_bytes() if out_stl.exists() else b""
        glb_bytes = out_glb.read_bytes() if out_glb.exists() else b""
        topo: dict = (
            json.loads(out_topo.read_text(encoding="utf-8"))
            if out_topo.exists()
            else {}
        )

        step_path.write_bytes(step_bytes)
        stl_path.write_bytes(stl_bytes)
        if glb_bytes:
            glb_path.write_bytes(glb_bytes)
        topo_path.write_text(json.dumps(topo, indent=2), encoding="utf-8")

        return {
            "step_path": str(step_path),
            "stl_path": str(stl_path),
            "glb_path": str(glb_path) if glb_bytes else None,
            "topology_path": str(topo_path),
            "glb_size": len(glb_bytes),
            "stl_size": len(stl_bytes),
            "step_size": len(step_bytes),
            "named_parts": [
                e.get("name")
                for e in topo.get("entities", [])
                if e.get("type") == "solid" and e.get("name")
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run agent build123d code with correct exports")
    parser.add_argument("script", help="Python file containing build123d code (must assign to 'result')")
    parser.add_argument("--out-dir", default=".", help="Directory to write result.step/.stl/.glb/.json")
    parser.add_argument("--timeout", type=int, default=120, help="Subprocess timeout in seconds")
    args = parser.parse_args()

    code_text = Path(args.script).read_text(encoding="utf-8")
    if "result" not in code_text:
        print("WARNING: script does not seem to assign to 'result'.", file=sys.stderr)

    try:
        result = run(code_text, Path(args.out_dir), timeout=args.timeout)
    except DesignRuleViolation as exc:
        print(json.dumps({"code": "design_rule_violation", "message": str(exc)}, indent=2))
        raise SystemExit(2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
