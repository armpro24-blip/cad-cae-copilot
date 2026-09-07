"""Two more ways a solver result can be wrong — and only one of them is known.

`classify_credibility` already refuses to stamp `executed_solver_result` on a run
that did not complete, and on a run whose mesh band is `unreliable`. Two gaps
were left, and they are NOT the same kind of gap:

1. **Stale geometry.** A completed run whose deck was built for a geometry
   revision the package has since moved past reports the OLD model's numbers.
   `cae.run_solver` refuses to *solve* such a deck (`stale_deck`), but a result
   solved before the edit keeps its rank-4 stamp afterwards. The revision
   counter says the model changed: a *positive* finding, so it downgrades, like
   the unreliable band.

2. **An unjudged mesh.** `simulation_runner` writes `band: null` with
   `measured_on: "not_determined"` for a hollow or highly non-convex body, whose
   bounding box is the outer envelope and not a wall, so there is no thickness
   to count elements through. `_read_mesh_accuracy_band` returned `None` for that
   AND for a package meshed before accuracy existed, so the two were
   indistinguishable and neither did anything.

   This one does **not** downgrade. `unverified` is rank 0 — the same rank as
   "no solver ran at all" — so downgrading an unjudged mesh would under-claim a
   real solve as hard as the original defect over-claimed a bad one. Unknown is
   not known-bad; `None` is not `False`. It is recorded as a qualification: the
   rank stands and the stamp says what was not checked.

The staleness signal is deliberately derived from each run's
`deck_provenance.json`, not from `edit_impact.stale`. That flag is set by
`cad.edit_parameter` and cleared only by a CAD write, so the correct sequence —
edit, re-mesh, new deck, solve `run_002` — leaves it standing; a rule keyed on it
would fire on every correct re-solve. That is `by-construction` from the review
lens, and `test_a_correct_re_solve_is_not_stale` is the guard against it.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from aieng.cae_result_summary import (
    _build_result_contract,
    generate_cae_result_summary,
    read_solver_evidence,
)
from aieng.converters.credibility import classify_credibility

# ── the classifier: a positive finding downgrades, an unknown qualifies ──────


def test_a_stale_geometry_revision_downgrades_a_completed_run() -> None:
    out = classify_credibility("solver", solver_executed=True, geometry_stale=True)

    assert out["tier"] == "unverified"
    assert out["rank"] == 0
    assert "geometry revision" in out["downgrade_reason"]
    assert out["signals"]["geometry_stale"] is True


def test_a_run_solved_for_the_current_revision_keeps_its_tier() -> None:
    out = classify_credibility("solver", solver_executed=True, geometry_stale=False)

    assert out["tier"] == "executed_solver_result"
    assert out["rank"] == 4
    assert "downgrade_reason" not in out
    # False is recorded — "checked, and it is current" is a fact worth keeping.
    assert out["signals"]["geometry_stale"] is False


def test_an_unrecorded_revision_is_not_treated_as_stale() -> None:
    """A package too old to answer must not be punished for the question."""
    out = classify_credibility("solver", solver_executed=True, geometry_stale=None)

    assert out["tier"] == "executed_solver_result"
    assert "downgrade_reason" not in out
    assert "geometry_stale" not in out["signals"]


def test_an_unjudged_mesh_qualifies_the_claim_without_moving_the_rank() -> None:
    out = classify_credibility(
        "solver", solver_executed=True, mesh_accuracy_judged=False
    )

    assert out["tier"] == "executed_solver_result", "unknown is not known-bad"
    assert out["rank"] == 4
    assert "downgrade_reason" not in out
    assert len(out["qualifications"]) == 1
    qualification = out["qualifications"][0]
    assert "UNKNOWN, not good" in qualification
    assert "cae.mesh_convergence" in qualification
    assert out["signals"]["mesh_accuracy_judged"] is False


def test_a_judged_mesh_carries_no_qualification() -> None:
    out = classify_credibility(
        "solver",
        solver_executed=True,
        mesh_accuracy_band="reliable",
        mesh_accuracy_judged=True,
    )

    assert out["tier"] == "executed_solver_result"
    assert "qualifications" not in out


def test_a_package_with_no_mesh_accuracy_at_all_carries_no_qualification() -> None:
    """The other absence. Nothing was recorded, so there is nothing to state."""
    out = classify_credibility(
        "solver", solver_executed=True, mesh_accuracy_judged=None
    )

    assert out["tier"] == "executed_solver_result"
    assert "qualifications" not in out
    assert "mesh_accuracy_judged" not in out["signals"]


def test_an_unreliable_band_still_downgrades() -> None:
    """The pre-existing invariant: known-bad is a different answer from unknown."""
    out = classify_credibility(
        "solver",
        solver_executed=True,
        mesh_accuracy_band="unreliable",
        mesh_accuracy_judged=True,
    )

    assert out["tier"] == "unverified"
    assert "unreliable" in out["downgrade_reason"]


def test_a_lower_tier_is_not_qualified_about_a_mesh_it_never_claimed() -> None:
    out = classify_credibility(
        "surrogate", is_solver_evidence=False, mesh_accuracy_judged=False
    )

    assert out["tier"] == "surrogate_prediction"
    assert "qualifications" not in out


# ── the reader: two absences told apart ─────────────────────────────────────


def _package(
    path: Path,
    *,
    accuracy: dict[str, Any] | None = None,
    runs: dict[str, tuple[bool, int | None]] | None = None,
    current_revision: int | None = None,
    metrics_run_id: str | None = None,
) -> Path:
    """A package carrying only what these readers look at.

    `runs` maps run_id -> (completed, deck geometry_revision or None).
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        if accuracy is not None:
            zf.writestr(
                "simulation/mesh/mesh_metadata.json",
                json.dumps({"schema_version": "0.1", "accuracy": accuracy}),
            )
        for run_id, (completed, revision) in (runs or {}).items():
            zf.writestr(
                f"simulation/runs/{run_id}/solver_run.json",
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": "completed" if completed else "failed",
                        "solved": completed,
                    }
                ),
            )
            if revision is not None:
                zf.writestr(
                    f"simulation/runs/{run_id}/deck_provenance.json",
                    json.dumps({"run_id": run_id, "geometry_revision": revision}),
                )
        if current_revision is not None:
            zf.writestr(
                "state/revalidation_status.json",
                json.dumps({"current_geometry_revision": current_revision}),
            )
        if metrics_run_id is not None:
            zf.writestr(
                "results/computed_metrics.json",
                json.dumps({"metrics_source": {"run_id": metrics_run_id}}),
            )
    return path


def _evidence(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r") as zf:
        return dict(read_solver_evidence(zf))


def test_no_mesh_metadata_records_nothing_about_accuracy(tmp_path: Path) -> None:
    evidence = _evidence(_package(tmp_path / "bare.aieng"))

    assert evidence["mesh_accuracy_band"] is None
    assert evidence["mesh_accuracy_judged"] is None


def test_a_banded_mesh_reports_the_band_and_that_it_was_judged(tmp_path: Path) -> None:
    evidence = _evidence(
        _package(tmp_path / "banded.aieng", accuracy={"band": "marginal"})
    )

    assert evidence["mesh_accuracy_band"] == "marginal"
    assert evidence["mesh_accuracy_judged"] is True


def test_a_mesh_that_could_not_be_judged_says_so(tmp_path: Path) -> None:
    """The hollow-body block `simulation_runner` actually writes (#536)."""
    evidence = _evidence(
        _package(
            tmp_path / "hollow.aieng",
            accuracy={
                "band": None,
                "measured_on": "not_determined",
                "governing_body": "housing",
                "reason": "the box is the OUTER envelope of a hollow body",
            },
        )
    )

    assert evidence["mesh_accuracy_band"] is None
    assert evidence["mesh_accuracy_judged"] is False, (
        "'measured, no verdict possible' is not 'nothing recorded'"
    )


def test_an_empty_mesh_assessment_is_also_unjudged(tmp_path: Path) -> None:
    """`assess_mesh_accuracy` returns early with no band on a degenerate mesh."""
    evidence = _evidence(
        _package(
            tmp_path / "degenerate.aieng",
            accuracy={"reason": "degenerate extent or mesh size — cannot assess."},
        )
    )

    assert evidence["mesh_accuracy_judged"] is False


# ── the reader: staleness derived per run, from the deck's own provenance ────


def test_a_baseline_solve_of_an_unedited_package_is_not_stale(tmp_path: Path) -> None:
    """Revision 0 on both sides. The convention three readers now share.

    Recorded as `None` instead, the comparison would read "unknown vs 1" and the
    check could never fire on the sequence it exists for. That exact mistake
    shipped once already, in `_package_geometry_revision`.
    """
    evidence = _evidence(
        _package(tmp_path / "baseline.aieng", runs={"run_001": (True, 0)})
    )

    assert evidence["geometry_stale"] is False


def test_a_result_from_before_an_edit_is_stale(tmp_path: Path) -> None:
    evidence = _evidence(
        _package(
            tmp_path / "edited.aieng",
            runs={"run_001": (True, 0)},
            current_revision=1,
        )
    )

    assert evidence["geometry_stale"] is True


def test_a_correct_re_solve_is_not_stale(tmp_path: Path) -> None:
    """The guard against a rule that fires on every correct input.

    Edit, re-mesh, new deck for run_002, solve. `edit_impact.stale` is still set
    here — only a CAD write clears it — so reading that flag would downgrade
    this, the documented happy path, every time.
    """
    evidence = _evidence(
        _package(
            tmp_path / "resolved.aieng",
            runs={"run_001": (True, 0), "run_002": (True, 1)},
            current_revision=1,
            metrics_run_id="run_002",
        )
    )

    assert evidence["geometry_stale"] is False
    assert evidence["completed_run_count"] == 2


def test_metrics_that_name_no_run_among_several_are_unattributable(
    tmp_path: Path,
) -> None:
    """Two completed runs, one stale and one current, and nothing says which.

    Picking "the newest run id" would answer this by string order — `run_2`
    before `run_10`, `baseline` before `after_edit` — and `run_id` is
    caller-supplied. A guess presented as a reading is worse than "unknown".
    """
    evidence = _evidence(
        _package(
            tmp_path / "ambiguous.aieng",
            runs={"run_001": (True, 0), "run_002": (True, 1)},
            current_revision=1,
        )
    )

    assert evidence["geometry_stale"] is None


def test_the_run_the_metrics_name_is_the_one_judged(tmp_path: Path) -> None:
    """`computed_metrics.json` is one path, so it says which run it came from.

    Here the newest run is current but the standing metrics came from the old
    one — judging the newest would call a stale number fresh.
    """
    evidence = _evidence(
        _package(
            tmp_path / "named.aieng",
            runs={"run_001": (True, 0), "run_002": (True, 1)},
            current_revision=1,
            metrics_run_id="run_001",
        )
    )

    assert evidence["geometry_stale"] is True


def test_a_deck_with_no_recorded_revision_is_unknown_not_stale(tmp_path: Path) -> None:
    evidence = _evidence(
        _package(tmp_path / "legacy.aieng", runs={"run_001": (True, None)},
                 current_revision=3)
    )

    assert evidence["geometry_stale"] is None


def test_a_package_with_no_completed_run_has_no_staleness_to_report(
    tmp_path: Path,
) -> None:
    evidence = _evidence(
        _package(tmp_path / "failed.aieng", runs={"run_001": (False, 0)},
                 current_revision=1)
    )

    assert evidence["geometry_stale"] is None
    assert evidence["solver_executed"] is False


# ── the result summary states the same two things the stamp does ────────────


def _contract(**kwargs: Any) -> dict[str, Any]:
    return _build_result_contract(
        namelist=set(),
        computed_values={"extrema_computed": True, "source": "results/computed_metrics.json"},
        solver_runs=[{"run_id": "run_001", "status": "completed", "solved": True,
                      "source_artifact": "simulation/runs/run_001/solver_run.json"}],
        legacy_rest_summary=None,
        **kwargs,
    )


def test_the_summary_reports_a_stale_geometry_tier() -> None:
    contract = _contract(geometry_stale=True)

    assert contract["claim_tier"] == "stale_geometry"
    assert "before the edit" in contract["reason"]


def test_the_summary_keeps_the_solver_tier_for_an_unjudged_mesh() -> None:
    contract = _contract(mesh_accuracy_judged=False)

    assert contract["claim_tier"] == "executed_solver_result", (
        "rewriting the tier would tell every reader checking it that no solver ran"
    )
    assert len(contract["qualifications"]) == 1
    assert "UNKNOWN, not good" in contract["qualifications"][0]


def test_the_summary_qualifies_nothing_when_the_mesh_was_judged() -> None:
    assert _contract(mesh_accuracy_band="reliable", mesh_accuracy_judged=True)[
        "qualifications"
    ] == []
    assert _contract()["qualifications"] == []


def test_the_whole_summary_carries_the_qualification_end_to_end(
    tmp_path: Path,
) -> None:
    """Wired, not merely available: the contract builder's callers pass it on."""
    pkg = _package(
        tmp_path / "hollow_solved.aieng",
        accuracy={"band": None, "measured_on": "not_determined"},
        runs={"run_001": (True, 0)},
    )

    contract = generate_cae_result_summary(pkg)["result_contract"]

    assert contract["claim_tier"] == "executed_solver_result"
    assert contract["qualifications"], contract
