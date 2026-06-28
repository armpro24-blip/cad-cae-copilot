"""CAE calibration / benchmark comparison packs for issue #433.

Provides simple structural linear-static reference cases with analytical or
well-documented reference values and tolerance bands. The comparisons are
intentionally conservative: a coarse mesh, missing solver, or unknown load case
lowers confidence instead of being ignored.

This module is pure and does not execute solvers. Optional real-solver smoke
tests live in the test suite and are skip-gated when CalculiX is unavailable.
"""

from __future__ import annotations

import math
from typing import Any


CALIBRATION_CASES: dict[str, dict[str, Any]] = {
    "tension_rod": {
        "title": "Tension rod",
        "description": "Axial tension of a square steel rod.",
        "analysis_type": "static",
        "material": "Steel",
        "geometry": {
            "length_mm": 100.0,
            "cross_section_mm2": 100.0,
        },
        "loading": {"force_n": 1000.0, "direction": "+X"},
        "references": {
            "max_displacement": {
                "value": 1000.0 * 100.0 / (210000.0 * 100.0),
                "unit": "mm",
                "tolerance_percent": 10.0,
                "gate": True,
                "note": "Axial elongation from Hooke's law.",
            },
            "max_von_mises_stress": {
                "value": 1000.0 / 100.0,
                "unit": "MPa",
                "tolerance_percent": 10.0,
                "gate": False,
                "note": "Nominal axial stress; end constraints can create local concentrations.",
            },
        },
        "limitations": [
            "Linear-static, small-strain assumption.",
            "Uniform end load approximated as distributed nodal force.",
            "Stress is informational unless mesh is refined at the fixed end.",
        ],
    },
    "cantilever_end_load": {
        "title": "Cantilever end load",
        "description": "End-loaded cantilever beam (Euler-Bernoulli reference).",
        "analysis_type": "static",
        "material": "Steel",
        "geometry": {
            "length_mm": 100.0,
            "width_mm": 10.0,
            "height_mm": 20.0,
        },
        "loading": {"force_n": 100.0, "direction": "-Z"},
        "references": {
            "max_displacement": {
                "value": 0.023809523809523808,
                "unit": "mm",
                "tolerance_percent": 10.0,
                "gate": True,
                "note": "Tip deflection from Euler-Bernoulli beam theory.",
            },
            "max_von_mises_stress": {
                "value": 15.0,
                "unit": "MPa",
                "tolerance_percent": 15.0,
                "gate": False,
                "note": "Maximum bending stress at the fixed end; coarse mesh may under-resolve peak.",
            },
        },
        "limitations": [
            "Euler-Bernoulli thin-beam theory ignores shear deformation.",
            "C3D8 coarse mesh under-resolves stress gradient at fixed end.",
            "Linear-static, isotropic linear-elastic material assumed.",
        ],
    },
}


def list_calibration_cases() -> list[dict[str, Any]]:
    """Return lightweight metadata for every available calibration case."""
    return [
        {
            "id": case_id,
            "title": case["title"],
            "description": case["description"],
            "analysis_type": case["analysis_type"],
            "gated_metrics": [
                name for name, ref in case["references"].items() if ref.get("gate") is True
            ],
        }
        for case_id, case in CALIBRATION_CASES.items()
    ]


def get_calibration_case(case_id: str) -> dict[str, Any] | None:
    """Return a full calibration case definition, or None if unknown."""
    case = CALIBRATION_CASES.get(case_id)
    if case is None:
        return None
    return {"id": case_id, **case}


def compare_to_benchmark(
    computed_metrics: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    """Compare computed metrics against a named calibration case.

    Returns a structured comparison with per-metric status, deviation, and
    overall pass/fail/warning verdict. Missing metrics lower confidence but do
    not silently pass.
    """
    case = get_calibration_case(case_id)
    if case is None:
        return {
            "status": "error",
            "case_id": case_id,
            "message": f"Unknown calibration case: {case_id}",
        }

    references = case["references"]
    metric_results: list[dict[str, Any]] = []
    gated_passed = True
    has_gating_metric = False
    overall_status = "passed"

    for metric_name, ref in references.items():
        computed = _find_metric(computed_metrics, metric_name)
        if computed is None:
            metric_results.append(
                {
                    "metric": metric_name,
                    "status": "missing",
                    "computed": None,
                    "reference": ref["value"],
                    "unit": ref["unit"],
                    "deviation_percent": None,
                    "note": "Metric not available in computed results.",
                }
            )
            overall_status = _worsen(overall_status, "warning")
            if ref.get("gate"):
                gated_passed = False
                has_gating_metric = True
            continue

        deviation_percent = _deviation_percent(computed, ref["value"])
        within_tolerance = deviation_percent is not None and abs(deviation_percent) <= ref["tolerance_percent"]

        if within_tolerance:
            metric_status = "passed"
        else:
            metric_status = "failed"
            overall_status = _worsen(overall_status, "failed")
            if ref.get("gate"):
                gated_passed = False
                has_gating_metric = True

        metric_results.append(
            {
                "metric": metric_name,
                "status": metric_status,
                "computed": computed,
                "reference": ref["value"],
                "unit": ref["unit"],
                "deviation_percent": deviation_percent,
                "tolerance_percent": ref["tolerance_percent"],
                "gate": ref.get("gate", False),
                "note": ref.get("note"),
            }
        )

    if overall_status == "passed" and has_gating_metric and not gated_passed:
        overall_status = "failed"
    elif overall_status == "passed" and not has_gating_metric and any(r["status"] == "missing" for r in metric_results):
        overall_status = "warning"

    return {
        "status": overall_status,
        "case_id": case_id,
        "title": case["title"],
        "description": case["description"],
        "metric_results": metric_results,
        "gated_passed": gated_passed,
        "limitations": case["limitations"],
    }


def assess_calibration(
    computed_metrics: dict[str, Any],
    case_id: str | None = None,
) -> dict[str, Any]:
    """Run benchmark comparison, falling back to auto-detection when no case is named.

    Auto-detection is best-effort and conservative: if a unique case cannot be
    inferred from the metrics, the result is 'unknown' rather than a guess.
    """
    if case_id:
        return compare_to_benchmark(computed_metrics, case_id)

    candidates: list[dict[str, Any]] = []
    for candidate_id in CALIBRATION_CASES:
        result = compare_to_benchmark(computed_metrics, candidate_id)
        has_match = any(m["status"] != "missing" for m in result["metric_results"])
        if has_match and result["status"] in {"passed", "warning", "failed"}:
            candidates.append(result)

    if not candidates:
        return {
            "status": "unknown",
            "case_id": None,
            "message": (
                "No calibration case could be matched from the available metrics. "
                "Provide a case_id to run an explicit comparison."
            ),
        }

    # Prefer the case with the fewest missing metrics (most complete match).
    candidates.sort(key=lambda r: sum(1 for m in r["metric_results"] if m["status"] == "missing"))
    return candidates[0]


def _find_metric(metrics: dict[str, Any], name: str) -> float | None:
    """Extract a numeric metric from common computed-metrics shapes."""
    if not isinstance(metrics, dict):
        return None

    # Direct value.
    value = metrics.get(name)
    if isinstance(value, (int, float)):
        return float(value)

    # Nested dict such as {"max_von_mises_stress": {"value": 12.3, "unit": "MPa"}}.
    nested = metrics.get(name)
    if isinstance(nested, dict):
        v = nested.get("value")
        if isinstance(v, (int, float)):
            return float(v)

    # Plural / alternate keys.
    for alt in (name.replace("max_", ""), name + "_mpa", name + "_mm"):
        v = metrics.get(alt)
        if isinstance(v, (int, float)):
            return float(v)

    return None


def _deviation_percent(computed: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return ((computed - reference) / reference) * 100.0


def _worsen(current: str, update: str) -> str:
    order = {"passed": 0, "warning": 1, "failed": 2}
    return update if order.get(update, 0) > order.get(current, 0) else current
