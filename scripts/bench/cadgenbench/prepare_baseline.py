"""Freeze the approved Codex-plan pilot inputs and honest not-run case records.

This command does not authenticate, call a model, execute CAD or upload data.
Run with the aieng311 Python interpreter and -I -B.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import yaml


SEED = 202611
DATASET_REVISION = "f76f965585817c621d6ea0d150d745adf670e66e"
MODEL_REQUESTED = "gpt-6.1-sol"
SAMPLE = (
    ("108", "generation"), ("124", "generation"), ("127", "generation"),
    ("134", "generation"), ("147", "generation"),
    ("204", "editing"), ("206", "editing"), ("247", "editing"),
    ("248", "editing"), ("250", "editing"),
)


def input_records(data_root: Path, task_id: str, task_type: str) -> list[dict[str, Any]]:
    """Verify task metadata and hash public fixture files without executing them."""
    fixture = data_root / task_id
    metadata = yaml.safe_load((fixture / "description.yaml").read_text(encoding="utf-8"))
    if metadata.get("task_type", "generation") != task_type:
        raise ValueError(f"Unexpected task_type for frozen fixture {task_id}")
    for name in metadata["input_files"]:
        path = (fixture / name).resolve()
        if not path.is_relative_to(fixture.resolve()) or not path.is_file():
            raise ValueError(f"Missing or unsafe declared input for fixture {task_id}")
    records = []
    for path in sorted(fixture.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(data_root):
            raise ValueError(f"Unexpected external input for fixture {task_id}")
        content = path.read_bytes()
        records.append({
            "path": path.relative_to(data_root).as_posix(), "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return records


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blocker", required=True, help="Observed shared startup blocker; not a per-task failure")
    args = parser.parse_args()
    if not sys.flags.isolated:
        parser.error("Run with Python -I")
    if args.output.exists():
        parser.error("Output exists; refusing to overwrite frozen or partial case evidence")
    data_root = (args.workspace / ".bench/data").resolve()
    cases = []
    for task_id, task_type in SAMPLE:
        cases.append({
            "task_id": task_id, "task_type": task_type,
            "inputs": input_records(data_root, task_id, task_type),
            "completion": "not_attempted", "status": "blocked_before_inference",
            "shared_blocker": args.blocker, "model_resolved": None,
            "output_step": None, "local_validity": "not_checked",
            "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
            "elapsed_seconds": None, "iterations_completed": 0,
            "actual_dollars": None, "quota_consumed": None,
        })
    manifest = {
        "format_version": 1, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "baseline": "Codex coding plan, no aieng MCP; not original CADGenBench reference",
        "seed": SEED, "sample_frozen_before_inference": True,
        "dataset_revision": DATASET_REVISION,
        "model_requested": MODEL_REQUESTED, "model_availability_verified": False,
        "model_calls_observed": 0, "extra_api_billing_authorized": False,
        "original_reference_run": False, "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "seed": SEED, "cases": len(cases),
                      "generation": 5, "editing": 5, "model_calls_observed": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
