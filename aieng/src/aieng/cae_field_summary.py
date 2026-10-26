"""LLM-facing summaries for high-magnitude CAE field regions.

This module summarizes ``results/field_regions.json``. It does not parse full
fields, run solvers, interpolate meshes, or claim physical correctness.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .schema_versions import FIELD_SUMMARY_SCHEMA

FIELD_REGIONS_PATH = "results/field_regions.json"
FIELD_SUMMARY_PATH = "results/field_summary.json"
FIELD_SUMMARY_MD_PATH = "results/field_summary.md"
RESULT_SUMMARY_PATH = "results/result_summary.json"
RESULTS_DIR = "results/"


def generate_field_summary(package_path: str | Path) -> dict[str, Any]:
    """Return a structured summary of ``results/field_regions.json``."""
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"package not found: {path}")

    field_regions: dict[str, Any] | None = None
    targets_status: list[dict[str, Any]] = []
    with zipfile.ZipFile(path, "r") as zf:
        if FIELD_REGIONS_PATH in zf.namelist():
            try:
                raw = json.loads(zf.read(FIELD_REGIONS_PATH))
                if isinstance(raw, dict):
                    field_regions = raw
            except json.JSONDecodeError:
                field_regions = None
        targets_status = _read_targets_status(zf)

    if field_regions is None:
        return _empty_summary(targets_status)

    clusters = [
        cluster for cluster in field_regions.get("clusters", [])
        if isinstance(cluster, dict)
    ]
    clusters_sorted = sorted(
        clusters,
        key=lambda c: float(c.get("magnitude", {}).get("value", 0.0))
        if isinstance(c.get("magnitude"), dict) else 0.0,
        reverse=True,
    )
    warnings = [
        str(w) for w in field_regions.get("warnings", [])
        if isinstance(w, str)
    ]

    llm_summary: dict[str, Any] = {
        "one_line": _one_line(field_regions, clusters_sorted),
        "key_findings": _key_findings(field_regions, clusters_sorted),
        "risks": _risks(field_regions, clusters_sorted),
        "limitations": [
            "Field regions are observational clusters extracted from an external result file.",
            "No full-field data is serialized into this summary.",
            "No physical correctness, convergence, or design safety claim is made by this artifact.",
        ],
    }
    if targets_status:
        llm_summary["targets_status"] = targets_status

    return {
        "schema_version": FIELD_SUMMARY_SCHEMA,
        "summary_type": "cae_field_regions",
        "source": {
            "field_regions_path": FIELD_REGIONS_PATH,
            "source_frd": field_regions.get("source_frd"),
            "field": field_regions.get("field"),
            "metric": field_regions.get("metric"),
        },
        "status": {
            "has_field_regions": True,
            "cluster_count": len(clusters_sorted),
            "warnings": warnings,
        },
        "clusters": clusters_sorted,
        "llm_summary": llm_summary,
        "claim_policy": {
            "observational_only": True,
            "physical_correctness_not_claimed": True,
            "full_field_not_serialized": True,
        },
    }


def generate_field_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a concise markdown field summary."""
    status = summary.get("status", {})
    source = summary.get("source", {})
    llm = summary.get("llm_summary", {})
    lines = [
        "# CAE Field Region Summary",
        "",
        llm.get("one_line", "No field-region summary available."),
        "",
        f"**Field:** {source.get('field') or 'unknown'}",
        f"**Metric:** {source.get('metric') or 'unknown'}",
        f"**Clusters:** {status.get('cluster_count', 0)}",
        "",
        "## Key Findings",
        "",
    ]
    for finding in llm.get("key_findings", []):
        lines.append(f"- {finding}")
    lines += ["", "## Risks", ""]
    for risk in llm.get("risks", []):
        lines.append(f"- {risk}")
    lines += ["", "## Limitations", ""]
    for limitation in llm.get("limitations", []):
        lines.append(f"- {limitation}")
    return "\n".join(lines) + "\n"


def write_field_summary_package(
    package_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write ``results/field_summary.json`` and ``results/field_summary.md``."""
    path = Path(package_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names:
            raise ValueError("package is missing manifest.json")
        if FIELD_REGIONS_PATH not in names:
            raise FileNotFoundError(f"{FIELD_REGIONS_PATH} missing")
        if not overwrite:
            for existing in (FIELD_SUMMARY_PATH, FIELD_SUMMARY_MD_PATH):
                if existing in names:
                    raise FileExistsError(f"{existing} already exists; use --overwrite to replace it")
        manifest = json.loads(zf.read("manifest.json"))
        members = _read_existing_members(zf)

    summary = generate_field_summary(path)
    markdown = generate_field_summary_markdown(summary)
    _rewrite_package(path, members, manifest, summary, markdown)
    return path


def _empty_summary(targets_status: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    llm_summary: dict[str, Any] = {
        "one_line": "No field-region artifact is available.",
        "key_findings": [],
        "risks": ["No regional field observations are available for stress/displacement reasoning."],
        "limitations": [
            "No physical correctness, convergence, or design safety claim is made.",
        ],
    }
    if targets_status:
        llm_summary["targets_status"] = targets_status
    return {
        "schema_version": FIELD_SUMMARY_SCHEMA,
        "summary_type": "cae_field_regions",
        "source": {
            "field_regions_path": FIELD_REGIONS_PATH,
            "source_frd": None,
            "field": None,
            "metric": None,
        },
        "status": {
            "has_field_regions": False,
            "cluster_count": 0,
            "warnings": [f"{FIELD_REGIONS_PATH} missing or malformed"],
        },
        "clusters": [],
        "llm_summary": llm_summary,
        "claim_policy": {
            "observational_only": True,
            "physical_correctness_not_claimed": True,
            "full_field_not_serialized": True,
        },
    }


def _read_targets_status(zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Return a minimal targets_status list pulled from result_summary.targets.

    The Phase 34 field_summary surfaces target-compliance state *as already
    determined* by result_summary; it does not re-evaluate the targets,
    re-assert physical correctness, or invent values when absent. When
    ``result_summary.json`` is missing or malformed, an empty list is
    returned so the optional schema field stays absent in the writeback.
    """
    if RESULT_SUMMARY_PATH not in zf.namelist():
        return []
    try:
        raw = json.loads(zf.read(RESULT_SUMMARY_PATH))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
    targets = raw.get("targets")
    if not isinstance(targets, dict):
        return []
    items = targets.get("items")
    if not isinstance(items, list):
        return []

    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        met = item.get("met")
        if not isinstance(met, bool) and met != "unknown":
            continue
        entry: dict[str, Any] = {
            "id": item_id,
            "metric": item.get("metric") if isinstance(item.get("metric"), str) else None,
            "met": met,
        }
        source = item.get("source")
        if isinstance(source, str) and source:
            entry["source"] = source
        else:
            entry["source"] = None
        out.append(entry)
    return out


def _near_uniform(field_regions: dict[str, Any]) -> bool:
    """True when the extractor found no concentration (field_statistics)."""
    stats = field_regions.get("field_statistics")
    return isinstance(stats, dict) and stats.get("near_uniform") is True


def _one_line(field_regions: dict[str, Any], clusters: list[dict[str, Any]]) -> str:
    field = field_regions.get("field", "unknown")
    metric = field_regions.get("metric", "unknown")
    if not clusters:
        return f"No high-magnitude {field}/{metric} clusters were reported."
    if _near_uniform(field_regions):
        # A percentile cut always selects nodes; on a near-uniform field the
        # selected ones are not concentrations, so they are not "high" either.
        return (
            f"The {field}/{metric} field is near-uniform; {len(clusters)} top-percentile "
            "cluster(s) were reported, but none is a concentration."
        )
    return f"{len(clusters)} high-magnitude {field}/{metric} cluster(s) were reported from external result data."


def _key_findings(field_regions: dict[str, Any], clusters: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    for cluster in clusters:
        mag = cluster.get("magnitude", {})
        loc = cluster.get("location", {})
        if not isinstance(mag, dict) or not isinstance(loc, dict):
            continue
        feature_ref = cluster.get("feature_ref")
        feature_text = f" near feature {feature_ref}" if feature_ref else " with no feature mapping evidence"
        findings.append(
            f"{cluster.get('id')} peaks at {mag.get('value')} {mag.get('unit')} "
            f"around ({loc.get('x')}, {loc.get('y')}, {loc.get('z')}){feature_text}."
        )
    if field_regions.get("warnings"):
        findings.append("Extraction warnings are present; inspect results/field_regions.json before drawing conclusions.")
    return findings


def _risks(field_regions: dict[str, Any], clusters: list[dict[str, Any]]) -> list[str]:
    risks = []
    if any(cluster.get("feature_ref") is None for cluster in clusters):
        risks.append("One or more clusters could not be mapped back to a .aieng feature ID.")
    if clusters and not _near_uniform(field_regions):
        risks.append("High-magnitude regions require external engineering review before design decisions.")
    return risks


def _read_existing_members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, bytes]]:
    skip = {"manifest.json", FIELD_SUMMARY_PATH, FIELD_SUMMARY_MD_PATH}
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    seen: set[str] = set()
    for info in zf.infolist():
        if info.filename in skip or info.filename in seen:
            continue
        seen.add(info.filename)
        members.append((info, b"" if info.is_dir() else zf.read(info.filename)))
    return members


def _rewrite_package(
    path: Path,
    members: list[tuple[zipfile.ZipInfo, bytes]],
    manifest: dict[str, Any],
    summary: dict[str, Any],
    markdown: str,
) -> None:
    resources = manifest.setdefault("resources", {})
    results = resources.setdefault("results", {})
    if not isinstance(results, dict):
        results = {}
        resources["results"] = results
    results["field_summary"] = FIELD_SUMMARY_PATH
    results["field_summary_markdown"] = FIELD_SUMMARY_MD_PATH

    existing_filenames = {info.filename for info, _ in members}
    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as fh:
        temp_path = Path(fh.name)

    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for info, data in members:
                zf.writestr(info, data)
            if RESULTS_DIR not in existing_filenames:
                zf.writestr(RESULTS_DIR, b"")
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            zf.writestr(FIELD_SUMMARY_PATH, json.dumps(summary, indent=2, sort_keys=True) + "\n")
            zf.writestr(FIELD_SUMMARY_MD_PATH, markdown)
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
