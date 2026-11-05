"""Score one A/B trial from the package it left behind.

Run inside the backend environment, with the checkout's backend on PYTHONPATH
and AIENG_PLATFORM_DATA pointing at the trial's data directory:

    conda run -n aieng311 python score.py --project-name NAME

Prints one JSON object. Reads only; the comparison is re-derived from the
runs' own FRDs by `cae.compare_runs`, not from what the agent reported.
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-name", required=True)
    args = parser.parse_args()

    from app import runtime
    from app.app_factory import create_app

    create_app()
    projects = runtime.invoke_tool("aieng.list_projects", {}).get("projects", [])
    matches = [p for p in projects if p.get("name") == args.project_name]
    score: dict = {"project_found": bool(matches), "project_count": len(projects)}
    if not matches:
        # An agent that named the project differently still did the work.
        matches = [p for p in projects if p.get("aieng_file")]
        score["fallback_project"] = bool(matches)
    if not matches:
        print(json.dumps(score))
        return 0
    project_id = matches[-1]["id"]
    score["project_id"] = project_id

    source = runtime.invoke_tool("cad.get_source", {"project_id": project_id})
    score["named_parts"] = source.get("named_parts")

    comparison = runtime.invoke_tool("cae.compare_runs", {"project_id": project_id})
    score["compare_status"] = comparison.get("status")
    score["runs"] = [(comparison.get(side) or {}).get("run_id") for side in ("baseline", "current")]
    score["geometry_changed"] = comparison.get("geometry_changed")
    score["binding_count_changed"] = comparison.get("binding_count_changed")
    rows = {row.get("metric"): row for row in comparison.get("comparison") or []}
    for name in ("max_von_mises_stress", "max_displacement"):
        row = rows.get(name) or {}
        pct = row.get("percent_change")
        score[name] = {"before": row.get("before"), "after": row.get("after"),
                       "change_pct": round(pct, 2) if isinstance(pct, (int, float)) else None}
    score["compare_warnings"] = comparison.get("warnings")
    if comparison.get("status") != "ok":
        score["compare_message"] = comparison.get("message")

    # The promised task succeeded only if two solves of two different
    # geometries exist and the thicker plate is no worse in either metric.
    stress = score["max_von_mises_stress"]["change_pct"]
    disp = score["max_displacement"]["change_pct"]
    score["task_success"] = bool(
        score["geometry_changed"] is True
        and stress is not None and disp is not None
        and stress < 0 and disp < 0
    )
    print(json.dumps(score, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
