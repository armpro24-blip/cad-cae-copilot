"""The entry points must give the same answer about results (P0-1).

Measured on the 44 packages in `aieng-ui/data/projects` before this change:

  8 packages carry results. On every one of them,
      agent_context.cae.results_available          = False
      agent_context.cae.artifact_detection.has_results = True
  and the `/engineering-action-plan` endpoint's own `has_results` = False,
  because it checked ONLY `simulation/results_summary.json` — a path that
  exists in 0 of the 44.

Three fields, three definitions, two of them structurally unable to be true.
An agent cannot be asked to guess which boolean means what.

These tests build the package shapes by hand rather than reading the real
projects: the data directory is not a fixture, and a test that depends on it
stops being reproducible the moment someone deletes a project.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _evidence_entry(path: str, kind: str, exists: bool = True) -> dict:
    """The entry shape `aieng_bridge._upsert_evidence` writes."""
    return {
        "id": path.replace("/", "_").replace(".", "_"),
        "path": path,
        "kind": kind,
        "role": "solver_run_metadata",
        "exists": exists,
        "supports": ["numerical_result_source"] if exists else [],
    }


def _package_with_results(tmp_path: Path) -> Path:
    """A package shaped like the eight real ones that carry results."""
    package = tmp_path / "solved.aieng"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("results/result_summary.json", json.dumps({"metrics": {}}))
        zf.writestr("results/computed_metrics.json", json.dumps({"metrics": {}}))
        zf.writestr("results/evidence_index.json", json.dumps({
            "schema_version": "0.1",
            "evidence_type": "cae",
            "entries": [
                _evidence_entry("results/computed_metrics.json", "computed_metrics"),
                _evidence_entry("simulation/runs/run_001/solver_run.json", "result"),
                _evidence_entry("graph/constraints.json", "setup", exists=False),
            ],
        }))
        zf.writestr("simulation/runs/run_001/solver_run.json", json.dumps(
            {"state": "completed", "solved": True}))
    return package


def test_the_two_has_results_definitions_do_not_drift_apart() -> None:
    """One list, in two modules, that already disagreed once.

    The REST endpoint checked a single legacy member while the library detector
    checked five. Asserting set equality is what makes the comment in
    `engineering_action_plan` true rather than aspirational.
    """
    from aieng.cae_artifact_detector import _RESULT_PATHS
    from app.engineering_action_plan import _RESULT_MEMBERS

    assert set(_RESULT_MEMBERS) == set(_RESULT_PATHS), (
        "the endpoint and the library detector disagree about what a result "
        "artifact is; they answer the same question and must use one list"
    )


def test_the_legacy_only_member_is_not_the_whole_definition() -> None:
    """The specific regression: it used to be the only path checked."""
    from app.engineering_action_plan import _RESULT_MEMBERS

    assert "simulation/results_summary.json" in _RESULT_MEMBERS, "still read"
    assert len(_RESULT_MEMBERS) > 1, (
        "checking only the pre-canonical path made this False for every "
        "package on disk, including all 8 that have results"
    )


class TestTheEntryPointsAgree:
    @staticmethod
    def _cae_payload(package: Path) -> dict:
        from app.package_inspection import summarize_cae_payload

        def member(name: str):
            with zipfile.ZipFile(package) as zf:
                if name not in zf.namelist():
                    return None
                return json.loads(zf.read(name))

        return summarize_cae_payload(
            constraints=None,
            parsed_materials=None,
            parsed_boundary_conditions=None,
            parsed_loads=None,
            cae_mapping=None,
            evidence_index=member("results/evidence_index.json"),
            validation_status=None,
        )

    def test_a_package_with_results_reports_results_available(self, tmp_path: Path) -> None:
        """Was False on all 8 real packages that have results."""
        from aieng.cae_artifact_detector import detect_cae_artifacts

        package = _package_with_results(tmp_path)
        payload = self._cae_payload(package)

        assert payload["results_available"] is True, payload
        assert detect_cae_artifacts(package)["has_results"] is True
        assert payload["evidence_index_shape"] == "entries"

    def test_the_evidence_list_is_populated_too(self, tmp_path: Path) -> None:
        """`evidence_count` was 0 for every real package, for the same reason."""
        payload = self._cae_payload(_package_with_results(tmp_path))

        assert payload["evidence_count"] == 3, payload
        assert payload["result_evidence_count"] == 2, payload
        paths = [item["artifact_path"] for item in payload["evidence"]]
        assert "results/computed_metrics.json" in paths, paths

    def test_a_planned_artifact_is_carried_but_not_counted(self, tmp_path: Path) -> None:
        payload = self._cae_payload(_package_with_results(tmp_path))
        planned = [i for i in payload["evidence"] if i["artifact_exists"] is False]
        assert len(planned) == 1, "the placeholder must still be visible"
        assert "graph/constraints.json" == planned[0]["artifact_path"]

    def test_a_package_with_no_index_answers_unknown_not_no(self) -> None:
        """36 of the 44 packages are in this state; None is the honest answer."""
        from app.package_inspection import summarize_cae_payload

        payload = summarize_cae_payload(
            constraints=None, parsed_materials=None, parsed_boundary_conditions=None,
            parsed_loads=None, cae_mapping=None, validation_status=None,
            evidence_index=None,
        )
        assert payload["results_available"] is None, payload
        assert payload["evidence_index_shape"] is None

    def test_an_index_with_no_result_evidence_answers_no(self, tmp_path: Path) -> None:
        from app.package_inspection import summarize_cae_payload

        payload = summarize_cae_payload(
            constraints=None, parsed_materials=None, parsed_boundary_conditions=None,
            parsed_loads=None, cae_mapping=None, validation_status=None,
            evidence_index={"entries": [_evidence_entry("task/spec.yaml", "task")]},
        )
        assert payload["results_available"] is False, payload


class TestTheFieldAndMetricStates:
    """The other two states P0-1 asks to separate."""

    @staticmethod
    def _payload(**overrides):
        from app.package_inspection import summarize_cae_payload

        base = dict(
            package_members=None, computed_metrics=None, constraints=None,
            parsed_materials=None, parsed_boundary_conditions=None,
            parsed_loads=None, cae_mapping=None, evidence_index=None,
            validation_status=None,
        )
        return summarize_cae_payload(**{**base, **overrides})

    def test_available_fields_reports_what_the_package_carries(self) -> None:
        payload = self._payload(package_members=[
            "results/fields/displacement.summary.json",
            "results/fields/stress.summary.json",
            "geometry/source.py",
        ])
        assert payload["available_fields"] == ["displacement", "stress"]

    def test_a_target_that_merely_mentions_stress_is_not_a_field(self) -> None:
        """The defect: it was derived from constraints, not from field data.

        A project with design targets and no solver run reported
        `available_fields: ["stress"]`, which an agent would reasonably read as
        "there is a stress field to look at".
        """
        payload = self._payload(
            package_members=["geometry/source.py"],
            constraints={"constraints": [
                {"metric": "max_von_mises_stress", "type": "max"},
                {"metric": "max_displacement", "type": "max"},
            ]},
        )
        assert payload["available_fields"] == [], payload["available_fields"]
        assert payload["constraints_count"] == 2, "the targets are still reported"

    def test_metrics_parsed_is_tri_state(self) -> None:
        assert self._payload()["metrics_parsed"] is None
        assert self._payload(computed_metrics={"load_cases": []})["metrics_parsed"] is False
        parsed = self._payload(computed_metrics={"load_cases": [
            {"metrics": {"max_displacement": {"value": 0.14, "unit": "mm"}}}
        ]})
        assert parsed["metrics_parsed"] is True
        assert parsed["parsed_metric_names"] == ["max_displacement"]

    def test_a_metric_slot_with_no_value_is_not_parsed(self) -> None:
        payload = self._payload(computed_metrics={"load_cases": [
            {"metrics": {"max_displacement": {"value": None, "unit": "mm"}}}
        ]})
        assert payload["metrics_parsed"] is False, payload


def test_every_separated_state_reaches_the_agent(tmp_path) -> None:
    """`_cae_block` is an explicit allow-list, and it silently drops what it omits.

    `evidence_index_shape` was defined by the producer in #530 and never listed
    here, so it never reached an agent — a state defined and then lost one call
    later is not a defined state.
    """
    from app.agent_context import _cae_block

    producer_states = {
        "results_available": True,
        "metrics_parsed": True,
        "parsed_metric_names": ["max_displacement"],
        "evidence_index_shape": "entries",
        "available_fields": ["displacement"],
    }
    block = _cae_block({"cae": producer_states}, {})

    for key, value in producer_states.items():
        assert block[key] == value, f"{key} was dropped between producer and agent"
