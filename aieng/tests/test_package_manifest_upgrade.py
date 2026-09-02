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
    """Re-running the migration must be a no-op, on every input shape it accepts."""
    for source in (
        _LEGACY,
        {**_LEGACY, "units": {"length": "m"}},          # partial nested field
        {"schema_version": "0.1", "resources": []},     # malformed value, preserved
        {},
    ):
        once = upgrade_manifest(source, "m")
        assert upgrade_manifest(once, "m") == once, source


def test_an_empty_or_missing_manifest_still_yields_a_valid_one() -> None:
    for source in (None, {}):
        upgraded = upgrade_manifest(source, "m")
        assert upgraded["model_id"] == "m"
        assert upgraded["resources"]["ai"] == {"patches": []}


def _manifest_schema() -> dict[str, Any]:
    """The PACKAGED schema — the one `validate.py` actually serves.

    This test first read a second copy at the repo root that nothing loaded, and
    passed only because `manifest.schema.json` happened to be identical in both.
    That tree is gone (#517); this path is now the only one.
    """
    path = (
        Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "manifest.schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_upgraded_manifest_satisfies_the_real_schema() -> None:
    """The point of the exercise, checked against the schema itself."""
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    conforming_resources = {"results": {"result_summary": "results/result_summary.json"}}
    upgraded = upgrade_manifest({"schema_version": "0.1", "resources": conforming_resources}, "m")

    jsonschema.validate(upgraded, _manifest_schema())


def test_a_run_keyed_resource_tree_validates_at_its_real_depth() -> None:
    """The schema's depth cap was accidental, and it is gone (#513).

    The workbench records solver runs as `simulation.runs.<run_id>.<artifact>` —
    three levels — while the schema spelled two out by hand. Both readers
    (`validate._resource_paths`, `ai.summary_writer._resource_paths`) already
    walked to leaf strings recursively, and the schema's own description already
    said "nested maps of package-relative paths" without stating a depth. Only
    the schema disagreed, so the manifest of every package that had been solved
    failed validation.
    """
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    upgraded = upgrade_manifest(_LEGACY, "m")
    assert upgraded["resources"]["simulation"]["runs"]["run_001"]["solver_input"]
    jsonschema.validate(upgraded, _manifest_schema())


def test_the_resource_index_still_holds_paths_at_any_depth() -> None:
    """Recursive is not "anything goes" — a leaf is still a non-empty path.

    Without this, lifting the depth cap would quietly also lift the leaf check,
    and a resource index could carry numbers, nulls, or empty strings that no
    reader can open.
    """
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    schema = _manifest_schema()
    for depth, bad_leaf in ((1, 7), (3, ""), (4, None)):
        resources: object = bad_leaf
        for level in range(depth):
            resources = {f"level_{level}": resources}
        manifest = {**upgrade_manifest(_LEGACY, "m"), "resources": resources}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(manifest, schema)


def test_a_partial_nested_field_keeps_the_defaults_it_did_not_mention() -> None:
    """"Declared wins" must apply leaf by leaf, not wholesale.

    Replacing the whole `units` object with a partial one leaves the result
    missing required keys — a conformance repair that produces a
    non-conforming manifest. The first version of this test used a COMPLETE
    units dict, which is exactly the input shape that cannot expose the bug.
    """
    upgraded = upgrade_manifest({**_LEGACY, "units": {"length": "m"}}, "m")

    assert upgraded["units"]["length"] == "m", "the declared leaf wins"
    for inherited in ("mass", "force", "stress"):
        assert inherited in upgraded["units"], (
            f"{inherited} was dropped; the manifest no longer conforms"
        )

    partial_provenance = upgrade_manifest({**_LEGACY, "created_by": {"tool": "other"}}, "m")
    assert partial_provenance["created_by"]["tool"] == "other"
    assert partial_provenance["created_by"]["created_at"], "still required"


def test_a_malformed_value_is_preserved_rather_than_replaced_by_defaults() -> None:
    """Discarding it would invent data the package never declared.

    `{"resources": []}` is malformed, but silently substituting the default
    skeleton would report resources the package does not have. Keeping it means
    the validator still says so, which is the honest outcome for a migration
    whose contract is "discard nothing".
    """
    upgraded = upgrade_manifest({"schema_version": "0.1", "resources": []}, "m")
    assert upgraded["resources"] == []
