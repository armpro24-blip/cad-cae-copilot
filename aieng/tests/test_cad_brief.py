"""Tests for the CAD brief + validation-target planning artifact (#290)."""
from __future__ import annotations

from aieng.converters.cad_brief import normalize_cad_brief


def _kinds(brief) -> list[str]:
    return [t["kind"] for t in brief["validation_targets"]]


def test_brief_derives_targets_from_parts_and_size() -> None:
    b = normalize_cad_brief({
        "request": "gearbox", "units": "mm", "model_type": "assembly",
        "parts": [{"name": "housing", "size_mm": [120, 80, 60]}, {"name": "cover"}],
        "overall_size_mm": [120, 80, 66],
    })
    tgts = b["validation_targets"]
    assert ("named_part_present", "housing") in [(t["kind"], t.get("part")) for t in tgts]
    assert ("named_part_present", "cover") in [(t["kind"], t.get("part")) for t in tgts]
    assert any(t["kind"] == "part_count" and t["count"] == 2 for t in tgts)
    assert any(t["kind"] == "overall_size" for t in tgts)
    assert any(t["kind"] == "part_size" and t["part"] == "housing" for t in tgts)
    assert any(t["kind"] == "no_floating_parts" for t in tgts)  # assembly with 2 parts
    assert b["units"] == "mm" and b["model_type"] == "assembly"


def test_brief_units_normalized_and_model_inferred() -> None:
    b = normalize_cad_brief({"units": "in", "parts": [{"name": "x"}]})
    assert b["units"] == "inch"
    assert b["model_type"] == "single_part"  # one part → not an assembly


def test_brief_validates_explicit_targets() -> None:
    b = normalize_cad_brief({"validation_targets": [
        {"kind": "part_center", "part": "a", "center_mm": [0, 0, 0]},
        {"kind": "bogus_kind"},
    ]})
    assert "part_center" in _kinds(b)
    assert "bogus_kind" not in _kinds(b)
    assert any("bogus_kind" in w for w in b["warnings"])


def test_brief_dedupes_derived_and_explicit() -> None:
    b = normalize_cad_brief({
        "parts": [{"name": "a"}],
        "validation_targets": [{"kind": "named_part_present", "part": "a"}],
    })
    n = sum(1 for t in b["validation_targets"] if t["kind"] == "named_part_present" and t.get("part") == "a")
    assert n == 1  # derived + explicit collapse to one


def test_a_single_part_with_two_bodies_still_gets_the_floating_check() -> None:
    """The shape the rule used to skip — and the one where it is certain.

    A `single_part` whose bodies are detached is broken geometry; AGENTS.md
    calls a floating part "usually a coordinate typo". The derivation only ran
    for `assembly`/`product`, i.e. the case where parts can legitimately sit
    apart, bolted with clearance. The existing test above pins the assembly
    case, which is exactly the input shape that cannot expose this.
    """
    brief = normalize_cad_brief({
        "request": "CNC bracket", "units": "mm", "model_type": "single_part",
        "parts": [{"name": "base_plate"}, {"name": "rib_main"}],
    })
    assert any(t["kind"] == "no_floating_parts" for t in brief["validation_targets"])


def test_a_single_body_model_gets_no_floating_check() -> None:
    """With one part there is nothing for it to float away from."""
    brief = normalize_cad_brief({
        "request": "a plate", "units": "mm", "parts": [{"name": "plate"}],
    })
    assert not any(t["kind"] == "no_floating_parts" for t in brief["validation_targets"])


def test_an_organic_model_is_the_one_exception() -> None:
    """A detached body may be intended there — a prop, a hovering element."""
    brief = normalize_cad_brief({
        "request": "a character", "units": "mm", "model_type": "organic",
        "parts": [{"name": "torso"}, {"name": "cape"}],
    })
    assert not any(t["kind"] == "no_floating_parts" for t in brief["validation_targets"])
