"""Tests for the shared V&V-40 credibility classifier (#218)."""

from aieng.converters.credibility import (
    CREDIBILITY_TIERS,
    classify_credibility,
    credibility_rank,
)


def test_tier_order_is_strictly_increasing() -> None:
    ranks = [credibility_rank(t) for t in CREDIBILITY_TIERS]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)  # strictly increasing, no ties
    assert ranks[0] == 1 and ranks[-1] == 4


def test_executed_solver_outranks_proxy_outranks_surrogate_outranks_critique() -> None:
    solver = classify_credibility("solver", solver_executed=True)
    proxy = classify_credibility(
        "proxy_assembly", contact_physics_modeled=False, bolt_preload_modeled=False
    )
    surrogate = classify_credibility("surrogate", is_solver_evidence=False, uncertainty_std=0.2)
    critique = classify_credibility("critique")

    assert solver["rank"] > proxy["rank"] > surrogate["rank"] > critique["rank"]
    assert solver["tier"] == "executed_solver_result"
    assert proxy["tier"] == "proxy_assembly_result"
    assert surrogate["tier"] == "surrogate_prediction"
    assert critique["tier"] == "critique_finding"


def test_solver_claim_without_execution_is_downgraded_to_unverified() -> None:
    out = classify_credibility("solver", solver_executed=False)
    assert out["tier"] == "unverified"
    assert out["rank"] == 0
    assert "downgrade_reason" in out
    # A real executed solver outranks the un-executed claim.
    assert credibility_rank("executed_solver_result") > out["rank"]


def test_solver_with_no_flag_does_not_silently_claim_solver_tier() -> None:
    # Honest default: absent solver_executed must not earn the top tier.
    out = classify_credibility("solver")
    assert out["tier"] == "unverified"


def test_surrogate_carries_uncertainty_in_signals() -> None:
    out = classify_credibility("surrogate", is_solver_evidence=False, uncertainty_std=0.35)
    assert out["signals"]["uncertainty_std"] == 0.35
    assert out["signals"]["is_solver_evidence"] is False


def test_production_ready_is_false_unless_explicitly_true() -> None:
    assert classify_credibility("solver", solver_executed=True)["production_ready"] is False
    assert (
        classify_credibility("solver", solver_executed=True, production_ready=True)[
            "production_ready"
        ]
        is True
    )


def test_unknown_kind_is_unverified() -> None:
    out = classify_credibility("something_made_up")
    assert out["tier"] == "unverified"
    assert out["rank"] == 0


def test_stamp_is_self_describing() -> None:
    out = classify_credibility("critique")
    assert set(out) >= {"tier", "rank", "label", "evidence_basis", "production_ready", "tier_order"}
    assert out["tier_order"] == list(CREDIBILITY_TIERS)


def test_producers_stamp_their_tier() -> None:
    # The canonical producers must carry the shared stamp at the right tier.
    from aieng.converters.critique_engine import critique_geometry

    crit = critique_geometry({}, {})  # no solids → skipped, still stamped
    assert crit["credibility"]["tier"] == "critique_finding"
    assert crit["credibility"]["rank"] == 1


def test_unreliable_mesh_downgrades_an_executed_solver_result() -> None:
    """Running the solver is not the same as earning the top tier.

    Measured on the reference cantilever: linear tets with ~1.7 elements through
    the thickness returned 48% of the analytical root stress — non-conservative
    — and were still stamped `executed_solver_result`. The mesh verdict is now
    part of the honesty invariant, not prose in a report nobody diffs.
    """
    out = classify_credibility(
        "solver", solver_executed=True, mesh_accuracy_band="unreliable"
    )
    assert out["tier"] == "unverified"
    assert out["rank"] == 0
    assert "under-predicted" in out["downgrade_reason"]
    assert out["signals"]["mesh_accuracy_band"] == "unreliable"
    # the reason must tell the caller what to DO about it
    assert "re-mesh" in out["downgrade_reason"]


def test_reliable_and_marginal_meshes_keep_the_executed_solver_tier() -> None:
    """Only 'unreliable' downgrades. A false alarm on an accurate mesh costs
    credibility exactly like missing a bad one — ~1.7 QUADRATIC elements across
    the thickness measured 97% of theory and must keep the tier."""
    for band in ("reliable", "marginal", None):
        out = classify_credibility(
            "solver", solver_executed=True, mesh_accuracy_band=band
        )
        assert out["tier"] == "executed_solver_result", band
        assert "downgrade_reason" not in out, band


def test_mesh_band_cannot_promote_an_unexecuted_solver_claim() -> None:
    """A pristine mesh does not make an un-run solver credible."""
    out = classify_credibility(
        "solver", solver_executed=False, mesh_accuracy_band="reliable"
    )
    assert out["tier"] == "unverified"
    assert "solver_executed is not true" in out["downgrade_reason"]
