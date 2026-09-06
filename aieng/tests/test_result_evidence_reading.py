"""One definition of "does this package register result evidence" (P0-1).

Three components each used a different spelling of the same document:

  the schema requires   `evidence_items`, entries typed `evidence_type`
  the workbench writes  `entries`,        entries typed `kind`
  the reader looked for `evidence_items` + `evidence_type` in
                        `{"solver_result", "mesh_evidence"}` — values no
                        writer produces

So `results_available` was structurally incapable of being true. Measured
across the 44 packages on disk: 8 carry an evidence index, **all 8 use
`entries`**, none uses `evidence_items`, and every one of the 8 reported
`results_available: False` while `artifact_detection.has_results` reported
True on the same call.

`read_result_evidence` accepts both shapes rather than picking one. Renaming
either side would fix new packages and silently redefine every existing one as
having no results — the same trade already made for `maps_to.cae_target_id`
and for `@face:` setup targets.
"""

from __future__ import annotations

import pytest

from aieng.cae_result_summary import read_result_evidence


def _entry(**overrides: object) -> dict:
    """An entry in the shape `aieng_bridge._upsert_evidence` actually writes."""
    return {
        "id": "results_computed_metrics_json",
        "path": "results/computed_metrics.json",
        "kind": "result",
        "role": "solver_run_metadata",
        "exists": True,
        "supports": ["numerical_result_source"],
        **overrides,
    }


class TestBothSpellingsRead:
    def test_the_shape_the_workbench_writes(self) -> None:
        reading = read_result_evidence({"entries": [_entry()]})
        assert reading["registered"] is True
        assert reading["result_entry_count"] == 1
        assert reading["index_shape"] == "entries"

    def test_the_shape_the_schema_requires(self) -> None:
        reading = read_result_evidence(
            {"evidence_items": [{"evidence_type": "solver_result", "exists": True}]}
        )
        assert reading["registered"] is True
        assert reading["index_shape"] == "evidence_items"

    def test_the_shape_is_reported_so_a_caller_can_say_legacy_out_loud(self) -> None:
        """P0-1 asks for old evaluations to be marked, not silently inherited."""
        assert read_result_evidence({"entries": []})["index_shape"] == "entries"
        assert read_result_evidence({"evidence_items": []})["index_shape"] == "evidence_items"

    def test_an_index_carrying_both_keeps_the_evidence_from_both(self) -> None:
        """A migrated or hand-merged index is the case a first-match loses.

        An empty `evidence_items` beside a populated `entries` would report
        `registered: False` — the reader discarding the very data it exists to
        find, which is worse than not reading it at all.
        """
        reading = read_result_evidence({
            "evidence_items": [],
            "entries": [_entry()],
        })
        assert reading["registered"] is True, reading
        assert reading["result_entry_count"] == 1
        assert reading["index_shape"] == "evidence_items+entries"

    def test_entries_from_both_containers_are_counted_together(self) -> None:
        reading = read_result_evidence({
            "evidence_items": [{"evidence_type": "solver_result", "exists": True}],
            "entries": [_entry(), _entry(kind="setup")],
        })
        assert reading["entry_count"] == 3
        assert reading["result_entry_count"] == 2


class TestTheThreeAnswersAreDistinct:
    """"No index" and "an index with no results" are different facts."""

    def test_no_index_at_all_is_unknown_not_false(self) -> None:
        for absent in ({}, None, [], "not a document", {"schema_version": "0.1"}):
            reading = read_result_evidence(absent)
            assert reading["registered"] is None, absent
            assert reading["index_shape"] is None

    def test_an_index_with_no_result_entries_is_false(self) -> None:
        reading = read_result_evidence(
            {"entries": [_entry(kind="setup"), _entry(kind="task")]}
        )
        assert reading["registered"] is False
        assert reading["entry_count"] == 2
        assert reading["result_entry_count"] == 0

    def test_result_entries_make_it_true(self) -> None:
        assert read_result_evidence({"entries": [_entry()]})["registered"] is True


class TestEvidenceMustActuallyExist:
    """The index also lists evidence it merely EXPECTS."""

    def test_a_planned_artifact_is_not_evidence(self) -> None:
        """`exists: false` with empty `supports` is a placeholder.

        Counting it would report evidence the package does not have — the
        `invented-data` pattern, in the field that gates whether an agent may
        cite a result at all.
        """
        reading = read_result_evidence(
            {"entries": [_entry(exists=False, supports=[])]}
        )
        assert reading["registered"] is False
        assert reading["entry_count"] == 1
        assert reading["present_entry_count"] == 0

    def test_a_missing_exists_flag_is_not_read_as_absent(self) -> None:
        """An older writer that never tracked presence claimed no absence."""
        entry = _entry()
        del entry["exists"]
        assert read_result_evidence({"entries": [entry]})["registered"] is True

    def test_present_and_planned_entries_are_counted_apart(self) -> None:
        reading = read_result_evidence({"entries": [
            _entry(), _entry(exists=False), _entry(kind="setup"),
        ]})
        assert reading["entry_count"] == 3
        assert reading["present_entry_count"] == 2
        assert reading["result_entry_count"] == 1


@pytest.mark.parametrize("kind,expected", [
    # both vocabularies, result-bearing
    ("result", True), ("solver_result", True),
    ("computed_metrics", True), ("mesh_evidence", True),
    # evidence of something that is not a result
    ("setup", False), ("task", False), ("validation", False), ("field", False),
    ("", False),
])
def test_only_result_bearing_kinds_count(kind: str, expected: bool) -> None:
    reading = read_result_evidence({"entries": [_entry(kind=kind)]})
    assert reading["registered"] is expected, kind


def test_malformed_entries_are_skipped_not_crashed_on() -> None:
    reading = read_result_evidence({"entries": ["a string", None, 7, _entry()]})
    assert reading["registered"] is True
    assert reading["entry_count"] == 1, "only the object entry is an entry"


class TestParsedMetrics:
    """"Are numbers actually extracted?" — the third separated state.

    `results/computed_metrics.json` can exist and carry a metric whose `value`
    is None: the extractor recorded the slot and filled nothing. Reporting that
    as parsed puts a number-shaped hole where a reader expects a number.
    """

    @staticmethod
    def _document(*metrics: tuple[str, object]) -> dict:
        return {"load_cases": [
            {"id": "load_case_001",
             "metrics": {name: {"value": value, "unit": "mm"} for name, value in metrics}}
        ]}

    def test_no_document_is_unknown_not_no(self) -> None:
        from aieng.cae_result_summary import read_parsed_metrics

        for absent in (None, {}, [], "not a document"):
            assert read_parsed_metrics(absent)["parsed"] is None, absent

    def test_a_document_with_values_is_parsed(self) -> None:
        from aieng.cae_result_summary import read_parsed_metrics

        reading = read_parsed_metrics(
            self._document(("max_displacement", 0.14), ("max_von_mises_stress", 15.0))
        )
        assert reading["parsed"] is True
        assert reading["metric_names"] == ["max_displacement", "max_von_mises_stress"]
        assert reading["load_case_count"] == 1

    def test_a_metric_with_no_value_is_not_parsed(self) -> None:
        from aieng.cae_result_summary import read_parsed_metrics

        reading = read_parsed_metrics(self._document(("max_displacement", None)))
        assert reading["parsed"] is False, "the slot exists; the number does not"
        assert reading["metric_names"] == []
        assert reading["load_case_count"] == 1, "the load case is still there"

    def test_metrics_are_collected_across_load_cases(self) -> None:
        from aieng.cae_result_summary import read_parsed_metrics

        reading = read_parsed_metrics({"load_cases": [
            {"metrics": {"max_displacement": {"value": 0.1}}},
            {"metrics": {"max_von_mises_stress": {"value": 12.0}}},
            "not a load case",
        ]})
        assert reading["metric_names"] == ["max_displacement", "max_von_mises_stress"]
        assert reading["load_case_count"] == 2


class TestFieldMembers:
    """Which fields a package carries, whichever era wrote them."""

    def test_the_suffix_the_workbench_writes_is_recognised(self) -> None:
        from aieng.cae_artifact_detector import field_names

        assert field_names({
            "results/fields/displacement.summary.json",
            "results/fields/stress.summary.json",
        }) == ["displacement", "stress"]

    def test_the_import_era_suffix_still_is(self) -> None:
        from aieng.cae_artifact_detector import field_names

        assert field_names({"results/fields/von_mises_stress.vtu"}) == ["von_mises_stress"]

    def test_unrelated_members_are_not_fields(self) -> None:
        from aieng.cae_artifact_detector import field_members, field_names

        members = {"results/result_summary.json", "geometry/source.py",
                   "results/fields/", "results/fields/notes.txt"}
        assert field_members(members) == []
        assert field_names(members) == []
