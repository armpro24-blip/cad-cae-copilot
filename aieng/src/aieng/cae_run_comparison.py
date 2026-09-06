"""Compare two solver runs inside one `.aieng` package.

The promised task ends with "交付能追溯到几何版本和求解记录的前后对比" — a
before/after comparison traceable to the geometry version and the solver record.
Every ingredient for that already existed and none of it was assembled:

- `results/computed_metrics.json` is a **single fixed path**, so the second
  extraction replaces the first. The baseline's numbers leave the package the
  moment the re-solve is extracted. The FRD that produced them is still there
  under `simulation/runs/<run_id>/outputs/`, so nothing is lost — but recovering
  it was the reader's problem, not the workbench's.
- nothing in the repo read two runs together. The only before/after comparison
  that existed lived in the assertions of
  `aieng-ui/backend/tests/test_acceptance_edit_resolve_compare.py`, i.e. the
  deliverable existed as a test, not as something a user could be handed.

So this module derives each run's metrics from **that run's own FRD**, never from
the shared `computed_metrics.json`, and reports the geometry revision each deck
was built for beside the numbers. That last part is the point: a comparison
between two solves of the *same* geometry is the exact failure P0-2 found (0.0%
change on a beam whose thickness had doubled, `status: completed`), so it is
stated rather than left for the reader to notice.

Read-only: it opens the package, stages FRDs to a temporary directory, and
writes nothing.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .simulation.frd_result_extractor import extract_computed_metrics

__all__ = [
    "compare_runs",
    "list_runs",
]

RUNS_PREFIX = "simulation/runs/"
_FRD_SUFFIX = ".frd"


def _read_json(zf: zipfile.ZipFile, name: str) -> Any | None:
    try:
        return json.loads(zf.read(name).decode("utf-8"))
    except Exception:  # noqa: BLE001 - a missing/corrupt member is not fatal here
        return None


def _run_id_of(member: str) -> str | None:
    """`simulation/runs/run_002/outputs/result.frd` -> `run_002`."""
    if not member.startswith(RUNS_PREFIX):
        return None
    rest = member[len(RUNS_PREFIX):]
    run_id = rest.split("/", 1)[0]
    return run_id or None


def _sort_key(run_id: str) -> tuple[int, str]:
    """Order `run_2` before `run_10` — trailing digits sort numerically.

    Run ids are generated as `run_001`, so plain lexical order is usually right;
    a hand-written `run_2` would otherwise sort after `run_10` and pick the wrong
    baseline.
    """
    match = re.search(r"(\d+)$", run_id)
    return (int(match.group(1)) if match else -1, run_id)


def list_runs(package_path: str | Path) -> list[dict[str, Any]]:
    """Every solver run the package records, oldest id first.

    A run is anything with a directory under `simulation/runs/`; it is listed
    even when it has no result, because "this run exists and produced nothing"
    is a fact the caller needs in order to refuse honestly.
    """
    package = Path(package_path)
    runs: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(package, "r") as zf:
        names = zf.namelist()
        for member in names:
            run_id = _run_id_of(member)
            if not run_id:
                continue
            record = runs.setdefault(
                run_id,
                {
                    "run_id": run_id,
                    "frd_member": None,
                    "geometry_revision": None,
                    "solver_executed": None,
                    "analysis_type": None,
                    "solver": None,
                    "finished_at": None,
                    "solver_run_member": None,
                    "deck_provenance_member": None,
                },
            )
            if member.endswith(_FRD_SUFFIX) and f"/{run_id}/outputs/" in member:
                record["frd_member"] = member
            elif member == f"{RUNS_PREFIX}{run_id}/solver_run.json":
                record["solver_run_member"] = member
                data = _read_json(zf, member)
                if isinstance(data, dict):
                    state = str(data.get("state") or data.get("status") or "").lower()
                    # Same definition as `cae_result_summary._solver_run_completed`:
                    # a record that exists is not a run that succeeded.
                    record["solver_executed"] = state == "completed" and data.get("solved") is True
                    record["analysis_type"] = data.get("analysis_type")
                    record["solver"] = data.get("solver")
                    record["finished_at"] = data.get("finished_at")
            elif member == f"{RUNS_PREFIX}{run_id}/deck_provenance.json":
                record["deck_provenance_member"] = member
                data = _read_json(zf, member)
                if isinstance(data, dict):
                    revision = data.get("geometry_revision")
                    # `None` = no provenance recorded, which is NOT revision 0.
                    # A deck written before provenance existed cannot say what
                    # geometry it was built for, and saying "0" would invent it.
                    record["geometry_revision"] = (
                        int(revision) if isinstance(revision, int) else None
                    )
    return [runs[key] for key in sorted(runs, key=_sort_key)]


def _stage_frd(zf: zipfile.ZipFile, member: str, into: Path) -> Path:
    target = into / member.replace("/", "_")
    target.write_bytes(zf.read(member))
    return target


def _metrics_by_load_case(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = metrics.get("load_cases")
    if not isinstance(cases, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or "")
        values = case.get("metrics")
        if case_id and isinstance(values, dict):
            out[case_id] = values
    return out


def _numeric(entry: Any) -> tuple[float | None, str | None]:
    """(value, unit) from a metric entry, or (None, unit) when it is not a number.

    A metric whose `value` is null is recorded but not extracted — the same
    distinction `cae_result_summary.read_parsed_metrics` makes. Booleans are not
    numbers here even though Python says otherwise.
    """
    if isinstance(entry, dict):
        raw = entry.get("value")
        unit = entry.get("unit")
        unit_str = str(unit) if unit is not None else None
    else:
        raw = entry
        unit_str = None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, unit_str
    value = float(raw)
    return (value if math.isfinite(value) else None), unit_str


def _compare_one(
    metric: str,
    before_entry: Any,
    after_entry: Any,
    *,
    load_case_id: str,
) -> dict[str, Any]:
    before, before_unit = _numeric(before_entry)
    after, after_unit = _numeric(after_entry)
    row: dict[str, Any] = {
        "metric": metric,
        "load_case_id": load_case_id,
        "before": before,
        "after": after,
        "unit": before_unit or after_unit,
        "delta": None,
        "percent_change": None,
        "status": "unknown",
        "reason": None,
    }
    if before is None or after is None:
        missing = [
            side
            for side, value in (("baseline", before), ("current", after))
            if value is None
        ]
        row["reason"] = f"no extracted value in {' and '.join(missing)}"
        return row
    if before_unit and after_unit and before_unit != after_unit:
        # Subtracting mm from m produces a number, and that number is the lie.
        row["status"] = "incomparable"
        row["reason"] = f"unit changed between runs: {before_unit} -> {after_unit}"
        return row

    row["status"] = "compared"
    row["delta"] = after - before
    if before == 0.0:
        row["reason"] = "baseline is zero; percent change is undefined"
    else:
        row["percent_change"] = (after - before) / abs(before) * 100.0
    return row


def _pick(
    runs: list[dict[str, Any]],
    requested: str | None,
    *,
    role: str,
    fallback: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve one side of the comparison; returns (run, refusal)."""
    if not requested:
        return fallback, None
    match = next((run for run in runs if run["run_id"] == requested), None)
    if match is None:
        return None, {
            "status": "error",
            "code": "run_not_found",
            "message": (
                f"{role} run {requested!r} is not in this package. "
                f"Runs present: {', '.join(run['run_id'] for run in runs) or 'none'}."
            ),
        }
    if not match["frd_member"]:
        # Answering with a different run is the `asked-a-got-b` defect this
        # package's provenance work has fixed twice already.
        return None, {
            "status": "error",
            "code": "run_has_no_result",
            "message": (
                f"{role} run {requested!r} has no result file "
                f"({RUNS_PREFIX}{requested}/outputs/*.frd). It cannot be compared."
            ),
        }
    return match, None


def compare_runs(
    package_path: str | Path,
    *,
    baseline_run: str | None = None,
    current_run: str | None = None,
    load_case_id: str | None = None,
) -> dict[str, Any]:
    """Before/after comparison of two solver runs in one package.

    Args:
        package_path: the `.aieng` package.
        baseline_run: the "before" run id. Defaults to the oldest run with a
            result.
        current_run: the "after" run id. Defaults to the newest run with a
            result.
        load_case_id: compare only this load case. Defaults to every load case
            the two runs share, plus an honest `unknown` row for any case only
            one of them has.

    Returns a dict with `baseline`, `current`, `comparison`, `geometry_changed`
    and `warnings`, or a refusal with a `code`.
    """
    package = Path(package_path)
    if not package.exists():
        return {
            "status": "error",
            "code": "package_not_found",
            "message": f"No package at {package}",
        }

    runs = list_runs(package)
    solved = [run for run in runs if run["frd_member"]]
    if not baseline_run and not current_run and len(solved) < 2:
        return {
            "status": "error",
            "code": "not_enough_runs",
            "message": (
                f"A before/after comparison needs two runs with results; this "
                f"package has {len(solved)}. Re-solve after the edit under a new "
                "run_id (see workflow E)."
            ),
            "runs": [run["run_id"] for run in runs],
        }

    baseline, refusal = _pick(
        runs, baseline_run, role="baseline", fallback=solved[0] if solved else None
    )
    if refusal:
        return refusal
    current, refusal = _pick(
        runs, current_run, role="current", fallback=solved[-1] if solved else None
    )
    if refusal:
        return refusal
    if baseline is None or current is None:
        return {
            "status": "error",
            "code": "not_enough_runs",
            "message": "This package has no solver run with a result to compare.",
            "runs": [run["run_id"] for run in runs],
        }
    if baseline["run_id"] == current["run_id"]:
        return {
            "status": "error",
            "code": "same_run",
            "message": (
                f"baseline and current are both {baseline['run_id']!r}; comparing a "
                "run with itself reports no change whatever the geometry did."
            ),
        }

    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aieng_compare_") as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(package, "r") as zf:
            sides: dict[str, dict[str, Any]] = {}
            for role, run in (("baseline", baseline), ("current", current)):
                staged = _stage_frd(zf, run["frd_member"], tmpdir)
                metrics = extract_computed_metrics(
                    staged,
                    source_files=[run["frd_member"]],
                    run_id=run["run_id"],
                )
                for warning in metrics.get("warnings") or []:
                    warnings.append(f"{run['run_id']}: {warning}")
                sides[role] = _metrics_by_load_case(metrics)

    for role, run in (("baseline", baseline), ("current", current)):
        if run["solver_executed"] is False:
            warnings.append(
                f"{run['run_id']} did not complete ({run['solver_run_member']}); "
                "its numbers are from an FRD the solver did not finish writing."
            )
        elif run["solver_executed"] is None:
            warnings.append(
                f"{run['run_id']} records no solver_run.json, so whether the solver "
                "completed is unknown."
            )

    before_cases, after_cases = sides["baseline"], sides["current"]
    if load_case_id:
        case_ids = [load_case_id]
    else:
        case_ids = sorted(set(before_cases) | set(after_cases))

    comparison: list[dict[str, Any]] = []
    for case_id in case_ids:
        before_values = before_cases.get(case_id) or {}
        after_values = after_cases.get(case_id) or {}
        if not before_values and not after_values:
            comparison.append(
                {
                    "metric": None,
                    "load_case_id": case_id,
                    "status": "unknown",
                    "reason": "neither run has this load case",
                }
            )
            continue
        for metric in sorted(set(before_values) | set(after_values)):
            comparison.append(
                _compare_one(
                    metric,
                    before_values.get(metric),
                    after_values.get(metric),
                    load_case_id=case_id,
                )
            )

    before_rev = baseline["geometry_revision"]
    after_rev = current["geometry_revision"]
    if before_rev is None or after_rev is None:
        geometry_changed: bool | None = None
        warnings.append(
            "At least one deck records no geometry revision, so whether the "
            "geometry changed between these runs cannot be established from the "
            "package."
        )
    else:
        geometry_changed = before_rev != after_rev
        if not geometry_changed:
            warnings.append(
                f"Both decks were built for geometry revision {before_rev}: this "
                "compares two solves of the SAME geometry, so a near-zero change "
                "is expected and says nothing about an edit."
            )

    return {
        "status": "ok",
        "package": str(package),
        "baseline": _side(baseline, explicit=bool(baseline_run)),
        "current": _side(current, explicit=bool(current_run)),
        "geometry_changed": geometry_changed,
        "comparison": comparison,
        "warnings": warnings,
    }


def _side(run: dict[str, Any], *, explicit: bool) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "selected": "explicit" if explicit else "default",
        "geometry_revision": run["geometry_revision"],
        "solver_executed": run["solver_executed"],
        "analysis_type": run["analysis_type"],
        "solver": run["solver"],
        "finished_at": run["finished_at"],
        "result_file": run["frd_member"],
        "solver_run_record": run["solver_run_member"],
        "deck_provenance": run["deck_provenance_member"],
    }
