"""Score a reported cantilever answer against Euler-Bernoulli beam theory.

The cantilever task asks for the tip deflection and the peak von Mises stress
of a 200 x 20 x t mm aluminium beam (E = 69 GPa) under 100 N at the tip, for
t = 10 mm and t = 20 mm. Unlike the bracket task this has a reference answer,
so it scores what the agent REPORTED, whether or not it used the workbench.
"""
from __future__ import annotations

import json
import re

FORCE_N, LENGTH_MM, WIDTH_MM, E_MPA = 100.0, 200.0, 20.0, 69_000.0
# Deflection is a global quantity a converged 3D model matches within a few %
# (shear deflection adds ~0.2 % at L/t = 20); linear tetrahedra miss it by ~50 %.
# It alone decides success. The peak von Mises is NOT scored: it sits on the
# clamped edge, a singularity where a correct 3D model exceeds beam theory and
# keeps rising under refinement — measured +20 % at t = 20 mm (L/t = 10) in
# every arm. Its error is reported for information only.
DEFLECTION_TOLERANCE = 0.05


def beam_reference(thickness_mm: float) -> dict[str, float]:
    """Tip deflection F L^3 / (3 E I) and root bending stress F L (t/2) / I."""
    inertia = WIDTH_MM * thickness_mm ** 3 / 12.0
    return {
        "tip_deflection_mm": FORCE_N * LENGTH_MM ** 3 / (3.0 * E_MPA * inertia),
        "max_von_mises_mpa": FORCE_N * LENGTH_MM * (thickness_mm / 2.0) / inertia,
    }


REFERENCE = {"before": beam_reference(10.0), "after": beam_reference(20.0)}
SCORED_KEY = "tip_deflection_mm"


def extract_answer(final_text: str | None) -> dict | None:
    """The last ```json block of the final message, or None."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", final_text or "", re.S)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return None


def score_cantilever(final_text: str | None) -> dict:
    """Per-value relative error against beam theory, and overall success."""
    answer = extract_answer(final_text)
    if answer is None:
        return {"answer_found": False, "task_success": False}
    errors: dict[str, dict[str, float | None]] = {}
    within = True
    for case, reference in REFERENCE.items():
        errors[case] = {}
        for key, expected in reference.items():
            try:
                reported = float((answer.get(case) or {}).get(key))
            except (TypeError, ValueError):
                errors[case][key] = None
                within = within and key != SCORED_KEY
                continue
            error = (reported - expected) / expected
            errors[case][key] = round(error, 4)
            if key == SCORED_KEY:
                within = within and abs(error) <= DEFLECTION_TOLERANCE
    return {
        "answer_found": True,
        "answer": answer,
        "reference": {c: {k: round(v, 4) for k, v in r.items()} for c, r in REFERENCE.items()},
        "relative_error": errors,
        "task_success": within,
    }


# Sizing task: the smallest thickness meeting tip deflection <= 0.5 mm and a
# safety factor of 2 on a 276 MPa yield. Deflection governs (stress needs only
# t >= 6.6 mm). Timoshenko deflection = bending + shear, because the 3D model
# includes shear; at t ~ 16.7 mm shear adds ~0.6 %.
DEFLECTION_LIMIT_MM, POISSON, SHEAR_COEFFICIENT = 0.5, 0.33, 5.0 / 6.0
# Feasible within 1 % (a converged 3D model and Timoshenko agree to about that),
# and no more than 5 % heavier than the lightest feasible beam.
FEASIBILITY_SLACK, WASTE_LIMIT = 0.01, 0.05


def timoshenko_tip_deflection(thickness_mm: float) -> float:
    """Bending F L^3 / (3 E I) plus shear F L / (k G A)."""
    shear_modulus = E_MPA / (2.0 * (1.0 + POISSON))
    area = WIDTH_MM * thickness_mm
    return (beam_reference(thickness_mm)["tip_deflection_mm"]
            + FORCE_N * LENGTH_MM / (SHEAR_COEFFICIENT * shear_modulus * area))


def minimum_thickness_mm() -> float:
    """Bisection for the thickness at which the tip deflection equals the limit."""
    low, high = 1.0, 100.0
    for _ in range(80):
        mid = (low + high) / 2.0
        low, high = (mid, high) if timoshenko_tip_deflection(mid) > DEFLECTION_LIMIT_MM else (low, mid)
    return high


def score_sizing(final_text: str | None) -> dict:
    """Is the reported thickness feasible, not wasteful, and its deflection consistent?"""
    answer = extract_answer(final_text)
    if answer is None:
        return {"answer_found": False, "task_success": False}
    t_min = minimum_thickness_mm()
    try:
        thickness = float(answer.get("thickness_mm"))
        deflection = float(answer.get("tip_deflection_mm"))
    except (TypeError, ValueError):
        return {"answer_found": True, "answer": answer, "task_success": False, "reason": "non-numeric answer"}
    expected_deflection = timoshenko_tip_deflection(thickness)
    deflection_error = (deflection - expected_deflection) / expected_deflection
    feasible = thickness >= t_min * (1.0 - FEASIBILITY_SLACK)
    not_wasteful = thickness <= t_min * (1.0 + WASTE_LIMIT)
    consistent = abs(deflection_error) <= DEFLECTION_TOLERANCE
    return {
        "answer_found": True,
        "answer": answer,
        "reference_min_thickness_mm": round(t_min, 3),
        "thickness_excess": round(thickness / t_min - 1.0, 4),
        "deflection_relative_error": round(deflection_error, 4),
        "feasible": feasible,
        "not_wasteful": not_wasteful,
        "deflection_consistent": consistent,
        "task_success": feasible and not_wasteful and consistent,
    }


# Bracket answer task. No closed form: the reference is a mesh-convergence study
# through the workbench (quadratic tets at 4 / 2.8 / 2 / 1.4 mm, GCI < 0.6 %,
# apparent order ~2.2), Richardson-extrapolated. Only displacement is scored:
# the peak von Mises sits on the sharp rib-to-plate corner, a singularity that
# kept rising under refinement (11.1 -> 17.6 MPa at 8 mm).
BRACKET_REFERENCE = {"before": {"max_displacement_mm": 0.00504}, "after": {"max_displacement_mm": 0.002437}}


def score_bracket_answer(final_text: str | None) -> dict:
    """Relative error of each reported max displacement against the converged reference."""
    answer = extract_answer(final_text)
    if answer is None:
        return {"answer_found": False, "task_success": False}
    errors: dict[str, float | None] = {}
    for case, reference in BRACKET_REFERENCE.items():
        try:
            reported = float((answer.get(case) or {}).get("max_displacement_mm"))
            errors[case] = round((reported - reference["max_displacement_mm"]) / reference["max_displacement_mm"], 4)
        except (TypeError, ValueError):
            errors[case] = None
    return {
        "answer_found": True,
        "answer": answer,
        "reference": BRACKET_REFERENCE,
        "displacement_relative_error": errors,
        "task_success": all(e is not None and abs(e) <= DEFLECTION_TOLERANCE for e in errors.values()),
    }
