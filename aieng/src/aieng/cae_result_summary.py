"""CAE/post-processing result summary generator for .aieng packages.

This module generates LLM-readable summaries from detected CAE artifacts.
It does NOT run solvers, parse VTU/FRD numerical fields, or synthesize results.
All claims are honest: presence-only unless explicitly parsed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .cae_artifact_detector import CAE_ARTIFACT_PATHS, detect_cae_artifacts
from .schema_versions import (
    CAE_RESULT_SUMMARY_SCHEMA,
    EVIDENCE_INDEX_SCHEMA,
    FRD_COMPUTED_METRICS_SCHEMA,
)

__all__ = [
    "RESULT_SUMMARY_PATH",
    "EVIDENCE_INDEX_PATH",
    "POSTPROCESSING_SUMMARY_PATH",
    "RESULTS_DIR",
    "FIELD_SUMMARY_DISPLACEMENT_PATH",
    "FIELD_SUMMARY_STRESS_PATH",
    "FIELD_SUMMARIES_DIR",
    "LEGACY_REST_RESULT_SUMMARY_PATH",
    "generate_cae_result_summary",
    "generate_evidence_index",
    "generate_postprocessing_markdown",
    "write_cae_result_summary_package",
    "compare_design_targets_for_package",
    "write_design_target_comparisons_package",
]

RESULT_SUMMARY_PATH = "results/result_summary.json"
EVIDENCE_INDEX_PATH = "results/evidence_index.json"
POSTPROCESSING_SUMMARY_PATH = "results/postprocessing_summary.md"
RESULTS_DIR = "results/"
FIELD_SUMMARY_DISPLACEMENT_PATH = "results/fields/displacement.summary.json"
FIELD_SUMMARY_STRESS_PATH = "results/fields/stress.summary.json"
FIELD_SUMMARIES_DIR = "results/fields/"
LEGACY_REST_RESULT_SUMMARY_PATH = "simulation/results_summary.json"

# Artifact path → (kind, role, supports)
_EVIDENCE_CATALOG: dict[str, tuple[str, str, list[str]]] = {
    "graph/constraints.json": ("setup", "constraint_definitions", ["simulation_targets", "protected_regions"]),
    "simulation/cae_imports/parsed_materials.json": ("setup", "material_definitions", ["material_assignment", "stress_analysis"]),
    "simulation/cae_imports/parsed_boundary_conditions.json": ("setup", "boundary_condition_definitions", ["constraint_enforcement", "simulation_setup"]),
    "simulation/cae_imports/parsed_loads.json": ("setup", "load_definitions", ["load_application", "simulation_setup"]),
    "simulation/cae_mapping.json": ("setup", "cae_mapping", ["feature_to_cae_correspondence"]),
    "simulation/mesh/mesh_metadata.json": ("mesh", "mesh_metadata", ["mesh_quality_assessment", "result_field_mapping"]),
    "simulation/mesh/model.vtk": ("mesh", "visualization_mesh", ["mesh_visualization", "result_field_mapping"]),
    "simulation/mesh/model.vtu": ("mesh", "visualization_mesh", ["mesh_visualization", "result_field_mapping"]),
    "simulation/solver_settings.json": ("setup", "solver_settings", ["solver_configuration", "simulation_setup"]),
    "results/evidence_index.json": ("result", "evidence_ledger", ["provenance", "audit"]),
    "results/result_summary.json": ("result", "result_summary", ["post_processing", "llm_orientation"]),
    "results/fields/displacement.vtu": ("field", "displacement_field", ["displacement_visualization", "deformation_assessment"]),
    "results/fields/von_mises_stress.vtu": ("field", "von_mises_stress_field", ["stress_visualization", "failure_assessment"]),
    "results/fields/safety_factor.vtu": ("field", "safety_factor_field", ["safety_assessment", "design_review"]),
    "validation/status.yaml": ("validation", "validation_status", ["completeness_check", "design_review"]),
    # Metadata extensions (Phase 5)
    "results/solver_metadata.json": ("result", "solver_metadata", ["provenance", "solver_identification"]),
    "results/field_metadata.json": ("result", "field_metadata", ["provenance", "field_catalog"]),
    # Phase 6 — externally computed metrics
    "results/computed_metrics.json": ("computed_metrics", "external_postprocessing_metrics", [
        "LLM-readable numerical result summary",
        "post-processing evidence",
        "validation interpretation",
    ]),
    "results/field_regions.json": ("field", "field_region_clusters", ["regional_field_reasoning", "hotspot_triage"]),
    "results/field_summary.json": ("field", "field_region_summary", ["llm_orientation", "regional_field_reasoning"]),
    LEGACY_REST_RESULT_SUMMARY_PATH: ("result", "legacy_rest_result_summary", ["compatibility", "post_processing"]),
    "task/design_targets.yaml": ("task", "design_targets", ["target_compliance_assessment", "design_review"]),
    # Compact field summary artifacts (persisted evidence, no full arrays)
    "results/fields/displacement.summary.json": ("field", "cae_field_summary", ["displacement_extrema", "field_evidence", "audit"]),
    "results/fields/stress.summary.json": ("field", "cae_field_summary", ["stress_extrema", "field_evidence", "audit"]),
}


def generate_cae_result_summary(package_path: str | Path) -> dict[str, Any]:
    """Generate an honest CAE/post-processing result summary dict.

    Args:
        package_path: Path to the .aieng package.

    Returns:
        JSON-serializable summary dict (schema_version
        :data:`~aieng.schema_versions.CAE_RESULT_SUMMARY_SCHEMA`).
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"package not found: {path}")

    detection = detect_cae_artifacts(path)
    mode = detection["mode"]
    artifacts = detection["artifacts"]

    # Build artifact lists
    mesh_files = [p for p in ("simulation/mesh/mesh_metadata.json", "simulation/mesh/model.vtk", "simulation/mesh/model.vtu") if artifacts.get(p)]
    field_files = [p for p in ("results/fields/displacement.vtu", "results/fields/von_mises_stress.vtu", "results/fields/safety_factor.vtu") if artifacts.get(p)]
    result_summary_files = [p for p in (RESULT_SUMMARY_PATH, LEGACY_REST_RESULT_SUMMARY_PATH) if artifacts.get(p)]
    evidence_files = [p for p in ("results/evidence_index.json",) if artifacts.get(p)]
    validation_files = [p for p in ("validation/status.yaml",) if artifacts.get(p)]
    setup_files = [p for p in (
        "graph/constraints.json",
        "simulation/cae_imports/parsed_materials.json",
        "simulation/cae_imports/parsed_boundary_conditions.json",
        "simulation/cae_imports/parsed_loads.json",
        "simulation/cae_mapping.json",
        "simulation/solver_settings.json",
    ) if artifacts.get(p)]

    # Read metadata from zip (honest — does not parse numerical VTU/FRD content)
    solver_metadata = None
    field_metadata = None
    solver_settings = None
    load_cases: list[dict[str, Any]] = []
    computed_metrics = None
    legacy_rest_summary = None
    solver_runs: list[dict[str, Any]] = []
    with zipfile.ZipFile(path, "r") as zf:
        namelist = set(zf.namelist())
        solver_metadata = _read_solver_metadata(zf)
        field_metadata = _read_field_metadata(zf)
        solver_settings = _read_solver_settings(zf)
        load_cases = _read_load_cases(zf)
        computed_metrics = _read_computed_metrics(zf)
        legacy_rest_summary = _read_legacy_rest_result_summary(zf)
        if computed_metrics is None:
            computed_metrics = _legacy_rest_summary_to_computed_metrics(legacy_rest_summary)
        solver_runs = _read_solver_runs(zf)
        design_targets = _read_design_targets(zf)

    # Artifact presence used by the Phase 35 PR 2 design-target evaluator to
    # distinguish "unknown" (evidence file present, field missing) from
    # "not_evaluated" (no evidence file at all).
    artifact_presence = {
        "results/computed_metrics.json": "results/computed_metrics.json" in namelist,
        "graph/feature_graph.json": "graph/feature_graph.json" in namelist,
    }

    # Merge computed metrics into load cases where possible
    load_cases = _merge_load_cases_with_metrics(load_cases, computed_metrics)

    # Build computed_values block
    computed_values = _build_computed_values(computed_metrics, load_cases)
    result_contract = _build_result_contract(
        namelist=namelist,
        computed_values=computed_values,
        solver_runs=solver_runs,
        legacy_rest_summary=legacy_rest_summary,
    )
    targets = _compare_design_targets(design_targets, computed_values)
    design_target_comparisons = _build_design_target_comparisons(
        design_targets, computed_values, artifact_presence
    )

    # Credit a completed solver run: when solver_run.json evidence shows a
    # solved run, the metrics were *computed here*, not imported from an external
    # post-processor. Drive source/status/llm from that run so the summary stops
    # reporting an executed solve as "external / no solver executed".
    completed_runs = [run for run in solver_runs if _solver_run_completed(run)]
    solver_executed = bool(completed_runs)
    executed_solver: dict[str, Any] | None = None
    executed_converged: bool | None = None
    if completed_runs:
        latest_run = completed_runs[-1]
        executed_converged = (
            latest_run.get("converged") if isinstance(latest_run.get("converged"), bool) else None
        )
        frd_outputs = [
            str(f)
            for run in completed_runs
            for f in (run.get("output_files") or [])
            if str(f).lower().endswith((".frd", ".dat"))
        ]
        run_solver_name = latest_run.get("solver") or "CalculiX"
        executed_solver = {
            "solver": run_solver_name,
            "software": run_solver_name,
            "source_files": frd_outputs
            or (solver_metadata.get("source_files", []) if solver_metadata else []),
        }
    effective_source = executed_solver or solver_metadata

    # Warnings
    warnings: list[str] = []
    if mode in ("cae_result", "cae_validation") and not field_files:
        warnings.append("Result mode detected but no field files found.")
    if mode == "cae_setup" and not mesh_files:
        warnings.append("CAE setup present but no mesh files detected.")
    if mode == "cad_only":
        warnings.append("No CAE artifacts detected; package is CAD-only.")
    if computed_metrics and computed_metrics.get("warnings"):
        for w in computed_metrics["warnings"]:
            warnings.append(f"Computed metrics warning: {w}")
    if computed_metrics and computed_metrics.get("malformed"):
        warnings.append("results/computed_metrics.json is malformed; metrics ignored.")

    # Honest LLM summary
    summary_solver_meta = effective_source if solver_executed else solver_metadata
    one_line = _build_one_line_summary(mode, detection, summary_solver_meta, computed_values, solver_executed)
    key_findings = _build_key_findings(mode, detection, field_files, mesh_files, summary_solver_meta, load_cases, computed_values, solver_executed)
    risks = _build_risks(mode, detection, computed_values)
    recommended_next_actions = _build_recommended_actions(mode, detection, computed_values)
    limitations = _build_limitations(mode, detection, computed_values, solver_executed)

    return {
        "schema_version": CAE_RESULT_SUMMARY_SCHEMA,
        "summary_type": "cae_postprocessing",
        "source": {
            "package_path": str(path),
            "solver": effective_source["solver"] if effective_source else "external_or_unknown",
            "software": effective_source.get("software") if effective_source else None,
            "source_files": effective_source.get("source_files", []) if effective_source else [],
        },
        "result_contract": result_contract,
        "status": {
            "mode": mode,
            "has_cae_setup": detection["has_cae_setup"],
            "has_mesh": detection["has_mesh"],
            "has_results": detection["has_results"],
            "has_fields": detection["has_fields"],
            "has_validation": detection["has_validation"],
            "solved": True if solver_executed else None,
            "converged": executed_converged,
            "warnings": warnings,
        },
        "artifacts": {
            "mesh_files": mesh_files,
            "field_files": field_files,
            "result_summary_files": result_summary_files,
            "evidence_files": evidence_files,
            "validation_files": validation_files,
            "setup_files": setup_files,
        },
        "solver_settings": solver_settings,
        "load_cases": load_cases,
        "field_metadata": field_metadata,
        "computed_values": computed_values,
        "targets": targets,
        "design_target_comparisons": design_target_comparisons,
        "llm_summary": {
            "one_line": one_line,
            "key_findings": key_findings,
            "risks": risks,
            "recommended_next_actions": recommended_next_actions,
            "limitations": limitations,
        },
    }


def generate_evidence_index(package_path: str | Path) -> dict[str, Any]:
    """Generate an evidence index from detected CAE artifacts.

    Args:
        package_path: Path to the .aieng package.

    Returns:
        JSON-serializable evidence index dict (schema_version
        :data:`~aieng.schema_versions.EVIDENCE_INDEX_SCHEMA`).
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"package not found: {path}")

    detection = detect_cae_artifacts(path)
    artifacts = detection["artifacts"]

    entries: list[dict[str, Any]] = []
    for artifact_path, present in artifacts.items():
        catalog = _EVIDENCE_CATALOG.get(artifact_path)
        if catalog is None:
            continue
        kind, role, supports = catalog
        entry_id = artifact_path.replace("/", "_").replace(".", "_")
        entries.append({
            "id": entry_id,
            "path": artifact_path,
            "kind": kind,
            "role": role,
            "exists": present,
            "supports": supports if present else [],
        })

    # Add metadata artifacts that are not in the canonical 15-path list
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        for meta_path, catalog in _EVIDENCE_CATALOG.items():
            if meta_path in artifacts:
                continue  # Already handled above
            kind, role, supports = catalog
            present = meta_path in names
            entry_id = meta_path.replace("/", "_").replace(".", "_")
            entries.append({
                "id": entry_id,
                "path": meta_path,
                "kind": kind,
                "role": role,
                "exists": present,
                "supports": supports if present else [],
            })
        # Add dynamic load case entries
        for lc in _read_load_cases(zf):
            entry_id = f"load_case_{lc['id']}"
            entries.append({
                "id": entry_id,
                "path": lc["source_file"],
                "kind": "setup",
                "role": "load_case",
                "exists": True,
                "supports": ["load_application", "simulation_setup"],
            })

        # Dynamic: solver run output artifacts (simulation/runs/<id>/...)
        # These paths are not in the static catalog because run IDs are generated.
        for name in sorted(names):
            if "/runs/" in name and name.endswith("/solver_run.json"):
                entries.append({
                    "id": name.replace("/", "_").replace(".", "_"),
                    "path": name,
                    "kind": "result",
                    "role": "solver_run_metadata",
                    "exists": True,
                    "supports": ["solver_execution_evidence", "audit"],
                })
            elif "/runs/" in name and "/outputs/" in name and name.endswith(".frd"):
                entries.append({
                    "id": name.replace("/", "_").replace(".", "_"),
                    "path": name,
                    "kind": "result",
                    "role": "solver_raw_output",
                    "exists": True,
                    "supports": ["numerical_result_source", "post-processing evidence"],
                })

    return {
        "schema_version": EVIDENCE_INDEX_SCHEMA,
        "evidence_type": "cae_artifacts",
        "entries": entries,
    }


def generate_postprocessing_markdown(
    summary: dict[str, Any],
    evidence_index: dict[str, Any],
) -> str:
    """Generate a concise human/LLM-readable markdown summary.

    Args:
        summary: Output from `generate_cae_result_summary`.
        evidence_index: Output from `generate_evidence_index`.

    Returns:
        Markdown string.
    """
    status = summary.get("status", {})
    mode = status.get("mode", "unknown")
    artifacts = summary.get("artifacts", {})
    computed = summary.get("computed_values", {})
    targets = summary.get("targets", {})
    llm = summary.get("llm_summary", {})
    warnings = status.get("warnings", [])
    load_cases = summary.get("load_cases", [])
    solver_settings = summary.get("solver_settings")
    field_metadata = summary.get("field_metadata")
    source = summary.get("source", {})

    lines: list[str] = [
        "# CAE / Post-processing Summary",
        "",
        f"**Mode:** {mode}",
        "",
    ]

    if source.get("solver") and source["solver"] != "external_or_unknown":
        lines.append(f"**Solver:** {source['solver']}")
        if source.get("software"):
            lines.append(f"**Software:** {source['software']}")
        lines.append("")

    lines.append("## Detected artifacts")
    lines.append("")
    for category, files in artifacts.items():
        if files:
            lines.append(f"- **{category}**: {', '.join(files)}")
    lines.append("")

    if load_cases:
        lines.append("## Load cases")
        lines.append("")
        for lc in load_cases:
            name = lc.get("name") or lc.get("id", "unknown")
            lc_type = lc.get("type", "unknown")
            mag = lc.get("magnitude")
            unit = lc.get("unit")
            mag_str = f" {mag} {unit}" if mag is not None and unit else (f" {mag}" if mag is not None else "")
            lines.append(f"- **{name}** ({lc_type}){mag_str}")
        lines.append("")

    if solver_settings:
        lines.append("## Solver settings")
        lines.append("")
        st = solver_settings.get("solver_type") or solver_settings.get("type")
        at = solver_settings.get("analysis_type")
        if st:
            lines.append(f"- Solver type: {st}")
        if at:
            lines.append(f"- Analysis type: {at}")
        lines.append("")

    if field_metadata and field_metadata.get("fields"):
        lines.append("## Field metadata")
        lines.append("")
        lines.append(f"- Registered fields: {field_metadata.get('count', 0)}")
        lines.append("")

    # Phase 6 — computed metrics block
    if computed.get("extrema_computed"):
        lines.append("## Imported computed metrics")
        lines.append("")
        lines.append(f"_Source:_ `{computed.get('source')}`  ")
        lines.append(f"_Computed by:_ {computed.get('computed_by', 'unknown')}")
        lines.append("")
        for metric_key in ("max_von_mises_stress", "max_displacement", "minimum_safety_factor"):
            metric = computed.get(metric_key)
            if metric and isinstance(metric, dict) and metric.get("value") is not None:
                unit = metric.get("unit") or ""
                val = metric["value"]
                lines.append(f"- **{metric_key}:** {val} {unit}".strip())
        lines.append("")
        if computed.get("by_load_case"):
            lines.append("### Per load case")
            lines.append("")
            for blc in computed["by_load_case"]:
                lc_id = blc.get("id", "unknown")
                lines.append(f"- **{lc_id}**")
                for metric_key in ("max_von_mises_stress", "max_displacement", "minimum_safety_factor"):
                    metric = blc.get("metrics", {}).get(metric_key)
                    if metric and isinstance(metric, dict) and metric.get("value") is not None:
                        unit = metric.get("unit") or ""
                        val = metric["value"]
                        lines.append(f"  - {metric_key}: {val} {unit}".strip())
            lines.append("")
    else:
        lines.append("## Not computed")
        lines.append("")
        lines.append("- Max displacement: not computed")
        lines.append("- Max von Mises stress: not computed")
        lines.append("- Minimum safety factor: not computed")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    if targets.get("target_count"):
        lines.append("## Design Targets")
        lines.append("")
        for target in targets.get("items", []):
            metric = target.get("metric", "unknown")
            target_value = target.get("target_value")
            actual = target.get("actual_value")
            met = target.get("met")
            lines.append(
                f"- **{target.get('id', metric)}:** {metric} actual={actual} "
                f"target={target.get('operator')} {target_value} met={met}"
            )
        lines.append("")

    lines.append("## Limitations")
    lines.append("")
    for lim in llm.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")

    lines.append("## Recommended next actions")
    lines.append("")
    for action in llm.get("recommended_next_actions", []):
        lines.append(f"- {action}")
    lines.append("")

    return "\n".join(lines)


def write_cae_result_summary_package(
    package_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write result_summary.json, evidence_index.json, and postprocessing_summary.md into the package.

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
                for existing in (RESULT_SUMMARY_PATH, EVIDENCE_INDEX_PATH, POSTPROCESSING_SUMMARY_PATH):
                    if existing in names:
                        raise FileExistsError(
                            f"{existing} already exists; use --overwrite to replace it"
                        )
            manifest = json.loads(package.read("manifest.json"))
            existing_members = _read_existing_members(package)
            # Find lexicographically last FRD for field summary source reference
            frd_candidates = sorted(n for n in names if n.endswith("/outputs/result.frd"))
            frd_path = frd_candidates[-1] if frd_candidates else None
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {path}") from exc

    summary = generate_cae_result_summary(path)
    evidence = generate_evidence_index(path)
    markdown = generate_postprocessing_markdown(summary, evidence)

    # Build compact field summary artifacts from computed_values (no full arrays).
    computed_values = summary.get("computed_values") or {}
    field_summaries: dict[str, dict[str, Any]] = {}
    _disp = _build_field_summary("displacement", "max_displacement", "mm", computed_values, frd_path)
    if _disp is not None:
        field_summaries[FIELD_SUMMARY_DISPLACEMENT_PATH] = _disp
    _stress = _build_field_summary("stress", "max_von_mises_stress", "MPa", computed_values, frd_path)
    if _stress is not None:
        field_summaries[FIELD_SUMMARY_STRESS_PATH] = _stress

    _rewrite_package_with_summary(path, existing_members, manifest, summary, evidence, markdown, field_summaries)
    return path


def compare_design_targets_for_package(package_path: str | Path) -> dict[str, Any]:
    """Return the ``design_target_comparisons`` block for a .aieng package.

    Reuses the same normalization, comparator semantics, and evidence rules
    as :func:`generate_cae_result_summary`. Does NOT mutate the package or
    ``claim_map.json``.

    Raises:
        FileNotFoundError: if the package does not exist or it lacks
            ``task/design_targets.yaml``.
        ValueError: if the package is not a valid zip archive.
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"package not found: {path}")

    try:
        with zipfile.ZipFile(path, mode="r") as zf:
            namelist = set(zf.namelist())
            if "task/design_targets.yaml" not in namelist:
                raise FileNotFoundError(
                    f"task/design_targets.yaml not found in package: {path}"
                )
            design_targets = _read_design_targets(zf)
            computed_metrics = _read_computed_metrics(zf)
            load_cases = _read_load_cases(zf)
            artifact_presence = {
                "results/computed_metrics.json": "results/computed_metrics.json" in namelist,
                "graph/feature_graph.json": "graph/feature_graph.json" in namelist,
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {path}") from exc

    load_cases = _merge_load_cases_with_metrics(load_cases, computed_metrics)
    computed_values = _build_computed_values(computed_metrics, load_cases)
    return _build_design_target_comparisons(design_targets, computed_values, artifact_presence)


def write_design_target_comparisons_package(
    package_path: str | Path,
    *,
    summary_path: str = RESULT_SUMMARY_PATH,
) -> Path:
    """Inject ``design_target_comparisons`` into ``results/result_summary.json``.

    Reads the comparison block via :func:`compare_design_targets_for_package`
    and rewrites the package atomically (temp file + atomic move). All other
    fields of an existing ``result_summary.json`` are preserved verbatim. If
    the summary file does not exist, a minimal one is created containing the
    comparison block.

    This function never touches ``results/claim_map.json`` or any other
    artifact in the package.

    Args:
        package_path: Path to the .aieng package.
        summary_path: Path inside the package to write to. Defaults to
            ``results/result_summary.json``.

    Returns:
        Path to the updated package.
    """
    path = Path(package_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    comparisons = compare_design_targets_for_package(path)

    try:
        with zipfile.ZipFile(path, mode="r") as package:
            namelist = set(package.namelist())
            existing_summary: dict[str, Any] | None = None
            if summary_path in namelist:
                try:
                    raw = package.read(summary_path)
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        existing_summary = parsed
                except (json.JSONDecodeError, KeyError):
                    existing_summary = None
            seen: set[str] = set()
            members: list[tuple[zipfile.ZipInfo, bytes]] = []
            for info in package.infolist():
                if info.filename == summary_path or info.filename in seen:
                    continue
                seen.add(info.filename)
                data = b"" if info.is_dir() else package.read(info.filename)
                members.append((info, data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {path}") from exc

    if existing_summary is not None:
        merged = dict(existing_summary)
        merged["design_target_comparisons"] = comparisons
        summary_payload = merged
    else:
        summary_payload = {
            "schema_version": CAE_RESULT_SUMMARY_SCHEMA,
            "summary_type": "cae_postprocessing",
            "design_target_comparisons": comparisons,
        }

    existing_filenames = {info.filename for info, _ in members}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)
    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data in members:
                out_package.writestr(info, data)
            if RESULTS_DIR not in existing_filenames:
                out_package.writestr(RESULTS_DIR, b"")
            out_package.writestr(
                summary_path,
                json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
            )
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return path


def _build_one_line_summary(
    mode: str,
    detection: dict[str, Any],
    solver_metadata: dict[str, Any] | None,
    computed_values: dict[str, Any],
    solver_executed: bool = False,
) -> str:
    if mode == "cad_only":
        return "CAD-only package; no CAE artifacts detected."
    if mode == "cae_setup":
        return "CAE setup detected (constraints, materials, BCs, loads, or mapping) but no solver results found."
    if mode == "cae_result":
        solver_name = solver_metadata.get("solver") if solver_metadata else None
        if solver_executed and computed_values.get("extrema_computed"):
            return f"CAE results computed by an executed {solver_name or 'CalculiX'} solver run."
        if computed_values.get("extrema_computed"):
            return f"CAE result artifacts detected with imported computed metrics (solver: {solver_name or 'external'})."
        if solver_name and solver_name != "external_or_unknown":
            return f"CAE result artifacts detected (solver: {solver_name})."
        return "CAE result artifacts detected (external solver-output field files or evidence present)."
    if mode == "cae_validation":
        return "CAE validation status present; package includes review/validation artifacts."
    return f"Unknown CAE mode: {mode}"


def _build_key_findings(
    mode: str,
    detection: dict[str, Any],
    field_files: list[str],
    mesh_files: list[str],
    solver_metadata: dict[str, Any] | None,
    load_cases: list[dict[str, Any]],
    computed_values: dict[str, Any],
    solver_executed: bool = False,
) -> list[str]:
    findings: list[str] = []
    if mode == "cad_only":
        if load_cases:
            findings.append("No canonical CAE setup artifacts present, but load case definitions were detected.")
        else:
            findings.append("No CAE setup or result artifacts present.")
    if detection["has_cae_setup"]:
        findings.append("CAE setup artifacts present (constraints, materials, BCs, loads, or mapping).")
    if detection["has_mesh"]:
        findings.append("Mesh file(s) detected.")
    if detection["has_results"]:
        findings.append("Result evidence index detected.")
    if detection["has_fields"]:
        findings.append(f"Result field file(s) detected: {', '.join(field_files)}.")
    if detection["has_validation"]:
        findings.append("Validation status file detected.")
    if solver_metadata and solver_metadata.get("solver") and solver_metadata["solver"] != "external_or_unknown":
        findings.append(f"Solver identified: {solver_metadata['solver']}.")
    if load_cases:
        findings.append(f"Load case(s) detected: {len(load_cases)}.")
    if computed_values.get("extrema_computed"):
        verb = "Computed" if solver_executed else "Imported"
        findings.append(
            "Metrics computed from an executed solver run."
            if solver_executed
            else "Externally computed metrics are present and imported."
        )
        for metric_key in ("max_von_mises_stress", "max_displacement", "minimum_safety_factor"):
            metric = computed_values.get(metric_key)
            if metric and isinstance(metric, dict) and metric.get("value") is not None:
                unit = metric.get("unit") or ""
                findings.append(f"{verb} {metric_key}: {metric['value']} {unit}".strip())
    return findings


def _build_risks(
    mode: str,
    detection: dict[str, Any],
    computed_values: dict[str, Any],
) -> list[str]:
    risks: list[str] = []
    if mode == "cae_setup" and not detection["has_mesh"]:
        risks.append("Mesh not detected; external meshing may be required before solving.")
    if mode in ("cae_result", "cae_validation") and not detection["has_fields"]:
        risks.append("Result mode declared but no field files detected; result content may be missing.")
    if computed_values.get("extrema_computed"):
        sf = computed_values.get("minimum_safety_factor")
        if sf and isinstance(sf, dict) and sf.get("value") is not None and sf["value"] < 1.5:
            risks.append(f"Low safety factor detected ({sf['value']}); review design margins.")
    return risks


def _build_recommended_actions(
    mode: str,
    detection: dict[str, Any],
    computed_values: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if mode == "cad_only":
        actions.append("Import CAE deck or define simulation setup if CAE analysis is intended.")
        return actions
    if mode == "cae_setup":
        actions.append("Generate mesh and run external solver to produce result fields.")
        actions.append("Import solver result artifacts into the package when available.")
        return actions
    if mode in ("cae_result", "cae_validation"):
        actions.append("Review detected field files with an external post-processor (e.g., ParaView).")
        if not computed_values.get("extrema_computed"):
            actions.append("Parse numerical extrema from field files if quantitative assessment is needed.")
        else:
            actions.append("Validate imported metrics against external solver report before advancing claims.")
    return actions


def _build_limitations(
    mode: str,
    detection: dict[str, Any],
    computed_values: dict[str, Any],
    solver_executed: bool = False,
) -> list[str]:
    if solver_executed:
        # A real solver run produced these metrics; state the physics envelope,
        # not "no solver executed".
        limitations: list[str] = [
            "Linear static analysis only — no plasticity, contact, large deflection, or dynamics.",
            "Single-mesh result; run a mesh-convergence study to bound discretization error.",
        ]
        return limitations
    limitations = [
        "This summary is based on artifact presence only. No solver was executed. No numerical fields were parsed.",
    ]
    if detection["has_fields"] and not computed_values.get("extrema_computed"):
        limitations.append("Field files were detected, but numerical extrema were not computed in this summary.")
    if computed_values.get("extrema_computed"):
        limitations.append(f"Numerical metrics were imported from {computed_values.get('source')} and were not computed by aieng.")
        limitations.append("Source field files (VTU/FRD/ODB) are not parsed by aieng; metrics come from an external post-processor.")
    return limitations


def _build_field_summary(
    field_name: str,
    metric_key: str,
    unit: str,
    computed_values: dict[str, Any],
    frd_path: str | None,
) -> dict[str, Any] | None:
    """Build a compact field summary artifact dict from computed_values.

    Returns None if no computed value exists for this field.
    Does not store full per-node arrays; max_value only.
    claim_advancement is always "none".
    """
    metric = computed_values.get(metric_key)
    if not metric or not isinstance(metric, dict) or metric.get("value") is None:
        return None
    return {
        "schema_version": "0.1",
        "field_name": field_name,
        "unit": metric.get("unit") or unit,
        "source": {
            "frd_path": frd_path,
            "source_type": "computed_metrics",
            "computed_metrics_path": "results/computed_metrics.json",
        },
        "stats": {
            "min_value": None,
            "max_value": metric["value"],
            "node_count": None,
            "values_finite": None,
        },
        "evidence_role": f"{field_name}_extrema",
        "claim_advancement": "none",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _read_existing_members(package: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, bytes]]:
    skip = {
        "manifest.json",
        RESULT_SUMMARY_PATH,
        EVIDENCE_INDEX_PATH,
        POSTPROCESSING_SUMMARY_PATH,
        FIELD_SUMMARY_DISPLACEMENT_PATH,
        FIELD_SUMMARY_STRESS_PATH,
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
    evidence: dict[str, Any],
    markdown: str,
    field_summaries: dict[str, dict[str, Any]] | None = None,
) -> None:
    resources = manifest.setdefault("resources", {})
    results_resources = resources.setdefault("results", {})
    if not isinstance(results_resources, dict):
        raise ValueError("manifest resources.results must be an object")
    results_resources["result_summary"] = RESULT_SUMMARY_PATH
    results_resources["evidence_index"] = EVIDENCE_INDEX_PATH
    results_resources["postprocessing_summary"] = POSTPROCESSING_SUMMARY_PATH

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    existing_filenames = {info.filename for info, _ in existing_members}

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data in existing_members:
                out_package.writestr(info, data)
            if RESULTS_DIR not in existing_filenames:
                out_package.writestr(RESULTS_DIR, b"")
            out_package.writestr("manifest.json", manifest_json)
            out_package.writestr(RESULT_SUMMARY_PATH, json.dumps(summary, indent=2, sort_keys=True) + "\n")
            out_package.writestr(EVIDENCE_INDEX_PATH, json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            out_package.writestr(POSTPROCESSING_SUMMARY_PATH, markdown.encode("utf-8"))
            if field_summaries:
                if FIELD_SUMMARIES_DIR not in existing_filenames:
                    out_package.writestr(FIELD_SUMMARIES_DIR, b"")
                for _fs_path, _fs_data in field_summaries.items():
                    out_package.writestr(_fs_path, json.dumps(_fs_data, indent=2, sort_keys=True) + "\n")
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# ---------------------------------------------------------------------------
# Phase 5 metadata helpers (read from zip, do not parse numerical fields)
# ---------------------------------------------------------------------------

def _read_json_from_zip(zf: zipfile.ZipFile, path: str) -> Any | None:
    """Read and parse JSON from a zip member. Return None on missing or invalid."""
    if path not in zf.namelist():
        return None
    try:
        return json.loads(zf.read(path))
    except (json.JSONDecodeError, KeyError):
        return None


def _read_solver_metadata(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read results/solver_metadata.json and normalize to a small dict."""
    data = _read_json_from_zip(zf, "results/solver_metadata.json")
    if not isinstance(data, dict):
        return None
    return {
        "solver": data.get("solver") or data.get("solver_name") or "external_or_unknown",
        "software": data.get("software") or data.get("solver_version") or None,
        "source_files": data.get("source_files") or [],
    }


def _read_field_metadata(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read results/field_metadata.json and normalize to a small dict."""
    data = _read_json_from_zip(zf, "results/field_metadata.json")
    if not isinstance(data, dict):
        return None
    fields = data.get("fields") or []
    if isinstance(fields, dict):
        fields = [fields]
    if not isinstance(fields, list):
        fields = []
    return {
        "fields": fields,
        "format": data.get("format"),
        "count": len(fields),
    }


def _read_solver_settings(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read simulation/solver_settings.json and normalize."""
    data = _read_json_from_zip(zf, "simulation/solver_settings.json")
    if not isinstance(data, dict):
        return None
    return {
        "solver_type": data.get("solver_type") or data.get("type"),
        "analysis_type": data.get("analysis_type"),
        "parameters": data.get("parameters") or {},
    }


def _read_load_cases(zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Discover and normalize load case files under simulation/load_cases/*.json."""
    load_cases: list[dict[str, Any]] = []
    prefix = "simulation/load_cases/"
    for name in zf.namelist():
        if not name.startswith(prefix) or not name.endswith(".json") or name.endswith("/"):
            continue
        data = _read_json_from_zip(zf, name)
        if not isinstance(data, dict):
            continue
        normalized = {
            "id": data.get("id") or data.get("name") or Path(name).stem,
            "name": data.get("name") or data.get("id") or Path(name).stem,
            "type": data.get("type") or data.get("load_type") or "unknown",
            "magnitude": data.get("magnitude"),
            "unit": data.get("unit"),
            "description": data.get("description"),
            "source_file": name,
        }
        load_cases.append(normalized)
    load_cases.sort(key=lambda x: x["source_file"])
    return load_cases


# ---------------------------------------------------------------------------
# Phase 6 computed metrics helpers
# ---------------------------------------------------------------------------

def _read_computed_metrics(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read results/computed_metrics.json and normalize.

    Returns None if missing or malformed. Returns dict with normalized fields
    and a `malformed` flag when the file exists but is unreadable.
    """
    if "results/computed_metrics.json" not in zf.namelist():
        return None
    raw = _read_json_from_zip(zf, "results/computed_metrics.json")
    if not isinstance(raw, dict):
        return {"malformed": True, "warnings": ["File exists but is not a valid JSON object."]}

    metrics_source = raw.get("metrics_source") or {}
    load_cases_metrics = raw.get("load_cases") or []
    if not isinstance(load_cases_metrics, list):
        load_cases_metrics = []

    return {
        "schema_version": raw.get("schema_version", FRD_COMPUTED_METRICS_SCHEMA),
        "metrics_source": {
            "tool": metrics_source.get("tool") or "external_postprocessor",
            "software": metrics_source.get("software"),
            "source_files": metrics_source.get("source_files") or [],
        },
        "load_cases": load_cases_metrics,
        "warnings": raw.get("warnings") or [],
    }


def _read_legacy_rest_result_summary(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read the legacy REST all-in-one result summary, when present.

    This compatibility artifact is not the canonical MCP solver-run evidence. It
    can contribute normalized scalar metrics, but it must remain visibly marked
    as a legacy source.
    """
    raw = _read_json_from_zip(zf, LEGACY_REST_RESULT_SUMMARY_PATH)
    if not isinstance(raw, dict):
        return None
    return {
        "source_artifact": LEGACY_REST_RESULT_SUMMARY_PATH,
        "status": raw.get("status") or "unknown",
        "solver": raw.get("solver") or raw.get("solver_name") or "legacy_rest_simulation_runner",
        "load_case_id": raw.get("load_case_id") or "legacy_rest",
        "max_von_mises_stress_mpa": raw.get("von_mises_max_mpa"),
        "max_displacement_mm": raw.get("displacement_max_mm"),
        "minimum_safety_factor": raw.get("minimum_safety_factor"),
        "raw": raw,
    }


def _legacy_rest_summary_to_computed_metrics(
    legacy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Convert legacy REST scalar fields to the computed-metrics shape.

    The conversion is deliberately conservative: only successful legacy results
    with explicit numeric values become metrics, and the source stays
    ``legacy_rest_result_summary`` so downstream consumers do not confuse it with
    MCP ``cae.run_solver`` evidence.
    """
    if not legacy or legacy.get("status") != "success":
        return None

    metrics: dict[str, dict[str, Any]] = {}
    stress = legacy.get("max_von_mises_stress_mpa")
    if isinstance(stress, NUMERIC_TYPES_COMPAT):
        metrics["max_von_mises_stress"] = {
            "value": float(stress),
            "unit": "MPa",
            "source_artifact": LEGACY_REST_RESULT_SUMMARY_PATH,
        }
    displacement = legacy.get("max_displacement_mm")
    if isinstance(displacement, NUMERIC_TYPES_COMPAT):
        metrics["max_displacement"] = {
            "value": float(displacement),
            "unit": "mm",
            "source_artifact": LEGACY_REST_RESULT_SUMMARY_PATH,
        }
    safety_factor = legacy.get("minimum_safety_factor")
    if isinstance(safety_factor, NUMERIC_TYPES_COMPAT):
        metrics["minimum_safety_factor"] = {
            "value": float(safety_factor),
            "unit": None,
            "source_artifact": LEGACY_REST_RESULT_SUMMARY_PATH,
        }
    if not metrics:
        return None

    return {
        "schema_version": FRD_COMPUTED_METRICS_SCHEMA,
        "metrics_source": {
            "tool": "legacy_rest_simulation_runner",
            "software": legacy.get("solver"),
            "source_files": [LEGACY_REST_RESULT_SUMMARY_PATH],
        },
        "load_cases": [
            {
                "id": legacy.get("load_case_id") or "legacy_rest",
                "metrics": metrics,
                "source_artifact": LEGACY_REST_RESULT_SUMMARY_PATH,
            }
        ],
        "warnings": [
            "Metrics normalized from legacy simulation/results_summary.json; "
            "this is not MCP cae.run_solver execution evidence."
        ],
    }


def _read_solver_runs(zf: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Read solver-run metadata artifacts under simulation/runs/*."""
    runs: list[dict[str, Any]] = []
    for name in sorted(zf.namelist()):
        if not (name.startswith("simulation/runs/") and name.endswith("/solver_run.json")):
            continue
        data = _read_json_from_zip(zf, name)
        if not isinstance(data, dict):
            continue
        run = dict(data)
        run["source_artifact"] = name
        runs.append(run)
    return runs


def _solver_run_completed(run: dict[str, Any]) -> bool:
    status = str(run.get("state") or run.get("status") or "").lower()
    return status == "completed" and run.get("solved") is True


def _build_result_contract(
    *,
    namelist: set[str],
    computed_values: dict[str, Any],
    solver_runs: list[dict[str, Any]],
    legacy_rest_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the normalized solver-result contract block.

    Claim tiers are intentionally plain strings so readers can make honest UI
    choices without inferring too much from artifact presence.
    """
    completed_runs = [run for run in solver_runs if _solver_run_completed(run)]
    source_artifacts: list[str] = []
    if RESULT_SUMMARY_PATH in namelist:
        source_artifacts.append(RESULT_SUMMARY_PATH)
    if computed_values.get("source"):
        source_artifacts.append(str(computed_values["source"]))
    if legacy_rest_summary:
        source_artifacts.append(LEGACY_REST_RESULT_SUMMARY_PATH)
    source_artifacts.extend(run["source_artifact"] for run in solver_runs if run.get("source_artifact"))

    metrics_source = computed_values.get("source")
    if completed_runs:
        claim_tier = "executed_solver_result"
        reason = "Completed solver_run.json evidence is present."
    elif computed_values.get("extrema_computed") and metrics_source != LEGACY_REST_RESULT_SUMMARY_PATH:
        claim_tier = "imported_computed_metrics"
        reason = "Computed metrics exist, but no completed solver_run.json evidence is present."
    elif legacy_rest_summary and legacy_rest_summary.get("status") == "success":
        claim_tier = "legacy_rest_result"
        reason = "Only legacy REST simulation/results_summary.json success metadata is present."
    elif computed_values.get("extrema_computed"):
        claim_tier = "imported_computed_metrics"
        reason = "Computed metrics exist, but no completed solver_run.json evidence is present."
    else:
        claim_tier = "missing_or_unknown"
        reason = "No completed solver-run evidence or normalized metrics were found."

    return {
        "schema_version": "0.1",
        "canonical_summary_path": RESULT_SUMMARY_PATH,
        "legacy_rest_summary_path": (
            LEGACY_REST_RESULT_SUMMARY_PATH if legacy_rest_summary else None
        ),
        "claim_tier": claim_tier,
        "solver_execution_evidence": bool(completed_runs),
        "completed_solver_run_ids": [
            str(run.get("run_id") or Path(str(run.get("source_artifact"))).parent.name)
            for run in completed_runs
        ],
        "metrics_source": metrics_source,
        "source_artifacts": sorted(set(source_artifacts)),
        "reason": reason,
    }


def _read_design_targets(zf: zipfile.ZipFile) -> dict[str, Any] | None:
    """Read task/design_targets.yaml. Return None when absent or malformed."""
    path = "task/design_targets.yaml"
    if path not in zf.namelist():
        return None
    try:
        raw = yaml.safe_load(zf.read(path).decode("utf-8", errors="replace"))
    except Exception:
        return {"malformed": True, "targets": []}
    return raw if isinstance(raw, dict) else {"malformed": True, "targets": []}


def _compare_design_targets(
    design_targets: dict[str, Any] | None,
    computed_values: dict[str, Any],
) -> dict[str, Any]:
    """Compare explicit targets against imported computed values.

    ``met`` is ``unknown`` when the required metric is absent. This keeps target
    reporting honest: absence of evidence is not compliance and not failure.
    """
    if not design_targets:
        return {"present": False, "target_count": 0, "items": []}

    items: list[dict[str, Any]] = []
    for target in design_targets.get("targets", []) if isinstance(design_targets.get("targets"), list) else []:
        if not isinstance(target, dict):
            continue
        # Support both legacy (id/metric/operator/value) and modern
        # (target_id/target_type/comparator/threshold) field names.
        target_id = target.get("target_id") or target.get("id") or "target"
        metric = target.get("target_type") or target.get("metric")
        operator = target.get("comparator") or target.get("operator")
        target_value = target.get("threshold") if target.get("threshold") is not None else target.get("value")
        actual = _actual_metric_value(metric, computed_values)
        met: bool | str
        if actual is None or not isinstance(target_value, NUMERIC_TYPES_COMPAT):
            met = "unknown"
        else:
            met = _compare_numeric(float(actual), str(operator), float(target_value))
        items.append({
            "id": target_id,
            "metric": metric,
            "operator": operator,
            "target_value": target_value,
            "actual_value": actual,
            "unit": target.get("unit"),
            "met": met,
            "source": _metric_source(metric, computed_values),
        })

    return {
        "present": True,
        "target_count": len(items),
        "items": items,
        "warnings": ["task/design_targets.yaml is malformed"] if design_targets.get("malformed") else [],
    }


NUMERIC_TYPES_COMPAT = (int, float)


def _actual_metric_value(metric: Any, computed_values: dict[str, Any]) -> float | None:
    if not isinstance(metric, str):
        return None
    mapping = {
        "max_von_mises_stress": "max_von_mises_stress",
        "maximum_von_mises_stress": "max_von_mises_stress",
        "max_displacement": "max_displacement",
        "maximum_displacement": "max_displacement",
        "minimum_safety_factor": "minimum_safety_factor",
        "total_mass": "total_mass",
        "absolute_mass_target": "total_mass",
        "mass_reduction_percent": "mass_reduction_percent",
        "mass_reduction_target": "mass_reduction_percent",
    }
    key = mapping.get(metric)
    if key is None:
        return None
    value = computed_values.get(key)
    if isinstance(value, dict):
        raw = value.get("value")
        return float(raw) if isinstance(raw, NUMERIC_TYPES_COMPAT) else None
    return float(value) if isinstance(value, NUMERIC_TYPES_COMPAT) else None


def _metric_source(metric: Any, computed_values: dict[str, Any]) -> str | None:
    if _actual_metric_value(metric, computed_values) is None:
        return None
    return computed_values.get("source") or "results/computed_metrics.json"


def _compare_numeric(actual: float, operator: str, target: float) -> bool | str:
    if operator == "<=":
        return actual <= target
    if operator == ">=":
        return actual >= target
    if operator == "<":
        return actual < target
    if operator == ">":
        return actual > target
    if operator == "==":
        return actual == target
    return "unknown"


# ---------------------------------------------------------------------------
# Phase 35 PR 2 — structured design target comparison emission
# ---------------------------------------------------------------------------
#
# These helpers produce the ``design_target_comparisons`` block declared by
# ``schemas/design_target_comparison.schema.json``. They are additive: the
# existing ``_compare_design_targets`` helper and ``result["targets"]`` block
# are preserved verbatim for backward compatibility with consumers that
# already depend on the old shape.

_DESIGN_TARGET_QUANTITATIVE_TYPES = frozenset(
    {
        "minimum_safety_factor",
        "maximum_von_mises_stress",
        "maximum_displacement",
        "absolute_mass_target",
        "mass_reduction_target",
        # legacy metric names
        "max_von_mises_stress",
        "max_displacement",
        "total_mass",
        "mass_reduction_percent",
    }
)
_DESIGN_TARGET_POLICY_TYPES = frozenset({"objective_priority", "preserved_interface"})


def _normalize_design_target(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge legacy and modern target fields into a single internal shape.

    Legacy fields (Phase 35 PR 0 schema):
        ``id``, ``metric``, ``operator``, ``value``

    Modern fields (Phase 35 PR 1 schema):
        ``target_id``, ``target_type``, ``comparator``, ``threshold``

    Either form is acceptable; modern fields win when both are populated. The
    returned dict contains every documented attribute so downstream helpers
    do not need to re-check both naming styles.
    """
    target_id = raw.get("target_id") or raw.get("id") or "target"
    target_type = raw.get("target_type") or raw.get("metric")
    comparator = raw.get("comparator") or raw.get("operator")
    threshold = raw.get("threshold")
    if threshold is None:
        threshold = raw.get("value")
    return {
        "target_id": str(target_id),
        "target_type": target_type if isinstance(target_type, str) else None,
        "comparator": comparator if isinstance(comparator, str) else None,
        "threshold": threshold if isinstance(threshold, NUMERIC_TYPES_COMPAT) else None,
        "threshold_min": raw.get("threshold_min")
        if isinstance(raw.get("threshold_min"), NUMERIC_TYPES_COMPAT)
        else None,
        "threshold_max": raw.get("threshold_max")
        if isinstance(raw.get("threshold_max"), NUMERIC_TYPES_COMPAT)
        else None,
        "unit": raw.get("unit") if isinstance(raw.get("unit"), str) else None,
        "priority": raw.get("priority") if isinstance(raw.get("priority"), str) else None,
        "scope": raw.get("scope") if isinstance(raw.get("scope"), str) else None,
        "baseline_ref": raw.get("baseline_ref")
        if isinstance(raw.get("baseline_ref"), str)
        else None,
        "evidence_refs": [
            r for r in (raw.get("evidence_refs") or []) if isinstance(r, str)
        ],
        "protected_features": [
            f for f in (raw.get("protected_features") or []) if isinstance(f, dict)
        ],
        "protected_interfaces": [
            i for i in (raw.get("protected_interfaces") or []) if isinstance(i, dict)
        ],
        "objective_order": [
            o for o in (raw.get("objective_order") or []) if isinstance(o, str)
        ],
        "notes": raw.get("notes") if isinstance(raw.get("notes"), str) else None,
    }


def _apply_design_target_comparator(
    normalized: dict[str, Any],
    actual: float,
) -> str:
    """Evaluate the comparator for a quantitative target. Returns
    ``pass`` / ``fail`` / ``unknown``.

    ``unknown`` is returned when the comparator requires thresholds that are
    not present in the normalized target — for example ``within_range``
    without ``threshold_min`` or ``threshold_max``.
    """
    comparator = normalized.get("comparator")
    threshold = normalized.get("threshold")
    if comparator == "within_range":
        lo = normalized.get("threshold_min")
        hi = normalized.get("threshold_max")
        if lo is None or hi is None:
            return "unknown"
        return "pass" if lo <= actual <= hi else "fail"
    if comparator == "reduce_by_at_least":
        # actual is the achieved reduction percentage (mass_reduction_percent
        # via the existing _actual_metric_value mapping). Pass when it meets
        # or exceeds the declared threshold.
        if threshold is None:
            return "unknown"
        return "pass" if actual >= threshold else "fail"
    if threshold is None:
        return "unknown"
    if comparator == "<=":
        return "pass" if actual <= threshold else "fail"
    if comparator == "<":
        return "pass" if actual < threshold else "fail"
    if comparator == ">=":
        return "pass" if actual >= threshold else "fail"
    if comparator == ">":
        return "pass" if actual > threshold else "fail"
    if comparator == "==":
        return "pass" if actual == threshold else "fail"
    return "unknown"


def _evaluate_design_target(
    normalized: dict[str, Any],
    computed_values: dict[str, Any],
    artifact_presence: dict[str, bool],
) -> dict[str, Any]:
    """Evaluate one normalized target and return one ``items[]`` entry."""
    target_id = normalized["target_id"]
    target_type = normalized["target_type"] or ""
    comparator = normalized["comparator"] or ""

    expected: dict[str, Any] = {"comparator": comparator}
    if normalized["threshold"] is not None:
        expected["threshold"] = normalized["threshold"]
    if normalized["threshold_min"] is not None:
        expected["threshold_min"] = normalized["threshold_min"]
    if normalized["threshold_max"] is not None:
        expected["threshold_max"] = normalized["threshold_max"]
    if normalized["objective_order"]:
        expected["objective_order"] = list(normalized["objective_order"])

    actual: dict[str, Any] = {"value": None}
    source_artifacts: list[str] = []
    notes_parts: list[str] = []
    status = "not_evaluated"

    if target_type == "objective_priority":
        status = "not_evaluated"
        notes_parts.append(
            "Policy ordering target; not numerically evaluated in this phase."
        )
    elif target_type == "preserved_interface":
        if artifact_presence.get("graph/feature_graph.json"):
            status = "unknown"
            source_artifacts.append("graph/feature_graph.json")
            notes_parts.append(
                "Feature graph present; before/after diff evidence required to "
                "evaluate preservation in this phase."
            )
        else:
            status = "not_evaluated"
            notes_parts.append(
                "No graph/feature_graph.json evidence artifact found; "
                "preservation not evaluated."
            )
    elif target_type in _DESIGN_TARGET_QUANTITATIVE_TYPES:
        if not artifact_presence.get("results/computed_metrics.json", False):
            status = "not_evaluated"
            notes_parts.append(
                "No results/computed_metrics.json evidence artifact found."
            )
        else:
            source_artifacts.append("results/computed_metrics.json")
            value = _actual_metric_value(target_type, computed_values)
            if value is None:
                status = "unknown"
                notes_parts.append(
                    f"Metric for target_type '{target_type}' not present in "
                    "results/computed_metrics.json."
                )
            else:
                actual = {
                    "value": value,
                    "unit": normalized["unit"],
                    "source_artifact": "results/computed_metrics.json",
                }
                status = _apply_design_target_comparator(normalized, float(value))
                if status == "unknown":
                    notes_parts.append(
                        "Comparator could not be evaluated against the declared "
                        "thresholds."
                    )
    else:
        status = "not_evaluated"
        if target_type:
            notes_parts.append(
                f"Unsupported target_type '{target_type}'; not evaluated."
            )
        else:
            notes_parts.append("Target has no target_type / metric; not evaluated.")

    item: dict[str, Any] = {
        "target_id": target_id,
        "target_type": target_type,
        "expected": expected,
        "actual": actual,
        "comparator": comparator,
        "status": status,
    }
    if normalized["evidence_refs"]:
        item["evidence_refs"] = list(normalized["evidence_refs"])
    if source_artifacts:
        item["source_artifacts"] = source_artifacts
    if notes_parts:
        item["notes"] = " ".join(notes_parts)
    return item


def _build_design_target_comparisons(
    design_targets: dict[str, Any] | None,
    computed_values: dict[str, Any],
    artifact_presence: dict[str, bool],
) -> dict[str, Any]:
    """Produce the design_target_comparisons block declared by
    ``schemas/design_target_comparison.schema.json``.

    Behaviour:

    - Returns ``present: false`` with empty items when no targets exist or the
      design_targets file is malformed.
    - Walks every well-formed target through ``_normalize_design_target`` and
      ``_evaluate_design_target``, then aggregates status counts.
    - ``evaluated_at`` is filled with the current UTC time in ISO 8601 form.
    - ``target_set_id`` is propagated from the design_targets dict when present.

    This function never mutates ``claim_map.json`` or any other artifact.
    """
    empty_summary = {"total": 0, "pass": 0, "fail": 0, "unknown": 0, "not_evaluated": 0}
    if not design_targets or design_targets.get("malformed"):
        return {"present": False, "summary": empty_summary, "items": []}

    raw_targets = design_targets.get("targets")
    if not isinstance(raw_targets, list):
        return {"present": False, "summary": empty_summary, "items": []}

    items: list[dict[str, Any]] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_design_target(raw)
        items.append(_evaluate_design_target(normalized, computed_values, artifact_presence))

    summary_counts: dict[str, int] = {
        "total": len(items),
        "pass": 0,
        "fail": 0,
        "unknown": 0,
        "not_evaluated": 0,
    }
    for it in items:
        s = it.get("status")
        if isinstance(s, str) and s in summary_counts:
            summary_counts[s] += 1

    out: dict[str, Any] = {
        "present": bool(items),
        "summary": summary_counts,
        "items": items,
    }
    target_set_id = design_targets.get("target_set_id")
    if isinstance(target_set_id, str) and target_set_id:
        out["target_set_id"] = target_set_id
    out["evaluated_at"] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return out


def _merge_load_cases_with_metrics(
    load_cases: list[dict[str, Any]],
    computed_metrics: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach computed metrics to matching load cases.

    If a computed metric load case does not match any simulation load case,
    it is appended as a result-only entry.
    """
    if not computed_metrics or not computed_metrics.get("load_cases"):
        return load_cases

    # Build lookup by id
    by_id: dict[str, dict[str, Any]] = {lc["id"]: lc for lc in load_cases}
    merged = list(load_cases)
    seen_ids = set(by_id.keys())

    for cm_lc in computed_metrics["load_cases"]:
        if not isinstance(cm_lc, dict):
            continue
        lc_id = cm_lc.get("id") or "unknown"
        metrics = cm_lc.get("metrics") or {}
        if lc_id in by_id:
            by_id[lc_id]["metrics"] = metrics
        else:
            merged.append({
                "id": lc_id,
                "name": lc_id,
                "type": "unknown",
                "magnitude": None,
                "unit": None,
                "description": None,
                "source_file": "results/computed_metrics.json",
                "metrics": metrics,
            })
            seen_ids.add(lc_id)

    return merged


def _build_computed_values(
    computed_metrics: dict[str, Any] | None,
    load_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the computed_values block from imported metrics.

    If no valid metrics are present, returns the honest Phase 4/5 default.
    """
    if not computed_metrics or computed_metrics.get("malformed"):
        return {
            "extrema_computed": False,
            "max_displacement": None,
            "max_von_mises_stress": None,
            "minimum_safety_factor": None,
        }

    # Extract top-level metrics from the first load case that has them,
    # or aggregate across load cases for the same metric key.
    top_metrics: dict[str, Any] = {}
    by_load_case: list[dict[str, Any]] = []

    for lc in load_cases:
        metrics = lc.get("metrics") or {}
        if not metrics:
            continue
        entry: dict[str, Any] = {"id": lc["id"], "metrics": {}}
        for key in (
            "max_von_mises_stress",
            "max_displacement",
            "minimum_safety_factor",
            "total_mass",
            "mass_reduction_percent",
        ):
            m = metrics.get(key)
            if m and isinstance(m, dict) and m.get("value") is not None:
                entry["metrics"][key] = m
                # Top-level: keep first occurrence
                if key not in top_metrics:
                    top_metrics[key] = m
        if entry["metrics"]:
            by_load_case.append(entry)

    if not top_metrics:
        return {
            "extrema_computed": False,
            "max_displacement": None,
            "max_von_mises_stress": None,
            "minimum_safety_factor": None,
        }

    metrics_source = computed_metrics.get("metrics_source", {})
    source_files = metrics_source.get("source_files") if isinstance(metrics_source, dict) else None
    source = source_files[0] if isinstance(source_files, list) and source_files else "results/computed_metrics.json"

    return {
        "extrema_computed": True,
        "source": source,
        "computed_by": (metrics_source.get("tool") if isinstance(metrics_source, dict) else None) or "external_postprocessor",
        "metrics_source": metrics_source,
        "by_load_case": by_load_case,
        "max_displacement": top_metrics.get("max_displacement"),
        "max_von_mises_stress": top_metrics.get("max_von_mises_stress"),
        "minimum_safety_factor": top_metrics.get("minimum_safety_factor"),
        "total_mass": top_metrics.get("total_mass"),
        "mass_reduction_percent": top_metrics.get("mass_reduction_percent"),
    }
