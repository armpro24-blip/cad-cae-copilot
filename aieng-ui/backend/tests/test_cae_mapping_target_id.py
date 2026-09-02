"""`maps_to` names the CAE target it serves, under a name that says so (#513).

Measured on a freshly built workbench package, `aieng.validate` reported:

    CAE mapping at index 0 references unknown feature_id bc_001
    CAE mapping at index 1 references unknown feature_id load_001

and it was right. Every producer put the setup item's own id in
`maps_to.feature_id`, and every consumer joined it against
`setup.boundary_conditions[].target_feature` — not one of the eight readers ever
looked it up in `graph/feature_graph.json`. The field name described something
the field never held, so the format's own validator rejected every package the
product builds, and any interop consumer that believed the name would join
against the feature graph and find nothing.

The value is a CAE target id, so it is written as `cae_target_id`. Renaming a
join key is the kind of change that fails silently — a deck missing its
`*BOUNDARY` still solves, it just solves an unconstrained model — so the tests
below check the DECK, not only the validator, and check it for both spellings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aieng.simulation.cae_mapping_writer import mapping_target_id
from aieng.validate import validate_package
from app.simulation_runner import _build_calculix_deck

_MESH = """*NODE
1, 0.0, 0.0, 0.0
2, 1.0, 0.0, 0.0
3, 0.0, 1.0, 0.0
4, 0.0, 0.0, 1.0
*ELEMENT, TYPE=C3D4, ELSET=EALL
1, 1, 2, 3, 4
"""

_SETUP = {
    "material_name": "Al",
    "materials": {"Al": {"youngs_modulus": 69000.0, "poisson_ratio": 0.33}},
    "boundary_conditions": [{"target_feature": "bc_001", "type": "fixed"}],
    "loads": [{"target_feature": "load_001", "value_n": 500.0, "direction": [0.0, 0.0, -1.0]}],
}
_NSETS = {"BC_001": [1, 2], "LOAD_001": [3, 4]}


def _mapping(key: str) -> dict:
    """The same mapping written with the current key or the historical one."""
    return {
        "mappings": [
            {"cae_entity": "BC_001", "face_ids": ["face_005"],
             "maps_to": {key: "bc_001", "role": "fixed_support"}},
            {"cae_entity": "LOAD_001", "face_ids": ["face_006"],
             "maps_to": {key: "load_001", "role": "load_application"}},
        ]
    }


@pytest.mark.parametrize("key", ["cae_target_id", "feature_id"])
def test_the_deck_keeps_its_bcs_and_loads_under_either_spelling(key: str) -> None:
    """A package written before the rename must still solve the same problem.

    This is the failure mode worth a test: CalculiX does not object to a deck
    with no `*BOUNDARY`. It solves an unconstrained model and returns numbers.
    """
    deck, bc_written, load_written = _build_calculix_deck(
        _MESH, _SETUP, _NSETS, _mapping(key)
    )
    assert (bc_written, load_written) == (1, 1)
    assert "*BOUNDARY" in deck and "BC_001, 1, 3" in deck
    assert "*CLOAD" in deck and "LOAD_001, 3, " in deck


def test_both_spellings_produce_byte_identical_decks() -> None:
    current, *_ = _build_calculix_deck(_MESH, _SETUP, _NSETS, _mapping("cae_target_id"))
    legacy, *_ = _build_calculix_deck(_MESH, _SETUP, _NSETS, _mapping("feature_id"))
    assert current == legacy


def test_a_mapping_that_names_nothing_yields_no_bc_or_load() -> None:
    """The fallback must not degrade into "any string will do"."""
    orphan = {"mappings": [{"cae_entity": "BC_001", "maps_to": {"description": "the bottom"}}]}
    _deck, bc_written, load_written = _build_calculix_deck(_MESH, _SETUP, _NSETS, orphan)
    assert (bc_written, load_written) == (0, 0)


class TestTheAccessor:
    def test_prefers_the_current_key(self) -> None:
        both = {"maps_to": {"cae_target_id": "bc_001", "feature_id": "stale_001"}}
        assert mapping_target_id(both) == "bc_001"

    def test_falls_back_to_the_historical_key(self) -> None:
        assert mapping_target_id({"maps_to": {"feature_id": "bc_001"}}) == "bc_001"

    def test_accepts_a_bare_maps_to_block(self) -> None:
        assert mapping_target_id({"cae_target_id": "bc_001"}) == "bc_001"

    @pytest.mark.parametrize("mapping", [
        None, "bc_001", {}, {"maps_to": None}, {"maps_to": {}},
        {"maps_to": {"cae_target_id": ""}},
        {"maps_to": {"cae_target_id": 7}},
        {"maps_to": {"interface_id": "iface_1"}},
    ])
    def test_returns_none_when_no_target_is_named(self, mapping: object) -> None:
        assert mapping_target_id(mapping) is None


# ── the validator side ───────────────────────────────────────────────────────

_DEFAULT_BCS = [{"id": "bc_001", "target": "BC_001", "type": "fixed",
                 "dof_start": 1, "dof_end": 3, "value": 0}]


def _package(tmp_path: Path, maps_to: dict, boundary_conditions: list | None = None) -> Path:
    """The smallest package that reaches the CAE-mapping semantics check."""
    import zipfile

    if boundary_conditions is None:
        boundary_conditions = _DEFAULT_BCS
    package = tmp_path / "m.aieng"
    members = {
        "manifest.json": {
            "model_id": "m", "format_version": "0.1.0",
            "units": {"length": "mm", "mass": "kg", "force": "N", "stress": "MPa"},
            "resources": {}, "created_by": {"tool": "t", "created_at": "2026-01-01T00:00:00Z"},
        },
        "simulation/cae_imports/parsed_boundary_conditions.json": {
            "format": "aieng.parsed_cae_boundary_conditions", "format_version": "0.1.0",
            "boundary_conditions": boundary_conditions,
        },
        "simulation/cae_mapping.json": {
            "format": "aieng.cae_mapping", "format_version": "0.1.0",
            "source_files": [], "notes": ["authored"],
            "mappings": [{
                "cae_entity": "BC_001", "cae_type": "boundary_condition_target",
                "mapping_status": "mapped", "mapping_method": "resolved_from_pointer",
                "confidence": "high", "face_ids": ["face_005"], "maps_to": maps_to,
            }],
        },
    }
    with zipfile.ZipFile(package, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, json.dumps(body))
    return package


def _cae_failures(package: Path) -> list[str]:
    return [
        str(m.text) for m in (validate_package(package).messages or [])
        if "FAIL" in str(m.level) and "CAE mapping" in str(m.text)
    ]


def test_a_target_id_naming_a_real_boundary_condition_validates(tmp_path: Path) -> None:
    package = _package(tmp_path, {"cae_target_id": "bc_001", "role": "fixed_support"})
    assert _cae_failures(package) == []


def test_the_historical_spelling_of_a_real_target_still_validates(tmp_path: Path) -> None:
    """Old packages are not retroactively invalid — the data was always fine."""
    package = _package(tmp_path, {"feature_id": "bc_001", "role": "fixed_support"})
    assert _cae_failures(package) == []


def test_a_target_id_naming_nothing_is_still_caught(tmp_path: Path) -> None:
    """The check got a real referent, not a removal."""
    package = _package(tmp_path, {"cae_target_id": "bc_999", "role": "fixed_support"})
    assert any("unknown cae_target_id bc_999" in text for text in _cae_failures(package))


def test_a_dangling_feature_reference_is_still_caught(tmp_path: Path) -> None:
    """The fallback excuses a known CAE target, not any unknown feature."""
    package = _package(tmp_path, {"feature_id": "not_a_feature", "role": "fixed_support"})
    assert any("unknown feature_id not_a_feature" in text for text in _cae_failures(package))


def test_a_package_that_declares_no_bcs_or_loads_still_fails(tmp_path: Path) -> None:
    """A reference check must not switch itself off when there is nothing to check.

    The first version guarded the comparison with `and cae_target_ids`, so a
    package carrying a mapping but no boundary conditions and no loads accepted
    ANY target id. That is the `by-construction` shape: a rule unfireable for a
    whole class of input. An empty set means the mapping is dangling — which is
    precisely what the rule is for.
    """
    package = _package(
        tmp_path,
        {"cae_target_id": "bc_001", "role": "fixed_support"},
        boundary_conditions=[],
    )
    assert any("unknown cae_target_id bc_001" in text for text in _cae_failures(package))


def test_the_selection_key_a_producer_uses_is_a_valid_target(tmp_path: Path) -> None:
    """The two producers name the same entity differently, and both are right.

    `normalize_cae_bindings` writes the record `id`; AI preprocessing writes the
    selection key it also stores as the item's `target_feature` — the value every
    consumer joins on. Accepting only `id` would reject every AI-preprocessed
    package, which is the coin-toss this test pins down.
    """
    package = _package(
        tmp_path,
        {"cae_target_id": "face_bottom_group", "role": "fixed_support"},
        boundary_conditions=[{"id": "bc_001", "target_feature": "face_bottom_group",
                              "target": "BC_001", "type": "fixed",
                              "dof_start": 1, "dof_end": 3, "value": 0}],
    )
    assert _cae_failures(package) == []


def test_maps_to_must_still_identify_something(tmp_path: Path) -> None:
    package = _package(tmp_path, {"description": "the bottom face"})
    assert any("must include" in text for text in _cae_failures(package))
