"""Upgrading a legacy manifest must repair it without losing anything (#513).

Packages written before the workbench used `build_manifest` carry
`{"schema_version": "0.1"}` plus whatever `resources` later writers merged in.
They declare no `model_id`, so the format's validator rejects them and its own
AI summary writer reports each as `unknown_model`.

The upgrade is deliberately additive. A migration that "fixes" a package by
discarding fields it does not recognise trades one silent data loss for another,
so these tests pin what must survive as firmly as what must appear.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION
from aieng.package import LEGACY_MANIFEST_KEYS, upgrade_manifest

_LEGACY: dict[str, Any] = {
    "schema_version": "0.1",
    "resources": {
        "results": {"result_summary": "results/result_summary.json"},
        "simulation": {"runs": {"run_001": {"solver_input": "simulation/runs/run_001/x.inp"}}},
    },
}


def test_a_legacy_stub_gains_every_required_field() -> None:
    upgraded = upgrade_manifest(_LEGACY, "value_demo_cantilever")

    assert upgraded["model_id"] == "value_demo_cantilever"
    assert upgraded["format_version"] == FORMAT_VERSION
    assert upgraded["units"]["length"] == "mm"
    assert upgraded["created_by"]["tool"].startswith("aieng ")
    assert set(LEGACY_MANIFEST_KEYS).isdisjoint(upgraded), (
        "the legacy-only keys are additionalProperties violations carrying "
        "nothing; they go"
    )


def test_recorded_resources_survive_the_upgrade() -> None:
    """The resources block is real content later writers merged in."""
    upgraded = upgrade_manifest(_LEGACY, "m")
    resources = upgraded["resources"]

    assert resources["results"]["result_summary"] == "results/result_summary.json"
    assert resources["simulation"]["runs"]["run_001"]["solver_input"].endswith("x.inp")
    # …and the default skeleton is still there for the branches it did not carry.
    assert resources["ai"] == {"patches": []}
    assert "previews" in resources


def test_an_unknown_key_is_kept_not_discarded() -> None:
    """`geometry_execution` is a real provenance record the CAD path writes.

    It is currently an `additionalProperties` violation, which makes dropping it
    tempting — and wrong. The schema question is tracked separately; a migration
    must not answer it by deleting evidence.
    """
    upgraded = upgrade_manifest(
        {**_LEGACY, "geometry_execution": {"executed_at": "2026-01-01T00:00:00Z"}}, "m"
    )
    assert upgraded["geometry_execution"] == {"executed_at": "2026-01-01T00:00:00Z"}


def test_a_declared_field_wins_over_the_canonical_default() -> None:
    """An upgrade must never rename a model or rewrite someone's provenance."""
    stated = {
        "model_id": "chosen_by_the_author",
        "created_by": {"tool": "some other writer", "created_at": "2020-01-01T00:00:00Z"},
        "units": {"length": "m", "mass": "kg", "time": "s", "force": "N"},
    }
    upgraded = upgrade_manifest({**_LEGACY, **stated}, "derived_from_filename")

    assert upgraded["model_id"] == "chosen_by_the_author"
    assert upgraded["created_by"]["tool"] == "some other writer"
    assert upgraded["units"]["length"] == "m"


def test_upgrading_twice_changes_nothing() -> None:
    once = upgrade_manifest(_LEGACY, "m")
    twice = upgrade_manifest(once, "m")
    assert once == twice, "the migration must be safe to re-run"


def test_an_empty_or_missing_manifest_still_yields_a_valid_one() -> None:
    for source in (None, {}):
        upgraded = upgrade_manifest(source, "m")
        assert upgraded["model_id"] == "m"
        assert upgraded["resources"]["ai"] == {"patches": []}


def _manifest_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "schemas" / "manifest.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_upgraded_manifest_satisfies_the_real_schema() -> None:
    """The point of the exercise, checked against the schema itself."""
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    conforming_resources = {"results": {"result_summary": "results/result_summary.json"}}
    upgraded = upgrade_manifest({"schema_version": "0.1", "resources": conforming_resources}, "m")

    jsonschema.validate(upgraded, _manifest_schema())


def test_the_upgrade_cannot_repair_a_resources_tree_the_writer_shaped_wrongly() -> None:
    """Where this migration's power ends, stated rather than hidden.

    The workbench records solver runs as a nested map —
    `simulation: {runs: {run_001: {solver_input: "..."}}}` — while the schema
    declares each resource entry as a path string or a list of them. Adding the
    identity fields cannot fix that, and inventing a flattening here would be
    guessing at a writer's intent from a migration script. It is the residual
    `manifest.json` failure the conformance ratchet records, and it belongs with
    the other writer questions in #513.
    """
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    upgraded = upgrade_manifest(_LEGACY, "m")
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        jsonschema.validate(upgraded, _manifest_schema())

    assert "runs" in str(excinfo.value), (
        "if this stops failing, the writer or the schema was fixed — drop this "
        "test and lower the ratchet's manifest.json ceiling"
    )
