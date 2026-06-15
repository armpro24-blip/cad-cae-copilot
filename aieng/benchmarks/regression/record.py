"""Record the result of a single prompt execution in a benchmark run.

Usage:
    python record.py --run runs/run_xxx --prompt 001_cad_create_bracket \\
        --status passed --metrics path/to/metrics.json --artifacts package.aieng,generated.step
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"manifest.json not found in {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a single prompt result")
    parser.add_argument("--run", required=True, help="Run directory")
    parser.add_argument("--prompt", required=True, help="Prompt id")
    parser.add_argument("--status", required=True, choices=["passed", "failed", "skipped"])
    parser.add_argument("--metrics", help="Path to metrics JSON file")
    parser.add_argument("--artifacts", help="Comma-separated artifact filenames (relative to prompt dir)")
    parser.add_argument("--error", help="Short error message if status is failed")
    args = parser.parse_args(argv)

    run_dir = Path(args.run)
    prompt_dir = run_dir / args.prompt
    if not prompt_dir.exists():
        print(f"Prompt directory does not exist: {prompt_dir}")
        return 1

    manifest = load_manifest(run_dir)
    metrics: dict[str, Any] = {}
    if args.metrics:
        metrics_path = Path(args.metrics)
        if not metrics_path.exists():
            print(f"Metrics file not found: {metrics_path}")
            return 1
        metrics_text = metrics_path.read_text(encoding="utf-8")
        metrics = json.loads(metrics_text)
        # Copy metrics into prompt dir for self-containment.
        (prompt_dir / "metrics.json").write_text(metrics_text, encoding="utf-8")

    if args.error:
        metrics["error"] = args.error

    artifacts = [a.strip() for a in args.artifacts.split(",") if a.strip()] if args.artifacts else []
    missing = [a for a in artifacts if not (prompt_dir / a).exists()]
    if missing:
        print(f"Warning: artifacts not found in {prompt_dir}: {missing}")

    result = {
        "id": args.prompt,
        "status": args.status,
        "metrics": metrics,
        "artifacts": artifacts,
    }
    (prompt_dir / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    # Update manifest entry.
    found = False
    for entry in manifest.get("prompts", []):
        if entry["id"] == args.prompt:
            entry["status"] = args.status
            entry["metrics"] = metrics
            entry["artifacts"] = artifacts
            found = True
            break
    if not found:
        manifest.setdefault("prompts", []).append(result)

    save_manifest(run_dir, manifest)
    print(f"Recorded {args.prompt}: {args.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
