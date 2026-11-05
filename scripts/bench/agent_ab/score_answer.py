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
