"""An authored setup document must say who authored it (#513).

`simulation/cae_imports/parsed_*.json` were designed for the CAE import
direction: their schemas require the `parser` that read a deck and the
`source_file` it read. `cae.setup_static` writes the same members from
engineering intent and used to write them bare.

It must not borrow the import provenance either. The synthesised
`source_solver_deck.inp` in that directory supplies the *mesh*; these loads were
never parsed from it, so naming it as `source_file` would be an invented
provenance rather than a missing one. The schema now requires one or the other,
and this writer supplies `authored_by`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aieng.simulation.cae_setup_writer import authored_setup_document

_SCHEMAS = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_each_payload_gets_the_format_its_schema_pins() -> None:
    for key, fmt in (
        ("materials", "aieng.parsed_cae_materials"),
        ("loads", "aieng.parsed_cae_loads"),
        ("boundary_conditions", "aieng.parsed_cae_boundary_conditions"),
    ):
        doc = authored_setup_document(key, [], authored_by="cae.setup_static")
        assert doc["format"] == fmt
        assert doc["format_version"] == "0.1.0"
        assert doc["authored_by"] == "cae.setup_static"
        assert doc[key] == []


def test_an_unknown_payload_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown setup payload"):
        authored_setup_document("stresses", [], authored_by="x")


def test_an_authored_document_must_name_its_author() -> None:
    """`authored_by` is the whole point; an empty one is a missing provenance."""
    with pytest.raises(ValueError, match="authored_by is required"):
        authored_setup_document("loads", [], authored_by="")


@pytest.mark.parametrize(
    ("key", "schema_name", "items"),
    [
        (
            "materials",
            "parsed_cae_materials",
            [{"name": "Al6061-T6", "elastic": {"youngs_modulus": 69000.0, "poisson_ratio": 0.33},
              "density": 2.7e-09, "yield_strength": 276.0}],
        ),
        (
            "boundary_conditions",
            "parsed_cae_boundary_conditions",
            [{"id": "bc_001", "type": "fixed", "target": "BC_001",
              "dof_start": 1, "dof_end": 3, "value": 0}],
        ),
        (
            "loads",
            "parsed_cae_loads",
            [{"id": "load_001", "type": "force", "target": "LOAD_001",
              "value_n": 500.0, "direction": [0, 0, -1]}],
        ),
    ],
)
def test_an_authored_document_satisfies_its_schema(key, schema_name, items) -> None:
    """Including the fields the schemas had not declared.

    `yield_strength` feeds the safety-factor field, `type` picks the *BOUNDARY
    DOF range, and a load stated as magnitude-plus-direction is what an engineer
    writes — the deck generator decomposes it into DOF components itself.
    """
    jsonschema = pytest.importorskip("jsonschema")

    doc = authored_setup_document(key, items, authored_by="cae.setup_static")
    jsonschema.validate(doc, _schema(schema_name))


def test_a_deck_shaped_load_is_equally_valid() -> None:
    """Both forms describe the same load; neither is a lossy stand-in."""
    jsonschema = pytest.importorskip("jsonschema")

    deck_shaped = authored_setup_document(
        "loads",
        [{"id": "load_001_dof3", "target": "LOAD_001", "dof": 3, "value": -500.0}],
        authored_by="cae.setup_static",
    )
    jsonschema.validate(deck_shaped, _schema("parsed_cae_loads"))


def test_a_load_with_neither_form_is_refused_by_the_schema() -> None:
    """The `anyOf` must not degrade into "anything goes"."""
    jsonschema = pytest.importorskip("jsonschema")

    incomplete = authored_setup_document(
        "loads", [{"id": "load_001", "target": "LOAD_001"}], authored_by="cae.setup_static"
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(incomplete, _schema("parsed_cae_loads"))


def test_a_document_with_no_provenance_at_all_is_refused() -> None:
    """A document declaring neither `parser`/`source_file` nor `authored_by`."""
    jsonschema = pytest.importorskip("jsonschema")

    bare = {"format": "aieng.parsed_cae_loads", "format_version": "0.1.0",
            "loads": [{"id": "l", "target": "T", "dof": 3, "value": -1.0}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bare, _schema("parsed_cae_loads"))


def test_the_import_form_still_validates() -> None:
    """Widening for the authoring path must not stop describing an import."""
    jsonschema = pytest.importorskip("jsonschema")

    imported = {
        "format": "aieng.parsed_cae_loads",
        "format_version": "0.1.0",
        "source_file": "simulation/cae_imports/source_solver_deck.inp",
        "parser": {"format": "calculix", "scope": "phase_10a_minimal_cards"},
        "loads": [{"id": "l", "target": "T", "dof": 3, "value": -1.0}],
    }
    jsonschema.validate(imported, _schema("parsed_cae_loads"))
