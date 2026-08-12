"""The no-MCP fallback path must actually work (dogfood mission M6).

AGENTS.md promises a tool-less agent (Kimi Code CLI without MCP, a plain
terminal) two commands: run build123d through `agent_build123d_runner.py`, then
import the STEP with `agent_import_project.py`. Nothing exercised them — no
test, no CI job, only the doc — and the first one had been dead since the runner
template grew a second placeholder:

    NameError: name '__AIENG_SOURCE_LABEL_HINTS__' is not defined

on the very first command in the documented sequence. The runner re-implemented
the backend's placeholder substitution instead of calling its assembly function,
so it drifted silently the moment a placeholder was added.

These are slow (real build123d + OCC) but they are the only thing standing
between the fallback path and the next silent drift.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPTS = _BACKEND / "scripts"

_MODEL = """
from build123d import *

PLATE_THICKNESS = 8.0

plate = Box(40, 30, PLATE_THICKNESS)
plate.label = "base_plate"
plate.color = Color(0.55, 0.62, 0.70)

boss = Cylinder(5, 12).moved(Location((0, 0, 10)))
boss.label = "boss_main"

result = Compound(children=[plate, boss])
"""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=600,
        env=dict(os.environ), stdin=subprocess.DEVNULL,
    )


def _json_tail(stdout: str) -> dict:
    """The scripts print a human line or two before their JSON payload."""
    start = stdout.find("{")
    assert start >= 0, f"no JSON in output:\n{stdout}"
    return json.loads(stdout[start:])


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    pytest.importorskip("build123d")
    work = tmp_path_factory.mktemp("fallback")
    script = work / "my_model.py"
    script.write_text(_MODEL, encoding="utf-8")
    out = work / "output"

    proc = _run(
        [str(_SCRIPTS / "agent_build123d_runner.py"), str(script), "--out-dir", str(out)],
        cwd=_SCRIPTS,
    )
    assert proc.returncode == 0, (
        "the first command AGENTS.md gives a tool-less agent failed:\n"
        f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    result = _json_tail(proc.stdout)
    result["_work"] = str(work)
    return result


def test_the_runner_exports_all_three_formats(built: dict) -> None:
    assert built["step_size"] > 0, "no STEP written"
    assert built["stl_size"] > 0, "no STL written"
    assert built["glb_size"] > 0, (
        "no GLB written — the frontend cannot render the JSON glTF build123d "
        "produces without binary=True"
    )
    for key in ("step_path", "stl_path", "glb_path"):
        assert Path(built[key]).is_file(), f"{key} missing on disk"


def test_the_runner_preserves_part_labels(built: dict) -> None:
    """Labels are what makes the topology semantic rather than body_001."""
    assert set(built["named_parts"]) == {"base_plate", "boss_main"}, built["named_parts"]


def test_a_failed_require_reads_as_a_design_decision(tmp_path: Path) -> None:
    """AGENTS.md promises `code: design_rule_violation`, not a raw traceback.

    Fallback mode used to print the internal `__AIENG_DESIGN_RULE_VIOLATION__`
    marker plus a traceback through a temp file — the same information with the
    actionable part buried.
    """
    pytest.importorskip("build123d")
    script = tmp_path / "bad.py"
    script.write_text(
        "from build123d import *\n"
        "WALL_THICKNESS = 1.5\n"
        'require(WALL_THICKNESS >= 3.0, "wall below 3mm CNC minimum")\n'
        "result = Box(20, 20, WALL_THICKNESS)\n",
        encoding="utf-8",
    )
    proc = _run(
        [str(_SCRIPTS / "agent_build123d_runner.py"), str(script),
         "--out-dir", str(tmp_path / "out")],
        cwd=_SCRIPTS,
    )
    assert proc.returncode == 2, proc.stderr[-1000:]
    payload = _json_tail(proc.stdout)
    assert payload == {
        "code": "design_rule_violation",
        "message": "wall below 3mm CNC minimum",
    }, payload
    assert "__AIENG_" not in proc.stdout, "internal marker leaked to the agent"


def test_the_importer_publishes_a_viewable_project(built: dict) -> None:
    work = Path(built["_work"])
    data_root = work / "data"

    proc = _run([
        str(_SCRIPTS / "agent_import_project.py"), built["step_path"],
        "--name", "Fallback smoke", "--preview", built["glb_path"],
        "--project-id", "fallback_smoke", "--data-root", str(data_root),
    ], cwd=_SCRIPTS)
    assert proc.returncode == 0, f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"

    payload = _json_tail(proc.stdout)
    assert payload["import"]["status"] == "ok", payload["import"]
    assert payload["enrich"]["status"] == "ok", payload["enrich"]

    project_dir = data_root / "projects" / "fallback_smoke"
    metadata = json.loads((project_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "viewer_ready_glb", metadata["status"]
    assert (project_dir / "fallback_smoke.aieng").is_file()
    assert (project_dir / metadata["web_asset"]).is_file()
