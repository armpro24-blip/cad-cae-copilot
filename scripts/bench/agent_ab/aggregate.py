"""Tabulate A/B trial summaries: python aggregate.py RUNS_DIR"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def _row(s: dict) -> dict:
    score = s.get("score") or {}
    onboarding = s.get("onboarding_result_chars") or {}
    return {
        "run": f"{s['arm']}-{s['index']}",
        "success": score.get("task_success"),
        "stress_pct": (score.get("max_von_mises_stress") or {}).get("change_pct"),
        "disp_pct": (score.get("max_displacement") or {}).get("change_pct"),
        "cost_usd": round(s.get("cost_usd") or 0, 3),
        "input_tok_M": round((s.get("input_tokens_total") or 0) / 1e6, 2),
        "output_tok_k": round((s.get("output_tokens") or 0) / 1e3, 1),
        "turns": s.get("num_turns"),
        "tool_calls": s.get("tool_calls_total"),
        "guide_chars": onboarding.get("aieng_guide", 0),
        "errors": s.get("error_codes") or {},
        "wall_s": s.get("wall_s"),
    }


def main() -> int:
    rows = [_row(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(sys.argv[1]).glob("*/summary.json"))]
    keys = list(rows[0]) if rows else []
    print("| " + " | ".join(keys) + " |")
    print("|" + "---|" * len(keys))
    for r in rows:
        print("| " + " | ".join(str(r[k]) for k in keys) + " |")
    for arm in sorted({r["run"].rsplit("-", 1)[0] for r in rows}):
        arm_rows = [r for r in rows if r["run"].startswith(arm + "-")]
        med = {k: statistics.median(r[k] for r in arm_rows)
               for k in ("cost_usd", "input_tok_M", "output_tok_k", "turns", "tool_calls")}
        ok = sum(1 for r in arm_rows if r["success"])
        print(f"{arm}: success {ok}/{len(arm_rows)}, medians {med}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
