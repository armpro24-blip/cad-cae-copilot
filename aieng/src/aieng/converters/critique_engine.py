"""Pure, deterministic engineering critique engine.

This module contains the geometry/topology audit logic behind
``cad.critique`` without any project/backend coupling.  It reads a
topology_map + feature_graph and returns structured manufacturing-rule
findings so both the interactive CAD tool and batch design-study
evaluation can share one source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .credibility import classify_credibility


_THIN_PART_LABELS: tuple[str, ...] = (
    "wall", "rib", "cover", "lid", "back_plate", "base_plate",
    "plate", "shell", "flange",
)

_STANDARD_HOLE_DIAMETERS_MM: tuple[float, ...] = (
    1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0,
    12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 27.0, 30.0,
)


@dataclass(frozen=True)
class DfMRulePack:
    """Declarative thresholds for a target manufacturing process."""

    name: str
    min_wall_mm: float
    min_corner_radius_mm: float
    check_standard_holes: bool = True
    standard_hole_diameters_mm: tuple[float, ...] = _STANDARD_HOLE_DIAMETERS_MM
    notes: tuple[str, ...] = ()


_DFM_RULE_PACKS: dict[str, DfMRulePack] = {
    "cnc": DfMRulePack(
        name="cnc_aluminium",
        min_wall_mm=3.0,
        min_corner_radius_mm=2.0,
        check_standard_holes=True,
        notes=(
            "Assumes 3-axis CNC machining in aluminium. "
            "Does not check undercuts, deep pockets, or tool access.",
        ),
    ),
    "sheet_metal": DfMRulePack(
        name="sheet_metal",
        min_wall_mm=2.0,
        min_corner_radius_mm=0.5,
        check_standard_holes=True,
        notes=(
            "Assumes brake-formed sheet metal. Flange length and bend radius are "
            "coarse proxies; k-factor, bend relief, and hole-to-bend distance are "
            "not modeled.",
        ),
    ),
    "fdm": DfMRulePack(
        name="fdm",
        min_wall_mm=1.2,
        min_corner_radius_mm=1.0,
        check_standard_holes=False,
        notes=(
            "Assumes FDM/FFF printing. Does not check overhang angle, bridging, "
            "support access, or layer orientation.",
        ),
    ),
    "sla": DfMRulePack(
        name="sla",
        min_wall_mm=0.8,
        min_corner_radius_mm=0.4,
        check_standard_holes=False,
        notes=(
            "Assumes resin SLA printing. Does not check drain holes, support "
            "scarring, or build orientation.",
        ),
    ),
}


def get_rule_pack(process: str) -> DfMRulePack:
    """Return the rule pack for a process name, falling back to CNC."""
    return _DFM_RULE_PACKS.get(str(process or "cnc").lower().replace("-", "_"), _DFM_RULE_PACKS["cnc"])


def is_named_part_feature(feature: dict[str, Any]) -> bool:
    return feature.get("type") in {"named_part", "standard_part"} and bool(feature.get("name"))


def _has_canonical_engineering_label(feat: dict[str, Any]) -> bool:
    name = (feat.get("name") or "").lower()
    canonical = (
        "base_plate", "back_plate", "mount_plate",
        "mounting_hole", "rib", "boss", "flange",
        "interface_face", "load_interface",
        "wall", "cover", "lid", "shell",
    )
    return any(c in name for c in canonical)


def _add_finding(
    findings: list[dict[str, Any]],
    counter: int,
    severity: str,
    category: str,
    rule: str,
    feature: str,
    feature_id: str | None,
    observation: str,
    fix: str,
) -> int:
    findings.append({
        "id": f"find_{counter:03d}",
        "severity": severity,
        "category": category,
        "rule": rule,
        "feature": feature,
        "feature_id": feature_id,
        "observation": observation,
        "suggested_fix": fix,
    })
    return counter + 1


_FINISHING_FEATURE_TYPES = ("fillet", "chamfer")
_SHAPED_FEATURE_TYPES = ("loft", "revolve", "sweep")


def _bbox_contains(outer: list[float], inner: list[float], tol: float = 1.0) -> bool:
    """True if `outer` bbox fully contains `inner` (within tol)."""
    if len(outer) < 6 or len(inner) < 6:
        return False
    return (
        outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1] and outer[2] - tol <= inner[2]
        and outer[3] + tol >= inner[3] and outer[4] + tol >= inner[4] and outer[5] + tol >= inner[5]
    )


def _bbox_volume(bb: list[float]) -> float:
    if len(bb) < 6:
        return 0.0
    return max(0.0, bb[3] - bb[0]) * max(0.0, bb[4] - bb[1]) * max(0.0, bb[5] - bb[2])


# A body whose actual volume is well below its bounding-box volume is a HOLLOW
# enclosure (housing / shell). Parts inside it (bearing seats, gears) are
# legitimately internal — not a 'hidden part' fidelity problem.
_HOLLOW_FILL_RATIO = 0.6


def assess_modeling_fidelity(
    topology_map: dict[str, Any],
    feature_graph: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic 'does this read as designed, or as a crude primitive stack?' check.

    A SEPARATE axis from the DfM critique: a model can be perfectly manufacturable
    yet look unfinished. Uses signals already in the package — the advanced-feature
    tags (fillet/chamfer/loft/sweep/revolve) the feature graph records, per-part
    face counts, and bbox containment — so it is honest and reproducible. Findings
    are advisory (a crude box is a *quality* note, never a correctness failure) and
    each carries a concrete fix. Cylindrical parts (shafts/pins/bores) are NOT
    flagged as 'featureless' — a cylinder is legitimately a cylinder.
    """
    entities = topology_map.get("entities", [])
    bodies = {e["id"]: e for e in entities if e.get("type") == "solid"}
    features = feature_graph.get("features", [])
    named = [f for f in features if is_named_part_feature(f)]

    ftypes = {f.get("type") for f in features}
    has_finishing = any(t in ftypes for t in _FINISHING_FEATURE_TYPES)
    has_shaped = any(t in ftypes for t in _SHAPED_FEATURE_TYPES)

    findings: list[dict[str, Any]] = []
    counter = 1
    score = 100

    if bodies and not has_finishing:
        score -= 40
        counter = _add_finding(
            findings, counter, "medium", "modeling_fidelity", "no_edge_breaking",
            "(model)", None,
            "No fillets or chamfers detected anywhere — every edge is sharp, which reads "
            "as unfinished/crude.",
            "Break visible edges with fillet() / chamfer() applied LAST (after booleans): "
            "~5–15mm on enclosures/housings, ~1–4mm on machined parts.",
        )
    if bodies and not has_shaped and len(bodies) >= 1:
        # A filleted/chamfered model shows finishing intent, so primitive (boxy)
        # massing is a mild note, not a heavy penalty — many good mechanical parts
        # are box-based with broken edges. Only raw, unfinished primitives are docked hard.
        score -= 5 if has_finishing else 20
        extra = (
            " Edge-breaking is present, so this is a mild note (boxy mechanical massing is fine)."
            if has_finishing else ""
        )
        counter = _add_finding(
            findings, counter, "low", "modeling_fidelity", "primitive_stacking_only",
            "(model)", None,
            "The model uses no loft / sweep / revolve (primitive boxes/cylinders only)." + extra,
            "If any body is meant to read as designed or organic, shape it with loft() / "
            "sweep() / revolve() between profiles instead of stacking primitives.",
        )

    # Per-part: a bare 6-face axis-aligned box with no features (cylinders excluded).
    featureless = 0
    for feat in named:
        geo = feat.get("geometry_refs") or {}
        fc = geo.get("face_count") if isinstance(geo, dict) else None
        if fc == 6:
            featureless += 1
            if featureless <= 3:
                score -= 10
                counter = _add_finding(
                    findings, counter, "low", "modeling_fidelity", "featureless_box",
                    feat.get("name", "<unnamed>"), (geo or {}).get("body"),
                    f"{feat.get('name', '<unnamed>')} is a bare 6-face box — no fillets, pockets, "
                    "bosses, or other features.",
                    "If this is a visible or structural body, break its edges and add the detail "
                    "the part actually needs (seats, bosses, ribs); a raw box rarely is the part.",
                )

    # Per-part: a solid buried inside ANOTHER SOLID body is likely not visible and
    # usually a mistake. But a part inside a HOLLOW enclosure (housing/shell) — a
    # bearing seat, a gear, internals — is legitimately internal, so it is NOT
    # flagged. Hollowness is judged from the container's actual volume vs its bbox
    # volume (per-solid volume is recorded in the topology); unknown volume falls
    # back to flagging (bbox containment is then the only signal).
    hidden = 0
    body_list = [
        (bid, b.get("bounding_box") or [], b.get("name", bid), b.get("volume"))
        for bid, b in bodies.items()
    ]

    def _container_is_hollow(obb: list[float], ovol: Any) -> bool:
        bvol = _bbox_volume(obb)
        return isinstance(ovol, (int, float)) and bvol > 0 and (float(ovol) / bvol) < _HOLLOW_FILL_RATIO

    for bid, bb, name, _vol in body_list:
        if len(bb) < 6:
            continue
        container = next(
            (
                onm for oid, obb, onm, ovol in body_list
                if oid != bid and _bbox_contains(obb, bb) and not _container_is_hollow(obb, ovol)
            ),
            None,
        )
        if container is not None:
            hidden += 1
            if hidden <= 3:
                score -= 5
                counter = _add_finding(
                    findings, counter, "low", "modeling_fidelity", "possibly_hidden_part",
                    name, bid,
                    f"{name} sits entirely inside {container}'s bounding box — it may not be "
                    "visible externally (e.g. buried inside a housing).",
                    "Confirm this is intended; if it should be seen, expose it (open/section the "
                    "container, or move/resize the part). Heuristic: bbox containment, not a true "
                    "occlusion test.",
                )

    score = max(0, min(100, score))
    level = "designed" if score >= 75 else ("basic" if score >= 45 else "crude")
    return {
        "score": score,
        "level": level,
        "signals": {
            "has_edge_breaking": has_finishing,
            "has_shaped_bodies": has_shaped,
            "featureless_box_parts": featureless,
            "possibly_hidden_parts": hidden,
            "part_count": len(bodies),
        },
        "findings": findings,
        "note": (
            "Advisory modeling-quality heuristic (separate from manufacturability). 'crude' means "
            "primitive-stacked / unfinished, not non-manufacturable. Cylindrical parts are not "
            "penalised. Score is deterministic, not an aesthetic judgement."
        ),
    }


def critique_geometry(
    topology_map: dict[str, Any],
    feature_graph: dict[str, Any],
    *,
    mode: str = "auto",
    process: str = "cnc",
    min_wall_mm: float | None = None,
    min_corner_radius_mm: float | None = None,
    rule_pack: DfMRulePack | None = None,
) -> dict[str, Any]:
    mode = str(mode or "auto")
    pack = rule_pack or get_rule_pack(process)
    min_wall = float(min_wall_mm if min_wall_mm is not None else pack.min_wall_mm)
    min_corner_radius = float(
        min_corner_radius_mm if min_corner_radius_mm is not None else pack.min_corner_radius_mm
    )
    standard_holes = (
        pack.standard_hole_diameters_mm if pack.check_standard_holes else ()
    )

    findings: list[dict[str, Any]] = []
    counter = 1

    entities = topology_map.get("entities", [])
    bodies = {e["id"]: e for e in entities if e.get("type") == "solid"}
    body_count = len(bodies)

    if body_count == 0:
        return {
            "status": "ok",
            "mode": mode,
            "process": pack.name,
            "verdict": "skipped",
            "message": "No solids in the topology map; nothing to critique.",
            "findings": [],
            "summary": {
                "findings_count": 0,
                "by_severity": {"high": 0, "medium": 0, "low": 0},
                "named_part_count": 0,
                "engineering_audit_run": False,
            },
            "rules_applied": {
                "process": pack.name,
                "min_wall_mm": min_wall,
                "min_corner_radius_mm": min_corner_radius,
                "check_standard_holes": pack.check_standard_holes,
                "standard_hole_diameters_mm": list(standard_holes),
            },
            "rule_source": "aieng/converters/critique_engine DfMRulePack",
            "assumptions": list(pack.notes),
            "fidelity": {
                "score": None, "level": "skipped", "findings": [],
                "signals": {"part_count": 0}, "note": "No solids to assess.",
            },
            "credibility": classify_credibility("critique"),
        }

    if body_count >= 2:
        body_data: list[tuple[str, dict[str, Any], tuple[float, float, float], float]] = []
        for body_id, b in bodies.items():
            bb = b.get("bounding_box") or []
            if len(bb) < 6:
                continue
            center = ((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2)
            size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
            body_data.append((body_id, b, center, size))

        if len(body_data) >= 2:
            mean_size = sum(d[3] for d in body_data) / len(body_data)
            gap_threshold = max(mean_size, 50.0)

            for body_id, b, c1, s1 in body_data:
                min_gap = float("inf")
                for other_id, _, c2, s2 in body_data:
                    if other_id == body_id:
                        continue
                    center_dist = (
                        (c1[0] - c2[0]) ** 2
                        + (c1[1] - c2[1]) ** 2
                        + (c1[2] - c2[2]) ** 2
                    ) ** 0.5
                    gap = center_dist - (s1 + s2) / 2.0
                    if gap < min_gap:
                        min_gap = gap

                if min_gap > gap_threshold:
                    counter = _add_finding(
                        findings,
                        counter,
                        "high",
                        "geometry",
                        "floating_component",
                        b.get("name", body_id),
                        body_id,
                        f"{b.get('name', body_id)}: nearest other part is "
                        f"~{min_gap:.0f}mm away (typical part size {mean_size:.0f}mm) "
                        "— this part is disconnected from the rest of the model.",
                        "Check the Location() / .moved() coordinates for this part; "
                        "it may be a typo that placed it far from the body.",
                    )

    features = feature_graph.get("features", [])
    named_features = [f for f in features if is_named_part_feature(f)]

    engineering_eligible = (
        mode == "engineering"
        or (mode == "auto" and any(_has_canonical_engineering_label(f) for f in named_features))
    )

    if engineering_eligible:
        for feat in named_features:
            name = feat.get("name") or ""
            name_lower = name.lower()
            if not any(t in name_lower for t in _THIN_PART_LABELS):
                continue
            geo = feat.get("geometry_refs") or {}
            body_id = geo.get("body") if isinstance(geo, dict) else None
            body = bodies.get(body_id) if body_id else None
            if not body:
                continue
            bb = body.get("bounding_box") or []
            if len(bb) < 6:
                continue
            dims = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
            positive = [d for d in dims if d > 0]
            if not positive:
                continue
            thinnest = min(positive)
            if thinnest < min_wall:
                severity = "high" if any(t in name_lower for t in ("wall", "shell", "back_plate")) else "medium"
                counter = _add_finding(
                    findings,
                    counter,
                    severity,
                    "manufacturing_rule",
                    "min_wall_thickness",
                    name,
                    body_id,
                    f"{name}: thinnest dimension is {thinnest:.2f}mm; {pack.name} minimum is {min_wall:.1f}mm.",
                    f"Increase the thinnest dimension of {name} to at least {min_wall:.1f}mm "
                    f"or switch to a process with a lower min wall threshold.",
                )

        if standard_holes:
            # Faces carried by recognized thread features: a tapped hole is drilled
            # to a (non-standard) tap-drill diameter on purpose, so flagging it as a
            # non-standard hole is a false positive. Skip those holes.
            threaded_faces: set[str] = set()
            for feat in features:
                if feat.get("type") != "thread":
                    continue
                refs = feat.get("geometry_refs") or {}
                if isinstance(refs, dict):
                    threaded_faces.update(str(fid) for fid in (refs.get("faces") or []))

            for feat in features:
                if feat.get("type") not in ("mounting_hole", "mounting_hole_pattern"):
                    continue
                geo = feat.get("geometry_refs") or {}
                hole_faces = {str(fid) for fid in (geo.get("faces") or [])} if isinstance(geo, dict) else set()
                if hole_faces & threaded_faces:
                    continue
                params = feat.get("parameters") or {}
                # Accept both the runtime (`hole_diameter_mm`) and the core
                # RuleBasedFeatureRecognizer (`diameter_mm`) field names.
                diameter = params.get("hole_diameter_mm")
                if not isinstance(diameter, (int, float)):
                    diameter = params.get("diameter_mm")
                if isinstance(diameter, (int, float)):
                    nearest = min(standard_holes, key=lambda d: abs(d - float(diameter)))
                    if abs(float(diameter) - nearest) > 0.3:
                        counter = _add_finding(
                            findings,
                            counter,
                            "low",
                            "manufacturing_rule",
                            "standard_hole_size",
                            feat.get("name", "<unnamed>"),
                            (feat.get("geometry_refs", {}) or {}).get("body")
                            if isinstance(feat.get("geometry_refs"), dict) else None,
                            f"{feat.get('name', '<unnamed>')}: hole diameter {float(diameter):.2f}mm is "
                            f"non-standard for {pack.name}; closest standard drill is {nearest:.1f}mm.",
                            f"Round the hole diameter to {nearest:.1f}mm to use an off-the-shelf drill.",
                        )

        looks_like_bracket = any(
            "bracket" in (b.get("name") or "").lower()
            or "plate" in (b.get("name") or "").lower()
            for b in bodies.values()
        )
        has_mounting = any(
            f.get("type") in ("mounting_hole", "mounting_hole_pattern") for f in features
        )
        if looks_like_bracket and not has_mounting:
            counter = _add_finding(
                findings,
                counter,
                "medium",
                "engineering",
                "missing_mounting_interface",
                "(model)",
                None,
                "Model contains a plate / bracket part but no mounting holes were detected.",
                "Add at least one Hole() / cboreHole() / cskHole() to expose a mounting interface, "
                "or label the holes you have so the topology heuristic picks them up.",
            )

    severity_counts = {
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
        "low": sum(1 for f in findings if f["severity"] == "low"),
    }
    if severity_counts["high"] > 0:
        verdict = "fails_audit"
    elif severity_counts["medium"] > 0:
        verdict = "passes_with_warnings"
    elif severity_counts["low"] > 0:
        verdict = "passes_with_notes"
    else:
        verdict = "passes"

    fail_first = [
        f"{f['feature']}: {f['observation']}"
        for f in findings
        if f["severity"] in ("high", "medium")
    ][:5]

    for f in findings:
        f["rule_pack"] = pack.name
        f["thresholds"] = {
            "min_wall_mm": min_wall,
            "min_corner_radius_mm": min_corner_radius,
            "check_standard_holes": pack.check_standard_holes,
        }

    return {
        "status": "ok",
        "mode": mode,
        "process": pack.name,
        "verdict": verdict,
        "summary": {
            "findings_count": len(findings),
            "by_severity": severity_counts,
            "named_part_count": len(named_features),
            "engineering_audit_run": engineering_eligible,
        },
        "fail_first_objections": fail_first,
        "findings": findings,
        "rules_applied": {
            "process": pack.name,
            "min_wall_mm": min_wall,
            "min_corner_radius_mm": min_corner_radius,
            "check_standard_holes": pack.check_standard_holes,
            "standard_hole_diameters_mm": list(standard_holes),
        },
        "rule_source": "aieng/converters/critique_engine DfMRulePack",
        "assumptions": list(pack.notes),
        "fidelity": assess_modeling_fidelity(topology_map, feature_graph),
        "credibility": classify_credibility("critique"),
    }
