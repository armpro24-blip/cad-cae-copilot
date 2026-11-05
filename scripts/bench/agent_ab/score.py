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
import re
import sys
import zipfile

# The prompt's edit: base plate 8 -> 12 mm. The rib stands on the plate, so the
# model's total height grows by exactly the thickness change.
EXPECTED_HEIGHT_GAIN_MM = 4.0
HEIGHT_TOLERANCE_MM = 0.05


def _deck_height(package: zipfile.ZipFile, run_id: str) -> float | None:
    """Z extent of the run's mesh nodes, read from its own solver deck."""
    try:
        deck = package.read(f"simulation/runs/{run_id}/solver_input.inp").decode("utf-8", "replace")
    except KeyError:
        return None
    match = re.search(r"^\*NODE[^\n]*\n(.*?)(?=^\*)", deck, re.M | re.S | re.I)
    if not match:
        return None
    zs = []
    for line in match.group(1).splitlines():
        fields = line.split(",")
        if len(fields) >= 4:
            try:
                zs.append(float(fields[3]))
            except ValueError:
                continue
    return max(zs) - min(zs) if zs else None


def _solved_runs(package: zipfile.ZipFile) -> list[str]:
    runs = []
    for name in sorted(package.namelist()):
        if name.startswith("simulation/runs/") and name.endswith("/solver_run.json"):
            record = json.loads(package.read(name))
            if record.get("state") == "completed" or record.get("status") == "completed":
                runs.append(name.split("/")[2])
    return runs


def _edit_pair(heights: dict[str, float | None]) -> tuple[str, str] | None:
    """(thin, thick) runs whose heights differ by the requested edit.

    Either may come first: an agent can solve 12 mm and then re-solve 8 mm on
    the same mesh size. Of several pairs, the two closest in run order are the
    ones solved back to back, so most likely under the same mesh settings;
    ties go to the latest.
    """
    order = {run: i for i, run in enumerate(sorted(heights))}
    pairs = [
        (thin, thick) for thin in heights for thick in heights
        if heights[thin] is not None and heights[thick] is not None
        and abs(heights[thick] - heights[thin] - EXPECTED_HEIGHT_GAIN_MM) <= HEIGHT_TOLERANCE_MM
    ]
    if not pairs:
        return None
    return min(pairs, key=lambda p: (abs(order[p[0]] - order[p[1]]), -max(order[p[0]], order[p[1]])))


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

    # Compare the runs that show the requested edit, not whichever runs are the
    # oldest and newest: an agent that re-solved at another mesh size leaves
    # two 8 mm runs at the ends of the list.
    from app.project_io import get_project, resolve_project_path
    from app.config import Settings

    settings = Settings.from_env()
    package_path = resolve_project_path(settings, project_id, get_project(settings, project_id).get("aieng_file"))
    with zipfile.ZipFile(package_path) as package:
        heights = {run: _deck_height(package, run) for run in _solved_runs(package)}
    score["run_heights_mm"] = {run: None if h is None else round(h, 3) for run, h in heights.items()}
    pair = _edit_pair(heights)
    score["edit_pair_found"] = pair is not None
    if pair is None:
        score["task_success"] = False
        print(json.dumps(score, default=str))
        return 0
    comparison = runtime.invoke_tool(
        "cae.compare_runs", {"project_id": project_id, "baseline_run": pair[0], "current_run": pair[1]},
    )
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

    # The promised task succeeded only in the requested project, with a solve
    # before and after the 8 -> 12 mm edit, and the thicker plate no worse in
    # either metric. A fallback project is scored for diagnosis only.
    stress = score["max_von_mises_stress"]["change_pct"]
    disp = score["max_displacement"]["change_pct"]
    score["task_success"] = bool(
        score["project_found"]
        and score["geometry_changed"] is True
        and stress is not None and disp is not None
        and stress < 0 and disp < 0
    )
    print(json.dumps(score, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
