"""A load case is a requirement: written in engineering language, checked when written.

The point is not storage. It is that (a) the wording is validated against the
real geometry before anyone spends a solver run on it, and (b) the acceptance
criteria land in the package's existing design-targets doc, so nothing invents a
second verdict system.
"""
from __future__ import annotations

import pytest

from app.cae_load_cases import (
    acceptance_to_design_targets,
    normalize_load_case,
    read_load_cases,
    upsert_load_case,
)


def _case(**over):
    payload = {
        "name": "motor_thrust",
        "material": "Al6061-T6",
        "fix": "底面",
        "load": {"at": "rib_main top", "force_n": 500, "direction": "-Z"},
        "acceptance": {"min_safety_factor": 2.0},
    }
    payload.update(over)
    return payload


def test_normalizes_a_complete_case() -> None:
    case = normalize_load_case(_case())
    assert case["name"] == "motor_thrust"
    assert case["fix"] == "底面"
    assert case["load"]["force_n"] == 500.0
    assert case["acceptance"] == {"min_safety_factor": 2.0}
    assert case["analysis_type"] == "static"
    assert case["authored_at"]


def test_zero_force_requirement_is_refused() -> None:
    """A requirement that loads nothing can be neither met nor missed."""
    with pytest.raises(ValueError, match="0 N"):
        normalize_load_case(_case(load={"at": "rib_main top", "force_n": 0}))


def test_missing_fix_is_refused_with_examples() -> None:
    with pytest.raises(ValueError, match="where the part is held"):
        normalize_load_case(_case(fix=""))


def test_unknown_acceptance_criterion_names_the_supported_ones() -> None:
    with pytest.raises(ValueError, match="min_safety_factor"):
        normalize_load_case(_case(acceptance={"max_wobble": 3}))


def test_unloaded_case_is_allowed() -> None:
    """Modal/thermal cases legitimately carry no force."""
    case = normalize_load_case(_case(load=None, analysis_type="modal", acceptance={}))
    assert "load" not in case
    assert case["analysis_type"] == "modal"


def test_acceptance_becomes_design_targets_with_the_right_operators() -> None:
    case = normalize_load_case(_case(acceptance={
        "min_safety_factor": 2.0, "max_displacement_mm": 0.5, "max_stress_mpa": 120,
    }))
    doc = acceptance_to_design_targets(case)

    assert doc is not None
    by_metric = {t["metric"]: t for t in doc["targets"]}
    assert by_metric["minimum_safety_factor"]["operator"] == ">="
    assert by_metric["minimum_safety_factor"]["value"] == 2.0
    assert by_metric["max_displacement"]["operator"] == "<="
    assert by_metric["max_displacement"]["unit"] == "mm"
    assert by_metric["max_von_mises_stress"]["value"] == 120
    # The honesty contract the existing schema requires.
    assert doc["claim_policy"]["compliance_requires_evidence"] is True
    assert doc["claim_policy"]["physical_correctness_not_claimed"] is True


def test_no_acceptance_writes_no_targets() -> None:
    """An unconstrained load case must not fabricate a requirement nobody wrote."""
    case = normalize_load_case(_case(acceptance={}))
    assert acceptance_to_design_targets(case) is None


def test_reauthoring_replaces_only_its_own_targets() -> None:
    first = normalize_load_case(_case(acceptance={"min_safety_factor": 2.0}))
    doc = acceptance_to_design_targets(first)
    doc["targets"].append({"id": "hand_written", "metric": "total_mass",
                           "operator": "<=", "value": 5})

    revised = normalize_load_case(_case(acceptance={"min_safety_factor": 3.0}))
    doc2 = acceptance_to_design_targets(revised, doc)

    ids = [t["id"] for t in doc2["targets"]]
    assert "hand_written" in ids, "hand-written targets must survive"
    assert ids.count("motor_thrust__minimum_safety_factor") == 1, "no duplicate on revision"
    sf = next(t for t in doc2["targets"] if t["id"] == "motor_thrust__minimum_safety_factor")
    assert sf["value"] == 3.0


def test_other_load_cases_targets_survive() -> None:
    a = normalize_load_case(_case(name="thrust", acceptance={"min_safety_factor": 2.0}))
    doc = acceptance_to_design_targets(a)
    b = normalize_load_case(_case(name="landing", acceptance={"max_displacement_mm": 1.0}))
    doc = acceptance_to_design_targets(b, doc)
    ids = {t["id"] for t in doc["targets"]}
    assert ids == {"thrust__minimum_safety_factor", "landing__max_displacement"}


def test_sidecar_roundtrip_and_revision() -> None:
    doc = read_load_cases(None)
    assert doc["load_cases"] == []
    doc = upsert_load_case(doc, normalize_load_case(_case(name="a")))
    doc = upsert_load_case(doc, normalize_load_case(_case(name="b")))
    doc = upsert_load_case(doc, normalize_load_case(_case(name="a", material="Steel-316L")))
    names = [c["name"] for c in doc["load_cases"]]
    assert sorted(names) == ["a", "b"], "revision must not duplicate"
    a = next(c for c in doc["load_cases"] if c["name"] == "a")
    assert a["material"] == "Steel-316L"


def test_corrupt_sidecar_degrades_to_empty() -> None:
    assert read_load_cases("{not json")["load_cases"] == []
    assert read_load_cases('{"load_cases": "nope"}')["load_cases"] == []
