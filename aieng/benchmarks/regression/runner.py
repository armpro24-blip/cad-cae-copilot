"""Lightweight fixed-prompt regression runner for the workbench.

Usage:
    python runner.py --tags core
    python runner.py --tags all --output runs/run_$(date -u +%%Y%%m%%dT%%H%%M%%SZ)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Ensure the aieng source package is importable for STEP import.
_SRC_PATH = str(Path(__file__).resolve().parents[3] / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)
from aieng.geometry.step_importer import import_step_package

# Minimal deterministic build123d scripts for the CAD-create prompts.
CAD_CREATE_SCRIPTS: dict[str, str] = {
    "001_cad_create_bracket": '''
from build123d import *
VERTICAL_HEIGHT, VERTICAL_WIDTH, THICKNESS = 60.0, 40.0, 5.0
HORIZONTAL_LENGTH, HOLE_DIAMETER = 50.0, 8.0
with BuildPart() as bracket:
    with BuildSketch(Plane.XY):
        Rectangle(VERTICAL_WIDTH, VERTICAL_HEIGHT, align=Align.MIN)
        with Locations((0, -HORIZONTAL_LENGTH)):
            Rectangle(VERTICAL_WIDTH, HORIZONTAL_LENGTH, align=Align.MIN)
    extrude(amount=THICKNESS)
    with Locations((VERTICAL_WIDTH / 2, VERTICAL_HEIGHT - 15)):
        Hole(radius=HOLE_DIAMETER / 2)
bracket.part.label = "bracket"
bracket.part.color = Color(0.75, 0.75, 0.78)
result = bracket.part
''',
    "002_cad_create_flange": '''
from build123d import *
OUTER_D, THICKNESS, BORE_D = 80.0, 10.0, 25.0
BOLT_D, BOLT_PCD, N_BOLTS = 8.0, 60.0, 4
with BuildPart() as flange:
    Cylinder(radius=OUTER_D / 2, height=THICKNESS)
    with Locations((0, 0, 0)):
        Hole(radius=BORE_D / 2)
    with PolarLocations(BOLT_PCD / 2, N_BOLTS):
        Hole(radius=BOLT_D / 2)
flange.part.label = "flange"
flange.part.color = Color(0.6, 0.6, 0.65)
result = flange.part
''',
    "003_cad_create_enclosure": '''
from build123d import *
W, H, D, WALL, FILLET = 100.0, 60.0, 40.0, 2.0, 3.0
CUT_W, CUT_H = 20.0, 10.0
with BuildPart() as enclosure:
    Box(W, H, D)
    fillet(enclosure.edges(), FILLET)
    with BuildPart(mode=Mode.SUBTRACT):
        Box(W - 2 * WALL, H - 2 * WALL, D - 2 * WALL)
    front = enclosure.faces().sort_by(Axis.X)[-1]
    with Locations(front):
        Box(CUT_W, CUT_H, WALL + 0.1)
enclosure.part.label = "enclosure"
result = enclosure.part
''',
    "004_cad_create_pipe_tee": '''
from build123d import *
OD, ID, MAIN_LEN, BRANCH_LEN = 30.0, 24.0, 80.0, 50.0
main = Cylinder(OD / 2, MAIN_LEN)
main -= Cylinder(ID / 2, MAIN_LEN)
branch = Cylinder(OD / 2, BRANCH_LEN)
branch -= Cylinder(ID / 2, BRANCH_LEN)
branch = branch.rotate(Axis.X, 90)
tee = main + branch
tee.label = "pipe_tee"
result = tee
''',
    "005_cad_create_mini_assembly": '''
from build123d import *
base = Box(80, 40, 5); base.label = "base_plate"; base.color = Color(0.8, 0.2, 0.2)
with Locations((-30, 0, 17.5)):
    pillar_l = Cylinder(5, 30); pillar_l.label = "pillar_left"; pillar_l.color = Color(0.2, 0.4, 0.8)
with Locations((30, 0, 17.5)):
    pillar_r = Cylinder(5, 30); pillar_r.label = "pillar_right"; pillar_r.color = Color(0.2, 0.6, 0.4)
result = Compound(children=[base, pillar_l, pillar_r])
''',
}


def load_prompts(tags: list[str] | None = None) -> list[dict[str, Any]]:
    """Load all prompts from disk, optionally filtered by tags."""
    prompts: list[dict[str, Any]] = []
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            _, front_matter, body = text.split("---", 2)
            meta = yaml.safe_load(front_matter) or {}
            body = body.strip()
        else:
            meta = {}
            body = text.strip()
        prompt = {"path": path, **meta, "prompt": body}
        if tags and not any(tag in prompt.get("tags", []) for tag in tags):
            continue
        prompts.append(prompt)
    return prompts


def run_build123d(script: str, output_dir: Path) -> dict[str, Any]:
    """Execute a build123d script and export STEP + metrics."""
    step_path = output_dir / "generated.step"
    package_path = output_dir / "package.aieng"

    wrapper = f"""
from pathlib import Path
from build123d import export_step
{script}
export_step(result, r"{step_path.resolve().as_posix()}")
"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "run_build123d.py"
            script_path.write_text(wrapper, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                return {"ok": False, "error": proc.stderr or "build123d subprocess failed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "build123d subprocess timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if not step_path.exists():
        return {"ok": False, "error": "STEP export was not produced"}

    # Import STEP into a minimal .aieng package.
    try:
        import_step_package(step_path, package_path, overwrite=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"STEP import failed: {exc}"}

    metrics = compute_step_metrics(step_path)
    enrich_package_with_metrics(package_path, metrics)

    return {"ok": True, "metrics": metrics}


def compute_step_metrics(step_path: Path) -> dict[str, Any]:
    """Use build123d to compute volumes and bounding boxes from a STEP file."""
    script = f"""
import json
from pathlib import Path
from build123d import import_step, Compound
shape = import_step(r"{step_path.resolve().as_posix()}")
if not isinstance(shape, Compound):
    shape = Compound(children=[shape])
children = shape.children if shape.children else [shape]
volumes = {{}}
bboxes = {{}}
for child in children:
    label = getattr(child, "label", None) or "part_001"
    volumes[label] = float(child.volume)
    bbox = child.bounding_box()
    bboxes[label] = {{
        "x": float(bbox.size.X), "y": float(bbox.size.Y), "z": float(bbox.size.Z)
    }}
Path("metrics.json").write_text(json.dumps({{"volumes": volumes, "bounding_boxes": bboxes, "part_count": len(volumes)}}))
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = Path(tmpdir) / "metrics.py"
        script_path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=tmpdir,
            timeout=120,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr or "metrics computation failed"}
        return json.loads((Path(tmpdir) / "metrics.json").read_text(encoding="utf-8"))


def enrich_package_with_metrics(package_path: Path, metrics: dict[str, Any]) -> None:
    """Inject a minimal geometry_report.json into the .aieng package."""
    report = {
        "parts": [
            {"label": label, "volume_mm3": vol, "bounding_box": {"dimensions": metrics.get("bounding_boxes", {}).get(label, {})}}
            for label, vol in metrics.get("volumes", {}).items()
        ]
    }
    topology = {"named_parts": [{"label": label} for label in metrics.get("volumes", {})]}
    with zipfile.ZipFile(package_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("geometry/geometry_report.json", json.dumps(report, indent=2))
        zf.writestr("geometry/topology_map.json", json.dumps(topology, indent=2))


def run_prompt(prompt: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Run a single prompt and capture artifacts."""
    prompt_dir = output_dir / prompt["id"]
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "prompt.md").write_text(prompt["prompt"], encoding="utf-8")

    script = CAD_CREATE_SCRIPTS.get(prompt["id"])
    if script is None:
        status = "skipped"
        details = {"reason": "runner does not yet implement this prompt category"}
        metrics: dict[str, Any] = {}
    else:
        result = run_build123d(script, prompt_dir)
        if result["ok"]:
            status = "passed"
            details = {}
            metrics = result.get("metrics", {})
        else:
            status = "failed"
            details = {"error": result.get("error", "unknown error")}
            metrics = {}

    (prompt_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    return {
        "id": prompt["id"],
        "status": status,
        "details": details,
        "metrics": metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the workbench regression benchmark")
    parser.add_argument("--tags", nargs="+", default=["core"], help="Tags to filter prompts (e.g. core cad_create cae)")
    parser.add_argument("--output", default=None, help="Output directory for this run")
    parser.add_argument("--adapter", default="direct", choices=["direct", "mcp"], help="Execution adapter")
    args = parser.parse_args(argv)

    if args.adapter != "direct":
        print(f"Adapter '{args.adapter}' is not yet implemented; falling back to direct.")

    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir = Path(args.output) if args.output else Path(__file__).resolve().parent / "runs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    tags = None if "all" in args.tags else args.tags
    prompts = load_prompts(tags)
    if not prompts:
        print(f"No prompts matched tags: {args.tags}")
        return 1

    started_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []
    for prompt in prompts:
        print(f"Running {prompt['id']} ...", end=" ")
        result = run_prompt(prompt, output_dir)
        results.append(result)
        print(result["status"])

    finished_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "tags": args.tags,
        "adapter": "direct",
        "prompts": results,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    passed = sum(1 for r in results if r["status"] == "passed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"\nRun complete: {output_dir}")
    print(f"Passed: {passed}, Skipped: {skipped}, Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
