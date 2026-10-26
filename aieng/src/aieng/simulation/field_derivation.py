"""Derived FE result fields from a per-node stress tensor / displacement vector.

Turns the raw CalculiX FRD per-node outputs — a symmetric stress tensor
``[Sxx, Syy, Szz, Sxy, Syz, Szx]`` and a displacement ``[D1, D2, D3]`` — into the
scalar result fields a CAE post-processor offers: von Mises, principal stresses
(S1/S2/S3), Tresca / max shear, individual stress components, displacement
magnitude + per-axis components, and a **safety-factor** field (yield ÷ von Mises).

Pure (numpy only); no FRD parsing or package I/O. The backend extractor reads the
FRD + material yield and calls these per node.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

# Canonical tensor order = the order CalculiX writes the FRD ``STRESS`` block:
# SXX, SYY, SZZ, SXY, SYZ, SZX (measured on every ccx FRD in the workspace; the
# ``-5`` component records name them in exactly this order). The shear entries
# are TENSOR components (sigma_ij), so von Mises carries the usual 6*(tau^2) term.
# The order matters beyond labels: swapping Syz and Szx changes the third
# invariant, so the principal stresses come out wrong (measured: up to 29% of
# peak von Mises per node on real packages) while von Mises does not notice.
STRESS_COMPONENT_ORDER: tuple[str, ...] = ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")
_STRESS_COMPONENT_INDEX: dict[str, int] = {
    "sxx": 0, "syy": 1, "szz": 2, "sxy": 3, "syz": 4, "sxz": 5,
}
# Alternative spellings of the same symmetric shear components.
_STRESS_NAME_ALIASES: dict[str, str] = {
    "SYX": "SXY", "SZY": "SYZ", "SXZ": "SZX",
}
_DISP_AXIS_INDEX: dict[str, int] = {"ux": 0, "uy": 1, "uz": 2}

# Catalog of selectable fields: name → {source, unit, label, requires_yield}.
# ``source`` is the FRD field the value derives from ("stress" / "displacement").
FIELD_CATALOG: dict[str, dict[str, Any]] = {
    "von_mises":      {"source": "stress", "unit": "MPa", "label": "Von Mises stress", "requires_yield": False},
    "s1":             {"source": "stress", "unit": "MPa", "label": "Max principal (S1)", "requires_yield": False},
    "s2":             {"source": "stress", "unit": "MPa", "label": "Mid principal (S2)", "requires_yield": False},
    "s3":             {"source": "stress", "unit": "MPa", "label": "Min principal (S3)", "requires_yield": False},
    "tresca":         {"source": "stress", "unit": "MPa", "label": "Tresca (S1-S3)", "requires_yield": False},
    "max_shear":      {"source": "stress", "unit": "MPa", "label": "Max shear", "requires_yield": False},
    "sxx":            {"source": "stress", "unit": "MPa", "label": "Stress Sxx", "requires_yield": False},
    "syy":            {"source": "stress", "unit": "MPa", "label": "Stress Syy", "requires_yield": False},
    "szz":            {"source": "stress", "unit": "MPa", "label": "Stress Szz", "requires_yield": False},
    "sxy":            {"source": "stress", "unit": "MPa", "label": "Stress Sxy", "requires_yield": False},
    "sxz":            {"source": "stress", "unit": "MPa", "label": "Stress Sxz", "requires_yield": False},
    "syz":            {"source": "stress", "unit": "MPa", "label": "Stress Syz", "requires_yield": False},
    "safety_factor":  {"source": "stress", "unit": "", "label": "Safety factor (yield/VM)", "requires_yield": True},
    "disp_magnitude": {"source": "displacement", "unit": "mm", "label": "Displacement magnitude", "requires_yield": False},
    "ux":             {"source": "displacement", "unit": "mm", "label": "Displacement Ux", "requires_yield": False},
    "uy":             {"source": "displacement", "unit": "mm", "label": "Displacement Uy", "requires_yield": False},
    "uz":             {"source": "displacement", "unit": "mm", "label": "Displacement Uz", "requires_yield": False},
}

# Backward-compatible aliases for the two original field names.
_FIELD_ALIASES = {"stress": "von_mises", "displacement": "disp_magnitude"}


def canonical_field_name(field_name: str) -> str:
    """Map legacy aliases (``stress``/``displacement``) to canonical field names."""
    key = str(field_name or "").strip().lower()
    return _FIELD_ALIASES.get(key, key)


def stress_tensor_from_frd(
    components: list[str] | tuple[str, ...] | None, values: list[float | None]
) -> tuple[float | None, ...] | None:
    """Reorder one FRD stress record into the canonical order by component NAME.

    ``components`` are the ``-5`` record names of the block. When they are
    absent the values are taken positionally in CalculiX's own order. When they
    are present but do not name all six components, ``None`` is returned rather
    than guessing which column is which.
    """
    names = [str(c or "").strip().upper() for c in (components or [])]
    if not any(names):
        return tuple(values[:6]) if len(values) >= 6 else None
    position = {_STRESS_NAME_ALIASES.get(n, n): i for i, n in enumerate(names)}
    if not all(c in position for c in STRESS_COMPONENT_ORDER):
        return None
    out: list[float | None] = []
    for comp in STRESS_COMPONENT_ORDER:
        i = position[comp]
        out.append(values[i] if i < len(values) else None)
    return tuple(out)


def von_mises(tensor: tuple[float, ...]) -> float:
    """Von Mises equivalent stress from [Sxx,Syy,Szz,Sxy,Syz,Szx]."""
    sxx, syy, szz, sxy, syz, szx = (float(x) for x in tensor[:6])
    return math.sqrt(
        0.5 * (
            (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
            + 6.0 * (sxy ** 2 + syz ** 2 + szx ** 2)
        )
    )


def principal_stresses(tensor: tuple[float, ...]) -> tuple[float, float, float]:
    """Principal stresses (S1 ≥ S2 ≥ S3): eigenvalues of [Sxx,Syy,Szz,Sxy,Syz,Szx]."""
    sxx, syy, szz, sxy, syz, szx = (float(x) for x in tensor[:6])
    m = np.array([[sxx, sxy, szx], [sxy, syy, syz], [szx, syz, szz]], dtype=float)
    ev = np.linalg.eigvalsh(m)  # ascending, real (symmetric)
    s3, s2, s1 = float(ev[0]), float(ev[1]), float(ev[2])
    return s1, s2, s3


def tresca(tensor: tuple[float, ...]) -> float:
    """Tresca equivalent stress = S1 - S3."""
    s1, _s2, s3 = principal_stresses(tensor)
    return s1 - s3


def max_shear(tensor: tuple[float, ...]) -> float:
    """Maximum shear stress = (S1 - S3) / 2."""
    s1, _s2, s3 = principal_stresses(tensor)
    return (s1 - s3) / 2.0


def safety_factor(von_mises_value: float, yield_strength: float | None) -> float | None:
    """Static safety factor = yield / von Mises. None if yield unknown or VM ~ 0.

    A near-zero von Mises (unstressed node) has no meaningful finite factor; it is
    returned as None rather than a misleading ``inf`` so the field caps/colors honestly.
    """
    if yield_strength is None or yield_strength <= 0 or von_mises_value <= 1e-9:
        return None
    return yield_strength / von_mises_value


def derive_stress_value(
    field_name: str, tensor: tuple[float, ...], *, yield_strength: float | None = None
) -> float | None:
    """Compute a stress-sourced field for one node, or None if unavailable."""
    name = canonical_field_name(field_name)
    if len(tensor) < 6 or any(v is None for v in tensor[:6]):
        return None
    if name == "von_mises":
        return von_mises(tensor)
    if name in _STRESS_COMPONENT_INDEX:
        return float(tensor[_STRESS_COMPONENT_INDEX[name]])
    if name in ("s1", "s2", "s3"):
        return principal_stresses(tensor)[("s1", "s2", "s3").index(name)]
    if name == "tresca":
        return tresca(tensor)
    if name == "max_shear":
        return max_shear(tensor)
    if name == "safety_factor":
        return safety_factor(von_mises(tensor), yield_strength)
    return None


def derive_displacement_value(
    field_name: str, d1: float | None, d2: float | None, d3: float | None
) -> float | None:
    """Compute a displacement-sourced field for one node, or None if unavailable."""
    name = canonical_field_name(field_name)
    if name in _DISP_AXIS_INDEX:
        comp = (d1, d2, d3)[_DISP_AXIS_INDEX[name]]
        return float(comp) if comp is not None else None
    if name == "disp_magnitude":
        if d1 is None or d2 is None or d3 is None:
            return None
        return math.sqrt(float(d1) ** 2 + float(d2) ** 2 + float(d3) ** 2)
    return None


def available_fields(
    *, has_stress: bool, has_displacement: bool, has_yield: bool
) -> list[dict[str, Any]]:
    """List the selectable fields given which FRD data + material yield are present."""
    out: list[dict[str, Any]] = []
    for name, meta in FIELD_CATALOG.items():
        if meta["source"] == "stress" and not has_stress:
            continue
        if meta["source"] == "displacement" and not has_displacement:
            continue
        if meta["requires_yield"] and not has_yield:
            continue
        out.append({"name": name, **meta})
    return out
