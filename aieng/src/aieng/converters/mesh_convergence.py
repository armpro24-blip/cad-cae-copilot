"""Mesh-convergence analysis — ASME V&V-20 Richardson extrapolation + GCI.

After a static solve answers *"what is the stress/deflection?"*, the next
engineering question is *"can I trust it, or is it mesh noise?"* This module is the
deterministic answer: given the same metric solved on ≥3 progressively finer
meshes, it estimates the apparent order of convergence, the mesh-independent
(extrapolated) value, and the **Grid Convergence Index (GCI)** — the standard
ASME V&V-20 / Roache measure of discretization uncertainty — and returns an honest
converged / not-converged verdict.

Honesty boundary: the GCI is a *numerical discretization* uncertainty estimate for
THIS metric on THIS geometry and refinement sequence. It is not a proof of model
validity, not a claim about solver/material/boundary-condition correctness, and not
a certification. Three grids in the asymptotic range are assumed; the report flags
when that assumption looks violated (non-monotonic / out-of-range order).

Pure and dependency-free.
"""
from __future__ import annotations

import math
from typing import Any

# ASME V&V-20 recommends a 1.25 factor of safety for GCI with ≥3 grids
# (1.5 for only 2 grids / less-careful studies).
DEFAULT_GCI_SAFETY_FACTOR = 1.25
DEFAULT_CONVERGED_GCI_PERCENT = 5.0
# Celik et al. (2008) Step 2: "It is desirable that the grid refinement factor,
# r = h_coarse/h_fine, be greater than 1.3" (based on experience).
MIN_RECOMMENDED_REFINEMENT_RATIO = 1.3
# Differences smaller than this fraction of the metric are treated as zero — a
# relative tolerance, so the verdict does not depend on whether the metric is in
# MPa or Pa.
_FLAT_RELATIVE_TOLERANCE = 1e-9


def _positive_count(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) and value > 0 else None


def _clean_levels(levels: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Keep levels with a positive representative size and a finite value; sort
    FINE→COARSE (ascending size). Deduplicate identical sizes (keep first)."""
    out: list[dict[str, float]] = []
    seen_sizes: set[float] = set()
    for lvl in levels or []:
        if not isinstance(lvl, dict):
            continue
        size = lvl.get("size")
        value = lvl.get("value")
        if not isinstance(size, (int, float)) or isinstance(size, bool) or size <= 0:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if not math.isfinite(float(size)) or not math.isfinite(float(value)):
            continue
        if float(size) in seen_sizes:
            continue
        seen_sizes.add(float(size))
        entry = {"size": float(size), "value": float(value)}
        for key in ("node_count", "element_count"):
            count = _positive_count(lvl.get(key))
            if count is not None:
                entry[key] = count
        out.append(entry)
    out.sort(key=lambda e: e["size"])  # ascending size = fine → coarse
    return out


def _with_representative_h(clean: list[dict[str, float]], dimension: int) -> str:
    """Set each level's grid size ``h`` and return its basis.

    Celik et al. (2008) Eq. 1 defines h = [(1/N) sum dV_i]^(1/3) from the cells
    actually used (Eq. 2, exponent 1/2, for a 2-D mesh). The geometry (total
    volume) is identical across levels, so h_i is proportional to
    N_i^(-1/dimension), and only ratios of h enter the GCI. A
    requested mesh size is not that: measured on three workspace packages,
    Gmsh turned a nominal 2.0 refinement into realized ratios of 1.45-2.06, and
    the order and GCI computed from the nominal ratio describe a refinement
    that never happened. The requested size is used only when a level has no
    element count, and the report says so.
    """
    if clean and all("element_count" in lvl for lvl in clean):
        for lvl in clean:
            lvl["h"] = lvl["element_count"] ** (-1.0 / dimension)
        clean.sort(key=lambda e: e["h"])
        return "element_count"
    for lvl in clean:
        lvl["h"] = lvl["size"]
    return "requested_size"


def analyze_mesh_convergence(
    levels: list[dict[str, Any]],
    *,
    metric_name: str | None = None,
    safety_factor: float = DEFAULT_GCI_SAFETY_FACTOR,
    converged_gci_percent: float = DEFAULT_CONVERGED_GCI_PERCENT,
    dimension: int = 3,
) -> dict[str, Any]:
    """Estimate convergence of ONE metric across refined meshes (ASME V&V-20 GCI).

    Args:
        levels: per-mesh results — ``{size, value, [node_count]}`` where ``size`` is
            the representative element size *h* (smaller = finer) and ``value`` is
            the metric at that mesh. Failed/missing values are dropped.
        metric_name: echoed into the report.
        safety_factor: GCI factor of safety (default 1.25 for ≥3 grids).
        converged_gci_percent: GCI threshold (%) below which the finest mesh is
            deemed converged (default 5%).
        dimension: mesh dimension (3 for solid meshes) — the exponent in
            h = N^(-1/dimension) when levels carry ``element_count``.

    Returns:
        A report with ``apparent_order``, ``extrapolated_value``,
        ``gci_fine_percent`` / ``gci_coarse_percent``, ``asymptotic_range``,
        ``converged`` + ``verdict``, per-pair deltas, and an honesty block. With
        <3 usable grids it degrades to ``indeterminate`` (2 grids → relative change
        only) rather than guessing.
    """
    clean = _clean_levels(levels)
    if dimension not in (2, 3):
        raise ValueError(f"dimension must be 2 or 3, not {dimension!r}")
    h_basis = _with_representative_h(clean, dimension)
    base: dict[str, Any] = {
        "metric": metric_name,
        "levels": clean,
        "level_count": len(clean),
        "h_basis": h_basis,
        "safety_factor": safety_factor,
        "converged_gci_percent": converged_gci_percent,
        "honesty": {
            "is_discretization_uncertainty_only": True,
            "model_validated": False,
            "production_ready": False,
            "note": (
                "GCI estimates numerical discretization uncertainty for this metric "
                "on this geometry/refinement only — not model validity or certification."
            ),
        },
    }

    warnings: list[str] = []
    qualifications: list[str] = []
    base["warnings"] = warnings
    base["qualifications"] = qualifications

    if len(clean) < 2:
        base.update({
            "converged": None,
            "verdict": "insufficient_grids",
            "message": "need at least 2 (ideally 3) refined meshes to assess convergence",
        })
        return base

    # Use the THREE finest grids for the GCI triple (or the only two available).
    f = clean[0]   # finest
    m = clean[1]   # medium
    phi_f, phi_m = f["value"], m["value"]
    r_mf = m["h"] / f["h"] if f["h"] > 0 else float("nan")  # refinement ratio ≥ 1

    # Pairwise relative change between the two finest grids (always available).
    denom_f = abs(phi_f) if phi_f != 0 else None
    rel_change_fine = (abs(phi_m - phi_f) / denom_f) if denom_f else None
    base["relative_change_finest_pair_percent"] = (
        round(rel_change_fine * 100.0, 4) if rel_change_fine is not None else None
    )
    base["refinement_ratio_finest_pair"] = round(r_mf, 4) if math.isfinite(r_mf) else None
    if h_basis == "requested_size":
        qualifications.append(
            "grid size h is the REQUESTED mesh size, not the realized one (no "
            "element counts given); Celik et al. (2008) Eq. 1 defines h from the "
            "cells actually used, and a mesher may not deliver the requested size"
        )
    _warn_small_ratio(warnings, "fine/medium", r_mf)

    if len(clean) < 3:
        # Two grids: report the relative change; GCI's order estimate needs three.
        conv = rel_change_fine is not None and rel_change_fine * 100.0 <= converged_gci_percent
        base.update({
            "apparent_order": None,
            "extrapolated_value": None,
            "gci_fine_percent": None,
            "converged": bool(conv) if rel_change_fine is not None else None,
            "verdict": "two_grid_relative_change_only",
            "message": (
                "only two grids — reporting relative change; add a third (finer) mesh "
                "for a Grid Convergence Index and an order-of-accuracy estimate"
            ),
        })
        return base

    c = clean[2]  # coarse
    phi_c = c["value"]
    r_cm = c["h"] / m["h"] if m["h"] > 0 else float("nan")
    _warn_small_ratio(warnings, "medium/coarse", r_cm)

    eps_mf = phi_m - phi_f   # medium − fine
    eps_cm = phi_c - phi_m   # coarse − medium

    pairs = [
        {"pair": "fine_medium", "delta": round(eps_mf, 6), "r": round(r_mf, 4)},
        {"pair": "medium_coarse", "delta": round(eps_cm, 6), "r": round(r_cm, 4)},
    ]
    base["pairs"] = pairs

    # Celik et al. (2008) Step 3: if either eps32 or eps21 is "very close" to
    # zero the procedure does not work — oscillatory convergence or, rarely, the
    # exact solution. Only a flat FINEST pair supports "converged"; a flat
    # coarse pair under a finest pair that still moves says nothing about
    # convergence (it used to be reported `converged_flat`).
    scale = max(abs(phi_f), abs(phi_m), abs(phi_c))
    tol = _FLAT_RELATIVE_TOLERANCE * scale
    flat_fine = abs(eps_mf) <= tol
    flat_coarse = abs(eps_cm) <= tol
    if flat_fine:
        if not flat_coarse:
            qualifications.append(
                "the two finest grids agree but the coarse grid differs, so no "
                "order of accuracy can be estimated (Celik et al. 2008, Step 3); "
                "the agreement may be coincidental — add a finer grid to confirm"
            )
        base.update({
            "apparent_order": None,
            "extrapolated_value": round(phi_f, 6),
            "gci_fine_percent": 0.0,
            "asymptotic_range": None,
            "converged": True,
            "verdict": "converged_flat",
            "message": "metric change between the two finest grids is negligible",
        })
        return base
    if flat_coarse:
        base.update({
            "apparent_order": None,
            "extrapolated_value": None,
            "gci_fine_percent": None,
            "asymptotic_range": None,
            "converged": None,
            "verdict": "indeterminate",
            "message": (
                "the two coarsest grids agree while the finest pair still changes "
                f"({base['relative_change_finest_pair_percent']}%); no order or GCI "
                "can be estimated — refine further (Celik et al. 2008, Step 3)"
            ),
        })
        return base

    ratio = eps_cm / eps_mf
    non_monotonic = ratio < 0  # sign change ⇒ oscillatory convergence

    # Apparent order p (Celik et al. 2008, Eq. 3a-3c).
    p: float | None = None
    diverging = False
    if math.isfinite(r_mf) and r_mf > 1.0 and math.isfinite(r_cm) and r_cm > 1.0:
        p = _apparent_order(ratio, r_mf, r_cm)
        if p is None:
            diverging = True
            warnings.append(
                f"|eps32/eps21| = {round(abs(ratio), 6)} is too small for any positive "
                "order: the change between grids does not shrink under refinement, so "
                "the grids are not converging (or not in the asymptotic range)"
            )

    extrapolated: float | None = None
    gci_fine: float | None = None
    gci_coarse: float | None = None
    asymptotic_ratio: float | None = None

    if p is not None and math.isfinite(p) and r_mf > 1.0:
        rp = r_mf ** p
        if abs(rp - 1.0) > 1e-9:
            # Fine-grid Richardson extrapolation (Roache): phi_ext = phi_f + (phi_f − phi_m)/(r^p − 1).
            extrapolated = phi_f + (phi_f - phi_m) / (rp - 1.0)
            if extrapolated != 0:
                # Celik et al. (2008) Eq. 6, the extrapolated relative error.
                base["extrapolated_relative_error_percent"] = round(
                    abs((extrapolated - phi_f) / extrapolated) * 100.0, 4
                )
            e_a_fine = abs((phi_f - phi_m) / phi_f) if phi_f != 0 else None
            if e_a_fine is not None:
                gci_fine = safety_factor * e_a_fine / (rp - 1.0)
            rp_cm = r_cm ** p
            if abs(rp_cm - 1.0) > 1e-9 and phi_m != 0:
                e_a_coarse = abs((phi_m - phi_c) / phi_m)
                gci_coarse = safety_factor * e_a_coarse / (rp_cm - 1.0)
            # Asymptotic-range check: GCI_coarse / (r^p · GCI_fine) ≈ 1.
            if gci_fine and gci_coarse and gci_fine > 0:
                asymptotic_ratio = gci_coarse / (rp * gci_fine)

    gci_fine_pct = round(gci_fine * 100.0, 4) if gci_fine is not None else None
    gci_coarse_pct = round(gci_coarse * 100.0, 4) if gci_coarse is not None else None
    in_asymptotic = (
        asymptotic_ratio is not None and abs(asymptotic_ratio - 1.0) <= 0.10
    )

    if asymptotic_ratio is not None and not in_asymptotic:
        # Unknown-not-bad: the GCI still stands, but it is a reliable error band
        # only inside the asymptotic range, so a "converged" verdict says so.
        qualifications.append(
            f"GCI_coarse / (r21^p * GCI_fine) = {round(asymptotic_ratio, 4)}, not ~1: "
            "the grids may not be in the asymptotic range, where the GCI is a "
            "reliable error band"
        )

    if diverging:
        verdict = "diverging_not_converged"
        converged = False
    elif gci_fine_pct is None:
        verdict = "indeterminate"
        converged = None
    elif gci_fine_pct <= converged_gci_percent and not non_monotonic:
        verdict = "converged"
        converged = True
    elif non_monotonic:
        verdict = "oscillatory_not_converged"
        converged = False
    else:
        verdict = "not_converged_refine_further"
        converged = False

    base.update({
        "apparent_order": round(p, 4) if p is not None and math.isfinite(p) else None,
        "extrapolated_value": round(extrapolated, 6) if extrapolated is not None else None,
        "gci_fine_percent": gci_fine_pct,
        "gci_coarse_percent": gci_coarse_pct,
        "asymptotic_range": in_asymptotic,
        "asymptotic_ratio": round(asymptotic_ratio, 4) if asymptotic_ratio is not None else None,
        "non_monotonic": non_monotonic,
        "converged": converged,
        "verdict": verdict,
        "message": _verdict_message(verdict, gci_fine_pct, converged_gci_percent),
    })
    return base


def _log_rp_minus_s(log_r: float, p: float, s: float) -> float:
    """ln(r^p - s) without overflow: p ln r + ln(1 - s r^-p)."""
    return p * log_r + math.log1p(-s * math.exp(-p * log_r))


def _apparent_order(ratio: float, r21: float, r32: float) -> float | None:
    """Solve Celik et al. (2008) Eq. 3 for the apparent order p, or None.

    Eq. 3a-3c, p = |ln|eps32/eps21| + q(p)| / ln r21 with
    q(p) = ln((r21^p - s)/(r32^p - s)), is equivalent on its positive branch to
    M(p) = p ln r21 + ln((r32^p - s)/(r21^p - s)) = ln|eps32/eps21|, and M is
    strictly increasing in p > 0 for either sign s. So the root is unique,
    exists exactly when ln|eps32/eps21| > M(0+), and bisection finds it.

    This replaces two failure modes of the earlier solver: the constant-ratio
    closed form dropped the absolute value, so a series whose differences GROW
    under refinement got p < 0 and a negative GCI that read as "converged"; and
    the fixed-point iteration Celik suggests diverges when r21 is small next to
    r32 (measured: OverflowError at r21 = 1.2, r32 = 2) — it is a solution
    method, not the definition.
    """
    s = 1.0 if ratio > 0 else -1.0
    target = math.log(abs(ratio))
    l21, l32 = math.log(r21), math.log(r32)

    def model(p: float) -> float:
        return p * l21 + _log_rp_minus_s(l32, p, s) - _log_rp_minus_s(l21, p, s)

    # M(0+): ln(ln r32 / ln r21) for monotone grids, 0 for oscillatory ones.
    floor = math.log(l32 / l21) if s > 0 else 0.0
    if not target > floor:
        return None
    lo, hi = 0.0, 1.0
    while model(hi) < target:
        hi *= 2.0
        if hi > 1e3:  # beyond any physical order; the data are degenerate
            return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid <= 0.0:
            break
        if model(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-13 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def _warn_small_ratio(warnings: list[str], pair: str, r: float) -> None:
    if math.isfinite(r) and r < MIN_RECOMMENDED_REFINEMENT_RATIO:
        warnings.append(
            f"{pair} refinement ratio r = {round(r, 4)} < "
            f"{MIN_RECOMMENDED_REFINEMENT_RATIO}; Celik et al. (2008) recommend "
            "r > 1.3 so the discretization error is distinguishable from other noise"
        )


def _verdict_message(verdict: str, gci_fine_pct: float | None, threshold: float) -> str:
    if verdict == "converged":
        return (
            f"finest-grid GCI {gci_fine_pct}% ≤ {threshold}% — the metric is "
            "mesh-converged within the discretization-uncertainty band"
        )
    if verdict == "not_converged_refine_further":
        return (
            f"finest-grid GCI {gci_fine_pct}% > {threshold}% — refine the mesh "
            "further; the reported value still carries this discretization uncertainty"
        )
    if verdict == "diverging_not_converged":
        return (
            "the change between grids does not shrink under refinement — no positive "
            "order of accuracy exists; the metric is not converging on these grids"
        )
    if verdict == "oscillatory_not_converged":
        return "metric changes non-monotonically with refinement — not in the asymptotic range; refine/inspect"
    return "could not estimate a Grid Convergence Index from these grids"
