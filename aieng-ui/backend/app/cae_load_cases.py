"""Load cases as a requirement: the physics written in engineering language.

`cad.author_brief` lets an engineer state geometric intent before building.
This is its physics counterpart: state, in the same ordinary words, **how the
part is loaded and what it must survive** — before any mesh or solver exists.

Why this belongs in the requirement rather than in tool arguments: the load case
is the thing a reviewer argues about ("is the bottom really fully clamped?"),
the thing that gets reused across design iterations, and the thing that must be
attached to a result for the result to mean anything. Today it evaporates into
one tool call.

Two properties make a recorded load case worth more than a sentence in a Word
document:

- it is **checked when written** — every phrase is resolved against the current
  geometry at authoring time, so "底面" is confirmed to denote exactly one face
  before anyone spends a solver run on it;
- it is **executable** — `cae.apply_load_case` materialises it into the CAE
  setup, so the requirement and what was actually solved cannot drift apart.

Acceptance criteria are written into the package's existing
`task/design_targets.yaml`, so the comparison machinery that already reports
pass/fail against computed metrics picks them up unchanged. This module invents
no parallel verdict system, and records no claim: a target is an acceptance
criterion, not evidence that the part meets it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "LOAD_CASES_FILENAME",
    "normalize_load_case",
    "acceptance_to_design_targets",
]

LOAD_CASES_FILENAME = "cae_load_cases.json"
FORMAT_VERSION = "0.1"

# acceptance key -> (design-target metric, operator, unit)
_ACCEPTANCE_METRICS: dict[str, tuple[str, str, str | None]] = {
    "min_safety_factor": ("minimum_safety_factor", ">=", None),
    "max_stress_mpa": ("max_von_mises_stress", "<=", "MPa"),
    "max_displacement_mm": ("max_displacement", "<=", "mm"),
    "max_mass_kg": ("total_mass", "<=", "kg"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_load_case(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize one authored load case (no geometry resolution here).

    Raises ``ValueError`` with a message aimed at the person writing the
    requirement, not at a schema.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("give the load case a name (e.g. \"motor_thrust\") so results can cite it")

    material = payload.get("material")
    if not material:
        raise ValueError("state the material — a library name like \"Al6061-T6\", or explicit properties")

    fix = payload.get("fix")
    if fix in (None, "", []):
        raise ValueError(
            "state where the part is held (`fix`) — e.g. \"底面\", \"bolt holes\", "
            "\"base_plate bottom\", or an @face: pointer"
        )

    load = payload.get("load")
    normalized_load: dict[str, Any] | None = None
    if load not in (None, {}):
        if not isinstance(load, dict):
            raise ValueError("`load` must be an object: {at, force_n, direction}")
        if load.get("at") in (None, "", []):
            raise ValueError("state where the load acts (`load.at`)")
        try:
            force_n = float(load.get("force_n"))
        except (TypeError, ValueError):
            raise ValueError("`load.force_n` must be the TOTAL force in newtons (e.g. 500)") from None
        if force_n == 0.0:
            raise ValueError(
                "`load.force_n` is 0 N — a requirement that loads nothing cannot be met or "
                "missed; state the real force"
            )
        normalized_load = {
            "at": load["at"],
            "force_n": force_n,
            "direction": load.get("direction", [0, 0, -1]),
        }

    acceptance_in = payload.get("acceptance") or {}
    if not isinstance(acceptance_in, dict):
        raise ValueError("`acceptance` must be an object of criteria, e.g. {min_safety_factor: 2}")
    acceptance: dict[str, float] = {}
    for key, value in acceptance_in.items():
        if key not in _ACCEPTANCE_METRICS:
            raise ValueError(
                f"unknown acceptance criterion {key!r}. Supported: "
                + ", ".join(sorted(_ACCEPTANCE_METRICS))
            )
        try:
            acceptance[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"acceptance.{key} must be a number") from None

    case: dict[str, Any] = {
        "name": name,
        "material": material,
        "fix": fix,
        "analysis_type": str(payload.get("analysis_type") or "static"),
        "acceptance": acceptance,
        "authored_at": _now(),
    }
    if normalized_load is not None:
        case["load"] = normalized_load
    if payload.get("description"):
        case["description"] = str(payload["description"])
    if payload.get("mesh_size_mm"):
        case["mesh_size_mm"] = payload["mesh_size_mm"]
    return case


def acceptance_to_design_targets(
    case: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Fold a load case's acceptance criteria into a `task/design_targets.yaml` doc.

    Returns ``None`` when the case declares no criteria — an unconstrained load
    case is legitimate (you may just want the number), and inventing a target
    would fabricate a requirement nobody wrote.

    Targets from the same load case are replaced on re-authoring; targets from
    other load cases and hand-written ones are preserved.
    """
    acceptance = case.get("acceptance") or {}
    if not acceptance:
        return None

    doc: dict[str, Any] = dict(existing or {})
    doc["format_version"] = doc.get("format_version") or "0.1.1"
    doc["claim_policy"] = {
        "targets_are_acceptance_criteria": True,
        "compliance_requires_evidence": True,
        "physical_correctness_not_claimed": True,
    }

    name = case["name"]
    prefix = f"{name}__"
    kept = [
        t for t in (doc.get("targets") or [])
        if isinstance(t, dict) and not str(t.get("id", "")).startswith(prefix)
    ]

    for key, value in sorted(acceptance.items()):
        metric, operator, unit = _ACCEPTANCE_METRICS[key]
        target: dict[str, Any] = {
            "id": f"{prefix}{metric}",
            "metric": metric,
            "operator": operator,
            "value": value,
            "source": f"load case {name!r} (cae.author_load_case)",
            "notes": (
                "Acceptance criterion recorded with the load case. Comparison "
                "requires computed metrics from an executed solver run; the target "
                "itself asserts nothing about the part."
            ),
        }
        if unit:
            target["unit"] = unit
        kept.append(target)

    doc["targets"] = kept
    return doc


def read_load_cases(raw: str | bytes | None) -> dict[str, Any]:
    """Parse the sidecar, tolerating absence and corruption."""
    if not raw:
        return {"format_version": FORMAT_VERSION, "load_cases": []}
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"format_version": FORMAT_VERSION, "load_cases": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("load_cases"), list):
        return {"format_version": FORMAT_VERSION, "load_cases": []}
    doc.setdefault("format_version", FORMAT_VERSION)
    return doc


def upsert_load_case(doc: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Replace a same-named case, else append. Requirements get revised."""
    cases = [c for c in (doc.get("load_cases") or [])
             if isinstance(c, dict) and c.get("name") != case["name"]]
    cases.append(case)
    return {"format_version": doc.get("format_version") or FORMAT_VERSION, "load_cases": cases}
