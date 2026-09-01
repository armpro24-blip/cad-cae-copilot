"""`analysis/cae_result_map.json` must earn its credibility stamp.

Dogfooding `cae.map_results` — a documented pipeline step no test named — found
the stamp hard-coded: ``classify_credibility("solver", solver_executed=True)``.
The classifier's whole purpose is the honesty invariant "the tier is never more
credible than the evidence", and it was being handed its own conclusion, so the
downgrade could not fire on this path.

Measured on the #368 cantilever package, before the fix — same package, same
classifier, two callers:

| package state                     | cae_result_map      | result_summary            |
|-----------------------------------|---------------------|---------------------------|
| real ccx run                      | executed_solver_result | executed_solver_result |
| mesh accuracy band = `unreliable` | executed_solver_result | **unreliable_mesh**       |
| **no solver run evidence at all** | executed_solver_result | **imported_computed_metrics** |

The third row is the serious one: numbers that were imported, never solved, were
stamped "Executed-solver result". When two sibling paths disagree about honesty,
the honest one is the spec.
"""

from __future__ import annotations

from typing import Any

from aieng.converters.cae_result_contract import (
    DEFAULT_LOAD_CASE_ID,
    normalize_calculix_computed_metrics,
    normalize_calculix_field_regions,
)
from aieng.converters.cae_result_map import map_cae_results

_TOPOLOGY: dict[str, Any] = {
    "entities": [
        {"id": "body_001", "type": "solid", "bounding_box": [-50, -10, -10, 50, 10, 10],
         "face_ids": ["face_001"]},
    ]
}
_CM: dict[str, Any] = {
    "schema_version": "0.1",
    "load_cases": [{"id": "lc_real", "results": [
        {"result_type": "stress", "metric": "max_von_mises_stress", "max": 7.2, "unit": "MPa"},
    ]}],
}
_FR: dict[str, Any] = {
    "schema_version": "0.1",
    "regions": [{"id": "cluster_001", "result_type": "stress",
                 "load_case_id": DEFAULT_LOAD_CASE_ID, "load_case_id_is_placeholder": True,
                 "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "value": {"peak": 7.2, "max": 7.2, "unit": "MPa"}, "node_count": 12}],
}


def _map(**evidence: Any) -> dict[str, Any]:
    return map_cae_results(
        computed_metrics=_CM, field_regions=_FR,
        topology_map=_TOPOLOGY, object_registry={}, **evidence,
    )


# ── the stamp must follow the evidence ───────────────────────────────────────

def test_an_executed_run_still_earns_the_top_tier() -> None:
    """The fix must not simply demote everything — that would be no gate either."""
    stamp = _map(solver_executed=True)["credibility"]
    assert stamp["tier"] == "executed_solver_result"
    assert stamp["rank"] == 4
    assert stamp["production_ready"] is False


def test_metrics_with_no_solver_evidence_are_not_an_executed_solver_result() -> None:
    """The measured row 3: imported numbers stamped as if a solver produced them."""
    stamp = _map(solver_executed=False)["credibility"]
    assert stamp["tier"] == "unverified"
    assert stamp["rank"] == 0
    assert "solver_executed" in stamp["downgrade_reason"]


def test_omitting_the_evidence_does_not_grant_the_claim() -> None:
    """A caller that supplies no evidence must not be believed by default.

    This is the shape of the original defect: the value was supplied by the
    producer rather than read, so no input could ever contradict it.
    """
    assert _map()["credibility"]["tier"] == "unverified"


def test_an_unreliable_mesh_downgrades_the_map_as_it_downgrades_the_summary() -> None:
    """The solver ran, on a mesh that cannot resolve what it was asked."""
    stamp = _map(solver_executed=True, mesh_accuracy_band="unreliable")["credibility"]
    assert stamp["tier"] == "unverified"
    assert "under-predicted" in stamp["downgrade_reason"]

    for band in ("reliable", "marginal", None):
        kept = _map(solver_executed=True, mesh_accuracy_band=band)["credibility"]
        assert kept["tier"] == "executed_solver_result", f"band {band!r} must not downgrade"


def test_the_evidence_behind_the_stamp_is_recorded() -> None:
    """A stamp nobody can audit is the same as a stamp nobody derived."""
    provenance = _map(solver_executed=True, mesh_accuracy_band="marginal")["provenance"]
    assert provenance["solver_evidence"] == {
        "solver_executed": True,
        "mesh_accuracy_band": "marginal",
        "read_from_package": True,
        "results_present": True,
    }
    assert _map()["provenance"]["solver_evidence"]["read_from_package"] is False


def test_the_notes_do_not_contradict_the_stamp() -> None:
    """It said "no solver/mesher executed" beside `solver_executed: true`."""
    result = _map(solver_executed=True)
    note = result["notes"][0]
    assert "runs no solver" in note, note
    assert result["credibility"]["signals"]["solver_executed"] is True


# ── a placeholder load-case id is not a second load case ─────────────────────

def test_a_placeholder_region_id_is_aligned_and_the_alignment_is_recorded() -> None:
    """Measured: the map reported two load cases for one analysis.

    `overall` carried the metrics' real id, every cluster carried the
    normalizer's placeholder, and the two halves of one artifact could not be
    joined — while both reported the same 7.2 MPa peak.
    """
    result = _map(solver_executed=True)

    assert result["load_cases"] == ["lc_real"], "one analysis, one load case"
    assert {o["load_case_id"] for o in result["overall"]} == {"lc_real"}
    assert {r["load_case_id"] for r in result["mapped_results"]} == {"lc_real"}
    # Substituted, not silently: the caller can see what was aligned.
    assert result["mapped_results"][0]["load_case_id_source"] == (
        f"aligned_from_placeholder:{DEFAULT_LOAD_CASE_ID}"
    )


def test_two_genuinely_declared_ids_are_never_merged() -> None:
    """Alignment is for a placeholder, not for disagreeing declarations."""
    regions = {"schema_version": "0.1", "regions": [dict(
        _FR["regions"][0], load_case_id="lc_other", load_case_id_is_placeholder=False)]}
    result = map_cae_results(
        computed_metrics=_CM, field_regions=regions,
        topology_map=_TOPOLOGY, object_registry={}, solver_executed=True,
    )
    assert sorted(result["load_cases"]) == ["lc_other", "lc_real"]
    assert "load_case_id_source" not in result["mapped_results"][0]


def test_ambiguity_is_left_alone_when_several_ids_are_declared() -> None:
    """With two real load cases, which one a placeholder meant is unknowable."""
    metrics = {"schema_version": "0.1", "load_cases": [
        {"id": "lc_a", "results": []}, {"id": "lc_b", "results": []},
    ]}
    result = map_cae_results(
        computed_metrics=metrics, field_regions=_FR,
        topology_map=_TOPOLOGY, object_registry={}, solver_executed=True,
    )
    assert result["mapped_results"][0]["load_case_id"] == DEFAULT_LOAD_CASE_ID
    assert "load_case_id_source" not in result["mapped_results"][0]


# ── the normalizer must say when it invented the id ──────────────────────────

def test_the_normalizer_marks_an_id_it_stamped_in() -> None:
    metrics = normalize_calculix_computed_metrics(
        {"load_cases": [{"metrics": {"max_von_mises_stress": {"value": 7.2, "unit": "MPa"}}}]}
    )
    assert metrics["load_cases"][0]["id"] == DEFAULT_LOAD_CASE_ID
    assert metrics["load_cases"][0]["id_is_placeholder"] is True

    regions = normalize_calculix_field_regions(
        {"field": "S", "clusters": [{"id": "c1", "location": {"x": 0, "y": 0, "z": 0},
                                     "magnitude": {"value": 7.2, "unit": "MPa"}}]}
    )
    assert regions["regions"][0]["load_case_id_is_placeholder"] is True


def test_a_declared_id_is_not_marked_as_a_placeholder() -> None:
    metrics = normalize_calculix_computed_metrics(
        {"load_cases": [{"id": "lc_real", "metrics": {}}]}
    )
    assert "id_is_placeholder" not in metrics["load_cases"][0]

    regions = normalize_calculix_field_regions(
        {"field": "S", "load_case_id": "lc_real",
         "clusters": [{"id": "c1", "location": {"x": 0, "y": 0, "z": 0},
                       "magnitude": {"value": 7.2, "unit": "MPa"}}]}
    )
    assert regions["regions"][0]["load_case_id_is_placeholder"] is False


def test_an_artifact_written_before_the_flag_is_still_recognised() -> None:
    """Self-healing: the placeholder is recomputed at read time.

    A package normalized before `load_case_id_is_placeholder` existed carries no
    flag; recognising the exported placeholder value keeps those packages
    correct instead of permanently mis-labelled (`stale-artifact`).
    """
    legacy = {"schema_version": "0.1", "regions": [
        {k: v for k, v in _FR["regions"][0].items() if k != "load_case_id_is_placeholder"}
    ]}
    result = map_cae_results(
        computed_metrics=_CM, field_regions=legacy,
        topology_map=_TOPOLOGY, object_registry={}, solver_executed=True,
    )
    assert result["load_cases"] == ["lc_real"]
    assert result["mapped_results"][0]["load_case_id_source"].startswith("aligned_from_placeholder")


# ── the two callers must read the same evidence ──────────────────────────────

def _minimal_package(path, *, solver_run: bool, band: str | None = None) -> None:
    """A package with just the members both credibility paths read."""
    import json
    import zipfile

    mesh: dict[str, Any] = {"element_type": "C3D10", "schema_version": "0.1"}
    if band:
        mesh["accuracy"] = {"band": band, "reason": "fixture"}
    members = {
        "analysis/computed_metrics.json": _CM,
        "analysis/field_regions.json": _FR,
        "geometry/topology_map.json": _TOPOLOGY,
        "simulation/mesh/mesh_metadata.json": mesh,
    }
    if solver_run:
        members["simulation/runs/run_001/solver_run.json"] = {
            "state": "completed", "solved": True, "solver": "calculix",
        }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            zf.writestr(name, json.dumps(payload))


def test_the_package_builder_reads_the_evidence_rather_than_assuming_it(tmp_path) -> None:
    """The tool path, not just the pure function.

    `build_cae_result_map_for_package` is what `cae.map_results` calls; if it
    did not pass the evidence through, every test above would pass while the
    tool stayed broken.
    """
    from aieng.converters.cae_result_map import build_cae_result_map_for_package

    solved = tmp_path / "solved.aieng"
    _minimal_package(solved, solver_run=True)
    stamp = build_cae_result_map_for_package(solved)["credibility"]
    assert stamp["tier"] == "executed_solver_result"

    imported = tmp_path / "imported.aieng"
    _minimal_package(imported, solver_run=False)
    stamp = build_cae_result_map_for_package(imported)["credibility"]
    assert stamp["tier"] == "unverified", "no solver run in the package, yet it claimed one"

    coarse = tmp_path / "coarse.aieng"
    _minimal_package(coarse, solver_run=True, band="unreliable")
    stamp = build_cae_result_map_for_package(coarse)["credibility"]
    assert stamp["tier"] == "unverified", "the mesh band must reach the classifier"


def test_the_map_and_the_summary_agree_about_the_evidence(tmp_path) -> None:
    """One classifier, one reader, so the two stamps cannot drift apart again."""
    from aieng.cae_result_summary import read_solver_evidence
    from aieng.converters.cae_result_map import build_cae_result_map_for_package
    import zipfile

    for solver_run, band in ((True, None), (False, None), (True, "unreliable")):
        pkg = tmp_path / f"case_{solver_run}_{band}.aieng"
        _minimal_package(pkg, solver_run=solver_run, band=band)
        with zipfile.ZipFile(pkg) as zf:
            evidence = read_solver_evidence(zf)
        recorded = build_cae_result_map_for_package(pkg)["provenance"]["solver_evidence"]
        assert recorded["solver_executed"] == evidence["solver_executed"]
        assert recorded["mesh_accuracy_band"] == evidence["mesh_accuracy_band"]


def test_a_recorded_run_with_nothing_to_map_makes_no_solver_claim() -> None:
    """`solver_run.json` is the package's claim; results are the evidence.

    A package carrying a completed run but no metrics and no regions would
    otherwise stamp an EMPTY map as an executed-solver result.
    """
    result = map_cae_results(
        computed_metrics={"schema_version": "0.1", "load_cases": []},
        field_regions={"schema_version": "0.1", "regions": []},
        topology_map=_TOPOLOGY, object_registry={}, solver_executed=True,
    )
    assert result["credibility"]["tier"] == "unverified"
    assert result["provenance"]["solver_evidence"]["results_present"] is False
    assert any("no solver claim is made" in n for n in result["notes"])


def test_no_evidence_and_evidence_of_no_run_are_recorded_differently() -> None:
    """Both reach `unverified`; they are still not the same fact."""
    absent = _map()["provenance"]["solver_evidence"]
    negative = _map(solver_executed=False)["provenance"]["solver_evidence"]

    assert absent["solver_executed"] is None and absent["read_from_package"] is False
    assert negative["solver_executed"] is False and negative["read_from_package"] is True
    assert _map()["credibility"]["tier"] == _map(solver_executed=False)["credibility"]["tier"]
