"""Computed Metrics import and target mapping for AIENG Workbench.

Provides explicit user-driven import of postprocessed scalar metrics into the
`.aieng` package.  Preview/GET are read-only.  PUT mutates only
``results/computed_metrics.json`` and does not run solvers, edit CAD, generate
meshes, refresh claims, or certify engineering safety.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from .config import Settings
from .copilot_loop import _resolve_package
from .project_io import write_artifact_to_package

_COMPUTED_METRICS_PATH = "results/computed_metrics.json"
_MAX_METRICS = 500
_MAX_STRING_LEN = 500
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")


def _error(code: str, message: str, *, row: int | None = None, field: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "row": row, "field": field}


def _metric_value(value: Any, *, unit: Any = None, source: Any = None, field: str = "value", row: int | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, [_error("invalid_value", "Metric value must be numeric.", row=row, field=field)]
    if not math.isfinite(numeric):
        return None, [_error("invalid_value", "Metric value must be finite; NaN and Infinity are not allowed.", row=row, field=field)]

    item: dict[str, Any] = {"value": numeric}
    if unit not in (None, ""):
        if not isinstance(unit, str) or len(unit) > _MAX_STRING_LEN:
            errors.append(_error("invalid_unit", f"Unit must be a string up to {_MAX_STRING_LEN} characters.", row=row, field="unit"))
        else:
            item["unit"] = unit
    if source not in (None, ""):
        if not isinstance(source, str) or len(source) > _MAX_STRING_LEN:
            errors.append(_error("invalid_source", f"Source must be a string up to {_MAX_STRING_LEN} characters.", row=row, field="source"))
        else:
            item["source"] = source
    return (None if errors else item), errors


def _validate_name(name: Any, *, code: str, label: str, row: int | None = None, field: str | None = None) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    value = name.strip()
    if len(value) > _MAX_STRING_LEN or not _SAFE_NAME_RE.match(value):
        return None
    return value


def _normalize_metric_mapping(raw: Any, *, row: int | None = None, metric_field: str = "metric") -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return {}, [_error("invalid_metrics", "Metrics must be an object.", row=row, field=metric_field)]
    for key, value in raw.items():
        metric = _validate_name(key, code="invalid_metric", label="Metric", row=row, field=metric_field)
        if metric is None:
            errors.append(_error("invalid_metric", "Metric name must be non-empty and contain only letters, numbers, underscore, hyphen, dot, or colon.", row=row, field=metric_field))
            continue
        if isinstance(value, dict):
            item, item_errors = _metric_value(value.get("value"), unit=value.get("unit"), source=value.get("source"), field=f"{metric}.value", row=row)
        else:
            item, item_errors = _metric_value(value, field=metric, row=row)
        errors.extend(item_errors)
        if item is not None:
            metrics[metric] = item
    return metrics, errors


def _empty_document(source_format: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "metrics_source": {
            "tool": "aieng-ui.computed_metrics_import",
            "format": source_format,
            "imported_by": "user",
        },
        "global_metrics": {},
        "load_cases": [],
        "warnings": [],
    }


def normalize_computed_metrics_document(payload: Any, *, source_format: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    """Normalize accepted JSON shapes into the canonical computed metrics document."""
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return None, [_error("invalid_document", "Computed metrics payload must be a JSON object.")], warnings

    doc = _empty_document(source_format)
    if isinstance(payload.get("metrics_source"), dict):
        doc["metrics_source"].update({k: v for k, v in payload["metrics_source"].items() if k in {"tool", "format", "imported_by"}})
    if isinstance(payload.get("warnings"), list):
        doc["warnings"] = [str(w)[:_MAX_STRING_LEN] for w in payload["warnings"]]

    has_canonical_keys = any(k in payload for k in ("global_metrics", "load_cases"))

    global_raw = payload.get("global_metrics")
    if global_raw is not None:
        global_metrics, e = _normalize_metric_mapping(global_raw)
        errors.extend(e)
        doc["global_metrics"] = global_metrics
    elif not has_canonical_keys:
        # Simple object: {"max_stress": {"value": 1, "unit": "MPa"}, ...}
        simple = {k: v for k, v in payload.items() if k not in {"schema_version", "metrics_source", "warnings"}}
        global_metrics, e = _normalize_metric_mapping(simple)
        errors.extend(e)
        doc["global_metrics"] = global_metrics

    load_cases_raw = payload.get("load_cases") or []
    if not isinstance(load_cases_raw, list):
        errors.append(_error("invalid_load_cases", "load_cases must be an array.", field="load_cases"))
    else:
        seen_pairs: set[tuple[str, str]] = set()
        for index, load_case in enumerate(load_cases_raw):
            if not isinstance(load_case, dict):
                errors.append(_error("invalid_load_case", "Load case must be an object.", row=index, field="load_cases"))
                continue
            load_case_id = load_case.get("load_case_id") or load_case.get("id")
            load_case_id = _validate_name(load_case_id, code="invalid_load_case_id", label="Load case id", row=index, field="load_case_id")
            if load_case_id is None:
                errors.append(_error("invalid_load_case_id", "load_case_id is required and must contain only safe characters.", row=index, field="load_case_id"))
                continue
            metrics, e = _normalize_metric_mapping(load_case.get("metrics") or {}, row=index, metric_field="metrics")
            errors.extend(e)
            for metric in metrics:
                pair = (load_case_id, metric)
                if pair in seen_pairs:
                    errors.append(_error("duplicate_metric", f"Duplicate metric '{metric}' in load case '{load_case_id}'.", row=index, field=metric))
                seen_pairs.add(pair)
            doc["load_cases"].append({"load_case_id": load_case_id, "metrics": metrics})

    metrics_count = _metrics_count(doc)
    if metrics_count > _MAX_METRICS:
        errors.append(_error("too_many_metrics", f"Too many metrics: {metrics_count} (max {_MAX_METRICS}).", field="metrics"))
    if metrics_count == 0 and not errors:
        warnings.append("No metric values found.")
    return (None if errors else doc), errors, warnings


def parse_import_payload(payload: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str], str | None]:
    fmt = (payload.get("format") if isinstance(payload, dict) else None) or "json"
    text = (payload.get("text") if isinstance(payload, dict) else None)
    document = (payload.get("document") if isinstance(payload, dict) else None)
    if fmt not in {"json", "csv"}:
        return None, [_error("unsupported_format", "Supported formats are 'json' and 'csv'.", field="format")], [], fmt
    if fmt == "json":
        if document is None:
            if not isinstance(text, str):
                return None, [_error("missing_text", "JSON preview requires text or document.", field="text")], [], fmt
            try:
                document = json.loads(text)
            except json.JSONDecodeError as exc:
                return None, [_error("invalid_json", f"Invalid JSON: {exc.msg}.", row=exc.lineno, field="text")], [], fmt
        doc, errors, warnings = normalize_computed_metrics_document(document, source_format="json")
        return doc, errors, warnings, fmt
    if not isinstance(text, str):
        return None, [_error("missing_text", "CSV preview requires text.", field="text")], [], fmt
    doc, errors, warnings = parse_csv_metrics(text)
    return doc, errors, warnings, fmt


def parse_csv_metrics(text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    doc = _empty_document("csv")
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as exc:
        return None, [_error("invalid_csv", f"Invalid CSV: {exc}.")], warnings
    fields = set(reader.fieldnames or [])
    for required in ("metric", "value"):
        if required not in fields:
            errors.append(_error("missing_column", f"CSV must include '{required}' column.", field=required))
    if errors:
        return None, errors, warnings

    global_metrics: dict[str, dict[str, Any]] = {}
    load_cases: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for row_index, row in enumerate(reader, start=2):
        metric = _validate_name(row.get("metric"), code="invalid_metric", label="Metric", row=row_index, field="metric")
        if metric is None:
            errors.append(_error("invalid_metric", "Metric name must be non-empty and contain only safe characters.", row=row_index, field="metric"))
            continue
        load_case_id_raw = row.get("load_case_id")
        load_case_id = None
        if load_case_id_raw not in (None, ""):
            load_case_id = _validate_name(load_case_id_raw, code="invalid_load_case_id", label="Load case id", row=row_index, field="load_case_id")
            if load_case_id is None:
                errors.append(_error("invalid_load_case_id", "load_case_id contains unsafe characters.", row=row_index, field="load_case_id"))
                continue
        item, item_errors = _metric_value(row.get("value"), unit=row.get("unit"), source=row.get("source"), field="value", row=row_index)
        errors.extend(item_errors)
        if item is None:
            continue
        scope = load_case_id or "__global__"
        pair = (scope, metric)
        if pair in seen:
            errors.append(_error("duplicate_metric", f"Duplicate metric '{metric}' in {'global metrics' if load_case_id is None else load_case_id}.", row=row_index, field="metric"))
            continue
        seen.add(pair)
        if load_case_id is None:
            global_metrics[metric] = item
        else:
            load_cases.setdefault(load_case_id, {})[metric] = item

    doc["global_metrics"] = global_metrics
    doc["load_cases"] = [{"load_case_id": lc_id, "metrics": metrics} for lc_id, metrics in sorted(load_cases.items())]
    metrics_count = _metrics_count(doc)
    if metrics_count > _MAX_METRICS:
        errors.append(_error("too_many_metrics", f"Too many metrics: {metrics_count} (max {_MAX_METRICS}).", field="metrics"))
    if errors:
        return None, errors, warnings
    if metrics_count == 0:
        warnings.append("No metric rows found.")
    return doc, errors, warnings


def _metrics_count(doc: dict[str, Any] | None) -> int:
    if not isinstance(doc, dict):
        return 0
    total = len(doc.get("global_metrics") or {})
    for lc in doc.get("load_cases") or []:
        if isinstance(lc, dict) and isinstance(lc.get("metrics"), dict):
            total += len(lc["metrics"])
    return total


def _load_case_count(doc: dict[str, Any] | None) -> int:
    if not isinstance(doc, dict):
        return 0
    return len([lc for lc in doc.get("load_cases") or [] if isinstance(lc, dict)])


def _read_design_targets(pkg: Path) -> list[dict[str, Any]]:
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            for path in ("task/design_targets.yaml", "task/design_targets.yml", "targets/design_targets.json"):
                if path in zf.namelist():
                    raw = zf.read(path).decode("utf-8", errors="replace")
                    data = yaml.safe_load(raw)
                    if isinstance(data, dict) and isinstance(data.get("targets"), list):
                        return [t for t in data["targets"] if isinstance(t, dict)]
    except Exception:
        return []
    return []


def build_target_mapping(doc: dict[str, Any] | None, design_targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(doc, dict):
        doc = {}
    global_metrics = set((doc.get("global_metrics") or {}).keys())
    metric_load_cases: dict[str, set[str]] = {}
    for lc in doc.get("load_cases") or []:
        if not isinstance(lc, dict):
            continue
        load_case_id = lc.get("load_case_id") or lc.get("id")
        if not isinstance(load_case_id, str):
            continue
        for metric in (lc.get("metrics") or {}).keys():
            metric_load_cases.setdefault(str(metric), set()).add(load_case_id)

    mapping: list[dict[str, Any]] = []
    for target in design_targets:
        target_id = str(target.get("target_id") or target.get("id") or "unknown")
        label = str(target.get("label") or target.get("target_type") or target_id)
        metric = target.get("metric") or target.get("target_type")
        load_case_id = target.get("load_case_id")
        if not isinstance(metric, str) or not metric:
            mapping.append({"target_id": target_id, "target_label": label, "metric": "", "load_case_id": load_case_id, "status": "unknown", "matched_metric": None, "summary": "Target has no metric name to map."})
            continue
        if metric in global_metrics:
            mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": load_case_id, "status": "mapped", "matched_metric": metric, "summary": f"Metric '{metric}' is available globally."})
            continue
        cases = metric_load_cases.get(metric, set())
        if isinstance(load_case_id, str) and load_case_id:
            if load_case_id in cases:
                mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": load_case_id, "status": "mapped", "matched_metric": metric, "summary": f"Metric '{metric}' is available for load case '{load_case_id}'."})
            else:
                mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": load_case_id, "status": "missing_metric", "matched_metric": None, "summary": f"Metric '{metric}' is missing for load case '{load_case_id}'."})
        elif len(cases) == 1:
            only = next(iter(cases))
            mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": only, "status": "mapped", "matched_metric": metric, "summary": f"Metric '{metric}' is available for load case '{only}'."})
        elif len(cases) > 1:
            mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": None, "status": "ambiguous", "matched_metric": metric, "summary": f"Metric '{metric}' exists in multiple load cases; add load_case_id to the design target."})
        else:
            mapping.append({"target_id": target_id, "target_label": label, "metric": metric, "load_case_id": load_case_id, "status": "missing_metric", "matched_metric": None, "summary": f"Metric '{metric}' is not present in imported computed metrics."})
    return mapping


def _response(project_id: str, doc: dict[str, Any] | None, warnings: list[str], errors: list[dict[str, Any]], *, artifact_path: str | None, target_mapping: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "ok": not errors,
        "project_id": project_id,
        "artifact_path": artifact_path,
        "document": doc,
        "metrics_count": _metrics_count(doc),
        "load_case_count": _load_case_count(doc),
        "warnings": warnings,
        "errors": errors,
        "target_mapping": target_mapping or [],
        "claim_boundary": "Imported computed metrics are evidence inputs only. They do not certify the design, run a solver, or advance engineering claims.",
    }


def get_computed_metrics(settings: Settings, project_id: str) -> dict[str, Any]:
    try:
        pkg = _resolve_package(settings, project_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return _response(project_id, None, ["Project has no .aieng package."], [], artifact_path=None)
        raise
    design_targets = _read_design_targets(pkg)
    with zipfile.ZipFile(pkg, "r") as zf:
        if _COMPUTED_METRICS_PATH not in zf.namelist():
            return _response(project_id, None, ["No computed metrics artifact found in package."], [], artifact_path=None, target_mapping=build_target_mapping(None, design_targets))
        try:
            raw = json.loads(zf.read(_COMPUTED_METRICS_PATH).decode("utf-8"))
        except Exception:
            return _response(project_id, None, [], [_error("invalid_json", "results/computed_metrics.json is not valid JSON.")], artifact_path=_COMPUTED_METRICS_PATH)
    doc, errors, warnings = normalize_computed_metrics_document(raw, source_format="existing")
    if doc is None and errors:
        return _response(project_id, raw if isinstance(raw, dict) else None, warnings, errors, artifact_path=_COMPUTED_METRICS_PATH)
    if doc is not None:
        # Reading a STORED document: report the source it recorded, verbatim.
        # The normaliser is built for imports and stamped `imported_by: "user"`,
        # `format: "existing"` while dropping `run_id` / `source_files` — so the
        # engineering report called metrics this workbench's own solver run
        # produced a user import. Nothing recorded stays nothing.
        stored = raw.get("metrics_source") if isinstance(raw, dict) else None
        doc["metrics_source"] = dict(stored) if isinstance(stored, dict) else {}
    return _response(project_id, doc, warnings, [], artifact_path=_COMPUTED_METRICS_PATH, target_mapping=build_target_mapping(doc, design_targets))


def preview_computed_metrics(settings: Settings, project_id: str, payload: Any) -> dict[str, Any]:
    pkg = _resolve_package(settings, project_id)
    doc, errors, warnings, _fmt = parse_import_payload(payload)
    mapping = build_target_mapping(doc, _read_design_targets(pkg)) if doc is not None else []
    return _response(project_id, doc, warnings, errors, artifact_path=None, target_mapping=mapping)


def save_computed_metrics(settings: Settings, project_id: str, payload: Any) -> dict[str, Any]:
    pkg = _resolve_package(settings, project_id)
    if isinstance(payload, dict) and ("format" in payload or "text" in payload or "document" in payload):
        doc, errors, warnings, _fmt = parse_import_payload(payload)
    else:
        doc, errors, warnings = normalize_computed_metrics_document(payload, source_format="json")
    if errors or doc is None:
        raise HTTPException(status_code=422, detail={"ok": False, "errors": errors, "warnings": warnings})

    tmp = Path(tempfile.gettempdir()) / f"computed_metrics_{project_id}.json"
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        artifact = write_artifact_to_package(pkg, _COMPUTED_METRICS_PATH, tmp, overwrite=True)
    finally:
        if tmp.exists():
            tmp.unlink()
    mapping = build_target_mapping(doc, _read_design_targets(pkg))
    response = _response(project_id, doc, warnings, [], artifact_path=artifact["path"], target_mapping=mapping)
    response["changed_artifact_path"] = artifact["path"]
    return response

