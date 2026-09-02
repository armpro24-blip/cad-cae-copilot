"""A CAE mapping must record how each binding came to exist (#513).

`simulation/cae_mapping.json` has three producers. The CAE import path filled
the schema's provenance fields; the workbench's own authoring path and AI
preprocessing wrote a lean document without them, so the artifact could not
answer "how was this face chosen, and how much should I trust it" — the same
question the credibility stamp answers for results.

The fields are cheap to fill honestly and the answers differ, which is the
point: an explicit `@face:` pointer involved no inference, a resolved
engineering phrase did, and an LLM proposal is weaker than both.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aieng.simulation.cae_mapping_writer import (
    CAE_MAPPING_FORMAT,
    METHOD_AI,
    METHOD_INTENT,
    METHOD_POINTER,
    finalize_cae_mapping,
)

_BC = {
    "cae_entity": "BC_001",
    "face_ids": ["face_005"],
    "maps_to": {"feature_id": "bc_001", "role": "fixed_support"},
}
_LOAD = {
    "cae_entity": "LOAD_001_L",
    "face_ids": ["face_020"],
    "maps_to": {"feature_id": "load_001", "role": "load_application"},
}


def _finalized(method: str = METHOD_POINTER, **extra: Any) -> dict[str, Any]:
    return finalize_cae_mapping({"mappings": [dict(_BC), dict(_LOAD)], **extra}, method=method)


def test_the_document_gains_its_identity_and_a_provenance_note() -> None:
    doc = _finalized()

    assert doc["format"] == CAE_MAPPING_FORMAT
    assert doc["format_version"] == "0.1.0"
    assert doc["source_files"] == [], "an authored setup parsed no external deck"
    assert doc["notes"] and "no face was inferred" in doc["notes"][0]
    assert "schema_version" not in doc, "never a declared property"


def test_each_mapping_records_its_method_and_what_that_method_is_worth() -> None:
    pointer = _finalized(METHOD_POINTER)["mappings"][0]
    assert pointer["mapping_method"] == METHOD_POINTER
    assert pointer["confidence"] == "high", "an explicit face id involves no inference"

    intent = _finalized(METHOD_INTENT)["mappings"][0]
    assert intent["confidence"] == "medium", "a resolved phrase could pick the wrong face"

    ai = _finalized(METHOD_AI)["mappings"][0]
    assert ai["confidence"] == "medium"


def test_cae_type_comes_from_the_role_the_writer_already_recorded() -> None:
    bc, load = _finalized()["mappings"]
    assert bc["cae_type"] == "boundary_condition_target"
    assert load["cae_type"] == "load_target"


def test_cae_type_falls_back_on_the_nset_naming_convention() -> None:
    """A mapping with no role still has to be classified."""
    doc = finalize_cae_mapping(
        {"mappings": [
            {"cae_entity": "SOMETHING_L", "face_ids": ["f1"], "maps_to": {"feature_id": "x"}},
            {"cae_entity": "SOMETHING", "face_ids": ["f1"], "maps_to": {"feature_id": "x"}},
        ]},
        method=METHOD_POINTER,
    )
    assert doc["mappings"][0]["cae_type"] == "load_target"
    assert doc["mappings"][1]["cae_type"] == "boundary_condition_target"


def test_an_unbound_mapping_is_unresolved_with_no_confidence() -> None:
    """Status is real information the lean document was throwing away."""
    doc = finalize_cae_mapping(
        {"mappings": [{"cae_entity": "X", "face_ids": [], "maps_to": None}]},
        method=METHOD_POINTER,
    )
    entry = doc["mappings"][0]
    assert entry["mapping_status"] == "unresolved"
    assert entry["confidence"] == "none", "nothing was bound; claiming high would be a lie"


def test_a_legacy_confidence_that_was_really_a_method_moves() -> None:
    """AI preprocessing wrote `confidence: "ai_generated"`.

    That answers "how was this made", not "how sure are we" — and it is not a
    value the confidence enum has. It moves to `mapping_method`, and the
    confidence becomes an actual confidence.
    """
    doc = finalize_cae_mapping(
        {"mappings": [{**_BC, "confidence": "ai_generated"}]}, method=METHOD_POINTER
    )
    entry = doc["mappings"][0]
    assert entry["mapping_method"] == METHOD_AI, "the method the value really described"
    assert entry["confidence"] == "medium"


def test_a_stated_confidence_is_respected() -> None:
    doc = finalize_cae_mapping({"mappings": [{**_BC, "confidence": "low"}]}, method=METHOD_POINTER)
    assert doc["mappings"][0]["confidence"] == "low"


def test_existing_notes_and_source_files_are_not_overwritten() -> None:
    doc = finalize_cae_mapping(
        {"mappings": [dict(_BC)], "notes": ["imported from deck.inp"],
         "source_files": ["simulation/deck.inp"]},
        method="user_provided",
    )
    assert doc["notes"] == ["imported from deck.inp"]
    assert doc["source_files"] == ["simulation/deck.inp"]


def test_finalizing_twice_changes_nothing() -> None:
    once = _finalized()
    assert finalize_cae_mapping(once, method=METHOD_POINTER) == once


def test_face_signatures_and_face_ids_survive() -> None:
    """They are load-bearing: binding re-verification reads them."""
    signed = {**_BC, "face_signatures": {"face_005": {"surface_type": "plane", "normal": [0, 0, -1]}}}
    entry = finalize_cae_mapping({"mappings": [signed]}, method=METHOD_POINTER)["mappings"][0]
    assert entry["face_ids"] == ["face_005"]
    assert entry["face_signatures"]["face_005"]["surface_type"] == "plane"


def test_the_finalized_document_satisfies_the_packaged_schema() -> None:
    """Against the schema the library actually serves.

    There used to be a second copy of every schema at the repo root that nothing
    loaded, diverged from this one in 14 files; a test validating against it was
    green about a file the product does not use. Removed in #517 — this is now
    the only tree, and `validate.py` reads it through importlib.resources.
    """
    jsonschema = pytest.importorskip("jsonschema")

    schema_path = (
        Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "cae_mapping.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    for method in (METHOD_POINTER, METHOD_INTENT, METHOD_AI):
        jsonschema.validate(_finalized(method), schema)


def test_partially_mapped_keeps_a_real_confidence() -> None:
    """`partially_mapped` + `confidence: "none"` is rejected by validate.py.

    Something WAS bound, so "none" is both untrue and invalid — defaulting it
    that way would make this finalizer produce a document the validator refuses.
    """
    doc = finalize_cae_mapping(
        {"mappings": [{**_BC, "mapping_status": "partially_mapped"}]}, method=METHOD_POINTER
    )
    assert doc["mappings"][0]["confidence"] == "high"

    for unbound in ("unmapped", "unresolved"):
        entry = finalize_cae_mapping(
            {"mappings": [{**_BC, "mapping_status": unbound}]}, method=METHOD_POINTER
        )["mappings"][0]
        assert entry["confidence"] == "none", unbound


def test_a_malformed_mapping_is_passed_through_not_dropped() -> None:
    """Discarding it would delete a binding to make a validator happy."""
    doc = finalize_cae_mapping(
        {"mappings": [dict(_BC), "not a mapping", None]}, method=METHOD_POINTER
    )
    assert len(doc["mappings"]) == 3, "nothing may vanish"
    assert doc["mappings"][1] == "not a mapping"
    assert doc["mappings"][2] is None

    wrong_type = finalize_cae_mapping({"mappings": {"oops": True}}, method=METHOD_POINTER)
    assert wrong_type["mappings"] == {"oops": True}, "left for the validator to report"

    absent = finalize_cae_mapping({}, method=METHOD_POINTER)
    assert absent["mappings"] == [], "a genuinely absent key gets the required default"


def test_an_unknown_method_is_refused_at_the_call_site() -> None:
    """A caller typo would otherwise surface as a schema failure three steps on."""
    with pytest.raises(ValueError, match="unknown mapping_method"):
        finalize_cae_mapping({"mappings": []}, method="resolved_from_vibes")


def test_a_populated_maps_to_must_still_identify_something() -> None:
    """The new descriptive fields must not weaken the identity requirement.

    `minProperties: 1` alone would let `{"description": "top face"}` satisfy the
    schema while validate.py rejects it for carrying neither `feature_id` nor
    `interface_id` — adding optional fields quietly loosened a real constraint.
    """
    jsonschema = pytest.importorskip("jsonschema")

    schema_path = (
        Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "cae_mapping.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    described_only = finalize_cae_mapping(
        {"mappings": [{"cae_entity": "X", "face_ids": ["f1"],
                       "maps_to": {"description": "top face"}}]},
        method=METHOD_POINTER,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(described_only, schema)

    identified = finalize_cae_mapping(
        {"mappings": [{"cae_entity": "X", "face_ids": ["f1"],
                       "maps_to": {"description": "top face", "feature_id": "feat_1"}}]},
        method=METHOD_POINTER,
    )
    jsonschema.validate(identified, schema)
