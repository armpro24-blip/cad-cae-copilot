"""Parametric sizing sweep — rank dimension variants by real static FEA metrics.

This is the *brain* of the optimize→verify loop closure for static analysis: given
the per-variant results of sweeping ONE editable dimension (each variant solved with
the real static solver), it ranks the variants against an objective
(minimize mass / displacement / stress) subject to a stress (and optional
displacement) constraint, and recommends the best feasible value.

Honesty boundary (the whole point):
- A variant earns ``executed_solver_result`` credibility ONLY when its
  ``solver_executed`` flag is true; an unsolved/failed variant is ``unverified``
  and can never be recommended as if it were verified.
- ``safe_to_apply`` is true only when the recommended variant is feasible AND
  actually solved. The baseline geometry is never modified here — acceptance flows
  through the existing approval-gated ``cad.edit_parameter``.
- Mass is a geometric quantity supplied by the caller; stress/displacement come
  from the solver. No proxy/surrogate substitution.

Pure and dependency-free (imports only the shared credibility classifier) so both
the ``aieng`` core and the ``aieng-ui`` backend can use it.
"""
from __future__ import annotations

import math
from typing import Any

from .credibility import classify_credibility

# Objective → the metric key it minimizes.
_OBJECTIVE_METRIC: dict[str, str] = {
    "min_mass": "mass",
    "min_displacement": "max_displacement",
    "min_stress": "max_von_mises_stress",
}

# Per-variant status vocabulary.
STATUS_FEASIBLE = "feasible"      # solved, objective known, all constraints satisfied
STATUS_INFEASIBLE = "infeasible"  # solved but a constraint is violated
STATUS_UNKNOWN = "unknown"        # solved but a needed metric is missing (never a false pass)
STATUS_ERROR = "error"            # variant did not solve / errored
# Solved and inside every constraint — but on a mesh whose accuracy band is
# "unreliable". Such a mesh under-predicts stress and displacement (too stiff),
# so "inside the constraint" is exactly the verdict it cannot support. In a
# min-mass sweep at one mesh size the thinnest variant has the fewest elements
# through its thickness, so without this status the variant most likely to be
# wrong is also the one most likely to win.
STATUS_UNRELIABLE_MESH = "unreliable_mesh"

# Evaluator fields carried through ranking unchanged (diagnostics, not scores).
_PASSTHROUGH_KEYS = (
    "parameters", "likely_cause", "suggested_retry", "mesh_accuracy", "solve_status",
    "solver_diagnosis", "origin", "solver_retry",
)


def _positive_finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    return number


def _mesh_band(variant: dict[str, Any]) -> tuple[str | None, bool | None]:
    """(band, judged) from a variant's ``mesh_accuracy`` block; (None, None) if absent."""
    accuracy = variant.get("mesh_accuracy")
    if not isinstance(accuracy, dict):
        return None, None
    band = accuracy.get("band")
    if band is None:
        # `measured_on: not_determined` = the mesher recorded it could NOT judge
        # (hollow body). That qualifies the claim; it is not a downgrade.
        return None, False if accuracy.get("measured_on") == "not_determined" else None
    return str(band), True


def extract_static_metrics(computed_metrics: dict[str, Any] | None) -> dict[str, float | None]:
    """Pull worst-case static scalars out of a ``computed_metrics.json`` payload.

    Reads ``max_von_mises_stress`` / ``max_displacement`` from ``global_metrics``
    and every ``load_cases[].metrics`` block, returning the worst (max) of each
    across load cases. Robust to either ``{"value": x}`` or bare-scalar entries;
    a metric absent everywhere comes back ``None`` (never fabricated).
    """
    out: dict[str, float | None] = {"max_von_mises_stress": None, "max_displacement": None}
    if not isinstance(computed_metrics, dict):
        return out

    def _scalar(entry: Any) -> float | None:
        if isinstance(entry, dict):
            entry = entry.get("value")
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            return float(entry)
        return None

    blocks: list[dict[str, Any]] = []
    if isinstance(computed_metrics.get("global_metrics"), dict):
        blocks.append(computed_metrics["global_metrics"])
    for case in computed_metrics.get("load_cases") or []:
        if isinstance(case, dict) and isinstance(case.get("metrics"), dict):
            blocks.append(case["metrics"])

    for key in out:
        worst: float | None = None
        for block in blocks:
            val = _scalar(block.get(key))
            if val is not None and (worst is None or val > worst):
                worst = val
        out[key] = worst
    return out


def _evaluate_variant(
    variant: dict[str, Any],
    *,
    objective_metric: str,
    allowable_stress: float | None,
    displacement_limit: float | None,
) -> dict[str, Any]:
    """Classify ONE variant: status + reason + credibility stamp (does not rank)."""
    value = variant.get("value")
    metrics = variant.get("metrics") if isinstance(variant.get("metrics"), dict) else {}
    solver_executed = bool(variant.get("solver_executed"))
    error = variant.get("error")

    band, judged = _mesh_band(variant)
    credibility = classify_credibility(
        "solver",
        solver_executed=solver_executed,
        mesh_accuracy_band=band,
        mesh_accuracy_judged=judged,
    )

    entry: dict[str, Any] = {
        "value": value,
        "metrics": metrics,
        "solver_executed": solver_executed,
        "credibility": credibility,
    }
    for key in _PASSTHROUGH_KEYS:
        if variant.get(key) is not None:
            entry[key] = variant[key]

    if error or not solver_executed:
        entry["status"] = STATUS_ERROR
        entry["reason"] = str(error) if error else "variant did not solve"
        return entry

    stress = metrics.get("max_von_mises_stress")
    disp = metrics.get("max_displacement")
    objective_value = metrics.get(objective_metric)

    # A missing metric needed for the objective or a constraint → unknown, never
    # a silent pass.
    missing: list[str] = []
    if objective_value is None:
        missing.append(objective_metric)
    if allowable_stress is not None and stress is None:
        missing.append("max_von_mises_stress")
    if displacement_limit is not None and disp is None:
        missing.append("max_displacement")
    if missing:
        entry["status"] = STATUS_UNKNOWN
        entry["reason"] = f"missing metric(s): {', '.join(sorted(set(missing)))}"
        return entry

    violations: list[str] = []
    if allowable_stress is not None and stress > allowable_stress:
        violations.append(
            f"max_von_mises_stress {stress:g} > allowable {allowable_stress:g}"
        )
    if displacement_limit is not None and disp > displacement_limit:
        violations.append(
            f"max_displacement {disp:g} > limit {displacement_limit:g}"
        )

    if violations:
        entry["status"] = STATUS_INFEASIBLE
        entry["reason"] = "; ".join(violations)
        # An unreliable mesh is too stiff: the true stress/displacement are
        # HIGHER, so a violation it reports still holds. Only a pass is suspect.
    elif band == "unreliable":
        entry["status"] = STATUS_UNRELIABLE_MESH
        action = (variant.get("mesh_accuracy") or {}).get("recommended_action")
        entry["reason"] = (
            "constraints satisfied on a mesh whose accuracy band is 'unreliable' — "
            "stress and displacement are likely UNDER-predicted, so the pass is not "
            "evidence; not recommendable"
            + (f" ({action})" if action else "")
        )
    else:
        entry["status"] = STATUS_FEASIBLE
        entry["reason"] = "all constraints satisfied"
    entry["objective_value"] = objective_value
    return entry


def rank_sizing_sweep(
    variants: list[dict[str, Any]],
    *,
    objective: str = "min_mass",
    stress_limit: float | None = None,
    safety_factor: float = 1.0,
    displacement_limit: float | None = None,
    parameter_name: str | None = None,
) -> dict[str, Any]:
    """Rank swept dimension variants by a static-FEA objective + constraints.

    Args:
        variants: one dict per swept value — ``{value, metrics, solver_executed, [error]}``
            where ``metrics`` carries scalar ``max_von_mises_stress`` / ``max_displacement``
            / ``mass`` (any may be missing).
        objective: ``min_mass`` (default) / ``min_displacement`` / ``min_stress``.
        stress_limit: allowable stress numerator (e.g. material yield). The
            effective allowable is ``stress_limit / safety_factor``.
        safety_factor: divides ``stress_limit`` to set the allowable (default 1.0).
        displacement_limit: optional max-displacement constraint.
        parameter_name: the swept parameter's name, echoed into the report.

    Returns:
        A self-describing report: ranked variants (feasible-known first, by
        objective ascending), ``feasible_count``, ``recommended`` value, an honest
        ``credibility`` stamp, and ``safe_to_apply`` (true only when the
        recommendation is feasible AND solver-verified). Never mutates anything.

    Raises:
        ValueError: an unknown ``objective``, a non-positive or non-finite
            ``safety_factor``, or a non-positive / non-finite limit. These used
            to be replaced silently (``min_mass``, ``1.0``), so a caller asking
            for SF 0 — or a typo'd objective — got a different study than it
            asked for, reported as the one it asked for.
    """
    if objective not in _OBJECTIVE_METRIC:
        raise ValueError(
            f"unknown objective {objective!r}; expected one of {sorted(_OBJECTIVE_METRIC)}"
        )
    objective_metric = _OBJECTIVE_METRIC[objective]
    sf = _positive_finite("safety_factor", safety_factor)
    allowable_stress = (
        _positive_finite("stress_limit", stress_limit) / sf if stress_limit is not None else None
    )
    if displacement_limit is not None:
        displacement_limit = _positive_finite("displacement_limit", displacement_limit)

    evaluated = [
        _evaluate_variant(
            v,
            objective_metric=objective_metric,
            allowable_stress=allowable_stress,
            displacement_limit=displacement_limit,
        )
        for v in variants
    ]

    # Stable ordering: feasible (by objective asc) → unreliable-mesh pass →
    # infeasible → unknown → error.
    status_order = {
        STATUS_FEASIBLE: 0, STATUS_UNRELIABLE_MESH: 1, STATUS_INFEASIBLE: 2,
        STATUS_UNKNOWN: 3, STATUS_ERROR: 4,
    }

    def _sort_key(e: dict[str, Any]) -> tuple[int, float]:
        primary = status_order.get(e["status"], 4)
        obj = e.get("objective_value")
        return (primary, obj if isinstance(obj, (int, float)) else float("inf"))

    ranked = sorted(evaluated, key=_sort_key)
    for i, e in enumerate(ranked):
        e["rank"] = i + 1

    feasible = [e for e in ranked if e["status"] == STATUS_FEASIBLE]
    unreliable = [e for e in ranked if e["status"] == STATUS_UNRELIABLE_MESH]
    recommended = feasible[0] if feasible else None
    all_solved = bool(evaluated) and all(e["solver_executed"] for e in evaluated)

    if recommended is not None:
        recommendation_reason = (
            f"value={recommended['value']} minimizes {objective} "
            f"({objective_metric}={recommended.get('objective_value')}) among "
            f"{len(feasible)} feasible variant(s)"
        )
        if unreliable and any(
            (e.get("objective_value") or 0) < (recommended.get("objective_value") or 0)
            for e in unreliable
        ):
            recommendation_reason += (
                f"; {len(unreliable)} better-scoring variant(s) passed only on an "
                "unreliable mesh and were not considered — re-run them on a finer mesh"
            )
        safe_to_apply = recommended["solver_executed"]
    elif not evaluated:
        recommendation_reason = "no variants supplied"
        safe_to_apply = False
    elif unreliable:
        recommendation_reason = (
            f"no recommendable variant — {len(unreliable)} variant(s) satisfied the "
            "constraints only on a mesh whose accuracy band is 'unreliable' (stress "
            "likely under-predicted); re-run the sweep with a smaller mesh_size_mm"
        )
        safe_to_apply = False
    elif any(e["status"] in (STATUS_INFEASIBLE, STATUS_FEASIBLE) for e in evaluated):
        recommendation_reason = (
            "no feasible variant — every solved variant violates the constraint; "
            "widen the sweep range, relax the constraint, or change material"
        )
        safe_to_apply = False
    else:
        recommendation_reason = (
            "no variant could be evaluated (unsolved or missing metrics); "
            "results are unverified"
        )
        safe_to_apply = False

    # Overall credibility: executed only when every variant actually solved, and
    # no better than the mesh the recommendation stands on (or, with no
    # recommendation, than the unreliable pass that blocked one).
    if recommended is not None:
        band, judged = _mesh_band(recommended)
    elif unreliable:
        band, judged = "unreliable", True
    else:
        band, judged = None, None
    overall_credibility = classify_credibility(
        "solver",
        solver_executed=all_solved,
        mesh_accuracy_band=band,
        mesh_accuracy_judged=judged,
        notes=None if all_solved else "one or more variants did not produce solver metrics",
    )

    return {
        "objective": objective,
        "objective_metric": objective_metric,
        "parameter_name": parameter_name,
        "constraint": {
            "stress_limit": stress_limit,
            "safety_factor": sf,
            "allowable_stress": allowable_stress,
            "displacement_limit": displacement_limit,
        },
        "variants": ranked,
        "variant_count": len(ranked),
        "feasible_count": len(feasible),
        "unreliable_mesh_count": len(unreliable),
        "recommended": recommended,
        "recommendation_reason": recommendation_reason,
        "safe_to_apply": safe_to_apply,
        "credibility": overall_credibility,
        "honesty": {
            "solver_executed_all": all_solved,
            "baseline_modified": False,
            "production_ready": False,
        },
    }


def estimate_constraint_boundary(report: dict[str, Any]) -> dict[str, Any]:
    """Where between two swept values the active constraint is met — an ESTIMATE.

    A grid sweep can only recommend a grid point; the optimum of a monotone
    sizing problem lies on the constraint boundary, usually between the
    recommended (lightest feasible) value and its infeasible neighbour. This
    interpolates each violated constraint metric across that bracket and returns
    the value where it reaches its limit.

    Interpolation is linear in ``log(metric)`` vs ``log(value)``, i.e. it
    assumes a local power law ``metric ~ value**k``. That is exact for the
    sizing laws of slender members — Euler-Bernoulli bending stress
    ``6FL/(b t**2)`` (``k = -2`` in thickness) and tip deflection
    ``4FL**3/(E b t**3)`` (``k = -3``) — and the exponent is fitted from the two
    solved points, never assumed. Linear interpolation in ``value`` would
    overshoot a convex power law.

    Returns ``{status: "estimated", value, interpolated: True, solved: False,
    ...}`` or ``{status: "not_applicable", reason}``. Pure; never fabricates: a
    bracket whose ends are not both solved on a trustworthy mesh, or a metric
    that does not decrease across it, yields ``not_applicable``.
    """
    recommended = report.get("recommended")
    constraint = report.get("constraint") or {}
    limits = {
        "max_von_mises_stress": constraint.get("allowable_stress"),
        "max_displacement": constraint.get("displacement_limit"),
    }
    limits = {k: float(v) for k, v in limits.items() if isinstance(v, (int, float))}
    if recommended is None:
        return {"status": "not_applicable", "reason": "no recommended variant to bracket"}
    if not limits:
        return {"status": "not_applicable", "reason": "no stress or displacement constraint is set"}

    def _num(v: Any) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    rec_value = _num(recommended.get("value"))
    if rec_value is None or rec_value <= 0:
        return {"status": "not_applicable", "reason": "swept values are not positive numbers"}
    solved = sorted(
        (e for e in report.get("variants") or []
         if e.get("status") in (STATUS_FEASIBLE, STATUS_INFEASIBLE) and _num(e.get("value"))),
        key=lambda e: float(e["value"]),
    )
    index = next((i for i, e in enumerate(solved) if e is recommended), None)
    if index is None:
        return {"status": "not_applicable", "reason": "recommended variant is not among the solved ones"}
    neighbours = [solved[j] for j in (index - 1, index + 1) if 0 <= j < len(solved)]
    infeasible = [e for e in neighbours if e["status"] == STATUS_INFEASIBLE]
    if not infeasible:
        return {
            "status": "not_applicable",
            "reason": (
                "no infeasible variant next to the recommended one — the constraint is "
                "not active inside the swept range; extend the range past the "
                "recommended value to find the boundary"
            ),
        }
    if len(infeasible) > 1:
        return {"status": "not_applicable",
                "reason": "both neighbours are infeasible — feasibility is not monotone here"}
    outer = infeasible[0]
    v_in, v_out = rec_value, float(outer["value"])
    if v_out <= 0:
        return {"status": "not_applicable", "reason": "swept values are not positive numbers"}
    if _mesh_band(outer)[0] == "unreliable":
        # Its violation stands, but its NUMBERS are under-predicted: fitting the
        # exponent through them pulls the estimate toward the infeasible end.
        return {"status": "not_applicable",
                "reason": "the infeasible neighbour was solved on an unreliable mesh, so its "
                          "metrics are under-predicted; re-run it on a finer mesh"}

    estimates: list[tuple[float, str, float]] = []
    for metric, limit in limits.items():
        m_in = _num((recommended.get("metrics") or {}).get(metric))
        m_out = _num((outer.get("metrics") or {}).get(metric))
        if m_in is None or m_out is None or m_out <= limit:
            continue  # this constraint is not the one violated across the bracket
        if not (m_in <= limit < m_out) or m_in <= 0:
            return {"status": "not_applicable",
                    "reason": f"{metric} does not cross its limit monotonically across the bracket"}
        exponent = math.log(m_in / m_out) / math.log(v_in / v_out)
        value = v_out * (limit / m_out) ** (1.0 / exponent)
        estimates.append((value, metric, exponent))
    if not estimates:
        return {"status": "not_applicable", "reason": "no constraint metric crosses its limit"}
    # Every violated constraint must be met: take the estimate nearest the feasible end.
    value, metric, exponent = min(estimates, key=lambda e: abs(e[0] - v_in))
    return {
        "status": "estimated",
        "value": value,
        "interpolated": True,
        "solved": False,
        "governing_constraint": metric,
        "limit": limits[metric],
        "fitted_exponent": exponent,
        "bracket": {"feasible_value": v_in, "infeasible_value": v_out},
        "method": "log-log (local power-law) interpolation between the bracketing solved variants",
        "note": (
            "An interpolation, not a solve: it carries no solver credibility. Solve it "
            "(refine_boundary=true) before relying on it; at the boundary there is no margin."
        ),
    }
