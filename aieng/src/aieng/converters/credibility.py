"""V&V-40-inspired credibility tiering — ONE classifier for every advisory/result output.

ASME V&V-40 / NAFEMS principle: *model credibility must be commensurate with the
risk of the decision it informs.* The workbench already carries real honesty
signals (`solver_executed`, `is_solver_evidence`, `contact_physics_modeled`,
`bolt_preload_modeled`, `production_ready`, `uncertainty_std`) but they were
scattered and inconsistent across modules. This module consolidates them behind
a single, ordered credibility classification that every result-bearing output
can stamp, so the user and downstream agents read one legible tier instead of
re-deriving trust from a grab-bag of booleans.

Pure and dependency-free so both the `aieng` core converters and the
`aieng-ui` backend can import it.
"""

from __future__ import annotations

from typing import Any

# Ordered low → high credibility. Rank is ``index + 1``; a result that claims a
# tier its honesty flags do not support is downgraded to ``unverified`` (rank 0).
CREDIBILITY_TIERS: tuple[str, ...] = (
    "critique_finding",
    "surrogate_prediction",
    "proxy_assembly_result",
    "executed_solver_result",
)

_TIER_META: dict[str, dict[str, Any]] = {
    "critique_finding": {
        "rank": 1,
        "label": "Critique finding",
        "evidence_basis": (
            "deterministic geometric / manufacturability heuristic; no physics simulated"
        ),
    },
    "surrogate_prediction": {
        "rank": 2,
        "label": "Surrogate prediction",
        "evidence_basis": (
            "data-driven surrogate estimate with an uncertainty band; not solver evidence"
        ),
    },
    "proxy_assembly_result": {
        "rank": 3,
        "label": "Proxy-assembly result",
        "evidence_basis": (
            "simplified proxy model; contact physics and bolt preload not modeled"
        ),
    },
    "executed_solver_result": {
        "rank": 4,
        "label": "Executed-solver result",
        "evidence_basis": "result from an executed FEA solver run",
    },
}

_UNVERIFIED: dict[str, Any] = {
    "rank": 0,
    "label": "Unverified",
    "evidence_basis": "no executed evidence; setup or draft only",
}

# Producer-supplied ``evidence_kind`` aliases → canonical tier.
_KIND_TO_TIER: dict[str, str] = {
    "critique": "critique_finding",
    "critique_finding": "critique_finding",
    "design_rule": "critique_finding",
    "geometry": "critique_finding",
    "surrogate": "surrogate_prediction",
    "surrogate_prediction": "surrogate_prediction",
    "proxy_assembly": "proxy_assembly_result",
    "proxy_assembly_result": "proxy_assembly_result",
    "assembly_proxy": "proxy_assembly_result",
    "solver": "executed_solver_result",
    "executed_solver": "executed_solver_result",
    "executed_solver_result": "executed_solver_result",
}


def credibility_rank(tier: str) -> int:
    """Rank of a tier (higher = more credible). Unknown / unverified → 0."""
    meta = _TIER_META.get(tier)
    return int(meta["rank"]) if meta else int(_UNVERIFIED["rank"])


def classify_credibility(
    evidence_kind: str,
    *,
    solver_executed: bool | None = None,
    is_solver_evidence: bool | None = None,
    contact_physics_modeled: bool | None = None,
    bolt_preload_modeled: bool | None = None,
    uncertainty_std: float | None = None,
    production_ready: bool | None = None,
    mesh_accuracy_band: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Map an evidence kind + its honesty flags to ONE credibility tier.

    ``evidence_kind`` is what the producer believes it is — ``"critique"`` /
    ``"surrogate"`` / ``"proxy_assembly"`` / ``"solver"`` (aliases accepted).
    The honesty flags *downgrade* that claim when they contradict it: an
    ``evidence_kind="solver"`` output with ``solver_executed`` not ``True`` can
    never be an executed-solver result, so it falls to ``unverified``. This is
    the honesty invariant — the tier is never more credible than the evidence.

    ``mesh_accuracy_band`` extends that invariant past "did it run" to "could it
    have been right". A band of ``"unreliable"`` (too few elements across the
    thinnest section for the element order — see
    ``simulation_runner.assess_mesh_accuracy``) downgrades an executed solver
    result too: the solver ran, but on a mesh that under-predicts stress. Left
    unenforced this is exactly how a measured 48%-of-theory result came to be
    stamped ``executed_solver_result``.

    Returns a self-describing stamp::

        {tier, rank, label, evidence_basis, production_ready, tier_order,
         signals, [downgrade_reason], [notes]}

    Pure. ``production_ready`` is forced ``False`` unless explicitly ``True`` —
    the workbench never certifies production-readiness by default.
    """
    kind = (evidence_kind or "").strip().lower()
    base: str | None = _KIND_TO_TIER.get(kind)
    downgrade_reason: str | None = None

    if base == "executed_solver_result" and solver_executed is not True:
        base = None
        downgrade_reason = (
            "evidence_kind claims a solver result but solver_executed is not true"
        )
    elif base == "executed_solver_result" and str(mesh_accuracy_band or "").lower() == "unreliable":
        # A solver DID run — but on a mesh that cannot resolve the result it was
        # asked for. Measured: linear tets with ~1.7 elements through the
        # thickness returned 48% of the analytical root stress, and the old code
        # still stamped that `executed_solver_result`. Running the solver is not
        # the same as earning the top tier, so the mesh verdict is enforced here
        # rather than left to prose in a report nobody diffs.
        base = None
        downgrade_reason = (
            "a solver ran, but the mesh accuracy band is 'unreliable' — the "
            "result is likely under-predicted (non-conservative); re-mesh "
            "(finer, or quadratic elements) before treating it as a result"
        )
    elif base == "surrogate_prediction" and is_solver_evidence is True:
        # Contradictory: a surrogate marked as solver evidence only earns the
        # solver tier if a solver actually ran; otherwise it stays a surrogate.
        base = (
            "executed_solver_result" if solver_executed is True else "surrogate_prediction"
        )

    if base is None:
        meta = _UNVERIFIED
        tier = "unverified"
    else:
        meta = _TIER_META[base]
        tier = base

    signals = {
        "evidence_kind": kind or None,
        "solver_executed": solver_executed,
        "is_solver_evidence": is_solver_evidence,
        "contact_physics_modeled": contact_physics_modeled,
        "bolt_preload_modeled": bolt_preload_modeled,
        "uncertainty_std": uncertainty_std,
        "mesh_accuracy_band": mesh_accuracy_band,
    }
    signals = {k: v for k, v in signals.items() if v is not None}

    out: dict[str, Any] = {
        "tier": tier,
        "rank": int(meta["rank"]),
        "label": meta["label"],
        "evidence_basis": meta["evidence_basis"],
        "production_ready": production_ready is True,
        "tier_order": list(CREDIBILITY_TIERS),
        "signals": signals,
    }
    if downgrade_reason:
        out["downgrade_reason"] = downgrade_reason
    if notes:
        out["notes"] = notes
    return out
