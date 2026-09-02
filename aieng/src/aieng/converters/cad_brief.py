"""CAD brief + validation-target planning artifact v0 (#290).

Makes the pre-code modeling brief a first-class, testable artifact instead of
relying on prompt discipline: an agent declares the contract (units, model type,
parts, key dimensions) BEFORE calling cad.execute_build123d, and the brief
auto-derives a validation-target list that cad.validate_targets (#291) checks the
finished model against — closing the plan → build → verify loop.

Pure normalization (no LLM, no geometry): the agent supplies structure; this
validates/normalizes it and derives concrete targets. Honesty: a brief is a plan,
not a guarantee the model meets it — that is what validate_targets is for.
"""
from __future__ import annotations

from typing import Any

from aieng.converters.geometry_targets import TARGET_KINDS

CAD_BRIEF_PATH = "task/cad_brief.json"

_UNITS = {"mm": "mm", "millimeter": "mm", "cm": "cm", "centimeter": "cm",
          "in": "inch", "inch": "inch", "m": "m", "meter": "m"}
_MODEL_TYPES = {"single_part", "assembly", "product", "organic", "fixture"}


def _norm_units(raw: Any) -> str:
    return _UNITS.get(str(raw or "").strip().lower(), "mm")


def _as_xyz(v: Any) -> list[float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v):
        return [float(x) for x in v]
    return None


def normalize_cad_brief(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize an agent-supplied brief and derive validation targets.

    Returns ``{format, request, units, coordinate_convention, model_type, parts,
    overall_size_mm, key_dimensions, validation_targets, assumptions, warnings,
    honesty}``. ``validation_targets`` = explicit targets (validated) + targets
    derived from the declared parts / sizes (named_part_present, part_count,
    overall_size, part_size) — ready to hand to cad.validate_targets.
    """
    raw = raw if isinstance(raw, dict) else {}
    warnings: list[str] = []

    units = _norm_units(raw.get("units"))
    model_type = str(raw.get("model_type") or "").strip().lower()

    parts_in = raw.get("parts") if isinstance(raw.get("parts"), list) else []
    parts: list[dict[str, Any]] = []
    for p in parts_in:
        if not isinstance(p, dict) or not p.get("name"):
            warnings.append("dropped a part without a name")
            continue
        entry: dict[str, Any] = {"name": str(p["name"])}
        if p.get("role"):
            entry["role"] = str(p["role"])
        sz = _as_xyz(p.get("size_mm"))
        if sz:
            entry["size_mm"] = sz
        if isinstance(p.get("key_dimensions"), dict):
            entry["key_dimensions"] = p["key_dimensions"]
        parts.append(entry)

    if model_type not in _MODEL_TYPES:
        # infer a sensible default from the part count rather than guessing wrong
        model_type = "assembly" if len(parts) >= 2 else "single_part"

    overall = _as_xyz(raw.get("overall_size_mm"))
    tol = float(raw["tolerance_mm"]) if isinstance(raw.get("tolerance_mm"), (int, float)) else 1.0

    # ── derive validation targets from the declared contract ──────────────────
    targets: list[dict[str, Any]] = []
    for p in parts:
        targets.append({"kind": "named_part_present", "part": p["name"]})
    if parts:
        targets.append({"kind": "part_count", "count": len(parts)})
    if overall:
        targets.append({"kind": "overall_size", "size_mm": overall, "tolerance_mm": tol})
    for p in parts:
        if p.get("size_mm"):
            targets.append({"kind": "part_size", "part": p["name"], "size_mm": p["size_mm"], "tolerance_mm": tol})
    # Every multi-body model except an organic one. The condition used to be
    # `assembly`/`product` only, which skipped exactly the case where the finding
    # is CERTAIN: a `single_part` whose bodies are detached is broken geometry —
    # AGENTS.md calls a floating part "usually a coordinate typo" — while an
    # assembly's parts can legitimately sit apart, bolted with clearance. The
    # rule was applied to the debatable case and withheld from the hard error.
    #
    # `organic` is the one genuine exception: a detached body there may be
    # intended (a separate prop, a hovering element).
    if len(parts) >= 2 and model_type != "organic":
        targets.append({"kind": "no_floating_parts"})

    # explicit targets the agent passed (validated against the known kinds)
    for t in (raw.get("validation_targets") or []):
        if isinstance(t, dict) and str(t.get("kind")) in TARGET_KINDS:
            targets.append(t)
        else:
            warnings.append(f"dropped invalid validation_target: {t}")

    # de-dup identical targets while preserving order
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for t in targets:
        key = repr(sorted(t.items()))
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    return {
        "format": "aieng.cad_brief",
        "format_version": "0.1",
        "request": str(raw.get("request") or "").strip() or None,
        "units": units,
        "coordinate_convention": str(raw.get("coordinate_convention") or "z_up"),
        "model_type": model_type,
        "parts": parts,
        "overall_size_mm": overall,
        "key_dimensions": raw.get("key_dimensions") if isinstance(raw.get("key_dimensions"), dict) else {},
        "validation_targets": deduped,
        "assumptions": [str(a) for a in (raw.get("assumptions") or []) if isinstance(a, (str, int, float))],
        "warnings": warnings,
        "honesty": (
            "A brief is a pre-code plan/contract, not a guarantee the built model meets it. "
            "Verify the finished model against `validation_targets` with cad.validate_targets."
        ),
    }
