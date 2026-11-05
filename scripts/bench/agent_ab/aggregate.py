"""Tabulate A/B trial summaries: python aggregate.py RUNS_DIR"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def _scaled(value: float | None, scale: float, digits: int) -> float | None:
    """A missing measurement stays missing; it must not enter a median as 0."""
    return None if value is None else round(value / scale, digits)


def _row(s: dict) -> dict:
    """One table row from a trial's summary.json; an unscored trial has no success value."""
    score = s.get("score") or {}
    onboarding = s.get("onboarding_result_chars") or {}
    # Summaries written before trial_status existed: scored iff the score parsed.
    status = s.get("trial_status") or ("scoring_failed" if "error" in score else "scored")
    return {
        "run": f"{s['arm']}-{s['index']}",
        "status": status,
        "success": score.get("task_success") if status == "scored" else None,
        "stress_pct": (score.get("max_von_mises_stress") or {}).get("change_pct"),
        "disp_pct": (score.get("max_displacement") or {}).get("change_pct"),
        "cost_usd": _scaled(s.get("cost_usd"), 1, 3),
        "input_tok_M": _scaled(s.get("input_tokens_total"), 1e6, 2),
        "output_tok_k": _scaled(s.get("output_tokens"), 1e3, 1),
        "turns": s.get("num_turns"),
        "tool_calls": s.get("tool_calls_total"),
        "guide_chars": onboarding.get("aieng_guide", 0),
        "errors": s.get("error_codes") or {},
        "wall_s": s.get("wall_s"),
    }


def main() -> int:
    """Print the per-trial table and per-arm success and medians for RUNS_DIR."""
    rows = [_row(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(sys.argv[1]).glob("*/summary.json"))]
    keys = list(rows[0]) if rows else []
    print("| " + " | ".join(keys) + " |")
    print("|" + "---|" * len(keys))
    for r in rows:
        print("| " + " | ".join(str(r[k]) for k in keys) + " |")
    for arm in sorted({r["run"].rsplit("-", 1)[0] for r in rows}):
        arm_rows = [r for r in rows if r["run"].startswith(arm + "-")]
        scored = [r for r in arm_rows if r["status"] == "scored"]
        med = {}
        for key in ("cost_usd", "input_tok_M", "output_tok_k", "turns", "tool_calls"):
            present = [r[key] for r in arm_rows if r[key] is not None]
            med[key] = statistics.median(present) if present else "n/a"
        ok = sum(1 for r in scored if r["success"])
        invalid = len(arm_rows) - len(scored)
        note = f" ({invalid} unscored, excluded)" if invalid else ""
        print(f"{arm}: success {ok}/{len(scored)}{note}, medians {med}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
