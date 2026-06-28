"""Engineering Review Support Packet (v0.31).

Aggregates already-produced evidence inside a project's ``.aieng`` package and
the AIENG workbench state into a reviewable Markdown + JSON packet.

The packet is a *review support* artifact only. It does not:

  - certify design safety;
  - validate the design;
  - advance engineering claims;
  - run any CAD, mesh, or solver tool;
  - invent missing evidence — sections with no data are reported as
    ``missing`` with an explicit note, never fabricated.

Two entry points are exposed:

  - :func:`preview_review_support_packet` — read-only.  Builds the packet,
    returns the Markdown plus a structured section list.  Never writes to
    the ``.aieng`` package.
  - :func:`export_review_support_packet` — writes only
    ``reports/review_support/{packet_id}.md`` and ``.json`` into the
    package; never edits CAD, runs a solver, or advances claims.
"""

from __future__ import annotations

import json
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException

from .cae_calibration import assess_calibration
from .cae_credibility import assess_cae_credibility
from .computed_metrics import get_computed_metrics
from .config import Settings
from .copilot_loop import _resolve_package, list_loops
from .design_targets import get_design_targets
from .engineering_templates import (
    DRAFT_CAD_SCRIPT_PATH,
    DRAFT_FEA_SETUP_PATH,
    DRAFT_MANIFEST_PATH,
    DRAFT_TARGET_SUGGESTIONS_PATH,
    read_engineering_setup_draft_summary,
)
from .project_health import run_project_health_check
from .project_io import (
    AUDIT_EVENTS_PATH,
    REVALIDATION_STATUS_PATH,
    _parse_audit_events_jsonl,
    get_project,
    resolve_project_path,
    write_artifact_to_package,
)
from .target_comparison import compare_package_targets

PACKET_SCHEMA_VERSION = "0.1"
PACKET_DIR = "reports/review_support"
CLAIM_ADVANCEMENT: Literal["none"] = "none"

CLAIM_BOUNDARY = (
    "This packet supports engineering review. It does not certify design safety, "
    "validate the design, or advance engineering claims automatically. All results "
    "require qualified engineering review."
)

_SAFETY_BOUNDARY_MD = (
    "This packet supports engineering review.\n\n"
    "- It does not certify design safety.\n"
    "- It does not advance engineering claims automatically.\n"
    "- All results require qualified engineering review.\n"
)

SectionStatus = Literal["included", "missing", "partial", "error"]

_MAX_TABLE_ROWS = 50
_MAX_AUDIT_EVENTS = 25
_MAX_INLINE_TEXT = 4000


# ── helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_packet_id() -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"packet_{ts}_{uuid.uuid4().hex[:6]}"


def _md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _section(
    id_: str,
    title: str,
    status: SectionStatus,
    body_md: str,
    *,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    artifact_paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "title": title,
        "status": status,
        "body_md": body_md.strip() + "\n",
        "data": data or {},
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "artifact_paths": list(artifact_paths or []),
    }


def _truncate(text: str, cap: int = _MAX_INLINE_TEXT) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap] + "\n\n…(truncated)", True


def _read_package_member(pkg: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            if member not in zf.namelist():
                return None
            return zf.read(member)
    except Exception:
        return None


def _read_package_json(pkg: Path, member: str) -> Any | None:
    raw = _read_package_member(pkg, member)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _list_package_members(pkg: Path) -> list[str]:
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            return list(zf.namelist())
    except Exception:
        return []


# ── section builders ──────────────────────────────────────────────────────────


def _section_header(
    *, packet_id: str, project_id: str, project_name: str | None, package_path: Path | None
) -> dict[str, Any]:
    rows = [
        ("Packet id", packet_id),
        ("Project id", project_id),
        ("Project name", project_name or "(unnamed)"),
        ("Generated at", _now_iso()),
        ("Packet schema", PACKET_SCHEMA_VERSION),
        ("Source package", package_path.name if package_path else "(none)"),
        ("Claim advancement", CLAIM_ADVANCEMENT),
    ]
    body = "| Field | Value |\n|---|---|\n"
    for k, v in rows:
        body += f"| {_md_escape(k)} | {_md_escape(v)} |\n"
    body += f"\n> {CLAIM_BOUNDARY}\n"
    return _section(
        "header",
        "Header",
        "included",
        body,
        data={
            "packet_id": packet_id,
            "project_id": project_id,
            "project_name": project_name,
            "generated_at": _now_iso(),
            "source_package": str(package_path) if package_path else None,
            "claim_advancement": CLAIM_ADVANCEMENT,
        },
        artifact_paths=[str(package_path)] if package_path else [],
    )


def _section_safety_boundary() -> dict[str, Any]:
    return _section("safety_boundary", "Safety boundary", "included", _SAFETY_BOUNDARY_MD)


def _section_project_health(settings: Settings, project_id: str) -> dict[str, Any]:
    try:
        health = run_project_health_check(settings, project_id)
    except Exception as exc:
        return _section(
            "project_health",
            "Project Health",
            "error",
            f"Project Health Check raised an error: `{type(exc).__name__}`.",
            errors=[str(exc)],
        )
    checks = health.get("checks") or []
    counts: dict[str, int] = {"passed": 0, "warning": 0, "failed": 0, "unknown": 0}
    for c in checks:
        s = c.get("status")
        if s in counts:
            counts[s] += 1
        else:
            counts["unknown"] += 1
    readiness = health.get("readiness") or "unknown"
    body = f"**Readiness:** `{_md_escape(readiness)}`\n\n"
    body += "| Status | Count |\n|---|---|\n"
    for status_label in ("passed", "warning", "failed", "unknown"):
        body += f"| {status_label} | {counts[status_label]} |\n"
    notable = [c for c in checks if c.get("status") in {"failed", "warning"}]
    if notable:
        body += "\n**Open items**\n\n| Check | Status | Summary |\n|---|---|---|\n"
        for c in notable[:_MAX_TABLE_ROWS]:
            check_label = c.get("id") or c.get("check_id") or c.get("label")
            body += (
                f"| {_md_escape(check_label)} | {_md_escape(c.get('status'))} "
                f"| {_md_escape(c.get('summary'))} |\n"
            )
    actions = health.get("recommended_actions") or []
    if actions:
        body += "\n**Recommended actions**\n\n"
        for a in actions[:_MAX_TABLE_ROWS]:
            body += f"- {_md_escape(a.get('label') or a.get('action_id') or a)}\n"
    limitations = health.get("limitations") or []
    if limitations:
        body += "\n**Limitations**\n\n"
        for lim in limitations:
            body += f"- {_md_escape(lim)}\n"

    status: SectionStatus = "included" if checks else "partial"
    return _section(
        "project_health",
        "Project Health summary",
        status,
        body,
        data={"readiness": readiness, "counts": counts, "open_items": notable[:_MAX_TABLE_ROWS]},
    )


def _section_design_targets(settings: Settings, project_id: str) -> dict[str, Any]:
    resp = get_design_targets(settings, project_id)
    targets = resp.get("targets") or []
    if not targets:
        return _section(
            "design_targets",
            "Design Targets",
            "missing",
            "No design targets were found in the project package.",
            warnings=resp.get("warnings") or [],
        )
    body = "| Target id | Label | Metric | Operator | Value | Unit | Load case | Priority |\n"
    body += "|---|---|---|---|---|---|---|---|\n"
    for t in targets[:_MAX_TABLE_ROWS]:
        body += (
            f"| {_md_escape(t.get('target_id'))} "
            f"| {_md_escape(t.get('label') or t.get('target_type'))} "
            f"| {_md_escape(t.get('metric') or t.get('target_type'))} "
            f"| {_md_escape(t.get('operator') or t.get('comparator'))} "
            f"| {_md_escape(t.get('value') or t.get('threshold'))} "
            f"| {_md_escape(t.get('unit'))} "
            f"| {_md_escape(t.get('load_case_id'))} "
            f"| {_md_escape(t.get('priority'))} |\n"
        )
    rationale_rows = [(t.get("target_id"), t.get("rationale")) for t in targets if t.get("rationale")]
    if rationale_rows:
        body += "\n**Rationale**\n\n"
        for tid, rat in rationale_rows[:_MAX_TABLE_ROWS]:
            body += f"- `{_md_escape(tid)}`: {_md_escape(rat)}\n"
    truncated = len(targets) > _MAX_TABLE_ROWS
    if truncated:
        body += f"\n_…({len(targets) - _MAX_TABLE_ROWS} more target(s) not shown)_\n"
    return _section(
        "design_targets",
        "Design Targets",
        "included" if not truncated else "partial",
        body,
        data={"target_count": len(targets)},
        artifact_paths=[resp.get("artifact_path")] if resp.get("artifact_path") else [],
    )


def _format_metric_value(value: Any) -> str:
    if isinstance(value, dict):
        val = value.get("value")
        unit = value.get("unit")
        if val is None:
            return "n/a"
        return f"{val}{' ' + unit if unit else ''}"
    if value is None:
        return "n/a"
    return str(value)


def _section_engineering_setup_draft(pkg: Path | None) -> dict[str, Any]:
    """v0.34 — surface a saved template draft as an explicitly-drafted input.

    Reports `missing` when no template has been saved; that is the expected
    state for projects that did not use the template authoring path.
    """
    if pkg is None:
        return _section(
            "engineering_setup_draft",
            "Engineering Setup Draft",
            "missing",
            "No engineering setup draft was found (project has no package).",
        )
    summary = read_engineering_setup_draft_summary(pkg)
    if summary is None:
        return _section(
            "engineering_setup_draft",
            "Engineering Setup Draft",
            "missing",
            (
                "No engineering setup draft was found in the package. Template "
                "authoring is optional — a project can proceed entirely through "
                "manually authored design targets, CAD, and solver evidence."
            ),
        )
    body = "| Field | Value |\n|---|---|\n"
    body += f"| Template id | `{_md_escape(summary.get('template_id'))}` |\n"
    body += f"| Generated at | {_md_escape(summary.get('generated_at'))} |\n"
    body += f"| Parameter count | {summary.get('parameter_count')} |\n"
    body += f"| Manifest path | `{_md_escape(summary.get('manifest_path'))}` |\n"
    body += "\n**Draft is informational only.** It does not certify the design, is not a substitute "
    body += "for design targets, CAD, or solver evidence, and has not advanced any engineering claim.\n"
    body += "\n**Saved artifacts**\n\n"
    for ap in summary.get("artifact_paths") or []:
        body += f"- `{_md_escape(ap)}`\n"
    artifact_paths = [DRAFT_MANIFEST_PATH, DRAFT_CAD_SCRIPT_PATH, DRAFT_FEA_SETUP_PATH, DRAFT_TARGET_SUGGESTIONS_PATH]
    return _section(
        "engineering_setup_draft",
        "Engineering Setup Draft",
        "included",
        body,
        data={
            "template_id": summary.get("template_id"),
            "parameter_count": summary.get("parameter_count"),
        },
        artifact_paths=artifact_paths,
    )


def _section_computed_metrics(settings: Settings, project_id: str) -> dict[str, Any]:
    resp = get_computed_metrics(settings, project_id)
    doc = resp.get("document")
    metrics_count = resp.get("metrics_count") or 0
    if not doc or metrics_count == 0:
        return _section(
            "computed_metrics",
            "Computed Metrics",
            "missing",
            "No computed metrics were found in the project package.",
            warnings=resp.get("warnings") or [],
        )
    global_metrics = doc.get("global_metrics") or {}
    load_cases = doc.get("load_cases") or []
    body = ""
    if global_metrics:
        body += "**Global metrics**\n\n| Metric | Value |\n|---|---|\n"
        for name, value in list(global_metrics.items())[:_MAX_TABLE_ROWS]:
            body += f"| {_md_escape(name)} | {_md_escape(_format_metric_value(value))} |\n"
        body += "\n"
    if load_cases:
        body += "**Load-case metrics**\n\n| Load case | Metric | Value |\n|---|---|---|\n"
        rows = 0
        for lc in load_cases:
            lc_id = lc.get("load_case_id") or lc.get("id") or "(unnamed)"
            for name, value in (lc.get("metrics") or {}).items():
                if rows >= _MAX_TABLE_ROWS:
                    break
                body += (
                    f"| {_md_escape(lc_id)} | {_md_escape(name)} "
                    f"| {_md_escape(_format_metric_value(value))} |\n"
                )
                rows += 1
            if rows >= _MAX_TABLE_ROWS:
                break
        body += "\n"
    source = (doc.get("metrics_source") or {})
    if source:
        body += (
            f"\n_Source:_ tool=`{_md_escape(source.get('tool'))}` "
            f"format=`{_md_escape(source.get('format'))}` "
            f"imported_by=`{_md_escape(source.get('imported_by'))}`\n"
        )
    body += f"\n_Artifact:_ `{resp.get('artifact_path') or 'results/computed_metrics.json'}`\n"
    return _section(
        "computed_metrics",
        "Computed Metrics",
        "included",
        body,
        data={"metrics_count": metrics_count, "load_case_count": resp.get("load_case_count") or 0},
        artifact_paths=[resp.get("artifact_path")] if resp.get("artifact_path") else [],
    )


def _section_target_comparison(settings: Settings, project_id: str) -> dict[str, Any]:
    try:
        resp = compare_package_targets(settings, project_id)
    except HTTPException as exc:
        return _section(
            "target_comparison",
            "Target Comparison",
            "error",
            f"Target comparison could not be evaluated: {exc.detail}.",
            errors=[str(exc.detail)],
        )
    except Exception as exc:
        return _section(
            "target_comparison",
            "Target Comparison",
            "error",
            f"Target comparison raised `{type(exc).__name__}`.",
            errors=[str(exc)],
        )
    # `compare_package_targets` exposes both a nested `comparison` block and the
    # flattened `items` + `summary` at the top level — prefer the top-level keys
    # since they're the canonical UI shape.
    items = (
        resp.get("items")
        or (resp.get("comparison") or {}).get("items")
        or (resp.get("comparisons") or {}).get("items")
        or []
    )
    summary = (
        resp.get("summary")
        or (resp.get("comparison") or {}).get("summary")
        or (resp.get("comparisons") or {}).get("summary")
        or {}
    )
    warnings = list(resp.get("warnings") or [])
    if not items:
        return _section(
            "target_comparison",
            "Target Comparison",
            "missing",
            "No target comparison items available "
            "(targets and/or computed metrics not yet present).",
            warnings=warnings,
        )
    body = "**Status summary**\n\n| Status | Count |\n|---|---|\n"
    for key in ("pass", "fail", "unknown", "missing_metric", "ambiguous", "not_evaluated", "invalid"):
        if key in summary:
            body += f"| {key} | {summary.get(key)} |\n"
    body += "\n**Per-target comparison**\n\n"
    body += "| Target id | Label | Metric | Status | Reason |\n|---|---|---|---|---|\n"
    for item in items[:_MAX_TABLE_ROWS]:
        body += (
            f"| {_md_escape(item.get('target_id'))} "
            f"| {_md_escape(item.get('target_label'))} "
            f"| {_md_escape(item.get('metric'))} "
            f"| {_md_escape(item.get('status'))} "
            f"| {_md_escape(item.get('reason_code') or item.get('notes'))} |\n"
        )
    if warnings:
        body += "\n**Warnings**\n\n"
        for w in warnings:
            body += f"- {_md_escape(w)}\n"
    body += f"\n> {_md_escape(resp.get('claim_boundary') or CLAIM_BOUNDARY)}\n"
    return _section(
        "target_comparison",
        "Target Comparison",
        "included",
        body,
        data={"summary": summary, "item_count": len(items)},
        warnings=warnings,
    )


def _section_geometry_inspection(pkg: Path | None) -> dict[str, Any]:
    if pkg is None:
        return _section(
            "geometry_inspection",
            "Geometry Inspection Evidence",
            "missing",
            "No geometry inspection evidence found (project has no package).",
        )
    parsed = _read_package_json(pkg, "simulation/cae_imports/parsed_features.json")
    graph = _read_package_json(pkg, "graph/feature_graph.json")
    if parsed is None and graph is None:
        return _section(
            "geometry_inspection",
            "Geometry Inspection Evidence",
            "missing",
            "No geometry inspection evidence found.",
        )
    features = []
    if isinstance(parsed, dict):
        features = parsed.get("features") or []
    elif isinstance(graph, dict):
        features = graph.get("features") or []
    editable = [f for f in features if isinstance(f, dict) and (f.get("parameters") or f.get("editable_parameters"))]
    bridge = None
    generated_at = None
    if isinstance(parsed, dict):
        bridge = parsed.get("bridge_provider") or parsed.get("bridge") or parsed.get("provider")
        generated_at = parsed.get("generated_at") or parsed.get("inspected_at")
    body = "| Field | Value |\n|---|---|\n"
    body += f"| Parsed features artifact | {'present' if parsed is not None else 'missing'} |\n"
    body += f"| Feature graph artifact | {'present' if graph is not None else 'missing'} |\n"
    body += f"| Feature count | {len(features)} |\n"
    body += f"| Editable-parameter features | {len(editable)} |\n"
    if bridge:
        body += f"| Bridge provider | {_md_escape(bridge)} |\n"
    if generated_at:
        body += f"| Generated at | {_md_escape(generated_at)} |\n"
    artifacts = []
    if parsed is not None:
        artifacts.append("simulation/cae_imports/parsed_features.json")
    if graph is not None:
        artifacts.append("graph/feature_graph.json")
    status: SectionStatus = "included" if parsed is not None and graph is not None else "partial"
    return _section(
        "geometry_inspection",
        "Geometry Inspection Evidence",
        status,
        body,
        data={"feature_count": len(features), "editable_count": len(editable)},
        artifact_paths=artifacts,
    )


def _read_audit_events(pkg: Path | None) -> list[dict[str, Any]]:
    if pkg is None:
        return []
    raw = _read_package_member(pkg, AUDIT_EVENTS_PATH)
    if raw is None:
        return []
    try:
        text = raw.decode("utf-8")
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    try:
        events = list(_parse_audit_events_jsonl(text))
    except Exception:
        events = []
    # Permissive fallback: include any JSONL lines that decode to dicts but were
    # silently dropped by the strict core parser (missing schema fields, older
    # events, etc.). Review needs to see what's actually on disk, even if non-
    # canonical — better than reporting "missing" when the file has content.
    if not events and text.strip():
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                evt = json.loads(stripped)
            except Exception:
                continue
            if isinstance(evt, dict):
                events.append(evt)
    return events


def _section_cad_approval_records(pkg: Path | None) -> dict[str, Any]:
    events = _read_audit_events(pkg)
    cad_events = [e for e in events if str(e.get("tool") or e.get("tool_name") or "").startswith("cad.edit_parameter")]
    if not cad_events:
        return _section(
            "cad_approval",
            "CAD Parameter Edit / Approval Records",
            "missing",
            "No approval-gated CAD parameter edit records found.",
        )
    body = "| Timestamp | Status | Proposal | Parameter | Old | New |\n|---|---|---|---|---|---|\n"
    for e in cad_events[-_MAX_TABLE_ROWS:]:
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else e
        body += (
            f"| {_md_escape(e.get('timestamp') or e.get('ts'))} "
            f"| {_md_escape(e.get('status') or payload.get('status'))} "
            f"| {_md_escape(payload.get('proposal_id'))} "
            f"| {_md_escape(payload.get('parameter') or payload.get('param_name'))} "
            f"| {_md_escape(payload.get('old_value'))} "
            f"| {_md_escape(payload.get('new_value'))} |\n"
        )
    return _section(
        "cad_approval",
        "CAD Parameter Edit / Approval Records",
        "included",
        body,
        data={"event_count": len(cad_events)},
    )


def _section_structural_solver(pkg: Path | None) -> dict[str, Any]:
    if pkg is None:
        return _section(
            "structural_solver",
            "Structural CAE Execution / Result Extraction",
            "missing",
            "No structural solver run evidence found (project has no package).",
        )
    members = _list_package_members(pkg)
    solver_run_members = sorted(
        m for m in members if m.startswith("simulation/runs/") and m.endswith("/solver_run.json")
    )
    frd_members = sorted(m for m in members if m.endswith(".frd"))
    dat_members = sorted(m for m in members if m.endswith(".dat"))
    deck_members = sorted(m for m in members if m.endswith(".inp"))
    if not solver_run_members and not frd_members and not dat_members:
        return _section(
            "structural_solver",
            "Structural CAE Execution / Result Extraction",
            "missing",
            "No structural solver run evidence found.",
        )
    body = "| Field | Value |\n|---|---|\n"
    body += f"| Solver run records | {len(solver_run_members)} |\n"
    body += f"| Solver decks (.inp) | {len(deck_members)} |\n"
    body += f"| Result files (.frd) | {len(frd_members)} |\n"
    body += f"| Result files (.dat) | {len(dat_members)} |\n"
    body += f"| Computed metrics from solver | {'present' if 'results/computed_metrics.json' in members else 'missing'} |\n"
    if solver_run_members:
        body += "\n**Solver runs**\n\n"
        body += "| Path | Status | Solved | Return code | Started | Finished |\n|---|---|---|---|---|---|\n"
        for member in solver_run_members[-_MAX_TABLE_ROWS:]:
            data = _read_package_json(pkg, member) or {}
            body += (
                f"| `{_md_escape(member)}` "
                f"| {_md_escape(data.get('state'))} "
                f"| {_md_escape(data.get('solved'))} "
                f"| {_md_escape(data.get('return_code'))} "
                f"| {_md_escape(data.get('started_at'))} "
                f"| {_md_escape(data.get('finished_at'))} |\n"
            )
    artifacts = solver_run_members + frd_members + dat_members
    return _section(
        "structural_solver",
        "Structural CAE Execution / Result Extraction",
        "included",
        body,
        data={
            "solver_run_count": len(solver_run_members),
            "frd_count": len(frd_members),
            "dat_count": len(dat_members),
        },
        artifact_paths=artifacts[:_MAX_TABLE_ROWS],
    )


def _section_cae_credibility(settings: Settings, project_id: str, pkg: Path | None) -> dict[str, Any]:
    evidence = _cae_credibility_evidence(settings, project_id, pkg)
    tier = assess_cae_credibility(evidence)
    status: SectionStatus = "missing" if tier["tier"] == "no_result_artifact" else "partial"
    if tier["status"] == "supported":
        status = "included"

    body = "| Field | Value |\n|---|---|\n"
    body += f"| Credibility tier | `{_md_escape(tier['tier'])}` |\n"
    body += f"| Tier index | {_md_escape(tier['tier_index'])} |\n"
    body += f"| Status | `{_md_escape(tier['status'])}` |\n"
    body += f"| Certified | {_md_escape(tier['certified'])} |\n"
    body += f"| Missing next evidence | {_md_escape(', '.join(tier.get('missing_next') or []) or '(none)')} |\n"
    body += "\n**Limitations**\n\n"
    for item in tier.get("limitations") or []:
        body += f"- {_md_escape(item)}\n"
    body += (
        "\nThis section is read-only. It distinguishes artifact presence, solver "
        "completion, parsed metrics, plausibility checks, design-target comparison, "
        "benchmark calibration, and human review support; it does not run a solver "
        "or advance claims.\n"
    )
    return _section(
        "cae_credibility",
        "CAE Credibility",
        status,
        body,
        data={"credibility": tier, "evidence_inputs": evidence},
        warnings=list(tier.get("limitations") or []),
        artifact_paths=list(evidence.get("result_artifacts") or [])[:_MAX_TABLE_ROWS],
    )


def _cae_credibility_evidence(settings: Settings, project_id: str, pkg: Path | None) -> dict[str, Any]:
    if pkg is None:
        return {}
    members = _list_package_members(pkg)
    result_artifacts = sorted(
        member
        for member in members
        if member.endswith((".frd", ".dat"))
        or member in {"results/computed_metrics.json", "results/result_summary.json"}
    )
    solver_run_members = sorted(
        member for member in members if member.startswith("simulation/runs/") and member.endswith("/solver_run.json")
    )
    solver_run = _read_package_json(pkg, solver_run_members[-1]) if solver_run_members else None
    metrics = _read_package_json(pkg, "results/computed_metrics.json")
    plausibility = _read_package_json(pkg, "analysis/plausibility_checks.json")
    mesh_convergence = (
        _read_package_json(pkg, "analysis/mesh_convergence_report.json")
        or _read_package_json(pkg, "analysis/mesh_quality_report.json")
    )
    benchmark = (
        _read_package_json(pkg, "analysis/analytical_fea_scorecard.json")
        or _read_package_json(pkg, "benchmarks/analytical_fea_scorecard.json")
    )
    if benchmark is None and isinstance(metrics, dict):
        computed = (
            metrics.get("computed_metrics")
            or metrics.get("metrics")
            or metrics.get("global_metrics")
            or {}
        )
        calibration = assess_calibration(computed)
        if calibration.get("status") in {"passed", "warning", "failed"}:
            benchmark = {"status": calibration["status"], "case_id": calibration.get("case_id")}
    target_status = None
    try:
        comparison = compare_package_targets(settings, project_id)
        summary = ((comparison.get("comparisons") or {}).get("summary") or {})
        if summary.get("fail"):
            target_status = "failed"
        elif summary.get("pass"):
            target_status = "passed"
        elif summary.get("unknown") or summary.get("not_evaluated"):
            target_status = "warning"
    except Exception:
        target_status = None

    evidence: dict[str, Any] = {
        "result_artifacts": result_artifacts,
        "solver_run": solver_run,
        "parsed_metrics": metrics,
    }
    if plausibility is not None:
        evidence["plausibility_checks"] = plausibility
    if target_status is not None:
        evidence["design_target_comparison"] = {"status": target_status}
    if mesh_convergence is not None:
        evidence["mesh_convergence"] = mesh_convergence
    if benchmark is not None:
        evidence["benchmark_comparison"] = benchmark
    return evidence


def _section_copilot_loop(settings: Settings, project_id: str) -> dict[str, Any]:
    try:
        loops_resp = list_loops(settings, project_id)
    except Exception as exc:
        return _section(
            "copilot_loop",
            "Copilot Loop Summary",
            "error",
            f"Could not list Copilot Loops: `{type(exc).__name__}`.",
            errors=[str(exc)],
        )
    loops = loops_resp.get("loops") or []
    if not loops:
        return _section(
            "copilot_loop",
            "Copilot Loop Summary",
            "missing",
            "No Copilot Loops have been started for this project.",
        )
    latest = loops[0]
    steps = latest.get("steps") or []
    body = "**Latest loop**\n\n| Field | Value |\n|---|---|\n"
    body += f"| Loop id | `{_md_escape(latest.get('loop_id'))}` |\n"
    body += f"| Status | {_md_escape(latest.get('status'))} |\n"
    body += f"| Created at | {_md_escape(latest.get('created_at'))} |\n"
    body += f"| Updated at | {_md_escape(latest.get('updated_at'))} |\n"
    if latest.get("selected_proposal_id"):
        body += f"| Selected proposal | {_md_escape(latest.get('selected_proposal_id'))} |\n"
    if steps:
        body += "\n**Step statuses**\n\n| Step | Status |\n|---|---|\n"
        for step in steps[:_MAX_TABLE_ROWS]:
            body += f"| {_md_escape(step.get('id') or step.get('label'))} | {_md_escape(step.get('status'))} |\n"
    metric_summary = latest.get("metric_summary")
    target_summary = latest.get("target_summary")
    if metric_summary or target_summary:
        body += "\n**Embedded summaries**\n\n"
        if metric_summary:
            body += f"- Metric summary keys: `{', '.join(sorted((metric_summary or {}).keys()))}`\n"
        if target_summary:
            body += f"- Target summary keys: `{', '.join(sorted((target_summary or {}).keys()))}`\n"
    if len(loops) > 1:
        body += f"\n_Total loops on file: {len(loops)} (showing latest)._\n"
    return _section(
        "copilot_loop",
        "Copilot Loop Summary",
        "included",
        body,
        data={"loop_count": len(loops), "latest_loop_id": latest.get("loop_id")},
    )


def _section_stale_evidence(pkg: Path | None) -> dict[str, Any]:
    if pkg is None:
        return _section(
            "stale_evidence",
            "Stale Evidence",
            "missing",
            "No revalidation status available (project has no package).",
        )
    rev = _read_package_json(pkg, REVALIDATION_STATUS_PATH)
    if not isinstance(rev, dict):
        return _section(
            "stale_evidence",
            "Stale Evidence",
            "missing",
            "No revalidation status recorded. Stale evidence is unknown.",
        )
    requires_reval = bool(rev.get("requires_revalidation"))
    stale_paths = rev.get("stale_artifacts") or rev.get("stale_paths") or []
    if not isinstance(stale_paths, list):
        stale_paths = []
    body = "| Field | Value |\n|---|---|\n"
    body += f"| Requires revalidation | {requires_reval} |\n"
    body += f"| Current geometry revision | {_md_escape(rev.get('current_geometry_revision'))} |\n"
    body += f"| Last validated geometry revision | {_md_escape(rev.get('last_validated_geometry_revision'))} |\n"
    body += f"| Stale artifact count | {len(stale_paths)} |\n"
    if stale_paths:
        body += "\n**Stale artifacts**\n\n"
        for p in stale_paths[:_MAX_TABLE_ROWS]:
            body += f"- `{_md_escape(p)}`\n"
    status: SectionStatus = "included" if (requires_reval or stale_paths) else "partial"
    return _section(
        "stale_evidence",
        "Stale Evidence",
        status,
        body,
        data={"requires_revalidation": requires_reval, "stale_count": len(stale_paths)},
        artifact_paths=[REVALIDATION_STATUS_PATH],
    )


def _fallback_evidence_lifecycle_summary(pkg: Path, project_id: str) -> dict[str, Any]:
    members = set(_list_package_members(pkg))
    rev = _read_package_json(pkg, REVALIDATION_STATUS_PATH)
    stale_paths: list[str] = []
    if isinstance(rev, dict):
        stale_raw = rev.get("stale_artifacts") or rev.get("stale_paths") or []
        if isinstance(stale_raw, list):
            stale_paths = [str(path) for path in stale_raw if path]

    unsupported: list[dict[str, Any]] = []
    for member in sorted(members):
        if not member.lower().endswith(".frd"):
            continue
        raw = _read_package_member(pkg, member)
        if raw is None:
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            unsupported.append({
                "path": member,
                "unsupported_reason": "binary or non-UTF-8 FRD evidence",
            })

    expected = [
        "geometry/topology_map.json",
        "graph/feature_graph.json",
        "simulation/setup.yaml",
        "results/computed_metrics.json",
        "results/evidence_index.json",
        "results/result_summary.json",
    ]
    missing = [
        {
            "path": path,
            "reason": "expected_evidence_not_found",
        }
        for path in expected
        if path not in members
    ]
    stale = [{"path": path} for path in stale_paths]
    current_count = max(0, len(members) - len(stale_paths) - len(unsupported))
    summary = {
        "current": current_count,
        "stale": len(stale),
        "unsupported": len(unsupported),
        "claim_supporting": 0,
        "missing": len(missing),
    }
    status = "warning" if any(summary[key] for key in ("stale", "unsupported", "missing")) else "ok"
    return {
        "schema_version": "0.1",
        "project_id": project_id,
        "claim_advancement": CLAIM_ADVANCEMENT,
        "status": status,
        "summary": summary,
        "governance": {
            "automatic_claim_advancement": False,
            "claim_advancement_requires_explicit_review": True,
            "fallback_summary": True,
        },
        "stale_evidence": stale,
        "unsupported_evidence": unsupported,
        "claim_supporting_evidence": [],
        "missing_evidence": missing,
    }


def _section_evidence_lifecycle(
    settings: Settings,
    project_id: str,
    pkg: Path | None,
) -> dict[str, Any]:
    if pkg is None:
        return _section(
            "evidence_lifecycle",
            "Evidence Lifecycle",
            "missing",
            "No evidence lifecycle summary available (project has no package).",
        )
    try:
        from .routers.evidence import _build_evidence_lifecycle_summary

        lifecycle = _build_evidence_lifecycle_summary(pkg, project_id=project_id)
    except Exception as exc:
        lifecycle = _fallback_evidence_lifecycle_summary(pkg, project_id)
        lifecycle.setdefault("warnings", []).append(
            f"Used compatibility fallback because full lifecycle summary failed: {type(exc).__name__}."
        )

    summary = lifecycle.get("summary") or {}
    body = "| Lifecycle state | Count |\n|---|---|\n"
    for key in ("current", "stale", "unsupported", "claim_supporting", "missing"):
        body += f"| {key} | {int(summary.get(key) or 0)} |\n"
    body += (
        "\nThis rollup is read-only and does not advance claims. "
        "Missing evidence remains unknown/not evaluated.\n"
    )
    for warning in lifecycle.get("warnings") or []:
        body += f"\n_Warning: {_md_escape(warning)}_\n"

    def add_items(title: str, items: list[dict[str, Any]]) -> None:
        nonlocal body
        if not items:
            return
        body += f"\n**{title}**\n\n"
        for item in items[:_MAX_TABLE_ROWS]:
            path = item.get("path")
            reason = item.get("unsupported_reason") or item.get("reason") or ""
            suffix = f" - {_md_escape(reason)}" if reason else ""
            body += f"- `{_md_escape(path)}`{suffix}\n"

    add_items("Stale evidence", lifecycle.get("stale_evidence") or [])
    add_items("Unsupported evidence", lifecycle.get("unsupported_evidence") or [])
    add_items("Claim-supporting evidence", lifecycle.get("claim_supporting_evidence") or [])
    add_items("Missing evidence", lifecycle.get("missing_evidence") or [])

    status: SectionStatus = "included" if lifecycle.get("status") == "ok" else "partial"
    return _section(
        "evidence_lifecycle",
        "Evidence Lifecycle",
        status,
        body,
        data={
            "status": lifecycle.get("status"),
            "summary": summary,
            "claim_advancement": lifecycle.get("claim_advancement"),
            "governance": lifecycle.get("governance") or {},
        },
        warnings=lifecycle.get("warnings") or [],
        artifact_paths=[str(pkg)],
    )


def _section_audit_trail(pkg: Path | None) -> dict[str, Any]:
    events = _read_audit_events(pkg)
    if not events:
        return _section(
            "audit_trail",
            "Audit / Tool Calls",
            "missing",
            "No audit / tool-call records found in the package.",
        )
    body = "| Timestamp | Tool | Status | Artifacts written |\n|---|---|---|---|\n"
    shown = events[-_MAX_AUDIT_EVENTS:]
    for e in shown:
        artifacts = e.get("artifacts_written") or e.get("artifacts") or []
        artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
        body += (
            f"| {_md_escape(e.get('timestamp') or e.get('ts'))} "
            f"| {_md_escape(e.get('tool') or e.get('tool_name'))} "
            f"| {_md_escape(e.get('status') or e.get('event_type'))} "
            f"| {artifact_count} |\n"
        )
    if len(events) > _MAX_AUDIT_EVENTS:
        body += f"\n_…({len(events) - _MAX_AUDIT_EVENTS} earlier event(s) not shown)_\n"
    return _section(
        "audit_trail",
        "Audit / Tool Calls",
        "included" if len(events) <= _MAX_AUDIT_EVENTS else "partial",
        body,
        data={"event_count": len(events), "shown": len(shown)},
        artifact_paths=[AUDIT_EVENTS_PATH],
    )


def _section_known_limitations(section_results: list[dict[str, Any]]) -> dict[str, Any]:
    bullets = [
        "This packet is a review support artifact only — it does not certify the design.",
        "Engineering claims are not advanced by generating this packet.",
        "Unit conversions are not normalized; values appear in the units they were imported with.",
        "Comparator semantics use exact-equality for `==`/`!=` operators; consider tolerances during review.",
        "Long content (tables, audit events, embedded reports) is capped — see the Audit section if entries appear truncated.",
    ]
    missing = [s["title"] for s in section_results if s.get("status") == "missing"]
    partial = [s["title"] for s in section_results if s.get("status") == "partial"]
    errored = [s["title"] for s in section_results if s.get("status") == "error"]
    body = "**Boundary**\n\n"
    for b in bullets:
        body += f"- {b}\n"
    if missing:
        body += "\n**Missing evidence sections**\n\n"
        for name in missing:
            body += f"- {_md_escape(name)}\n"
    if partial:
        body += "\n**Partial / capped sections**\n\n"
        for name in partial:
            body += f"- {_md_escape(name)}\n"
    if errored:
        body += "\n**Sections that errored during generation**\n\n"
        for name in errored:
            body += f"- {_md_escape(name)}\n"
    return _section("known_limitations", "Known Limitations", "included", body)


# ── packet assembly ───────────────────────────────────────────────────────────


def _resolve_package_optional(settings: Settings, project_id: str) -> Path | None:
    try:
        return _resolve_package(settings, project_id)
    except HTTPException:
        return None


def _project_name(settings: Settings, project_id: str) -> str | None:
    try:
        project = get_project(settings, project_id)
    except HTTPException:
        return None
    return project.get("name")


def _build_sections(settings: Settings, project_id: str, *, packet_id: str) -> list[dict[str, Any]]:
    pkg = _resolve_package_optional(settings, project_id)
    project_name = _project_name(settings, project_id)
    sections: list[dict[str, Any]] = []
    sections.append(_section_header(packet_id=packet_id, project_id=project_id, project_name=project_name, package_path=pkg))
    sections.append(_section_safety_boundary())
    sections.append(_section_project_health(settings, project_id))
    sections.append(_section_design_targets(settings, project_id))
    sections.append(_section_engineering_setup_draft(pkg))
    sections.append(_section_computed_metrics(settings, project_id))
    sections.append(_section_target_comparison(settings, project_id))
    sections.append(_section_geometry_inspection(pkg))
    sections.append(_section_cad_approval_records(pkg))
    sections.append(_section_structural_solver(pkg))
    sections.append(_section_cae_credibility(settings, project_id, pkg))
    sections.append(_section_copilot_loop(settings, project_id))
    sections.append(_section_evidence_lifecycle(settings, project_id, pkg))
    sections.append(_section_stale_evidence(pkg))
    sections.append(_section_audit_trail(pkg))
    sections.append(_section_known_limitations(sections))
    return sections


def _render_markdown(sections: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Engineering Review Support Packet", ""]
    for sec in sections:
        lines.append(f"## {sec['title']}")
        lines.append("")
        lines.append(sec["body_md"].rstrip())
        lines.append("")
    text = "\n".join(lines)
    capped, was_truncated = _truncate(text, cap=200_000)
    if was_truncated:
        capped += "\n\n_…(packet truncated for readability — see source artifacts for full data)_\n"
    return capped


def _build_manifest(
    *,
    packet_id: str,
    project_id: str,
    sections: list[dict[str, Any]],
    package_path: Path | None,
    markdown_path: str | None,
    json_path: str | None,
) -> dict[str, Any]:
    source_artifacts: list[str] = []
    if package_path is not None:
        source_artifacts.append(str(package_path))
    for sec in sections:
        for artifact in sec.get("artifact_paths") or []:
            if artifact and artifact not in source_artifacts:
                source_artifacts.append(artifact)
    target_comparison_summary = None
    stale_artifact_count = None
    for sec in sections:
        if sec["id"] == "target_comparison" and sec.get("data", {}).get("summary"):
            target_comparison_summary = sec["data"]["summary"]
        if sec["id"] == "stale_evidence":
            stale_artifact_count = sec.get("data", {}).get("stale_count")
    return {
        "schema_version": PACKET_SCHEMA_VERSION,
        "packet_id": packet_id,
        "project_id": project_id,
        "generated_at": _now_iso(),
        "source_artifacts": source_artifacts,
        "markdown_path": markdown_path,
        "json_path": json_path,
        "sections": [
            {
                "id": sec["id"],
                "title": sec["title"],
                "status": sec["status"],
                "artifact_paths": sec.get("artifact_paths") or [],
                "warnings": sec.get("warnings") or [],
                "data": sec.get("data") or {},
            }
            for sec in sections
        ],
        "target_comparison_summary": target_comparison_summary,
        "stale_artifact_count": stale_artifact_count,
        "claim_advancement": CLAIM_ADVANCEMENT,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def _packet_response(
    *,
    packet_id: str,
    sections: list[dict[str, Any]],
    preview_markdown: str | None,
    markdown_path: str | None,
    manifest_path: str | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    for sec in sections:
        warnings.extend(sec.get("warnings") or [])
        errors.extend(sec.get("errors") or [])
    return {
        "ok": True,
        "packet_id": packet_id,
        "markdown_path": markdown_path,
        "manifest_path": manifest_path,
        "preview_markdown": preview_markdown,
        "sections": [
            {
                "id": sec["id"],
                "title": sec["title"],
                "status": sec["status"],
                "warnings": sec.get("warnings") or [],
                "artifact_paths": sec.get("artifact_paths") or [],
            }
            for sec in sections
        ],
        "warnings": warnings,
        "errors": errors,
        "claim_advancement": CLAIM_ADVANCEMENT,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def preview_review_support_packet(
    settings: Settings, project_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build and return the packet without writing anything into the package."""
    payload = payload or {}
    packet_id = str(payload.get("packet_id") or _new_packet_id())
    sections = _build_sections(settings, project_id, packet_id=packet_id)
    markdown = _render_markdown(sections)
    return _packet_response(
        packet_id=packet_id,
        sections=sections,
        preview_markdown=markdown,
        markdown_path=None,
        manifest_path=None,
    )


def export_review_support_packet(
    settings: Settings, project_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the packet and write only the Markdown and JSON artifacts into the package.

    Never runs CAD/CAE tools, never edits any other artifact in the package.
    """
    payload = payload or {}
    packet_id = str(payload.get("packet_id") or _new_packet_id())
    include_preview = bool(payload.get("include_preview_markdown", True))

    try:
        package_path = _resolve_package(settings, project_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {
                "ok": False,
                "packet_id": packet_id,
                "markdown_path": None,
                "manifest_path": None,
                "preview_markdown": None,
                "sections": [],
                "warnings": [],
                "errors": ["Project has no .aieng package; cannot export packet."],
                "claim_advancement": CLAIM_ADVANCEMENT,
                "claim_boundary": CLAIM_BOUNDARY,
            }
        raise

    sections = _build_sections(settings, project_id, packet_id=packet_id)
    markdown = _render_markdown(sections)
    markdown_artifact_path = f"{PACKET_DIR}/{packet_id}.md"
    json_artifact_path = f"{PACKET_DIR}/{packet_id}.json"
    manifest = _build_manifest(
        packet_id=packet_id,
        project_id=project_id,
        sections=sections,
        package_path=package_path,
        markdown_path=markdown_artifact_path,
        json_path=json_artifact_path,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="aieng_review_packet_"))
    try:
        md_tmp = tmp_dir / f"{packet_id}.md"
        md_tmp.write_text(markdown, encoding="utf-8")
        json_tmp = tmp_dir / f"{packet_id}.json"
        json_tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_artifact_to_package(package_path, markdown_artifact_path, md_tmp, overwrite=True)
        write_artifact_to_package(package_path, json_artifact_path, json_tmp, overwrite=True)
    finally:
        for child in tmp_dir.glob("*"):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    response = _packet_response(
        packet_id=packet_id,
        sections=sections,
        preview_markdown=markdown if include_preview else None,
        markdown_path=markdown_artifact_path,
        manifest_path=json_artifact_path,
    )
    return response


__all__ = [
    "CLAIM_ADVANCEMENT",
    "CLAIM_BOUNDARY",
    "PACKET_DIR",
    "PACKET_SCHEMA_VERSION",
    "export_review_support_packet",
    "preview_review_support_packet",
]
