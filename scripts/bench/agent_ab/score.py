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

# The prompt's geometry: base plate 8 mm before the edit and 12 mm after, with a
# 25 mm rib standing centred on it in both.
PLATE_BEFORE_MM = 8.0
PLATE_AFTER_MM = 12.0
RIB_HEIGHT_MM = 25.0
DIMENSION_TOLERANCE_MM = 0.05
# Nodes this close to the footprint's outer edge belong to the plate only: the
# centred 60 x 5 mm rib is far from the 120 x 80 mm plate's edge.
EDGE_BAND_MM = 0.5
# Two meshes sample the same bolt hole at different nodes: measured, the XY
# centroids of one hole moved by up to 0.5 mm between the 8 and 12 mm meshes.
# The holes are 50 mm apart with a 5 mm radius, so 1 mm still tells holes apart.
RESTRAINT_TOLERANCE_MM = 1.0
# The prompt's load: 500 N in -Z, applied identically before and after the edit.
EXPECTED_FORCE_N = (0.0, 0.0, -500.0)
FORCE_TOLERANCE_N = 5.0


_MATERIAL_CARDS = ("*ELASTIC", "*DENSITY", "*EXPANSION", "*CONDUCTIVITY", "*PLASTIC")


def _deck_facts(package: zipfile.ZipFile, run_id: str) -> dict | None:
    """What the run actually solved, read from its own deck.

    plate: z extent of the nodes on the footprint's outer edge (plate only);
    rib: total z extent minus the plate; force: resultant of the *CLOAD
    entries per DOF 1-3, an NSET target counting once per member node;
    material: every data line of the material property cards; restraints: per
    *BOUNDARY entry, the XY centroid of the restrained nodes with its DOF range
    and value — independent of NSET names and of the plate thickness, since the
    bolt holes are vertical.
    """
    try:
        deck = package.read(f"simulation/runs/{run_id}/solver_input.inp").decode("utf-8", "replace")
    except KeyError:
        return None
    nodes: dict[int, tuple[float, float, float]] = {}
    nsets: dict[str, list[int]] = {}
    cloads: list[list[str]] = []
    boundaries: list[list[str]] = []
    material: list[str] = []
    keyword, nset, generate = "", None, False
    for raw in deck.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            parts = [p.strip().upper() for p in line.split(",")]
            keyword = parts[0]
            nset = next((p.split("=", 1)[1] for p in parts[1:] if p.startswith("NSET=")), None)
            generate = "GENERATE" in parts[1:]
            if keyword == "*NSET" and nset:
                nsets.setdefault(nset, [])
            continue
        fields = [f.strip() for f in line.split(",") if f.strip()]
        try:
            if keyword == "*NODE" and len(fields) >= 4:
                nodes[int(fields[0])] = (float(fields[1]), float(fields[2]), float(fields[3]))
            elif keyword == "*NSET" and nset:
                if generate:
                    first, last, step = (int(v) for v in (fields + ["1"])[:3])
                    nsets[nset].extend(range(first, last + 1, step))
                else:
                    nsets[nset].extend(int(v) for v in fields)
            elif keyword == "*CLOAD" and len(fields) >= 3:
                cloads.append(fields)
            elif keyword == "*BOUNDARY" and len(fields) >= 2:
                boundaries.append(fields)
            elif keyword in _MATERIAL_CARDS:
                material.append(keyword + ":" + ",".join(fields))
        except ValueError:
            continue

    def members(target: str) -> list[int]:
        return [int(target)] if target.isdigit() else nsets.get(target.upper(), [])

    force = [0.0, 0.0, 0.0]
    for target, dof, value, *_ in cloads:
        if dof in ("1", "2", "3"):
            force[int(dof) - 1] += float(value) * len(members(target))

    restraints = []
    for target, first, *rest in boundaries:
        points = [nodes[n] for n in members(target) if n in nodes]
        if not points:
            restraints.append(("unresolved", target))
            continue
        last = rest[0] if rest else first
        value = rest[1] if len(rest) > 1 else "0"
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        restraints.append((round(cx, 2), round(cy, 2), first, last, value))

    plate = rib = None
    if nodes:
        xs, ys, zs = zip(*nodes.values())
        x_lo, x_hi, y_lo, y_hi = min(xs), max(xs), min(ys), max(ys)
        edge_z = [
            z for x, y, z in nodes.values()
            if min(x - x_lo, x_hi - x, y - y_lo, y_hi - y) <= EDGE_BAND_MM
        ]
        plate = max(edge_z) - min(edge_z)
        rib = max(zs) - min(zs) - plate
    return {
        "plate": plate,
        "rib": rib,
        "force": [round(f, 3) for f in force],
        "material": material,
        "restraints": sorted(restraints, key=str),
    }


def _setup_problems(before: dict, after: dict) -> list[str]:
    """Reasons the two runs did not solve the requested, identical setup."""
    problems = []
    for label, facts in (("before", before), ("after", after)):
        if any(abs(f - e) > FORCE_TOLERANCE_N for f, e in zip(facts["force"], EXPECTED_FORCE_N)):
            problems.append(f"{label}: applied force {facts['force']} N, expected {list(EXPECTED_FORCE_N)}")
        if not facts["material"] or not facts["restraints"]:
            problems.append(f"{label}: deck records no material or no restraint")
        if any(r[0] == "unresolved" for r in facts["restraints"]):
            problems.append(f"{label}: a restraint target names no nodes")
    if before["material"] != after["material"]:
        problems.append("material changed between the runs")
    unmatched = _unmatched_restraints(before["restraints"], after["restraints"])
    if unmatched:
        problems.append(f"restraints changed: {unmatched}")
    return problems


def _unmatched_restraints(before: list, after: list) -> list:
    """Restraints with no counterpart: same DOFs and value, centroid within tolerance.

    Matched one to one, so four holes restrained before must be four holes
    restrained after — a dropped or duplicated hole stays unmatched.
    """
    remaining = list(after)
    unmatched = []
    for entry in before:
        candidates = [
            other for other in remaining
            if entry[0] != "unresolved" and other[0] != "unresolved"
            and entry[2:] == other[2:]
            and abs(entry[0] - other[0]) <= RESTRAINT_TOLERANCE_MM
            and abs(entry[1] - other[1]) <= RESTRAINT_TOLERANCE_MM
        ]
        if candidates:
            remaining.remove(min(candidates, key=lambda o: abs(o[0] - entry[0]) + abs(o[1] - entry[1])))
        else:
            unmatched.append(("before", entry))
    return unmatched + [("after", other) for other in remaining]


def _solved_runs(package: zipfile.ZipFile) -> list[str]:
    runs = []
    for name in sorted(package.namelist()):
        if name.startswith("simulation/runs/") and name.endswith("/solver_run.json"):
            record = json.loads(package.read(name))
            if record.get("state") == "completed" or record.get("status") == "completed":
                runs.append(name.split("/")[2])
    return runs


def _matches(value: float | None, expected: float) -> bool:
    return value is not None and abs(value - expected) <= DIMENSION_TOLERANCE_MM


def _edit_pair(facts: dict[str, dict | None]) -> tuple[str, str] | None:
    """(8 mm, 12 mm) runs, each with the 25 mm rib, as read from their decks.

    Either may come first: an agent can solve 12 mm and then re-solve 8 mm on
    the same mesh size. Of several pairs, the two closest in run order are the
    ones solved back to back, so most likely under the same mesh settings;
    ties go to the latest.
    """
    def has(run: str, plate: float) -> bool:
        f = facts.get(run) or {}
        return _matches(f.get("plate"), plate) and _matches(f.get("rib"), RIB_HEIGHT_MM)

    order = {run: i for i, run in enumerate(sorted(facts))}
    pairs = [
        (thin, thick) for thin in facts for thick in facts
        if has(thin, PLATE_BEFORE_MM) and has(thick, PLATE_AFTER_MM)
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
    if package_path is None or not package_path.exists():
        # The agent produced no package: a failed task, not an unscorable trial.
        score.update(package_found=False, task_success=False)
        print(json.dumps(score, default=str))
        return 0
    with zipfile.ZipFile(package_path) as package:
        facts = {run: _deck_facts(package, run) for run in _solved_runs(package)}
    score["run_geometry_mm"] = {
        run: None if f is None or f["plate"] is None
        else {"plate": round(f["plate"], 3), "rib": round(f["rib"], 3)}
        for run, f in facts.items()
    }
    pair = _edit_pair(facts)
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
    # A lower stress after the edit means nothing if the load or restraint
    # changed with it: check what each deck actually applied.
    score["setup_problems"] = _setup_problems(facts[pair[0]], facts[pair[1]])
    score["applied_force_n"] = {run: facts[run]["force"] for run in pair}
    if comparison.get("status") != "ok":
        score["compare_message"] = comparison.get("message")

    # The promised task succeeded only in the requested project, with a solve
    # before and after the 8 -> 12 mm edit under the same requested load,
    # material and restraints, and the thicker plate no worse in either metric.
    # A fallback project is scored for diagnosis only.
    stress = score["max_von_mises_stress"]["change_pct"]
    disp = score["max_displacement"]["change_pct"]
    score["task_success"] = bool(
        score["project_found"]
        and score["geometry_changed"] is True
        and score["binding_count_changed"] is False
        and not score["setup_problems"]
        and stress is not None and disp is not None
        and stress < 0 and disp < 0
    )
    print(json.dumps(score, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
