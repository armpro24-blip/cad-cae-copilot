"""Initialize a regression benchmark run directory for an AI agent.

Usage:
    python init_run.py --tags core --output runs/run_$(date -u +%Y%m%dT%H%M%SZ)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompts(tags: list[str] | None = None) -> list[dict[str, Any]]:
    """Load all prompts, optionally filtered by tags."""
    prompts: list[dict[str, Any]] = []
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            _, front_matter, body = text.split("---", 2)
            meta = yaml.safe_load(front_matter) or {}
        else:
            meta = {}
            body = text
        prompt = {"path": path, **meta, "prompt": body.strip()}
        if tags and not any(tag in prompt.get("tags", []) for tag in tags):
            continue
        prompts.append(prompt)
    return prompts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize a regression benchmark run")
    parser.add_argument("--tags", nargs="+", default=["core"], help="Tags to filter prompts")
    parser.add_argument("--output", required=True, help="Output run directory")
    args = parser.parse_args(argv)

    tags = None if "all" in args.tags else args.tags
    prompts = load_prompts(tags)
    if not prompts:
        print(f"No prompts matched tags: {args.tags}")
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = output_dir.name
    started_at = datetime.now(timezone.utc).isoformat()

    results: list[dict[str, Any]] = []
    for prompt in prompts:
        prompt_dir = output_dir / prompt["id"]
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "prompt.md").write_text(prompt["prompt"], encoding="utf-8")
        plan = {
            "id": prompt["id"],
            "tags": prompt.get("tags", []),
            "seed_package": prompt.get("seed_package"),
            "status": "pending",
        }
        (prompt_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        results.append({"id": prompt["id"], "status": "pending", "metrics": {}, "artifacts": []})

    manifest = {
        "run_id": run_id,
        "started_at": started_at,
        "tags": args.tags,
        "adapter": "mcp-agent",
        "prompts": results,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print(f"Initialized run: {output_dir}")
    print(f"Prompts: {len(prompts)}")
    for prompt in prompts:
        print(f"  - {prompt['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
