"""Compare two regression runs and emit a Markdown diff report.

Usage:
    python compare.py --baseline runs/run_20260610T000000Z --current runs/run_20260615T083000Z
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"manifest.json not found in {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def format_pct(value: float | str | None) -> str:
    """Format a percentage value for the diff report."""
    if value is None:
        return "0.0%"
    if isinstance(value, str):
        return value
    return f"{value:+.1f}%"


def metric_delta(baseline: dict[str, Any], current: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Compute a simple numeric delta for a scalar metric."""
    b = baseline.get(key)
    c = current.get(key)
    if b is None or c is None:
        return None
    try:
        b_val = float(b)
        c_val = float(c)
        delta = c_val - b_val
        if b_val == 0:
            delta_pct = None if c_val == 0 else "∞"
        else:
            delta_pct = delta / b_val * 100
        return {"baseline": b_val, "current": c_val, "delta": delta, "delta_pct": delta_pct}
    except (TypeError, ValueError):
        return None


def build_diff(baseline_dir: Path, current_dir: Path) -> str:
    baseline = load_manifest(baseline_dir)
    current = load_manifest(current_dir)

    lines = [
        "# Regression Diff Report",
        "",
        f"- Baseline: `{baseline.get('run_id')}`",
        f"- Current: `{current.get('run_id')}`",
        f"- Baseline started: {baseline.get('started_at', 'unknown')}",
        f"- Current started: {current.get('started_at', 'unknown')}",
        "",
        "## Summary",
        "",
    ]

    baseline_prompts = {p["id"]: p for p in baseline.get("prompts", [])}
    current_prompts = {p["id"]: p for p in current.get("prompts", [])}
    all_ids = sorted(set(baseline_prompts) | set(current_prompts))

    status_table = []
    for pid in all_ids:
        b_status = baseline_prompts.get(pid, {}).get("status", "missing")
        c_status = current_prompts.get(pid, {}).get("status", "missing")
        marker = ""
        if b_status != c_status:
            marker = " ⚠️"
        status_table.append(f"| {pid} | {b_status} | {c_status} |{marker}")

    lines.append("| Prompt | Baseline | Current |")
    lines.append("|---|---|---|")
    lines.extend(status_table)
    lines.append("")

    for pid in all_ids:
        b = baseline_prompts.get(pid, {})
        c = current_prompts.get(pid, {})
        lines.append(f"## {pid}")
        lines.append("")
        lines.append(f"- Baseline status: {b.get('status', 'missing')}")
        lines.append(f"- Current status: {c.get('status', 'missing')}")

        b_metrics = b.get("metrics", {})
        c_metrics = c.get("metrics", {})
        if b_metrics or c_metrics:
            lines.append("")
            lines.append("### Metrics")
            lines.append("")
            lines.append("| Metric | Baseline | Current | Delta |")
            lines.append("|---|---|---|---|")

            # Volume per named part
            b_volumes = b_metrics.get("volumes", {})
            c_volumes = c_metrics.get("volumes", {})
            for part in sorted(set(b_volumes) | set(c_volumes)):
                d = metric_delta(b_volumes, c_volumes, part)
                if d:
                    lines.append(
                        f"| volume_{part} | {d['baseline']:.2f} | {d['current']:.2f} | {d['delta']:+.2f} ({format_pct(d['delta_pct'])}) |"
                    )
                else:
                    lines.append(f"| volume_{part} | {b_volumes.get(part, '-')} | {c_volumes.get(part, '-')} | - |")

            # Bounding boxes per named part
            b_bboxes = b_metrics.get("bounding_boxes", {})
            c_bboxes = c_metrics.get("bounding_boxes", {})
            for part in sorted(set(b_bboxes) | set(c_bboxes)):
                for axis in ("x", "y", "z"):
                    d = metric_delta(b_bboxes.get(part, {}), c_bboxes.get(part, {}), axis)
                    if d:
                        lines.append(
                            f"| bbox_{part}_{axis} | {d['baseline']:.2f} | {d['current']:.2f} | {d['delta']:+.2f} ({format_pct(d['delta_pct'])}) |"
                        )

            # Part count
            d = metric_delta(b_metrics, c_metrics, "part_count")
            if d:
                lines.append(f"| part_count | {int(d['baseline'])} | {int(d['current'])} | {int(d['delta']):+d} |")

        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two regression runs")
    parser.add_argument("--baseline", required=True, help="Baseline run directory")
    parser.add_argument("--current", required=True, help="Current run directory")
    parser.add_argument("--output", default=None, help="Output path for diff.md")
    args = parser.parse_args(argv)

    baseline_dir = Path(args.baseline)
    current_dir = Path(args.current)

    report = build_diff(baseline_dir, current_dir)

    output_path = Path(args.output) if args.output else current_dir / "diff_against_baseline.md"
    output_path.write_text(report, encoding="utf-8")
    print(f"Diff report written to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
