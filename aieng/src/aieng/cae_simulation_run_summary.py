"""CAE simulation run summary generator for .aieng packages.

This module generates LLM-readable summaries of recorded external solver runs
from detected run metadata artifacts. It does NOT execute solvers, generate
meshes, or validate physical correctness. All claims are honest: metadata-only.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .schema_versions import CAE_SIMULATION_RUN_SUMMARY_SCHEMA

SIMULATION_RUN_SUMMARY_PATH = "simulation/simulation_run_summary.json"
SIMULATION_RUN_MARKDOWN_PATH = "simulation/simulation_run_summary.md"
SIMULATION_DIR = "simulation/"


def _read_json_from_zip(zf: zipfile.ZipFile, path: str) -> Any | None:
    """Read and parse JSON from a zip member. Return None on missing or invalid."""
    if path not in zf.namelist():
        return None
    try:
        return json.loads(zf.read(path))
    except (json.JSONDecodeError, KeyError):
        return None


def _read_runs_from_package(zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Read solver run metadata from simulation/runs/*/ and legacy paths."""
    runs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    warnings: list[str] = []

    # Structured runs: simulation/runs/<run_id>/solver_run.json
    for name in zf.namelist():
        if name.startswith("simulation/runs/") and name.endswith("/solver_run.json"):
            parts = name.split("/")
            if len(parts) >= 4:
                run_id = parts[2]
                if run_id in seen_ids:
                    continue
                seen_ids.add(run_id)
                raw = _read_json_from_zip(zf, name)
                if raw is None:
                    warnings.append(f"{name} is malformed; skipped.")
                    continue
                if isinstance(raw, dict):
                    raw["_source_path"] = name
                    runs.append(raw)

    # Legacy fallback: simulation/solver_run.json
    if "simulation/solver_run.json" in zf.namelist() and "simulation/solver_run.json" not in {
        r.get("_source_path") for r in runs
    }:
        raw = _read_json_from_zip(zf, "simulation/solver_run.json")
        if raw is None:
            warnings.append("simulation/solver_run.json is malformed; skipped.")
        elif isinstance(raw, dict):
            raw["_source_path"] = "simulation/solver_run.json"
            raw.setdefault("run_id", "legacy")
            runs.append(raw)

    return runs, warnings


def _normalize_run(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw run dict into a canonical run entry.

    ``cae.run_solver`` writes the run record (``solver_run.json``) with
    ``state`` / ``solved`` / ``converged`` / ``warnings`` / ``errors`` at the
    **top level**. Older/alternate records may nest them under a ``status``
    block. Read the top-level value first and fall back to the nested one, so a
    real completed run is recognised — previously the nested-only read left every
    run ``state="unknown"`` / ``solved=None``, so an executed solve looked
    unverified and ``has_completed_run`` stayed false."""
    status = raw.get("status")
    if not isinstance(status, dict):
        status = {}

    def _pick(key: str) -> Any:
        value = raw.get(key)
        return value if value is not None else status.get(key)

    state = _pick("state") or "unknown"
    solved = _pick("solved") if isinstance(_pick("solved"), bool) else None
    converged = _pick("converged") if isinstance(_pick("converged"), bool) else None
    status_warnings = _pick("warnings") or []
    status_errors = _pick("errors") or []

    input_files = raw.get("input_files") or []
    output_files = raw.get("output_files") or []

    log_file = None
    source_path = raw.get("_source_path", "")
    if source_path:
        parent = "/".join(source_path.split("/")[:-1])
        candidate = f"{parent}/solver_log.txt" if parent else "simulation/solver_log.txt"
        log_file = candidate

    return {
        "run_id": raw.get("run_id") or "unknown",
        "solver": raw.get("solver") or "unknown",
        "software": raw.get("software") or "unknown",
        "software_version": raw.get("software_version"),
        "analysis_type": raw.get("analysis_type") or "unknown",
        "state": state,
        "solved": solved,
        "converged": converged,
        "warnings": status_warnings if isinstance(status_warnings, list) else [],
        "errors": status_errors if isinstance(status_errors, list) else [],
        "input_files": input_files if isinstance(input_files, list) else [],
        "output_files": output_files if isinstance(output_files, list) else [],
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
        "duration_seconds": raw.get("duration_seconds"),
        "log_file": log_file,
    }


def _determine_latest_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the latest run deterministically.

    Prefers finished_at timestamp, then started_at, then lexicographic run_id.
    """
    if not runs:
        return None

    def _sort_key(run: dict[str, Any]) -> tuple[str, str, str]:
        finished = run.get("finished_at") or ""
        started = run.get("started_at") or ""
        rid = run.get("run_id") or ""
        return (finished, started, rid)

    return max(runs, key=_sort_key)


def generate_simulation_run_summary(package_path: str | Path) -> dict[str, Any]:
    """Generate an honest CAE simulation run summary dict.

    Args:
        package_path: Path to the .aieng package.

    Returns:
        JSON-serializable summary dict (schema_version
        :data:`~aieng.schema_versions.CAE_SIMULATION_RUN_SUMMARY_SCHEMA`).
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"package not found: {path}")

    with zipfile.ZipFile(path, "r") as zf:
        raw_runs, read_warnings = _read_runs_from_package(zf)

    runs = [_normalize_run(r) for r in raw_runs]
    run_count = len(runs)
    has_simulation_runs = run_count > 0

    latest = _determine_latest_run(runs)
    latest_run_id = latest["run_id"] if latest else None

    has_completed_run = any(r["state"] == "completed" for r in runs)
    has_converged_run = any(r["converged"] is True for r in runs)
    has_failed_run = any(
        r["state"] in ("failed", "error", "crashed") or bool(r["errors"])
        for r in runs
    )

    all_warnings = list(read_warnings)
    for r in runs:
        for w in r["warnings"]:
            all_warnings.append(f"{r['run_id']}: {w}")

    run_files = [r["_source_path"] for r in raw_runs if "_source_path" in r]
    # Also include any solver_log.txt files found under simulation/runs/
    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            if name.startswith("simulation/runs/") and name.endswith("/solver_log.txt"):
                if name not in run_files:
                    run_files.append(name)

    llm = _build_llm_summary(
        has_simulation_runs=has_simulation_runs,
        run_count=run_count,
        latest_run=latest,
        has_completed_run=has_completed_run,
        has_converged_run=has_converged_run,
        has_failed_run=has_failed_run,
        runs=runs,
    )

    return {
        "schema_version": CAE_SIMULATION_RUN_SUMMARY_SCHEMA,
        "summary_type": "cae_simulation_run",
        "source": {
            "package_path": str(path),
            "run_files": run_files,
        },
        "status": {
            "has_simulation_runs": has_simulation_runs,
            "run_count": run_count,
            "latest_run_id": latest_run_id,
            "has_completed_run": has_completed_run,
            "has_converged_run": has_converged_run,
            "has_failed_run": has_failed_run,
            "warnings": all_warnings,
        },
        "runs": runs,
        "llm_summary": llm,
    }


def _build_llm_summary(
    *,
    has_simulation_runs: bool,
    run_count: int,
    latest_run: dict[str, Any] | None,
    has_completed_run: bool,
    has_converged_run: bool,
    has_failed_run: bool,
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the honest LLM-oriented summary block."""
    if not has_simulation_runs:
        one_line = "No simulation runs recorded in this package."
    elif latest_run:
        solver = latest_run.get("solver", "unknown")
        state = latest_run.get("state", "unknown")
        conv = "converged" if latest_run.get("converged") else "not converged"
        one_line = f"Latest run ({latest_run['run_id']}) using {solver} is {state} and {conv}. Total runs: {run_count}."
    else:
        one_line = f"{run_count} simulation run(s) recorded."

    key_findings: list[str] = []
    if not has_simulation_runs:
        key_findings.append("No solver run metadata found.")
    else:
        key_findings.append(f"{run_count} simulation run(s) recorded.")
        if has_completed_run:
            key_findings.append("At least one run completed.")
        if has_converged_run:
            key_findings.append("At least one run converged.")
        if has_failed_run:
            key_findings.append("At least one run failed or reported errors.")

    risks: list[str] = []
    if has_failed_run:
        risks.append("Failed or errored runs detected; review logs and inputs before trusting results.")
    if has_completed_run and not has_converged_run:
        risks.append("Completed runs did not converge; results may be unreliable.")
    if not has_simulation_runs:
        risks.append("No simulation runs recorded; cannot assess solver output quality.")
    if not risks:
        risks.append("No obvious run-level risks detected from metadata.")

    recommended_next_actions: list[str] = []
    if not has_simulation_runs:
        recommended_next_actions.append("Execute an external solver and record run metadata.")
    elif has_failed_run:
        recommended_next_actions.append("Review failed run logs and inputs; fix setup issues before re-running.")
    elif has_completed_run and not has_converged_run:
        recommended_next_actions.append("Investigate convergence issues; consider mesh refinement or load adjustments.")
    elif has_converged_run:
        recommended_next_actions.append("Proceed to post-processing and evidence review.")

    limitations: list[str] = [
        "This summary is based on recorded run metadata only.",
        "It does not execute solvers, validate physical correctness, or parse numerical fields.",
        "Convergence flags are metadata claims, not guarantees of valid simulation physics.",
        "Solver logs are referenced but not deeply parsed.",
    ]

    return {
        "one_line": one_line,
        "key_findings": key_findings,
        "risks": risks,
        "recommended_next_actions": recommended_next_actions,
        "limitations": limitations,
    }


def generate_simulation_run_markdown(summary: dict[str, Any]) -> str:
    """Generate a human/LLM-readable markdown summary from the simulation run summary dict."""
    status = summary.get("status", {})
    llm = summary.get("llm_summary", {})
    runs = summary.get("runs", [])
    lines: list[str] = []

    lines.append("# CAE Simulation Run Summary")
    lines.append("")
    lines.append(f"**Schema version:** {summary.get('schema_version', 'unknown')}")
    lines.append("")

    lines.append("## Status")
    lines.append("")
    lines.append(f"- **Runs recorded:** {'yes' if status.get('has_simulation_runs') else 'no'}")
    lines.append(f"- **Run count:** {status.get('run_count', 0)}")
    lines.append(f"- **Latest run:** {status.get('latest_run_id') or 'none'}")
    lines.append(f"- **Completed run:** {'yes' if status.get('has_completed_run') else 'no'}")
    lines.append(f"- **Converged run:** {'yes' if status.get('has_converged_run') else 'no'}")
    lines.append(f"- **Failed run:** {'yes' if status.get('has_failed_run') else 'no'}")
    lines.append("")

    if runs:
        lines.append("## Runs")
        lines.append("")
        for run in runs:
            solver = run.get("solver", "unknown")
            software = run.get("software", "unknown")
            analysis = run.get("analysis_type", "unknown")
            state = run.get("state", "unknown")
            conv = "converged" if run.get("converged") else "not converged"
            lines.append(f"- **{run['run_id']}** — {solver} / {software} — {analysis} — {state} — {conv}")
            if run.get("started_at"):
                lines.append(f"  - Started: {run['started_at']}")
            if run.get("finished_at"):
                lines.append(f"  - Finished: {run['finished_at']}")
            if run.get("duration_seconds") is not None:
                lines.append(f"  - Duration: {run['duration_seconds']}s")
            if run.get("log_file"):
                lines.append(f"  - Log: {run['log_file']}")
            if run.get("input_files"):
                lines.append(f"  - Inputs: {', '.join(run['input_files'])}")
            if run.get("output_files"):
                lines.append(f"  - Outputs: {', '.join(run['output_files'])}")
        lines.append("")

    if status.get("warnings"):
        lines.append("## Warnings")
        lines.append("")
        for w in status["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if llm.get("risks"):
        lines.append("## Risks")
        lines.append("")
        for risk in llm["risks"]:
            lines.append(f"- {risk}")
        lines.append("")

    if llm.get("recommended_next_actions"):
        lines.append("## Recommended next actions")
        lines.append("")
        for action in llm["recommended_next_actions"]:
            lines.append(f"- {action}")
        lines.append("")

    if llm.get("limitations"):
        lines.append("## Limitations")
        lines.append("")
        for lim in llm["limitations"]:
            lines.append(f"- {lim}")
        lines.append("")

    return "\n".join(lines)


def write_simulation_run_summary_package(
    package_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write simulation_run_summary.json and simulation_run_summary.md into the package.

    Uses the standard aieng safe-rewrite pattern (temp file + atomic move).

    Args:
        package_path: Path to the .aieng package.
        overwrite: Whether to overwrite existing summary files.

    Returns:
        Path to the updated package.
    """
    path = Path(package_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    try:
        with zipfile.ZipFile(path, mode="r") as package:
            names = set(package.namelist())
            if "manifest.json" not in names:
                raise ValueError("package is missing manifest.json")
            if not overwrite:
                for existing in (SIMULATION_RUN_SUMMARY_PATH, SIMULATION_RUN_MARKDOWN_PATH):
                    if existing in names:
                        raise FileExistsError(
                            f"{existing} already exists; use --overwrite to replace it"
                        )
            manifest = json.loads(package.read("manifest.json"))
            existing_members = _read_existing_members(package)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {path}") from exc

    summary = generate_simulation_run_summary(path)
    markdown = generate_simulation_run_markdown(summary)

    _rewrite_package_with_summary(path, existing_members, manifest, summary, markdown)
    return path


def _read_existing_members(package: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, bytes]]:
    skip = {
        "manifest.json",
        SIMULATION_RUN_SUMMARY_PATH,
        SIMULATION_RUN_MARKDOWN_PATH,
    }
    seen: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info in package.infolist():
        if info.filename in skip or info.filename in seen:
            continue
        seen.add(info.filename)
        data = b"" if info.is_dir() else package.read(info.filename)
        members.append((info, data))
    return members


def _rewrite_package_with_summary(
    path: Path,
    existing_members: list[tuple[zipfile.ZipInfo, bytes]],
    manifest: dict[str, Any],
    summary: dict[str, Any],
    markdown: str,
) -> None:
    resources = manifest.setdefault("resources", {})
    sim_resources = resources.setdefault("simulation", {})
    if not isinstance(sim_resources, dict):
        raise ValueError("manifest resources.simulation must be an object")
    sim_resources["simulation_run_summary"] = SIMULATION_RUN_SUMMARY_PATH
    sim_resources["simulation_run_summary_md"] = SIMULATION_RUN_MARKDOWN_PATH

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    existing_filenames = {info.filename for info, _ in existing_members}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data in existing_members:
                out_package.writestr(info, data)
            if SIMULATION_DIR not in existing_filenames:
                out_package.writestr(SIMULATION_DIR, b"")
            out_package.writestr("manifest.json", manifest_json)
            out_package.writestr(
                SIMULATION_RUN_SUMMARY_PATH,
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
            )
            out_package.writestr(SIMULATION_RUN_MARKDOWN_PATH, markdown.encode("utf-8"))
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
