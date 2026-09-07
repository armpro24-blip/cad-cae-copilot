"""CAE field, audit, manifest, consistency, claim, and evidence routes."""

from __future__ import annotations

import json
import logging
import sys
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from ..cad_generation import load_cad_brief
from ..computed_metrics import get_computed_metrics
from ..config import now_iso
from ..legacy_app_symbols import sync_main_symbols
from ..logging_utils import log_exception
from ..target_comparison import compare_package_targets

LOGGER = logging.getLogger("app.app_factory")

# Synthetic fallback metadata for every selectable CAE result field.
# Real FRD data overrides these min/max values; this map only ensures
# honest units and colormaps when no solver result is present yet.
_FIELD_SYNTHETIC_DEFAULTS: dict[str, dict[str, Any]] = {
    # Von Mises stress and legacy alias
    "von_mises": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    "stress": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    # Stress tensor components (signed)
    "sxx": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    "syy": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    "szz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    "sxy": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    "sxz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    "syz": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "coolwarm"},
    # Principal stresses and derived scalar stress measures
    "s1": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    "s2": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    "s3": {"min_value": -250.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    "tresca": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    "max_shear": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
    # Displacement magnitude and legacy alias
    "disp_magnitude": {"min_value": 0.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
    "displacement": {"min_value": 0.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
    # Per-axis displacement components (signed)
    "ux": {"min_value": -5.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
    "uy": {"min_value": -5.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
    "uz": {"min_value": -5.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
    # Safety factor (yield / von Mises)
    "safety_factor": {"min_value": 0.0, "max_value": 10.0, "unit": "", "colormap": "thermal"},
}


def _package_solver_evidence(package_path: Path | None) -> dict[str, Any]:
    """The package's own solver evidence, or `{}` when it cannot be read.

    `{}` (rather than fabricated flags) keeps every signal at `None`, which the
    classifier treats as "nothing recorded" — the honest answer when there is no
    package to ask.
    """
    if package_path is None or not package_path.exists():
        return {}
    try:
        from aieng.cae_result_summary import read_solver_evidence  # type: ignore[import]

        with zipfile.ZipFile(package_path, "r") as zf:
            return dict(read_solver_evidence(zf))
    except Exception:  # noqa: BLE001 - an unreadable package is not a field error
        log_exception(
            LOGGER,
            "Could not read solver evidence for a field credibility stamp.",
            subsystem="evidence.field_credibility",
            context={"package": str(package_path)},
        )
        return {}


def _field_credibility(
    source: str, aieng_root: Path, *, package_path: Path | None = None
) -> dict[str, Any]:
    """Return the V&V-40 credibility stamp for a field descriptor response.

    FRD-backed fields are stamped as ``executed_solver_result``; imported VTU
    fields are external result evidence but not proof that this workbench ran the
    solver; synthetic fallbacks are downgraded to ``unverified``.

    The FRD branch reads the package's mesh and geometry evidence so the same
    invariants that guard the result summary guard this stamp. Without it a field
    served off an ``unreliable`` mesh, or off a run superseded by a geometry
    edit, came back rank 4 while the summary for that very package said
    ``unreliable_mesh`` — two surfaces of one classifier disagreeing about
    honesty is a defect in the one that claims more.
    """
    aieng_src = aieng_root / "src"
    injected = False
    if str(aieng_src) not in sys.path:
        sys.path.insert(0, str(aieng_src))
        injected = True
    try:
        from aieng.converters.credibility import classify_credibility  # type: ignore[import]

        if source == "frd":
            evidence = _package_solver_evidence(package_path)
            return classify_credibility(
                "solver",
                # `True` rests on the parsed FRD this branch was reached with —
                # solver output read out of this package, not an assertion. The
                # mesh and geometry questions it cannot answer come from the
                # package's own evidence reader.
                solver_executed=True,
                is_solver_evidence=True,
                mesh_accuracy_band=evidence.get("mesh_accuracy_band"),
                mesh_accuracy_judged=evidence.get("mesh_accuracy_judged"),
                geometry_stale=evidence.get("geometry_stale"),
                notes="Per-node FRD data extracted from an executed solver run.",
            )
        if source == "vtu":
            return classify_credibility(
                "solver",
                solver_executed=False,
                is_solver_evidence=True,
                notes="Per-node VTU data imported from an external result; solver execution was not observed by this workbench.",
            )
        return classify_credibility(
            "solver",
            solver_executed=False,
            notes="Synthetic fallback; no solver result available for this field.",
        )
    finally:
        if injected:
            try:
                sys.path.remove(str(aieng_src))
            except ValueError:
                pass


def _sync_main_symbols() -> None:
    sync_main_symbols(globals())


def _unsupported_frd_reason(package_path: Path) -> str | None:
    """Return a reason when the newest FRD is present but not text-parseable."""
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            candidates = sorted(
                name for name in zf.namelist()
                if name.endswith("/outputs/result.frd")
            )
            if not candidates:
                return None
            raw = zf.read(candidates[-1])
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    if b"\x00" in raw:
        return "binary or non-UTF-8 FRD files are unsupported by the field viewer; provide a CalculiX text FRD export"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "binary or non-UTF-8 FRD files are unsupported by the field viewer; provide a CalculiX text FRD export"
    return None


def _unsupported_frd_artifacts(package_path: Path) -> dict[str, str]:
    """Return package FRD paths that cannot be decoded by the field viewer."""
    unsupported: dict[str, str] = {}
    message = (
        "binary or non-UTF-8 FRD files are unsupported by the field viewer; "
        "provide a CalculiX text FRD export"
    )
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            candidates = sorted(
                name for name in zf.namelist()
                if name.endswith(".frd") or name.endswith("/outputs/result.frd")
            )
            for name in candidates:
                raw = zf.read(name)
                if b"\x00" in raw:
                    unsupported[name] = message
                    continue
                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    unsupported[name] = message
    except (KeyError, zipfile.BadZipFile, OSError):
        return unsupported
    return unsupported


def _read_evidence_index_entries(
    package_path: Path,
    pkg_names: set[str],
) -> list[dict[str, Any]]:
    if "results/evidence_index.json" not in pkg_names:
        return []
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            raw = json.loads(zf.read("results/evidence_index.json"))
    except (json.JSONDecodeError, KeyError, zipfile.BadZipFile, OSError):
        return []
    entries = raw.get("entries") if isinstance(raw, dict) else None
    return [entry for entry in (entries or []) if isinstance(entry, dict)]


def _claim_support_paths(proposals: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for proposal in proposals:
        for path in proposal.get("supporting_evidence") or []:
            if isinstance(path, str) and path.strip():
                paths.add(path.strip())
    return paths


def _missing_expected_evidence_items(pkg_names: set[str]) -> list[dict[str, Any]]:
    has_geometry_file = any(
        name.startswith("geometry/") and name.lower().endswith((".step", ".stp", ".iges", ".glb", ".stl"))
        for name in pkg_names
    )
    has_solver_run = any(
        name.startswith("simulation/runs/") and name.endswith("solver_run.json")
        for name in pkg_names
    )
    has_solver_input = any(
        name.startswith("simulation/runs/") and name.endswith("solver_input.inp")
        for name in pkg_names
    )
    has_frd = any(
        name.startswith("simulation/runs/") and name.endswith(".frd")
        for name in pkg_names
    )

    expected: list[tuple[bool, str, str, str, str]] = [
        (has_geometry_file, "geometry/*.{step,stp,iges,glb,stl}", "geometry", "geometry", "compiled CAD geometry"),
        ("geometry/topology_map.json" in pkg_names, "geometry/topology_map.json", "topology_map", "geometry", "topology pointer map"),
        ("graph/feature_graph.json" in pkg_names, "graph/feature_graph.json", "feature_graph", "geometry", "editable feature graph"),
        ("simulation/setup.yaml" in pkg_names or "simulation/setup.json" in pkg_names, "simulation/setup.{yaml,json}", "cae_setup", "cae_setup", "CAE setup artifact"),
        (has_solver_input, "simulation/runs/*/solver_input.inp", "solver_input", "solver_output", "solver deck"),
        (has_solver_run, "simulation/runs/*/solver_run.json", "solver_run_metadata", "solver_output", "solver run metadata"),
        (has_frd, "simulation/runs/*/outputs/*.frd", "solver_raw_output", "solver_output", "raw solver field output"),
        ("results/computed_metrics.json" in pkg_names, "results/computed_metrics.json", "result", "solver_output", "computed result metrics"),
        ("results/evidence_index.json" in pkg_names, "results/evidence_index.json", "evidence_index", "evidence_index", "evidence index"),
        ("results/result_summary.json" in pkg_names, "results/result_summary.json", "cae_result_summary", "summary", "readable result summary"),
    ]

    missing: list[dict[str, Any]] = []
    for exists, path, kind, category, description in expected:
        if exists:
            continue
        missing.append({
            "path": path,
            "kind": kind,
            "category": category,
            "exists": False,
            "primary_state": "missing",
            "lifecycle_states": ["missing"],
            "description": description,
            "reason": "expected_evidence_not_found",
            "claim_advancement": "none",
        })
    return missing


def _rollup_lifecycle_status(counts: dict[str, int]) -> str:
    if counts.get("unsupported", 0) or counts.get("stale", 0) or counts.get("missing", 0):
        return "warning"
    if counts.get("current", 0) or counts.get("claim_supporting", 0):
        return "ok"
    return "not_evaluated"


def _build_evidence_lifecycle_summary(
    package_path: Path,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build a read-only evidence lifecycle rollup from package artifacts."""
    manifest = _generate_artifact_manifest(package_path)
    rs = _read_revalidation_status(package_path)
    revalidation = _build_revalidation_response(rs)
    unsupported_frd = _unsupported_frd_artifacts(package_path)

    with zipfile.ZipFile(package_path, "r") as zf:
        pkg_names: set[str] = {name for name in zf.namelist() if not name.endswith("/")}

    evidence_entries = _read_evidence_index_entries(package_path, pkg_names)
    proposals = _read_claim_proposals_from_package(package_path)
    supporting_paths = _claim_support_paths(proposals)

    counts: dict[str, int] = {
        "current": 0,
        "stale": 0,
        "unsupported": 0,
        "claim_supporting": 0,
        "missing": 0,
    }
    items: list[dict[str, Any]] = []
    stale_items: list[dict[str, Any]] = []
    unsupported_items: list[dict[str, Any]] = []
    claim_supporting_items: list[dict[str, Any]] = []

    for artifact in manifest.get("artifacts") or []:
        path = artifact.get("path")
        if not isinstance(path, str) or not path:
            continue
        resolved = _resolve_evidence_reference(
            path=path,
            pkg_names=pkg_names,
            evidence_entries=evidence_entries,
            revalidation_status=rs,
        )
        states: list[str] = []
        if path in unsupported_frd:
            primary = "unsupported"
            states.append(primary)
            counts["unsupported"] += 1
        elif "evidence_from_stale_geometry_state" in resolved.get("warnings", []):
            primary = "stale"
            states.append(primary)
            counts["stale"] += 1
        else:
            primary = "current"
            states.append(primary)
            counts["current"] += 1

        supports_claim_proposal = path in supporting_paths
        if supports_claim_proposal:
            states.append("claim_supporting")
            counts["claim_supporting"] += 1

        item = {
            "path": path,
            "kind": artifact.get("kind"),
            "category": artifact.get("category"),
            "exists": True,
            "primary_state": primary,
            "lifecycle_states": states,
            "evidence_role": resolved.get("evidence_role") or artifact.get("evidence_role"),
            "in_evidence_index": resolved.get("in_evidence_index"),
            "supports_claim_proposal": supports_claim_proposal,
            "usable_for_claim_proposal": bool(resolved.get("usable_for_claim_proposal")) and primary != "unsupported",
            "warnings": resolved.get("warnings") or [],
            "unsupported_reason": unsupported_frd.get(path),
            "claim_advancement": "none",
        }
        if primary == "stale":
            stale_items.append(item)
        if primary == "unsupported":
            unsupported_items.append(item)
        if supports_claim_proposal:
            claim_supporting_items.append(item)
        items.append(item)

    missing_items = _missing_expected_evidence_items(pkg_names)
    counts["missing"] += len(missing_items)
    items.extend(missing_items)

    return {
        "schema_version": "0.1",
        "project_id": project_id,
        "generated_at": now_iso(),
        "claim_advancement": "none",
        "status": _rollup_lifecycle_status(counts),
        "summary": counts,
        "governance": {
            "automatic_claim_advancement": False,
            "claim_advancement_requires_explicit_review": True,
            "stale_evidence_may_support_draft_proposals_only": True,
            "unsupported_evidence_is_not_claim_support": True,
        },
        "revalidation_status": revalidation,
        "claim_proposal_count": len(proposals),
        "evidence": items,
        "stale_evidence": stale_items,
        "unsupported_evidence": unsupported_items,
        "claim_supporting_evidence": claim_supporting_items,
        "missing_evidence": missing_items,
    }


def register_evidence_routes(app: FastAPI, *, active_settings: Any) -> None:
    _sync_main_symbols()

    @app.get("/api/projects/{project_id}/fields/{field_name}")
    def get_field_descriptor(
        project_id: str,
        field_name: str,
        load_case_id: str | None = None,
    ) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        meta = _FIELD_SYNTHETIC_DEFAULTS.get(
            field_name, {"min_value": 0.0, "max_value": 1.0, "unit": "", "colormap": "thermal"}
        )

        # Attempt real FRD extraction for the requested load case / step
        pkg = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        frd_data: dict[str, Any] | None = None
        if pkg is not None and pkg.exists():
            unsupported_frd = _unsupported_frd_reason(pkg)
            if unsupported_frd:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unsupported_frd_format",
                        "message": unsupported_frd,
                        "field_name": field_name,
                        "source": "frd",
                    },
                )
            try:
                frd_data = _extract_frd_field_data(
                    pkg, field_name, active_settings.aieng_root, load_case_id=load_case_id
                )
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to extract FRD field data; using synthetic field descriptor fallback.",
                    subsystem="app_factory.field_descriptor.read_frd",
                    context={"project_id": project_id, "field_name": field_name},
                )
                frd_data = None

        if frd_data is not None:
            return {
                "field_name": field_name,
                "project_id": project_id,
                "load_case_id": load_case_id or "load_case_001",
                "format": "vertex_json",
                "basis": "frd_nearest_node",
                "min_value": frd_data["min_value"],
                "max_value": frd_data["max_value"],
                "unit": frd_data["unit"],
                "colormap": meta["colormap"],
                "source": "frd",
                "values": frd_data["values"],
                "node_coords": frd_data["node_coords"],
                "vectors": frd_data.get("vectors"),
                "warnings": frd_data["warnings"],
                "credibility": _field_credibility(
                    "frd", active_settings.aieng_root, package_path=pkg
                ),
            }

        # No FRD — try an external VTU result (Code_Aster / ParaView / Elmer export).
        # Additive: leaves the FRD path untouched and only runs when FRD is absent.
        if pkg is not None and pkg.exists():
            try:
                from .. import vtu_importer

                vtu_data = vtu_importer.extract_vtu_field(pkg, field_name)
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to extract VTU field data; using synthetic field descriptor fallback.",
                    subsystem="app_factory.field_descriptor.read_vtu",
                    context={"project_id": project_id, "field_name": field_name},
                )
                vtu_data = None
            if vtu_data is not None:
                return {
                    "field_name": field_name,
                    "project_id": project_id,
                    "load_case_id": load_case_id or "load_case_001",
                    "format": "vertex_json",
                    "basis": "vtu_point_data",
                    "min_value": vtu_data["min_value"],
                    "max_value": vtu_data["max_value"],
                    "unit": vtu_data["unit"] or meta["unit"],
                    "colormap": meta["colormap"],
                    "source": "vtu",
                    "values": vtu_data["values"],
                    "node_coords": vtu_data["node_coords"],
                    "warnings": vtu_data["warnings"],
                    "credibility": _field_credibility("vtu", active_settings.aieng_root),
                }

        # Fallback to synthetic
        return {
            "field_name": field_name,
            "project_id": project_id,
            "load_case_id": load_case_id or "load_case_001",
            "format": "vertex_synthetic",
            "basis": "y_normalized",
            "min_value": meta["min_value"],
            "max_value": meta["max_value"],
            "unit": meta["unit"],
            "colormap": meta["colormap"],
            "source": "synthetic_mock",
            "credibility": _field_credibility("synthetic", active_settings.aieng_root),
        }

    @app.get("/api/projects/{project_id}/cae-result-fields")
    def list_cae_result_fields(project_id: str) -> dict[str, Any]:
        """List available CAE result fields from computed_metrics.json.

        Read-only. Does not execute solvers or advance claims.
        Returns compact metadata only; full per-node arrays are not served here.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        frd_source: str | None = _resolve_frd_in_package(package_path)
        unsupported_frd = _unsupported_frd_reason(package_path) if frd_source else None
        available_fields: list[dict[str, Any]] = []
        warnings: list[str] = []
        if unsupported_frd:
            warnings.append(unsupported_frd)

        computed_raw: dict[str, Any] | None = None
        try:
            with zipfile.ZipFile(package_path, "r") as _zf:
                if "results/computed_metrics.json" in _zf.namelist():
                    import json as _json
                    computed_raw = _json.loads(_zf.read("results/computed_metrics.json"))
        except Exception:
            log_exception(
                LOGGER,
                "Failed to read computed metrics while building field descriptor.",
                subsystem="app_factory.field_descriptor.read_computed_metrics",
                context={"project_id": project_id},
            )
            warnings.append("Could not read results/computed_metrics.json.")

        if computed_raw and isinstance(computed_raw, dict):
            first_metrics: dict[str, Any] = {}
            for lc in (computed_raw.get("load_cases") or []):
                if isinstance(lc, dict) and lc.get("metrics"):
                    first_metrics = lc["metrics"]
                    break
            for _fname, _fmeta in _CAE_RESULT_FIELDS.items():
                metric = first_metrics.get(_fmeta["metric_key"])
                if metric and isinstance(metric, dict) and metric.get("value") is not None:
                    available_fields.append({
                        "field_name": _fname,
                        "unit": metric.get("unit") or _fmeta["unit"],
                        "max_value": metric["value"],
                        "source_type": "computed_metrics",
                        "source_artifact": "results/computed_metrics.json",
                    })
        elif frd_source and not unsupported_frd:
            for _fname, _fmeta in _CAE_RESULT_FIELDS.items():
                available_fields.append({
                    "field_name": _fname,
                    "unit": _fmeta["unit"],
                    "max_value": None,
                    "source_type": "frd",
                    "source_artifact": frd_source,
                })
            warnings.append("computed_metrics.json absent; field availability inferred from FRD presence.")

        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "available_fields": available_fields,
            "frd_source": frd_source,
            "claim_advancement": "none",
            "revalidation_status": _build_revalidation_response(
                _read_revalidation_status(package_path)
            ),
            "warnings": warnings,
        }

    @app.get("/api/projects/{project_id}/cae-result-fields/{field_name}")
    def get_cae_result_field_summary(project_id: str, field_name: str) -> dict[str, Any]:
        """Compact summary statistics for a named CAE result field.

        Read-only. Does not serve full per-node arrays, execute solvers,
        or advance engineering claims.
        """
        if field_name not in _CAE_RESULT_FIELDS:
            raise HTTPException(
                status_code=404,
                detail=f"Field '{field_name}' not supported. Available: {sorted(_CAE_RESULT_FIELDS)}",
            )

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        _fmeta = _CAE_RESULT_FIELDS[field_name]
        frd_path_in_pkg = _resolve_frd_in_package(package_path)
        unsupported_frd = _unsupported_frd_reason(package_path) if frd_path_in_pkg else None
        warnings: list[str] = []
        if unsupported_frd:
            warnings.append(unsupported_frd)

        frd_stats: dict[str, Any] | None = None
        if frd_path_in_pkg is not None and not unsupported_frd:
            try:
                _raw = _extract_frd_field_data(package_path, field_name, active_settings.aieng_root)
                if _raw is not None:
                    import math as _math
                    frd_stats = {
                        "min_value": _raw["min_value"],
                        "max_value": _raw["max_value"],
                        "node_count": len(_raw["values"]),
                        "values_finite": all(_math.isfinite(v) for v in _raw["values"]),
                    }
                    warnings.extend(_raw.get("warnings") or [])
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to extract FRD statistics for field endpoint.",
                    subsystem="app_factory.field_descriptor.extract_frd_stats",
                    context={"project_id": project_id, "field_name": field_name},
                )
                warnings.append(f"FRD extraction failed for '{field_name}'.")

        cm_max_value: float | None = None
        cm_unit: str | None = None
        try:
            with zipfile.ZipFile(package_path, "r") as _zf:
                if "results/computed_metrics.json" in _zf.namelist():
                    import json as _json
                    _cm = _json.loads(_zf.read("results/computed_metrics.json"))
                    for _lc in (_cm.get("load_cases") or []):
                        _m = (_lc.get("metrics") or {}).get(_fmeta["metric_key"])
                        if _m and isinstance(_m, dict) and _m.get("value") is not None:
                            cm_max_value = _m["value"]
                            cm_unit = _m.get("unit") or _fmeta["unit"]
                            break
        except Exception:
            log_exception(
                LOGGER,
                "Failed to derive field descriptor values from computed metrics.",
                subsystem="app_factory.field_descriptor.computed_metric_lookup",
                context={"project_id": project_id, "field_name": field_name},
            )

        if frd_stats is None and cm_max_value is None:
            if unsupported_frd:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unsupported_frd_format",
                        "message": unsupported_frd,
                        "field_name": field_name,
                        "source": "frd",
                    },
                )
            raise HTTPException(
                status_code=404,
                detail=f"No result data for '{field_name}'. Run solver and extract results first.",
            )

        if frd_stats is not None:
            stats = frd_stats
            unit = _fmeta["unit"]
            source_type = "frd"
        else:
            stats = {"min_value": None, "max_value": cm_max_value, "node_count": None, "values_finite": None}
            unit = cm_unit or _fmeta["unit"]
            source_type = "computed_metrics"

        return {
            "schema_version": "0.1",
            "field_name": field_name,
            "unit": unit,
            "source": {
                "frd_path": frd_path_in_pkg,
                "source_type": source_type,
                "computed_metrics_path": "results/computed_metrics.json" if cm_max_value is not None else None,
            },
            "stats": stats,
            "evidence_role": _fmeta["evidence_role"],
            "claim_advancement": "none",
            "revalidation_status": _build_revalidation_response(
                _read_revalidation_status(package_path)
            ),
            "warnings": warnings,
        }

    @app.get("/api/projects/{project_id}/audit-events")
    def get_project_audit_events(project_id: str) -> dict[str, Any]:
        """Read the package runtime audit event log.

        Read-only. Returns events in append order (oldest first). Returns an
        empty list rather than 404 when no audit log exists yet. Does not
        execute solvers or advance claims.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        events = _read_audit_events_from_package(package_path)
        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "events": events,
            "count": len(events),
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/artifact-manifest")
    def get_project_artifact_manifest(project_id: str) -> dict[str, Any]:
        """Return a read-only manifest of all artifacts in the .aieng package.

        Classifies each artifact by kind and category. Annotates CAE result
        artifacts with revalidation/freshness context from the revalidation
        status artifact when present. Does not write to the package or advance
        any engineering claim.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return _generate_artifact_manifest(package_path)

    @app.get("/api/projects/{project_id}/evidence-lifecycle")
    def get_project_evidence_lifecycle(project_id: str) -> dict[str, Any]:
        """Return a read-only evidence lifecycle rollup for the .aieng package.

        Aggregates existing package artifacts into current, stale, unsupported,
        claim-supporting, and missing evidence states. This endpoint is purely
        observational: it never edits CAD/CAE artifacts, executes solvers, writes
        package members, or advances engineering claims.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return _build_evidence_lifecycle_summary(package_path, project_id=project_id)

    @app.get("/api/projects/{project_id}/package-consistency")
    def get_package_consistency(project_id: str) -> dict[str, Any]:
        """Run read-only consistency checks on the .aieng package metadata layers.

        Checks: (A) evidence index path coverage, (B) audit event artifact
        references, (C) field summary source traceability, (D) revalidation
        status consistency, (E) claim non-advancement. Does not mutate the
        package, execute solvers, or advance engineering claims.

        Stale state (requires_revalidation=True) is reported as 'warning', not
        'error' — stale state is valid while geometry edits are pending.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        checks = _run_package_consistency_checks(package_path)
        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "status": _rollup_check_status(checks),
            "claim_advancement": "none",
            "checks": checks,
        }

    @app.post("/api/projects/{project_id}/claim-proposals")
    def create_claim_proposal(
        project_id: str,
        body: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Create a claim proposal artifact in the .aieng package.

        Records a proposed claim update as an auditable artifact at
        claims/proposals/{proposal_id}.json. Does not accept the claim, does
        not create or modify claim maps, and does not advance any engineering
        claim. Supporting evidence must exist in the package or evidence index.

        Request: claim_id, proposed_status, supporting_evidence, rationale.
        proposed_status must be one of: supported, not_supported, needs_review.
        """
        data = body or {}
        claim_id = str(data.get("claim_id") or "").strip()
        proposed_status = str(data.get("proposed_status") or "").strip()
        supporting_evidence = data.get("supporting_evidence") or []
        rationale = str(data.get("rationale") or "").strip()

        errors = _validate_claim_proposal_request(
            claim_id=claim_id,
            proposed_status=proposed_status,
            supporting_evidence=supporting_evidence,
            rationale=rationale,
        )
        if errors:
            raise HTTPException(status_code=400, detail=errors[0])

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        with zipfile.ZipFile(package_path, "r") as zf:
            pkg_names: set[str] = set(zf.namelist())
            ei_raw = (
                zf.read("results/evidence_index.json")
                if "results/evidence_index.json" in pkg_names
                else None
            )

        evidence_entries: list[dict[str, Any]] = []
        if ei_raw:
            try:
                evidence_entries = json.loads(ei_raw).get("entries") or []
            except json.JSONDecodeError:
                pass

        rs = _read_revalidation_status(package_path)
        missing = [
            p for p in supporting_evidence
            if not _resolve_evidence_reference(
                path=p,
                pkg_names=pkg_names,
                evidence_entries=evidence_entries,
                revalidation_status=rs,
            )["usable_for_claim_proposal"]
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"supporting_evidence path(s) not found in package or evidence index: {missing}",
            )

        proposal = _build_claim_proposal(
            claim_id=claim_id,
            proposed_status=proposed_status,
            supporting_evidence=supporting_evidence,
            rationale=rationale,
        )
        proposal_path = _write_claim_proposal_to_package(package_path, proposal)

        try:
            _append_audit_event_to_package(
                package_path,
                _build_audit_event(
                    tool="claims.propose_update",
                    event_type="claim_proposal_created",
                    status="completed",
                    artifacts_written=[proposal_path],
                    evidence_created=[],
                    state_changes={
                        "claim_id": claim_id,
                        "proposed_status": proposed_status,
                    },
                    geometry_revision=None,
                    revalidation_status=None,
                ),
            )
        except Exception:
            log_exception(
                LOGGER,
                "Failed to write claim proposal audit artifact.",
                subsystem="app_factory.audit.claim_proposal",
                context={"project_id": project_id},
            )

        return {
            "schema_version": "0.1",
            "proposal": proposal,
            "proposal_path": proposal_path,
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/claim-proposals")
    def list_claim_proposals(project_id: str) -> dict[str, Any]:
        """List all claim proposal artifacts in the package.

        Read-only. Returns proposals sorted by created_at then proposal_id.
        Returns an empty list when no proposals exist.
        Never mutates the package or creates claim maps.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        proposals = _read_claim_proposals_from_package(package_path)
        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "count": len(proposals),
            "proposals": proposals,
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/claim-proposals/{proposal_id}")
    def get_claim_proposal(project_id: str, proposal_id: str) -> dict[str, Any]:
        """Read a single claim proposal by proposal_id.

        Read-only. Returns 404 if the proposal does not exist.
        Never mutates the package or creates claim maps.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        internal_path = f"{CLAIM_PROPOSALS_DIR}/{proposal_id}.json"
        with zipfile.ZipFile(package_path, "r") as zf:
            if internal_path not in zf.namelist():
                raise HTTPException(
                    status_code=404,
                    detail=f"claim proposal '{proposal_id}' not found",
                )
            try:
                proposal = json.loads(zf.read(internal_path))
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"proposal artifact is not valid JSON: {exc}",
                )

        return {
            "schema_version": "0.1",
            "proposal_path": internal_path,
            "proposal": proposal,
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/claim-proposals/{proposal_id}/support-packet")
    def get_claim_support_packet(project_id: str, proposal_id: str) -> dict[str, Any]:
        """Return a read-only support packet aggregating proposal + evidence + audit data.

        Assembles the proposal metadata, resolver outputs for each supporting
        evidence path, flattened warnings, stale/missing evidence counts, and
        the related audit events. Read-only — does not mutate the package,
        execute solvers, or advance any engineering claim.

        Returns 404 when the package or the proposal does not exist.
        Always returns claim_advancement: 'none'.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        internal_path = f"{CLAIM_PROPOSALS_DIR}/{proposal_id}.json"
        with zipfile.ZipFile(package_path, "r") as zf:
            pkg_names: set[str] = set(zf.namelist())
            if internal_path not in pkg_names:
                raise HTTPException(
                    status_code=404,
                    detail=f"claim proposal '{proposal_id}' not found",
                )
            try:
                proposal = json.loads(zf.read(internal_path))
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"proposal artifact is not valid JSON: {exc}",
                )
            ei_raw = (
                zf.read("results/evidence_index.json")
                if "results/evidence_index.json" in pkg_names
                else None
            )

        evidence_entries: list[dict[str, Any]] = []
        if ei_raw:
            try:
                evidence_entries = json.loads(ei_raw).get("entries") or []
            except json.JSONDecodeError:
                pass

        rs = _read_revalidation_status(package_path)
        audit_events = _read_audit_events_from_package(package_path)

        packet = _build_claim_support_packet(
            proposal=proposal,
            proposal_path=internal_path,
            pkg_names=pkg_names,
            evidence_entries=evidence_entries,
            revalidation_status=rs,
            audit_events=audit_events,
        )
        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "support_packet": packet,
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/evidence-references/resolve")
    def resolve_evidence_reference(
        project_id: str,
        path: str = Query(..., description="Package-internal artifact path to resolve"),
    ) -> dict[str, Any]:
        """Resolve a single package artifact path against the current package state.

        Read-only. Returns classification, evidence-index membership, revalidation
        freshness, and whether the path is usable as supporting evidence for a
        claim proposal. Does not mutate the package, execute solvers, or advance
        any engineering claim.

        Returns 404 when the package does not exist.
        Returns 400 when path is empty or not a valid internal package path.
        Always returns claim_advancement: 'none'.
        A path that is absent from the package still returns 200 with exists=false.
        """
        path = path.strip()
        if not path or not _is_internal_package_path(path):
            raise HTTPException(
                status_code=400,
                detail="path must be a non-empty relative package-internal path (no leading '/', no backslashes)",
            )

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        with zipfile.ZipFile(package_path, "r") as zf:
            pkg_names: set[str] = set(zf.namelist())
            ei_raw = (
                zf.read("results/evidence_index.json")
                if "results/evidence_index.json" in pkg_names
                else None
            )

        evidence_entries: list[dict[str, Any]] = []
        if ei_raw:
            try:
                evidence_entries = json.loads(ei_raw).get("entries") or []
            except json.JSONDecodeError:
                pass

        rs = _read_revalidation_status(package_path)
        resolved = _resolve_evidence_reference(
            path=path,
            pkg_names=pkg_names,
            evidence_entries=evidence_entries,
            revalidation_status=rs,
        )
        return {
            "schema_version": "0.1",
            "project_id": project_id,
            "resolved": resolved,
            "claim_advancement": "none",
        }

    @app.get("/api/projects/{project_id}/reports/credibility")
    def get_project_credibility_report(project_id: str) -> dict[str, Any]:
        """Return a structured credibility report for the project.

        Gathers available geometry evidence, CAE/result evidence, design-target
        comparisons, known assumptions, warnings, and missing evidence. This
        endpoint is read-only: it does not execute solvers, mutate CAD, or
        advance any engineering claim.
        """
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return _build_credibility_report(active_settings, project_id, package_path)


def _build_credibility_report(
    settings: Any,
    project_id: str,
    package_path: Path,
) -> dict[str, Any]:
    """Assemble a read-only credibility report from existing project artifacts."""
    warnings: list[str] = []
    missing_evidence: list[str] = []

    geometry_evidence, _, _, _ = _read_geometry_evidence(package_path, warnings)
    cae_evidence = _read_cae_evidence(package_path)
    result_evidence = _read_result_evidence(settings, project_id, package_path)
    design_targets = _read_design_target_comparison(settings, project_id, warnings)
    assumptions = _read_assumptions(settings, project_id, missing_evidence)
    warnings.extend(result_evidence.get("warnings") or [])
    warnings.extend(design_targets.get("warnings") or [])

    missing_evidence.extend(_missing_evidence_notes(geometry_evidence, cae_evidence, result_evidence, design_targets, assumptions))

    overall = _rollup_overall_status(
        geometry_evidence["status"],
        cae_evidence["status"],
        result_evidence["status"],
        design_targets["status"],
    )

    return {
        "schema_version": "0.1",
        "project_id": project_id,
        "package_path": str(package_path),
        "generated_at": now_iso(),
        "claim_advancement": "none",
        "summary": {
            "overall": overall,
            "geometry_evidence": geometry_evidence["status"],
            "cae_evidence": cae_evidence["status"],
            "result_evidence": result_evidence["status"],
            "design_targets": design_targets["status"],
        },
        "geometry_evidence": geometry_evidence,
        "cae_evidence": cae_evidence,
        "result_evidence": result_evidence,
        "design_targets": design_targets,
        "assumptions": assumptions,
        "warnings": warnings,
        "missing_evidence": missing_evidence,
        "revalidation_status": _build_revalidation_response(
            _read_revalidation_status(package_path)
        ),
    }


def _read_geometry_evidence(
    package_path: Path,
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None, int | None, list[str]]:
    """Inspect the package for compiled geometry artifacts and the last edit diff."""
    has_geometry = False
    has_topology_map = False
    has_feature_graph = False
    part_count: int | None = None
    named_parts: list[str] = []
    last_edit_diff: dict[str, Any] | None = None

    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            geometry_files = [n for n in names if n.startswith("geometry/")]
            has_geometry = bool(geometry_files) and any(
                n.endswith((".step", ".stp", ".glb", ".stl")) for n in geometry_files
            )
            has_topology_map = "geometry/topology_map.json" in names
            has_feature_graph = "graph/feature_graph.json" in names

            if has_topology_map:
                try:
                    topo = json.loads(zf.read("geometry/topology_map.json").decode("utf-8"))
                    entities = topo.get("entities") or []
                    part_entities = [
                        e for e in entities
                        if isinstance(e, dict) and e.get("type") in {"solid", "body"}
                    ]
                    part_count = len(part_entities)
                    named_parts = [
                        str(e.get("name") or e.get("id"))
                        for e in part_entities
                        if isinstance(e, dict) and e.get("name")
                    ]
                except Exception as exc:
                    warnings.append(f"Could not read topology_map.json: {exc}.")

            if "state/last_edit_diff.json" in names:
                try:
                    last_edit_diff = json.loads(zf.read("state/last_edit_diff.json").decode("utf-8"))
                except Exception as exc:
                    warnings.append(f"Could not read state/last_edit_diff.json: {exc}.")
    except zipfile.BadZipFile as exc:
        warnings.append(f"Package is not a valid zip file: {exc}.")

    score = sum([has_geometry, has_topology_map, has_feature_graph])
    if score == 3:
        status = "present"
    elif score > 0:
        status = "partial"
    else:
        status = "missing"

    geometry_evidence = {
        "status": status,
        "has_geometry": has_geometry,
        "has_topology_map": has_topology_map,
        "has_feature_graph": has_feature_graph,
        "part_count": part_count,
        "named_parts": named_parts,
        "last_edit_diff": last_edit_diff,
    }
    return geometry_evidence, last_edit_diff, part_count, named_parts


def _read_cae_evidence(package_path: Path) -> dict[str, Any]:
    """Inspect the package for solver input/run/FRD artifacts."""
    has_solver_input = False
    has_solver_run = False
    has_frd_output = False
    solver_run_artifacts: list[str] = []

    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = zf.namelist()
            for n in names:
                if "simulation/runs/" not in n:
                    continue
                if n.endswith("solver_input.inp"):
                    has_solver_input = True
                if n.endswith("solver_run.json"):
                    has_solver_run = True
                if n.endswith("result.frd") or ".frd" in n:
                    has_frd_output = True
                    solver_run_artifacts.append(n)
    except zipfile.BadZipFile:
        pass

    score = sum([has_solver_input, has_solver_run, has_frd_output])
    if score == 3:
        status = "present"
    elif score > 0:
        status = "partial"
    else:
        status = "missing"

    return {
        "status": status,
        "has_solver_input": has_solver_input,
        "has_solver_run": has_solver_run,
        "has_frd_output": has_frd_output,
        "solver_run_artifacts": solver_run_artifacts,
    }


def _read_result_evidence(
    settings: Any,
    project_id: str,
    package_path: Path,
) -> dict[str, Any]:
    """Read computed metrics and extract headline result scalars."""
    has_computed_metrics = False
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            has_computed_metrics = "results/computed_metrics.json" in zf.namelist()
    except zipfile.BadZipFile:
        pass

    cm = get_computed_metrics(settings, project_id)
    document = cm.get("document")
    status = "present" if (has_computed_metrics and cm.get("ok") and document is not None) else "missing"
    if has_computed_metrics and not (cm.get("ok") and document is not None):
        status = "partial"

    key_results: dict[str, Any] = {
        "max_von_mises_stress": None,
        "max_displacement": None,
        "safety_factor": None,
    }
    if isinstance(document, dict):
        key_results["max_von_mises_stress"] = _first_scalar(document, ["max_von_mises_stress", "von_mises_stress"])
        key_results["max_displacement"] = _first_scalar(document, ["max_displacement", "displacement"])
        key_results["safety_factor"] = _first_scalar(document, ["safety_factor", "min_safety_factor"])

    return {
        "status": status,
        "has_computed_metrics": has_computed_metrics,
        "computed_metrics": cm,
        "key_results": key_results,
        "warnings": list(cm.get("warnings") or []),
    }


def _first_scalar(document: dict[str, Any], names: list[str]) -> dict[str, Any] | None:
    """Return the first matching scalar metric from global or load-case metrics."""
    global_metrics = document.get("global_metrics") or {}
    for name in names:
        item = global_metrics.get(name)
        if isinstance(item, dict) and item.get("value") is not None:
            return {"value": item["value"], "unit": item.get("unit"), "metric": name}
    for lc in document.get("load_cases") or []:
        if not isinstance(lc, dict):
            continue
        metrics = lc.get("metrics") or {}
        for name in names:
            item = metrics.get(name)
            if isinstance(item, dict) and item.get("value") is not None:
                return {
                    "value": item["value"],
                    "unit": item.get("unit"),
                    "metric": name,
                    "load_case_id": lc.get("load_case_id") or lc.get("id"),
                }
    return None


def _read_design_target_comparison(
    settings: Any,
    project_id: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Return the design-target comparison block or an unevaluated placeholder."""
    try:
        comparison = compare_package_targets(settings, project_id)
    except Exception as exc:
        warnings.append(f"Could not compare design targets: {exc}.")
        return {
            "status": "unknown",
            "summary": {"total": 0, "pass": 0, "fail": 0, "unknown": 0, "not_evaluated": 0},
            "items": [],
            "warnings": [],
        }

    summary = comparison.get("summary") or {"total": 0, "pass": 0, "fail": 0, "unknown": 0, "not_evaluated": 0}
    total = summary.get("total", 0)
    passes = summary.get("pass", 0)
    fails = summary.get("fail", 0)
    if total == 0:
        status = "not_evaluated"
    elif fails > 0:
        status = "fail"
    elif passes == total:
        status = "pass"
    else:
        status = "partial"

    return {
        "status": status,
        "summary": summary,
        "items": comparison.get("items") or [],
        "warnings": list(comparison.get("warnings") or []),
    }


def _read_assumptions(
    settings: Any,
    project_id: str,
    missing_evidence: list[str],
) -> list[str]:
    """Read assumptions from the CAD brief sidecar if it exists."""
    brief = load_cad_brief(settings, project_id)
    if not isinstance(brief, dict):
        missing_evidence.append("No authored CAD brief assumptions.")
        return []
    assumptions = brief.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions:
        missing_evidence.append("No assumptions recorded in CAD brief.")
        return []
    return [str(a) for a in assumptions]


def _missing_evidence_notes(
    geometry_evidence: dict[str, Any],
    cae_evidence: dict[str, Any],
    result_evidence: dict[str, Any],
    design_targets: dict[str, Any],
    assumptions: list[str],
) -> list[str]:
    """Return human-readable missing-evidence notes."""
    notes: list[str] = []
    if geometry_evidence["status"] in ("partial", "missing"):
        notes.append("Geometry evidence is incomplete.")
    if cae_evidence["status"] in ("partial", "missing"):
        notes.append("CAE solver-run evidence is incomplete.")
    if result_evidence["status"] in ("partial", "missing"):
        notes.append("Computed result evidence is incomplete.")
    if design_targets["status"] == "not_evaluated":
        notes.append("No design targets have been authored.")
    if not assumptions:
        notes.append("No assumptions have been recorded.")
    return notes


def _rollup_overall_status(
    geometry: str,
    cae: str,
    result: str,
    design_targets: str,
) -> str:
    """Roll up an overall credibility status from the four subsystems."""
    tiers = [geometry, cae, result]
    if design_targets == "fail":
        return "fail"
    if all(t == "present" for t in tiers) and design_targets == "pass":
        return "pass"
    if all(t == "missing" for t in tiers) and design_targets == "not_evaluated":
        return "not_evaluated"
    if any(t in ("partial", "missing") for t in tiers) or design_targets in ("partial", "unknown"):
        return "partial"
    return "unknown"
