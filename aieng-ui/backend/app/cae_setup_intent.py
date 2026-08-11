"""Resolve engineering language ("the bottom face", "肋的顶面") to `@face:` ids.

Setting up a static analysis used to require reading a 22-face digest of normals
and areas, picking ids by eye, and hand-writing four JSON patches with NSET
names, DOF ranges, and direction vectors. That is the step engineers say they
cannot express in a requirement, and it is where a mis-pick silently becomes a
wrong answer.

This module is the vocabulary layer: it turns the words an engineer already uses
into topology face ids, **with the reason it chose them**, and refuses when the
words are ambiguous rather than guessing. It reads only the geometry the package
already carries (surface type, outward normal, area, owning body, and the
`support_candidate` / `mounting_candidate` roles the feature graph assigns).

It selects faces. It does not decide physics — magnitudes, directions and DOFs
stay with the caller.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "resolve_face_intent",
    "describe_face",
    "normalize_direction",
]

# Axis words → (axis index, sign). Chinese aliases included because that is what
# the engineers using this actually type.
_AXIS_WORDS: dict[str, tuple[int, float]] = {
    "bottom": (2, -1.0), "底": (2, -1.0), "底面": (2, -1.0), "下": (2, -1.0), "下表面": (2, -1.0),
    "top": (2, 1.0), "顶": (2, 1.0), "顶面": (2, 1.0), "上": (2, 1.0), "上表面": (2, 1.0),
    "left": (0, -1.0), "左": (0, -1.0), "左侧": (0, -1.0), "-x": (0, -1.0),
    "right": (0, 1.0), "右": (0, 1.0), "右侧": (0, 1.0), "+x": (0, 1.0),
    "front": (1, -1.0), "前": (1, -1.0), "前面": (1, -1.0), "-y": (1, -1.0),
    "back": (1, 1.0), "后": (1, 1.0), "后面": (1, 1.0), "+y": (1, 1.0),
    "-z": (2, -1.0), "+z": (2, 1.0),
}

_HOLE_WORDS = ("bolt hole", "bolt holes", "mounting hole", "mounting holes", "hole", "holes",
               "螺栓孔", "安装孔", "孔")
_LARGEST_FLAT_WORDS = ("largest flat", "largest face", "largest planar", "最大平面", "最大面")

# A normal must be at least this aligned with the requested axis to qualify
# outright (≈32° cone).
_ALIGNMENT_MIN = 0.85
# Fallback tier: a face that merely LEANS this way (≈78° cone) can still be "the
# top" of a sloped feature. Reported as inclined, with the angle, at medium
# confidence — never silently treated as if it were flat-on.
_INCLINED_MIN = 0.2
# Two candidates whose areas are within this ratio are "equally good" → ambiguous.
_AMBIGUOUS_AREA_RATIO = 0.9


def _faces(topology: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e for e in (topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "face" and e.get("id")
    ]


def _body_names(topology: dict[str, Any]) -> dict[str, str]:
    return {
        str(e.get("id")): str(e.get("name"))
        for e in (topology.get("entities") or [])
        if isinstance(e, dict) and e.get("type") == "solid" and e.get("name")
    }


def describe_face(face: dict[str, Any], body_names: dict[str, str] | None = None) -> str:
    """One human-readable line for a face — what an engineer needs to sanity-check."""
    parts = [f"@face:{face.get('id')}"]
    surface = str(face.get("surface_type") or "?")
    parts.append(surface)
    area = face.get("area")
    if isinstance(area, (int, float)):
        parts.append(f"{float(area):.1f} mm²")
    normal = face.get("normal")
    if isinstance(normal, list) and len(normal) >= 3:
        parts.append("normal=[{:.2f}, {:.2f}, {:.2f}]".format(*[float(c) for c in normal[:3]]))
    radius = face.get("radius")
    if isinstance(radius, (int, float)):
        parts.append(f"r={float(radius):.2f} mm")
    owner = (body_names or {}).get(str(face.get("body_id") or ""))
    if owner:
        parts.append(f"on {owner}")
    return "  ".join(parts)


def normalize_direction(value: Any) -> list[float] | None:
    """Accept ``[0,0,-1]``, ``"-Z"``, ``"down"``/``"向下"`` → a unit-ish vector."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return [float(value[0]), float(value[1]), float(value[2])]
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    aliases = {
        "down": "-z", "downward": "-z", "向下": "-z", "下": "-z",
        "up": "+z", "upward": "+z", "向上": "+z", "上": "+z",
    }
    key = aliases.get(key, key)
    if key in _AXIS_WORDS:
        axis, sign = _AXIS_WORDS[key]
        vec = [0.0, 0.0, 0.0]
        vec[axis] = sign
        return vec
    return None


def _explicit_pointer(intent: str) -> list[str] | None:
    """`@face:face_005` / bare `face_005` → verbatim, no interpretation."""
    tokens = re.findall(r"(?:@face:)?(face_\w+)", intent)
    return tokens or None


def _matching_body(intent: str, body_names: dict[str, str]) -> str | None:
    """A part name mentioned in the intent scopes the search to that body."""
    lowered = intent.lower()
    best: tuple[int, str] | None = None
    for body_id, name in body_names.items():
        if name.lower() in lowered:
            if best is None or len(name) > best[0]:
                best = (len(name), body_id)
    return best[1] if best else None


def _alignment(face: dict[str, Any], axis: int, sign: float) -> float | None:
    normal = face.get("normal")
    if not isinstance(normal, list) or len(normal) < 3:
        return None
    try:
        component = float(normal[axis]) * sign
    except (TypeError, ValueError):
        return None
    return component


def _area(face: dict[str, Any]) -> float:
    value = face.get("area")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _fail(reason: str, candidates: list[dict[str, Any]], body_names: dict[str, str]) -> dict[str, Any]:
    return {
        "status": "ambiguous" if candidates else "not_found",
        "face_ids": [],
        "reason": reason,
        "candidates": [describe_face(f, body_names) for f in candidates[:8]],
    }


def resolve_face_intent(topology: dict[str, Any], intent: Any) -> dict[str, Any]:
    """Resolve one engineering-language face intent against a package topology.

    Returns ``{status, face_ids, reason, confidence, candidates}``:

    - ``status: "ok"`` — ``face_ids`` is what the words denote and ``reason``
      says why, in the same terms (surface type, normal, area, owning part).
    - ``status: "ambiguous"`` — two or more faces fit equally well; the caller
      must choose. ``candidates`` lists them. Nothing is guessed.
    - ``status: "not_found"`` — the vocabulary matched nothing here.

    Understood today: an explicit ``@face:`` pointer (verbatim), the six face
    directions (bottom/top/left/right/front/back and ±X/±Y/±Z, with Chinese
    aliases), "largest flat face", and bolt/mounting holes (returns the group).
    Any of these may be scoped to a named part — "rib_main top", "肋的顶面".
    """
    faces = _faces(topology)
    body_names = _body_names(topology)
    if not faces:
        return {"status": "not_found", "face_ids": [], "reason": "topology has no faces",
                "candidates": [], "confidence": "none"}

    if isinstance(intent, (list, tuple)):
        merged: list[str] = []
        reasons: list[str] = []
        for item in intent:
            sub = resolve_face_intent(topology, item)
            if sub["status"] != "ok":
                return sub
            merged.extend(sub["face_ids"])
            reasons.append(sub["reason"])
        return {"status": "ok", "face_ids": merged, "reason": "; ".join(reasons),
                "candidates": [], "confidence": "high"}

    if not isinstance(intent, str) or not intent.strip():
        return {"status": "not_found", "face_ids": [], "reason": "empty face intent",
                "candidates": [], "confidence": "none"}

    text = intent.strip()
    lowered = text.lower()
    by_id = {str(f["id"]): f for f in faces}

    # 1. Explicit pointer wins — never reinterpret what the caller already resolved.
    explicit = _explicit_pointer(text)
    if explicit:
        missing = [fid for fid in explicit if fid not in by_id]
        if missing:
            return {"status": "not_found", "face_ids": [], "confidence": "none",
                    "reason": f"face id(s) not in this topology: {', '.join(missing)}",
                    "candidates": []}
        return {"status": "ok", "face_ids": explicit, "confidence": "high",
                "reason": "explicit pointer: "
                          + "; ".join(describe_face(by_id[f], body_names) for f in explicit),
                "candidates": []}

    # A named part scopes every rule below to that body.
    scoped_body = _matching_body(text, body_names)
    pool = [f for f in faces if not scoped_body or str(f.get("body_id")) == scoped_body]
    scope_note = f" on {body_names[scoped_body]}" if scoped_body else ""
    if not pool:
        return {"status": "not_found", "face_ids": [], "confidence": "none",
                "reason": f"no faces{scope_note}", "candidates": []}

    # 2. Bolt / mounting holes → the cylindrical group.
    if any(word in lowered for word in _HOLE_WORDS):
        holes = [
            f for f in pool
            if f.get("surface_type") == "cylinder"
            and ("mounting_candidate" in (f.get("roles") or []) or f.get("radius"))
        ]
        if holes:
            # Group by radius so "the bolt holes" means one consistent pattern.
            by_radius: dict[float, list[dict[str, Any]]] = {}
            for f in holes:
                by_radius.setdefault(round(float(f.get("radius") or 0.0), 3), []).append(f)
            largest_group = max(by_radius.values(), key=len)
            radius = float(largest_group[0].get("radius") or 0.0)
            return {
                "status": "ok",
                "face_ids": [str(f["id"]) for f in largest_group],
                "confidence": "high" if len(by_radius) == 1 else "medium",
                "reason": (
                    f"{len(largest_group)} cylindrical face(s) of r={radius:.2f} mm{scope_note}"
                    + ("" if len(by_radius) == 1 else
                       f" (the largest of {len(by_radius)} hole sizes present)")
                ),
                "candidates": [],
            }
        return _fail(f"no cylindrical hole faces found{scope_note}", [], body_names)

    # 3. Largest flat face.
    if any(word in lowered for word in _LARGEST_FLAT_WORDS):
        planes = [f for f in pool if f.get("surface_type") == "plane"]
        if not planes:
            return _fail(f"no planar faces found{scope_note}", [], body_names)
        planes.sort(key=_area, reverse=True)
        if len(planes) > 1 and _area(planes[1]) >= _area(planes[0]) * _AMBIGUOUS_AREA_RATIO:
            return _fail(
                "several planar faces have essentially the same area; name a direction "
                "(e.g. \"bottom\") or pass an @face: pointer",
                planes[:4], body_names,
            )
        return {"status": "ok", "face_ids": [str(planes[0]["id"])], "confidence": "high",
                "reason": "largest planar face" + scope_note + ": "
                          + describe_face(planes[0], body_names),
                "candidates": []}

    # 4. Direction words → the planar face most aligned with that axis.
    axis_hit: tuple[int, float] | None = None
    matched_word = ""
    for word, spec in _AXIS_WORDS.items():
        if word in lowered and len(word) > len(matched_word):
            axis_hit, matched_word = spec, word
    if axis_hit is None:
        return {
            "status": "not_found", "face_ids": [], "confidence": "none",
            "reason": (
                f"could not interpret {text!r}. Understood: a direction "
                "(bottom/top/left/right/front/back, ±X/±Y/±Z), \"largest flat face\", "
                "\"bolt holes\", any of those scoped to a part name, or an explicit "
                "@face: pointer"
            ),
            "candidates": [describe_face(f, body_names) for f in sorted(pool, key=_area, reverse=True)[:6]],
        }

    axis, sign = axis_hit
    planes = [f for f in pool if f.get("surface_type") == "plane"]
    scored = [
        (component, f) for f, component in
        ((f, _alignment(f, axis, sign)) for f in planes)
        if component is not None
    ]

    aligned = [pair for pair in scored if pair[0] >= _ALIGNMENT_MIN]
    inclined = False
    if not aligned:
        # A shape's "top" is not always axis-aligned: the top of a triangular
        # gusset is its sloped hypotenuse (measured normal [0.53, 0, 0.848] —
        # just under the strict bar). Fall back to the surface that faces that
        # way MOST, and say plainly that it is inclined, instead of refusing a
        # face every engineer would call the top.
        aligned = [pair for pair in scored if pair[0] >= _INCLINED_MIN]
        inclined = True
    if not aligned:
        return _fail(
            f"no planar face faces {matched_word}{scope_note} "
            f"(a curved face cannot be selected this way — pass an @face: pointer)",
            sorted(pool, key=_area, reverse=True)[:6], body_names,
        )

    if inclined:
        # Among merely inclined faces, "most facing that way" beats "biggest".
        aligned.sort(key=lambda pair: (pair[0], _area(pair[1])), reverse=True)
        rival_is_close = (
            len(aligned) > 1 and aligned[1][0] >= aligned[0][0] * _AMBIGUOUS_AREA_RATIO
        )
    else:
        aligned.sort(key=lambda pair: _area(pair[1]), reverse=True)
        rival_is_close = (
            len(aligned) > 1 and _area(aligned[1][1]) >= _area(aligned[0][1]) * _AMBIGUOUS_AREA_RATIO
        )
    component, best = aligned[0]

    if rival_is_close:
        return _fail(
            f"{len(aligned)} faces face {matched_word}{scope_note} about equally; "
            "scope it to a part (e.g. \"base_plate bottom\") or pass an @face: pointer",
            [f for _c, f in aligned[:6]], body_names,
        )

    note = ""
    if inclined:
        import math

        tilt = math.degrees(math.acos(max(-1.0, min(1.0, component))))
        note = f" (inclined {tilt:.0f}° from {matched_word}; the most {matched_word}-facing surface)"
    elif len(aligned) > 1:
        note = f" (largest of {len(aligned)} candidates)"

    return {
        "status": "ok",
        "face_ids": [str(best["id"])],
        "confidence": "medium" if (inclined or len(aligned) > 1) else "high",
        "reason": f"{matched_word}{scope_note}: " + describe_face(best, body_names) + note,
        "candidates": [],
    }
