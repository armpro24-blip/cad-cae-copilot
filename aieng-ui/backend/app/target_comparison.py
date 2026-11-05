"""Read-only target comparison API surface for `.aieng` packages.

Delegates numeric comparison semantics to the `aieng` core implementation and
adds UI-facing missing/ambiguous reason codes from the local metric mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .computed_metrics import build_target_mapping, get_computed_metrics
from .config import Settings
from .copilot_loop import _resolve_package
from .design_targets import get_design_targets

CLAIM_BOUNDARY = (
    "Target comparison is a deterministic read-only check against imported "
    "computed metrics. It does not certify the design, run a solver, mutate "
    "CAD, or advance engineering claims."
)


def _import_core_comparator(settings: Settings) -> Any | None:
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    candidate = str(aieng_src)
    try:
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_result_summary import compare_design_targets_for_package

        return compare_design_targets_for_package
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def compare_package_targets(settings: Settings, project_id: str) -> dict[str, Any]:
    """Compare design targets for a project package without mutating it."""
    try:
        package_path = _resolve_package(settings, project_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return _response(
                project_id=project_id,
                package_path=None,
                comparisons={"present": False, "summary": _empty_summary(), "items": []},
                warnings=["Project has no .aieng package."],
                source="aieng-ui",
            )
        raise

    comparator = _import_core_comparator(settings)
    if comparator is None:
        raise HTTPException(status_code=503, detail="aieng target comparator unavailable")

    warnings: list[str] = []
    try:
        comparisons = comparator(package_path)
    except FileNotFoundError as exc:
        return _response(
            project_id=project_id,
            package_path=package_path,
            comparisons={"present": False, "summary": _empty_summary(), "items": []},
            warnings=[str(exc)],
            source="aieng.core.compare_design_targets_for_package",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not isinstance(comparisons, dict):
        comparisons = {"present": False, "summary": _empty_summary(), "items": []}
        warnings.append("Core comparator returned an unexpected payload.")

    enriched = _enrich_with_mapping(settings, project_id, comparisons, warnings)
    return _response(
        project_id=project_id,
        package_path=package_path,
        comparisons=enriched,
        warnings=warnings,
        source="aieng.core.compare_design_targets_for_package",
    )


def acceptance_verdict(
    settings: Settings, project_id: str, package_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Pass/fail against the package's acceptance criteria, compactly.

    The comparison reached the agent only inside aieng.agent_context (~8k
    tokens). cae.extract_solver_results returns this where the numbers are
    produced, so the agent sees which requirement failed and by how much without
    another call — the compact requirement report that gave the largest gains in
    the CalculiX feedback loop of arXiv 2605.17448. None when the package records
    no criteria (or the comparison fails), so such a response is unchanged.

    The comparison reads the PROJECT's package. When `package_path` names
    another package (an explicit override, e.g. a design variant), the verdict
    is omitted: the project's criteria evaluated against the project's metrics
    would be presented beside the variant's freshly extracted numbers.
    """
    if package_path is not None:
        try:
            if Path(package_path).resolve() != Path(_resolve_package(settings, project_id)).resolve():
                return None
        except Exception:  # noqa: BLE001 - cannot tell which package: no verdict
            return None
    try:
        comparison = compare_package_targets(settings, project_id)
    except Exception:  # noqa: BLE001 - a verdict must never fail the extraction
        return None
    summary = comparison.get("summary") or {}
    if not summary.get("total"):
        return None
    # reason_code says WHY a target is unknown (missing metric, ambiguous load
    # case), which decides the agent's next step.
    keys = ("target_id", "target_type", "comparator", "expected", "actual", "status", "reason_code")
    not_passed = [
        {k: item[k] for k in keys if item.get(k) is not None}
        for item in comparison.get("items") or []
        if isinstance(item, dict) and item.get("status") != "pass"
    ]
    return {"summary": summary, "not_passed": not_passed}


def _enrich_with_mapping(
    settings: Settings,
    project_id: str,
    comparisons: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    try:
        targets = get_design_targets(settings, project_id).get("targets") or []
        metrics_doc = get_computed_metrics(settings, project_id).get("document")
        design_targets = [t for t in targets if isinstance(t, dict)]
        mapping = build_target_mapping(metrics_doc, design_targets)
    except Exception as exc:
        warnings.append(f"Could not enrich target mapping: {type(exc).__name__}.")
        design_targets = []
        metrics_doc = None
        mapping = []

    mapping_by_id = {str(m.get("target_id")): m for m in mapping if isinstance(m, dict)}
    targets_by_id = {str(t.get("target_id") or t.get("id") or ""): t for t in design_targets}
    out = dict(comparisons)
    items: list[dict[str, Any]] = []
    for raw in comparisons.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        target_id = str(item.get("target_id") or item.get("id") or "")
        mapped = mapping_by_id.get(target_id)
        reason_code = _reason_code(item)
        if mapped:
            map_status = mapped.get("status")
            item["target_label"] = mapped.get("target_label")
            item["metric"] = mapped.get("metric")
            item["load_case_id"] = mapped.get("load_case_id")
            item["metric_mapping_status"] = map_status
            if map_status in {"missing_metric", "ambiguous"}:
                item["status"] = "unknown"
                reason_code = str(map_status)
                item["actual"] = {"value": None}
                item["notes"] = mapped.get("summary") or item.get("notes")
            elif map_status == "mapped" and item.get("status") not in {"pass", "fail"}:
                fallback = _evaluate_ui_metric_target(targets_by_id.get(target_id), metrics_doc, mapped)
                if fallback is not None:
                    item.update(fallback)
                    reason_code = _reason_code(item)
        item["reason_code"] = reason_code
        items.append(item)
    out["items"] = items
    out["summary"] = _summary_for(items)
    out["present"] = bool(items)
    return out


def _evaluate_ui_metric_target(
    target: dict[str, Any] | None,
    metrics_doc: dict[str, Any] | None,
    mapped: dict[str, Any],
) -> dict[str, Any] | None:
    """Fill gaps where core cannot see aieng-ui's canonical global_metrics.

    Core remains the source for package-level comparison semantics. This small
    adapter only handles aieng-ui-authored metric targets that are already
    mapped to an imported scalar value but came back unevaluated from core.
    """
    if not isinstance(target, dict) or not isinstance(metrics_doc, dict):
        return None
    metric = mapped.get("metric") or target.get("metric") or target.get("target_type")
    if not isinstance(metric, str) or not metric:
        return None
    found = _find_metric_value(metrics_doc, metric, mapped.get("load_case_id"))
    if found is None:
        return None
    value, unit = found
    status = _compare_scalar_target(target, float(value))
    expected = _expected_from_target(target)
    return {
        "target_type": target.get("target_type") or target.get("metric") or metric,
        "comparator": target.get("comparator") or target.get("operator"),
        "expected": expected,
        "actual": {"value": value, "unit": target.get("unit") or unit, "source_artifact": "results/computed_metrics.json"},
        "source_artifacts": ["results/computed_metrics.json"],
        "status": status,
        "notes": "Evaluated from aieng-ui imported computed metrics.",
    }


def _find_metric_value(
    metrics_doc: dict[str, Any],
    metric: str,
    load_case_id: Any,
) -> tuple[float, str | None] | None:
    global_metrics = metrics_doc.get("global_metrics") or {}
    if isinstance(load_case_id, str) and load_case_id:
        for load_case in metrics_doc.get("load_cases") or []:
            if not isinstance(load_case, dict):
                continue
            if load_case.get("load_case_id") != load_case_id and load_case.get("id") != load_case_id:
                continue
            metrics = load_case.get("metrics") or {}
            item = metrics.get(metric) if isinstance(metrics, dict) else None
            return _metric_tuple(item)
        return None
    item = global_metrics.get(metric) if isinstance(global_metrics, dict) else None
    found = _metric_tuple(item)
    if found is not None:
        return found
    matches: list[tuple[float, str | None]] = []
    for load_case in metrics_doc.get("load_cases") or []:
        if not isinstance(load_case, dict):
            continue
        metrics = load_case.get("metrics") or {}
        if isinstance(metrics, dict):
            found = _metric_tuple(metrics.get(metric))
            if found is not None:
                matches.append(found)
    return matches[0] if len(matches) == 1 else None


def _metric_tuple(item: Any) -> tuple[float, str | None] | None:
    if isinstance(item, dict) and isinstance(item.get("value"), (int, float)):
        return float(item["value"]), item.get("unit") if isinstance(item.get("unit"), str) else None
    if isinstance(item, (int, float)):
        return float(item), None
    return None


def _expected_from_target(target: dict[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {"comparator": target.get("comparator") or target.get("operator")}
    threshold = target.get("threshold") if target.get("threshold") is not None else target.get("value")
    if threshold is not None:
        expected["threshold"] = threshold
    if target.get("threshold_min") is not None:
        expected["threshold_min"] = target.get("threshold_min")
    if target.get("threshold_max") is not None:
        expected["threshold_max"] = target.get("threshold_max")
    return expected


def _compare_scalar_target(target: dict[str, Any], actual: float) -> str:
    operator = target.get("comparator") or target.get("operator")
    threshold = target.get("threshold") if target.get("threshold") is not None else target.get("value")
    if operator == "within_range":
        lo = target.get("threshold_min")
        hi = target.get("threshold_max")
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            return "unknown"
        return "pass" if float(lo) <= actual <= float(hi) else "fail"
    if operator == "reduce_by_at_least":
        if not isinstance(threshold, (int, float)):
            return "unknown"
        return "pass" if actual >= float(threshold) else "fail"
    if operator in {"preserve", "priority"}:
        return "not_evaluated"
    if not isinstance(threshold, (int, float)):
        return "unknown"
    if operator == "<=":
        return "pass" if actual <= float(threshold) else "fail"
    if operator == "<":
        return "pass" if actual < float(threshold) else "fail"
    if operator == ">=":
        return "pass" if actual >= float(threshold) else "fail"
    if operator == ">":
        return "pass" if actual > float(threshold) else "fail"
    if operator == "==":
        return "pass" if actual == float(threshold) else "fail"
    return "unknown"


def _reason_code(item: dict[str, Any]) -> str | None:
    status = item.get("status")
    notes = str(item.get("notes") or "").lower()
    if status == "unknown" and "not present" in notes:
        return "missing_metric"
    if status == "not_evaluated" and "no results/computed_metrics.json" in notes:
        return "missing_metrics_artifact"
    if status == "not_evaluated" and "unsupported target_type" in notes:
        return "unsupported_target_type"
    if status == "pass":
        return "passed_threshold"
    if status == "fail":
        return "failed_threshold"
    return None


def _empty_summary() -> dict[str, int]:
    return {"total": 0, "pass": 0, "fail": 0, "unknown": 0, "not_evaluated": 0}


def _summary_for(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = _empty_summary()
    summary["total"] = len(items)
    for item in items:
        status = item.get("status")
        if isinstance(status, str) and status in summary:
            summary[status] += 1
        else:
            summary["unknown"] += 1
    return summary


def _response(
    *,
    project_id: str,
    package_path: Path | None,
    comparisons: dict[str, Any],
    warnings: list[str],
    source: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "project_id": project_id,
        "package_path": str(package_path) if package_path else None,
        "source": source,
        "comparison": comparisons,
        "summary": comparisons.get("summary") or _empty_summary(),
        "items": comparisons.get("items") or [],
        "warnings": warnings,
        "claim_boundary": CLAIM_BOUNDARY,
    }
