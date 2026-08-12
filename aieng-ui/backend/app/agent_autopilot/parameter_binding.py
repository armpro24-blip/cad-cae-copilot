"""Deterministic feature-parameter binding for dimensional edits.

Pure and side-effect-free. Resolves extracted parameter slots (name / value /
unit, from ``intent_resolution.extract_parameter_slots``) against a project's
editable feature-graph parameters, so a ``/modify`` "change the wall thickness to
5mm" can be pointed at a concrete ``cad.edit_parameter`` call (featureId /
parameterName) instead of a full regen — or, when the name is unknown/ambiguous,
the agent is told to ask the user rather than guess.

Mirrors ``mention_binding``'s honesty rule:

* index is ``None`` (no feature graph available)        → ``known = None`` (unverified)
* index is present but empty (no editable parameters)   → ``known = False``
* index present, no token overlap                       → ``known = False`` (not found)
* index present, two+ equally-good matches              → ``known = False`` (ambiguous)
* index present, single best match                      → ``known = True`` (+ bounds check)

Never invents a featureId/parameterName; ``known = False``/``None`` carries a
reason and (for ambiguous) the candidate list.
"""

from __future__ import annotations

import re
from typing import Any

from aieng.converters.optimization_variables import is_shape_bearing

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tokens that carry no naming signal (units, generic scaffolding) — dropped so
# they neither inflate nor dilute the match score.
_PARAM_STOPTOKENS = frozenset({
    "mm", "cm", "m", "deg", "degrees", "value", "param", "params", "parameter",
    "parameters", "global", "default", "model", "the", "of",
})


def _tokens(text: Any) -> set[str]:
    return {t for t in _TOKEN_RE.findall(str(text or "").lower()) if t not in _PARAM_STOPTOKENS}


# Feature-graph feature types → editing scope. A "global" parameter is shared and
# edits ripple across parts (collateral risk — see cad.edit_parameter's
# regression_diff); a "local" parameter belongs to one named part and is the safe,
# local edit; "unscoped" constants matched no part and live in a generic bucket.
_SCOPE_BY_FEATURE_TYPE = {
    "global_params": "global",
    "model_params": "unscoped",
}


def parameter_scope(feature_type: Any) -> str:
    """Editing scope for a feature type: ``local`` / ``global`` / ``unscoped``."""
    return _SCOPE_BY_FEATURE_TYPE.get(str(feature_type or ""), "local")


def scopes_from_source(source_code: Any, constant_names: Any) -> dict[str, str]:
    """Live scope per constant, read from the CURRENT source: ``local``/``global``.

    The feature graph is a stored artifact, so it can be older than the binder
    that wrote it. Measured on a bracket built before the constant→part fix of
    2026-08-11: `PLATE_THICKNESS` dimensions the plate AND positions the rib, but
    the stored graph still attached it to `rib_main` as a `named_part` — i.e.
    `scope: "local"`, "the safe single-part edit". `cad.edit_parameter` resolves
    its scope-risk gate from that same graph, so editing "the rib's thickness"
    would have skipped the `confirmScopeRisk` confirmation and resized the plate.
    `regression_diff` still reports `collateral_change` afterwards, but the whole
    point of the scope flag is to warn BEFORE.

    Constant→part binding is pure text analysis, so the honest answer is
    recomputable at read time. Returns only constants whose live usage disagrees
    with a naive per-feature reading; a constant used by 2+ named parts is
    ``global`` (shared — edits ripple).
    """
    text = str(source_code or "")
    names = {str(n) for n in (constant_names or []) if n}
    if not text or not names:
        return {}
    try:  # local import: the CAD module pulls in the heavy build123d stack
        from ..cad_generation import _constants_to_part_labels
    except Exception:  # noqa: BLE001 - scope enrichment is best-effort
        return {}
    try:
        const_to_labels = _constants_to_part_labels(text, names)
    except Exception:  # noqa: BLE001
        return {}
    return {
        const: ("global" if len(labels or ()) > 1 else "local")
        for const, labels in const_to_labels.items()
        if labels
    }


def build_parameter_index(
    feature_graph: Any, source_code: Any = None
) -> list[dict[str, Any]]:
    """Flatten ``feature_graph.features[].parameters`` into a searchable index. Pure.

    Each entry: ``{feature_id, feature_name, feature_type, scope, parameter_name,
    cad_parameter_name, current_value, min_value, max_value, search_tokens}``. The
    search tokens union the constant name (the strongest signal — e.g.
    ``WALL_THICKNESS`` → ``{wall, thickness}``), the inferred parameter name, and
    the feature name. ``scope`` records whether editing is ``local`` (one part),
    ``global`` (shared — edits ripple), or ``unscoped``. Returns ``[]`` for a
    missing/malformed graph or one with no editable params.

    Pass ``source_code`` (``geometry/source.py``) to have a stored ``local``
    scope re-checked against the live constant→part usage — see
    `scopes_from_source` for why a stored graph can disagree.
    """
    entries: list[dict[str, Any]] = []
    if not isinstance(feature_graph, dict):
        return entries
    features = feature_graph.get("features")
    if not isinstance(features, list):
        return entries
    for feature in features:
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id") or feature.get("feature_id")
        feature_name = feature.get("name") or ""
        feature_type = feature.get("type") or ""
        params = feature.get("parameters")
        if not isinstance(params, dict):
            continue
        for parameter_name, info in params.items():
            if not isinstance(info, dict):
                continue
            cad_parameter_name = info.get("cad_parameter_name") or parameter_name
            tokens = _tokens(cad_parameter_name) | _tokens(parameter_name) | _tokens(feature_name)
            entries.append({
                "feature_id": feature_id,
                "feature_name": feature_name,
                "feature_type": feature_type,
                "scope": parameter_scope(feature_type),
                "parameter_name": parameter_name,
                "cad_parameter_name": cad_parameter_name,
                "current_value": info.get("current_value"),
                "min_value": info.get("min_value"),
                "max_value": info.get("max_value"),
                "search_tokens": sorted(tokens),
            })

    live = scopes_from_source(source_code, {e["cad_parameter_name"] for e in entries})
    for entry in entries:
        # Only ever WIDEN: a stored `global`/`unscoped` stays as it is (those
        # already demand confirmation), but a stored `local` that the current
        # source shows touching several parts is corrected to `global` and says
        # so, rather than reading as the safe single-part edit it is not.
        if entry["scope"] == "local" and live.get(entry["cad_parameter_name"]) == "global":
            entry["scope"] = "global"
            entry["scope_source"] = "source_usage"
            entry["scope_note"] = (
                f"{entry['cad_parameter_name']} is used by more than one named part in the "
                "current source — editing it ripples, despite being listed under "
                f"'{entry['feature_name']}'"
            )
    return entries


def summarize_parameter_index(index: Any) -> dict[str, Any]:
    """Counts for an editable-parameter listing: total + per-scope. Pure."""
    by_scope: dict[str, int] = {"local": 0, "global": 0, "unscoped": 0}
    total = 0
    for entry in index or []:
        if not isinstance(entry, dict):
            continue
        total += 1
        scope = entry.get("scope")
        if scope in by_scope:
            by_scope[scope] += 1
    return {"total": total, "by_scope": by_scope}


def _score(slot_tokens: set[str], entry_tokens: set[str]) -> float:
    """Fraction of slot tokens covered by the entry (0..1). 0 when no overlap."""
    if not slot_tokens or not entry_tokens:
        return 0.0
    overlap = len(slot_tokens & entry_tokens)
    if overlap == 0:
        return 0.0
    return overlap / len(slot_tokens)


def _within_bounds(value: Any, lo: Any, hi: Any) -> bool | None:
    """True/False when both bounds are numeric, else None (unknown)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    out = True
    has_bound = False
    try:
        if lo is not None:
            has_bound = True
            out = out and v >= float(lo)
    except (TypeError, ValueError):
        pass
    try:
        if hi is not None:
            has_bound = True
            out = out and v <= float(hi)
    except (TypeError, ValueError):
        pass
    return out if has_bound else None


def bind_parameter_slots(slots: Any, index: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Bind extracted parameter slots to feature-graph parameters. Pure.

    ``slots`` is a list of ``ParameterSlot`` (or dicts with name/value/unit).
    ``index`` comes from ``build_parameter_index`` — or ``None`` when no feature
    graph is available (→ every binding is ``known = None``, never a false
    negative). Order is preserved.
    """
    bindings: list[dict[str, Any]] = []
    for slot in slots or []:
        name = getattr(slot, "name", None)
        value = getattr(slot, "value", None)
        unit = getattr(slot, "unit", None)
        if name is None and isinstance(slot, dict):
            name, value, unit = slot.get("name"), slot.get("value"), slot.get("unit")
        base = {"slot_name": name, "value": value, "unit": unit}

        if index is None:
            bindings.append({**base, "known": None, "reason": "no feature-parameter index available"})
            continue
        if not index:
            bindings.append({**base, "known": False, "reason": "project has no editable parameters"})
            continue

        slot_tokens = _tokens(name)
        scored = sorted(
            ((_score(slot_tokens, set(e["search_tokens"])), e) for e in index),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score = scored[0][0]
        if best_score <= 0.0:
            bindings.append({**base, "known": False, "reason": "no matching editable parameter"})
            continue

        ties = [e for s, e in scored if s == best_score]
        if len(ties) > 1:
            bindings.append({
                **base,
                "known": False,
                "reason": f"ambiguous: matches {len(ties)} editable parameters",
                "candidates": [
                    {"feature_id": e["feature_id"], "parameter_name": e["parameter_name"],
                     "cad_parameter_name": e["cad_parameter_name"]}
                    for e in ties
                ],
            })
            continue

        best = ties[0]
        bindings.append({
            **base,
            "known": True,
            "feature_id": best["feature_id"],
            "parameter_name": best["parameter_name"],
            "cad_parameter_name": best["cad_parameter_name"],
            "current_value": best["current_value"],
            "min_value": best["min_value"],
            "max_value": best["max_value"],
            "value_within_bounds": _within_bounds(value, best["min_value"], best["max_value"]),
            "match_score": round(best_score, 3),
        })
    return bindings


def _fmt_value(value: Any, unit: Any) -> str:
    try:
        num = float(value)
        shown = int(num) if num.is_integer() else num
    except (TypeError, ValueError):
        shown = value
    return f"{shown}{unit or ''}"


def format_parameter_bindings(bindings: Any) -> str:
    """One line per binding: a deterministic, agent-facing resolution summary."""
    lines: list[str] = []
    for b in bindings or []:
        if not isinstance(b, dict):
            continue
        target = _fmt_value(b.get("value"), b.get("unit"))
        name = b.get("slot_name")
        known = b.get("known")
        if known is True:
            bounds = ""
            if b.get("min_value") is not None and b.get("max_value") is not None:
                bounds = f", range {b['min_value']}–{b['max_value']}"
            warn = ""
            if b.get("value_within_bounds") is False:
                warn = " [OUT OF RANGE — confirm with the user before editing]"
            lines.append(
                f"{name}→{target}: cad.edit_parameter featureId={b.get('feature_id')} "
                f"parameterName={b.get('parameter_name')} (constant "
                f"{b.get('cad_parameter_name')}, current {b.get('current_value')}{bounds}){warn}"
            )
        elif known is False and b.get("candidates"):
            opts = ", ".join(
                f"{c.get('parameter_name')}@{c.get('feature_id')}" for c in b.get("candidates", [])
            )
            lines.append(f"{name}→{target}: AMBIGUOUS — matches {opts}; ask the user which to edit.")
        elif known is False:
            lines.append(
                f"{name}→{target}: no matching editable parameter ({b.get('reason')}); "
                f"ask the user or fall back to a cad.execute_build123d edit."
            )
        else:  # known is None
            lines.append(
                f"{name}→{target}: parameter index unavailable (unverified); confirm the "
                f"target dimension with the user or inspect aieng.agent_context first."
            )
    return "\n".join(lines)
