"""The design-target verdict must reach the surface agents actually read.

Measured 2026-08-11: `compare_design_targets_for_package` returned a correct
verdict on a real package (max_displacement 0.002721 mm <= 0.5 → pass;
minimum_safety_factor → unknown, with the reason), while
`aieng.agent_context` reported `target_comparison: {available: false, items: []}`
with NO warning — for every project, always.

Cause: `target_comparison._response` emits the block under **"comparison"**
(singular) and also flattens `summary`/`items` to the top level, but the reader
looked only for **"comparisons"** (plural). The same reader/writer shape
mismatch family as #488: tests written against the reader's assumed shape stay
green while the feature is dead in production.
"""
from __future__ import annotations

from app.agent_context import _target_comparison_block

_ITEMS = [
    {"target_id": "motor_thrust__max_displacement", "target_type": "max_displacement",
     "status": "pass", "actual": {"value": 0.002721, "unit": "mm"}},
    {"target_id": "motor_thrust__minimum_safety_factor", "status": "unknown",
     "actual": {"value": None},
     "notes": "Metric not present in results/computed_metrics.json."},
]
_SUMMARY = {"total": 2, "pass": 1, "fail": 0, "unknown": 1}


def test_reads_the_shape_compare_package_targets_actually_returns() -> None:
    """This is the real payload — singular key plus flattened top level."""
    response = {
        "ok": True,
        "comparison": {"present": True, "summary": _SUMMARY, "items": _ITEMS},
        "summary": _SUMMARY,
        "items": _ITEMS,
        "warnings": [],
    }
    block = _target_comparison_block(response)

    assert block["available"] is True
    assert len(block["items"]) == 2
    assert block["summary"]["pass"] == 1
    assert [i["target_id"] for i in block["unknown_targets"]] == [
        "motor_thrust__minimum_safety_factor"
    ]
    assert block["failed_targets"] == []


def test_flattened_only_payload_still_reads() -> None:
    block = _target_comparison_block(
        {"ok": True, "summary": _SUMMARY, "items": _ITEMS, "warnings": []}
    )
    assert block["available"] is True
    assert len(block["items"]) == 2


def test_legacy_plural_key_still_reads() -> None:
    block = _target_comparison_block(
        {"comparisons": {"summary": _SUMMARY, "items": _ITEMS}, "warnings": []}
    )
    assert block["available"] is True
    assert len(block["items"]) == 2


def test_failed_targets_are_separated() -> None:
    failing = [{"target_id": "t", "status": "fail", "actual": {"value": 9.0}}]
    block = _target_comparison_block({"comparison": {"items": failing}, "warnings": []})
    assert block["available"] is True
    assert len(block["failed_targets"]) == 1


def test_genuinely_empty_comparison_stays_unavailable() -> None:
    """No targets is not the same as a broken read — it must stay honest."""
    block = _target_comparison_block(
        {"ok": True, "comparison": {"present": False, "summary": {}, "items": []},
         "summary": {}, "items": [], "warnings": ["Project has no .aieng package."]}
    )
    assert block["available"] is False
    assert block["items"] == []
    assert block["warnings"] == ["Project has no .aieng package."]


def test_non_dict_response_reports_unavailable_with_a_warning() -> None:
    block = _target_comparison_block(None)
    assert block["available"] is False
    assert block["warnings"] == ["Target comparison unavailable."]
