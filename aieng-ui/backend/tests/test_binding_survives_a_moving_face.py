"""A binding must survive an edit that MOVES the face it named.

Found by running the promised task from a clean checkout on the part type the
promise actually names — a bracket, not the acceptance suite's single beam.

On a two-body bracket the constant that dimensions the plate also *positions*
the rib (`rib.moved(Location((0, 0, PLATE_THICKNESS)))`), which is exactly how
AGENTS.md's own Engineering Mode example is written. Doubling it moves the rib's
top face, so `topology_identity` cannot re-identify it: measured, `face_012`
retired and the load face came back as `face_022`. The recorded `@face:` pointer
then resolved to nothing and `cae.generate_solver_input` refused with
`unbound_setup_faces` — with `ai_preprocessing.run_ai_preprocessing` (an
Anthropic API key) as the only documented recovery, on a workbench whose stated
premise is that the backend needs no key. The promised edit -> re-solve ->
compare task could not complete on a bracket.

The fix is not a looser matcher. `cae.setup_static` already knew the words that
chose the face ("rib_main top"); it just threw them away after resolving. It now
stores them beside the pointer, and the SAME deterministic resolver runs again
when the pointer is dead. `resolve_face_intent` refuses ambiguity, so a phrase
that no longer picks exactly one face fails here exactly as it would have failed
at authoring time.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.simulation_runner import normalize_cae_bindings


def _topology(rib_top_id: str, rib_top_z: float) -> dict:
    """A plate plus a rib whose top face id and height depend on the edit."""
    return {
        "entities": [
            {"id": "body_001", "type": "solid", "name": "base_plate"},
            {"id": "body_002", "type": "solid", "name": "rib_main"},
            {"id": "face_005", "type": "face", "surface_type": "plane", "area": 9600.0,
             "normal": [0.0, 0.0, -1.0], "centroid": [0.0, 0.0, 0.0], "body_id": "body_001"},
            {"id": "face_006", "type": "face", "surface_type": "plane", "area": 9600.0,
             "normal": [0.0, 0.0, 1.0], "centroid": [0.0, 0.0, 12.0], "body_id": "body_001"},
            {"id": rib_top_id, "type": "face", "surface_type": "plane", "area": 360.0,
             "normal": [0.0, 0.0, 1.0], "centroid": [0.0, 0.0, rib_top_z],
             "body_id": "body_002"},
        ]
    }


def _package(
    path: Path,
    *,
    topology: dict,
    load_target: str,
    selector: str | None,
    mapping_face: str,
) -> Path:
    load: dict = {"id": "load_001", "type": "force", "target": load_target,
                  "value_n": 500.0, "direction": [0.0, 0.0, -1.0]}
    if selector is not None:
        load["target_selector"] = selector
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "bracket"}))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps({"loads": [load]}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
            {"boundary_conditions": [
                {"id": "bc_001", "type": "fixed", "target": "@face:face_005",
                 "target_selector": "base_plate bottom",
                 "dof_start": 1, "dof_end": 3, "value": 0}
            ]}
        ))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": [
            {"cae_entity": "LOAD_001", "face_ids": [mapping_face],
             "maps_to": {"cae_target_id": "load_001", "role": "load_application"}},
        ]}))
    return path


def _mapping(pkg: Path) -> list[dict]:
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read("simulation/cae_mapping.json"))["mappings"]


def _loads(pkg: Path) -> list[dict]:
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read("simulation/cae_imports/parsed_loads.json"))["loads"]


def test_a_moved_face_is_recovered_from_the_words_that_chose_it(tmp_path: Path) -> None:
    """The measured case: face_012 retired, the rib's top came back as face_022."""
    pkg = _package(
        tmp_path / "bracket.aieng",
        topology=_topology("face_022", 49.0),   # after the edit
        load_target="LOAD_001",                 # already normalised to an NSET
        selector="rib_main top",
        mapping_face="face_012",                # the retired face
    )

    result = normalize_cae_bindings(pkg)

    rebound = result["rebound_from_selector"]
    assert [r["id"] for r in rebound] == ["load_001"], rebound
    assert rebound[0]["face_id"] == "face_022"
    assert rebound[0]["selector"] == "rib_main top"
    # The face that was REPLACED, so the warning can name both ends of the move.
    assert rebound[0]["previous_face"] == "@face:face_012"


def test_the_recovered_face_is_written_back_to_the_package(tmp_path: Path) -> None:
    """In memory is not enough: the deck reads `cae_mapping.json` off disk.

    The mapping write was gated on `derived` — a NEW nset having been invented —
    and a rebind derives nothing, so the corrected binding was computed and then
    dropped. The deck still found the retired face and still refused.
    """
    pkg = _package(
        tmp_path / "bracket.aieng",
        topology=_topology("face_022", 49.0),
        load_target="LOAD_001",
        selector="rib_main top",
        mapping_face="face_012",
    )

    normalize_cae_bindings(pkg)

    load_mapping = next(m for m in _mapping(pkg) if m["cae_entity"] == "LOAD_001")
    assert load_mapping["face_ids"] == ["face_022"]
    # The old face's recorded character would make the next re-verification
    # compare against a face this binding no longer uses.
    assert "face_012" not in json.dumps(load_mapping)


def test_a_healthy_binding_is_not_reported_as_rebound(tmp_path: Path) -> None:
    """Every run after the first normalisation has an NSET-name target.

    Re-resolving those unconditionally reports a rebind that did not happen —
    and a rebind means "the face your setup named is gone and another one is
    carrying the load now", which must stay a real signal.
    """
    pkg = _package(
        tmp_path / "bracket.aieng",
        topology=_topology("face_012", 37.0),   # nothing moved
        load_target="LOAD_001",
        selector="rib_main top",
        mapping_face="face_012",
    )

    result = normalize_cae_bindings(pkg)

    assert result["rebound_from_selector"] == []
    assert next(m for m in _mapping(pkg) if m["cae_entity"] == "LOAD_001")["face_ids"] == ["face_012"]


def test_without_a_selector_nothing_is_recovered(tmp_path: Path) -> None:
    """A package authored before the selector existed carries no evidence.

    Same discipline as the `face_signatures` re-verification: absent evidence
    keeps the conservative refusal rather than being guessed around.
    """
    pkg = _package(
        tmp_path / "legacy.aieng",
        topology=_topology("face_022", 49.0),
        load_target="LOAD_001",
        selector=None,
        mapping_face="face_012",
    )

    result = normalize_cae_bindings(pkg)

    assert result["rebound_from_selector"] == []
    assert next(m for m in _mapping(pkg) if m["cae_entity"] == "LOAD_001")["face_ids"] == ["face_012"]


def test_an_ambiguous_selector_refuses_rather_than_picking(tmp_path: Path) -> None:
    """Two equally-top faces on the same part is exactly what setup_static refuses.

    Re-resolution must inherit that refusal, not become the loose matcher the
    authoring path deliberately is not.
    """
    topology = _topology("face_022", 49.0)
    topology["entities"].append(
        {"id": "face_023", "type": "face", "surface_type": "plane", "area": 360.0,
         "normal": [0.0, 0.0, 1.0], "centroid": [30.0, 0.0, 49.0], "body_id": "body_002"}
    )
    pkg = _package(
        tmp_path / "ambiguous.aieng",
        topology=topology,
        load_target="LOAD_001",
        selector="rib_main top",
        mapping_face="face_012",
    )

    result = normalize_cae_bindings(pkg)

    assert result["rebound_from_selector"] == []
    assert next(m for m in _mapping(pkg) if m["cae_entity"] == "LOAD_001")["face_ids"] == ["face_012"]


def test_setup_static_records_the_words_it_resolved(tmp_path: Path) -> None:
    """The whole recovery rests on the phrase being kept, so pin that it is."""
    from app.main import create_app, default_project, project_dir, save_project
    from app import runtime
    from tests.test_api import _make_patch_settings

    settings = _make_patch_settings(tmp_path)
    create_app(settings)
    project = save_project(settings, default_project("selector"))
    pid = project["id"]
    pkg = project_dir(settings, pid) / "b.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "bracket"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_topology("face_012", 37.0)))
    project["aieng_file"] = "b.aieng"
    save_project(settings, project)

    result = runtime.invoke_tool("cae.setup_static", {
        "project_id": pid,
        "material": "Al6061-T6",
        "fix": "base_plate bottom",
        "load": {"at": "rib_main top", "force_n": 500, "direction": "-Z"},
    })
    assert result["status"] == "ok", result

    assert _loads(pkg)[0]["target_selector"] == "rib_main top"
    with zipfile.ZipFile(pkg) as zf:
        bcs = json.loads(
            zf.read("simulation/cae_imports/parsed_boundary_conditions.json")
        )["boundary_conditions"]
    assert bcs[0]["target_selector"] == "base_plate bottom"


def test_a_rebind_is_reported_as_a_top_level_warning() -> None:
    """"A signal buried in a sub-block is not a signal" — AGENTS.md, of the stale flag.

    The recovery is the right answer, but it means the face the setup NAMED is
    gone and a different one is carrying the load now. Reporting that only
    inside `source_deck_synthesis` asks the caller to go digging for the single
    fact they most need to confirm.
    """
    from app.runtime_registry.cae import rebind_warnings_for

    warnings = rebind_warnings_for({
        "rebound_from_selector": [
            {"id": "load_001", "selector": "rib_main top",
             "face_id": "face_022", "previous_face": "@face:face_012"},
        ]
    })

    assert len(warnings) == 1
    text = warnings[0]
    assert "load_001" in text
    assert "@face:face_012" in text, "the warning must name the face that was replaced"
    assert "@face:face_022" in text, "and the one now carrying the binding"
    assert "rib_main top" in text, "and the phrase that chose it"


def test_no_rebind_produces_no_warning() -> None:
    """The normal case must stay quiet, or the signal stops meaning anything."""
    from app.runtime_registry.cae import rebind_warnings_for

    assert rebind_warnings_for({"rebound_from_selector": []}) == []
    assert rebind_warnings_for({}) == []
    assert rebind_warnings_for(None) == []


# ── a MULTI-face selector: "bolt holes" ─────────────────────────────────────
#
# Found by the part-family sweep. `cae.setup_static` splits `fix: "bolt holes"`
# into one BC per hole, every entry carrying the same phrase, so recovering them
# one at a time is impossible by construction: each entry asks "which single
# face is 'bolt holes'?" and gets four. That refused the whole promised task on
# a bolted mount plate — the most ordinary mechanical fixture there is.
#
# Measured after the fix, at a 3 mm mesh: all four recovered
# (bc_001..bc_004 -> face_011..face_014) and the task completed at
# -80.1% displacement / -65.4% stress for a doubled plate thickness.


def _holes_topology(hole_ids: list[str]) -> dict:
    entities: list[dict] = [{"id": "body_001", "type": "solid", "name": "mount_plate"}]
    for index, fid in enumerate(hole_ids):
        entities.append({
            "id": fid, "type": "face", "surface_type": "cylinder", "area": 314.0,
            "axis": [0.0, 0.0, 1.0], "radius": 5.0,
            "centroid": [45.0 - 30 * index, 25.0, 5.0], "body_id": "body_001",
        })
    return {"entities": entities}


def _bolted_package(
    path: Path,
    *,
    hole_ids: list[str],
    mapped_faces: list[str],
    dof_ends: list[int] | None = None,
) -> Path:
    """N bolt-hole BCs sharing one selector, mapped to now-retired faces."""
    ends = dof_ends or [3] * len(mapped_faces)
    bcs = [
        {"id": f"bc_{i + 1:03d}", "type": "fixed", "target": f"BC_{i + 1:03d}",
         "target_selector": "bolt holes",
         "dof_start": 1, "dof_end": ends[i], "value": 0}
        for i in range(len(mapped_faces))
    ]
    mappings = [
        {"cae_entity": f"BC_{i + 1:03d}", "face_ids": [face],
         "maps_to": {"cae_target_id": f"bc_{i + 1:03d}", "role": "fixed_support"}}
        for i, face in enumerate(mapped_faces)
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "plate"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_holes_topology(hole_ids)))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json",
                    json.dumps({"boundary_conditions": bcs}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps({"loads": []}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": mappings}))
    return path


def test_all_four_bolt_holes_are_recovered_from_one_phrase(tmp_path: Path) -> None:
    pkg = _bolted_package(
        tmp_path / "bolted.aieng",
        hole_ids=["face_011", "face_012", "face_013", "face_014"],   # after the edit
        mapped_faces=["face_007", "face_008", "face_009", "face_010"],  # retired
    )

    result = normalize_cae_bindings(pkg)

    rebound = {r["id"]: r["face_id"] for r in result["rebound_from_selector"]}
    assert rebound == {
        "bc_001": "face_011", "bc_002": "face_012",
        "bc_003": "face_013", "bc_004": "face_014",
    }, rebound
    # Every hole ends up bound exactly once — the union is what the physics is.
    bound = [f for m in _mapping(pkg) for f in m["face_ids"]]
    assert sorted(bound) == ["face_011", "face_012", "face_013", "face_014"]


def test_four_holes_becoming_three_is_refused(tmp_path: Path) -> None:
    """The restraint genuinely changed. A rebind must not paper over that."""
    pkg = _bolted_package(
        tmp_path / "lost.aieng",
        hole_ids=["face_011", "face_012", "face_013"],
        mapped_faces=["face_007", "face_008", "face_009", "face_010"],
    )

    result = normalize_cae_bindings(pkg)

    assert result["rebound_from_selector"] == []


def test_entries_sharing_a_phrase_but_not_the_physics_are_refused(tmp_path: Path) -> None:
    """Interchangeability is what makes the assignment immaterial.

    Face ids are not stable, so no per-entry identity could be honoured. A
    homogeneous group is exactly the case where none is needed; a group whose
    entries restrain different DOFs is not, and guessing which hole was which
    would be inventing a boundary condition.
    """
    pkg = _bolted_package(
        tmp_path / "mixed.aieng",
        hole_ids=["face_011", "face_012", "face_013", "face_014"],
        mapped_faces=["face_007", "face_008", "face_009", "face_010"],
        dof_ends=[3, 3, 3, 1],   # one hole restrains only DOF 1
    )

    result = normalize_cae_bindings(pkg)

    assert result["rebound_from_selector"] == []


@pytest.mark.parametrize("pressures, recovered", [([2.0, 2.0], True), ([2.0, 5.0], False)])
def test_a_pressure_group_is_interchangeable_only_at_equal_pressure(
    tmp_path: Path, pressures: list[float], recovered: bool
) -> None:
    """Pressure is part of the physics: unequal pressures must not be shuffled.

    `pressure_mpa` was missing from the interchangeability signature, so after
    the face ids retired, 2 MPa and 5 MPa on two holes were treated as one
    homogeneous group and reassigned to the replacement faces by sort order.
    """
    loads = [
        {"id": f"load_{i + 1:03d}", "type": "pressure", "target": f"LOAD_{i + 1:03d}",
         "target_selector": "bolt holes", "pressure_mpa": p}
        for i, p in enumerate(pressures)
    ]
    mappings = [
        {"cae_entity": f"LOAD_{i + 1:03d}", "face_ids": [face],
         "maps_to": {"cae_target_id": f"load_{i + 1:03d}", "role": "load_application"}}
        for i, face in enumerate(["face_007", "face_008"])
    ]
    pkg = tmp_path / "pressure.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "plate"}))
        zf.writestr("geometry/topology_map.json",
                    json.dumps(_holes_topology(["face_011", "face_012"])))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json",
                    json.dumps({"boundary_conditions": []}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps({"loads": loads}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": mappings}))

    result = normalize_cae_bindings(pkg)

    assert bool(result["rebound_from_selector"]) is recovered, result


# --- A force split by area follows the faces' areas, not the authored split ---


def _area_package(
    path: Path,
    *,
    faces: dict[str, float],
    mapped: list[str],
    shares: list[float],
    total: float | None,
    selector: str = "top",
) -> Path:
    """Two load entries from one phrase; `faces` is the CURRENT id -> area."""
    entities: list[dict] = [{"id": "body_001", "type": "solid", "name": "blk"}]
    for index, (fid, area) in enumerate(faces.items()):
        entity = {"id": fid, "type": "face", "surface_type": "plane",
                  "normal": [0.0, 0.0, 1.0], "centroid": [10.0 * index, 0.0, 10.0],
                  "body_id": "body_001"}
        if area:
            entity["area"] = area
        entities.append(entity)
    loads = []
    for i, share in enumerate(shares):
        entry = {"id": f"load_{i + 1:03d}", "type": "force", "target": f"LOAD_{i + 1:03d}",
                 "target_selector": selector, "value_n": share, "direction": [0.0, 0.0, -1.0]}
        if total is not None:
            entry.update(load_share_basis="area", total_force_n=total)
        loads.append(entry)
    mappings = [
        {"cae_entity": f"LOAD_{i + 1:03d}", "face_ids": [face],
         "maps_to": {"cae_target_id": f"load_{i + 1:03d}", "role": "load_application"}}
        for i, face in enumerate(mapped)
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "blk"}))
        zf.writestr("geometry/topology_map.json", json.dumps({"entities": entities}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json",
                    json.dumps({"boundary_conditions": []}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps({"loads": loads}))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": mappings}))
    return path


def test_a_resized_face_gets_its_new_area_share(tmp_path: Path) -> None:
    """Authored 800/200 over 800+200 mm²; one face grew to 1600 mm² since."""
    pkg = _area_package(tmp_path / "grown.aieng",
                        faces={"face_001": 1600.0, "face_002": 200.0},
                        mapped=["face_001", "face_002"], shares=[800.0, 200.0], total=1000.0)

    result = normalize_cae_bindings(pkg)

    written = {entry["id"]: entry["value_n"] for entry in _loads(pkg)}
    assert written["load_001"] == pytest.approx(1000.0 * 1600 / 1800)
    assert written["load_002"] == pytest.approx(1000.0 * 200 / 1800)
    assert sum(written.values()) == pytest.approx(1000.0)
    [group] = result["area_shares"]
    assert group["status"] == "recomputed" and group["selector"] == "top"
    assert {e["id"]: e["previous_n"] for e in group["entries"]} == {
        "load_001": 800.0, "load_002": 200.0}

    from app.runtime_registry.cae import rebind_warnings_for

    [warning] = rebind_warnings_for(result)
    assert "re-split" in warning and "800 -> 888.889 N" in warning


def test_unchanged_areas_change_nothing(tmp_path: Path) -> None:
    pkg = _area_package(tmp_path / "same.aieng",
                        faces={"face_001": 800.0, "face_002": 200.0},
                        mapped=["face_001", "face_002"], shares=[800.0, 200.0], total=1000.0)

    result = normalize_cae_bindings(pkg)

    assert result["area_shares"] == []


def test_an_area_shared_group_is_rebound_and_re_split(tmp_path: Path) -> None:
    """Unequal authored shares used to make the group non-interchangeable.

    The share is derived from the face it lands on, so the entries ARE
    interchangeable once it is re-split after the rebind: the hole faces
    retired, "bolt holes" still picks two, each gets its own area share.
    """
    topology = _holes_topology(["face_011", "face_012"])
    topology["entities"][2]["area"] = 3 * topology["entities"][1]["area"]  # deeper hole
    pkg = _area_package(tmp_path / "moved.aieng", faces={}, mapped=["face_001", "face_002"],
                        shares=[800.0, 200.0], total=1000.0, selector="bolt holes")
    _replace_member(pkg, "geometry/topology_map.json", json.dumps(topology))

    result = normalize_cae_bindings(pkg)

    assert len(result["rebound_from_selector"]) == 2, result
    mapping = {m["cae_entity"]: m["face_ids"][0] for m in _mapping(pkg)}
    by_face = {mapping[entry["target"]]: entry["value_n"] for entry in _loads(pkg)}
    assert by_face == pytest.approx({"face_011": 250.0, "face_012": 750.0})


def test_a_group_whose_phrase_now_picks_one_face_is_refused(tmp_path: Path) -> None:
    """Two entries, one face left: the count changed, as in four holes -> three.

    The single-face branch returned that face to EVERY entry of the group, so
    both loads were bound to the same face with nothing reported.
    """
    pkg = _area_package(tmp_path / "shrunk.aieng",
                        faces={"face_011": 300.0, "face_012": 900.0},
                        mapped=["face_001", "face_002"], shares=[800.0, 200.0], total=1000.0)

    result = normalize_cae_bindings(pkg)   # "top" resolves to the larger face only

    assert result["rebound_from_selector"] == []


def test_a_split_without_a_recorded_total_is_left_as_written(tmp_path: Path) -> None:
    """A setup written before the total was recorded has no basis to re-split on."""
    pkg = _area_package(tmp_path / "legacy.aieng",
                        faces={"face_001": 1600.0, "face_002": 200.0},
                        mapped=["face_001", "face_002"], shares=[800.0, 200.0], total=None)

    result = normalize_cae_bindings(pkg)

    assert result["area_shares"] == []
    assert [entry["value_n"] for entry in _loads(pkg)] == [800.0, 200.0]


def test_a_face_without_an_area_is_reported_not_guessed(tmp_path: Path) -> None:
    pkg = _area_package(tmp_path / "noarea.aieng",
                        faces={"face_001": 1600.0, "face_002": 0.0},
                        mapped=["face_001", "face_002"], shares=[800.0, 200.0], total=1000.0)

    result = normalize_cae_bindings(pkg)

    [group] = result["area_shares"]
    assert group["status"] == "not_recomputed"
    assert [entry["value_n"] for entry in _loads(pkg)] == [800.0, 200.0]


def _replace_member(pkg: Path, name: str, data: str) -> None:
    with zipfile.ZipFile(pkg) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members[name] = data.encode()
    with zipfile.ZipFile(pkg, "w") as zf:
        for n, d in members.items():
            zf.writestr(n, d)


def test_a_partly_retired_group_fills_the_free_faces(tmp_path: Path) -> None:
    """One hole kept its id, the other retired: the moved entry takes the FREE face.

    Assigning by index over every recovered face could give bc_001 (retired)
    face_011 — the face bc_002 still holds — so two restraints sat on one hole
    and the other hole carried none.
    """
    pkg = _bolted_package(
        tmp_path / "partial.aieng",
        hole_ids=["face_011", "face_013"],
        mapped_faces=["face_007", "face_011"],   # bc_001 retired, bc_002 still bound
    )

    result = normalize_cae_bindings(pkg)

    assert {r["id"]: r["face_id"] for r in result["rebound_from_selector"]} == {
        "bc_001": "face_013"}
    bound = sorted(f for m in _mapping(pkg) for f in m["face_ids"])
    assert bound == ["face_011", "face_013"]


def test_two_groups_sharing_phrase_and_total_are_not_merged(tmp_path: Path) -> None:
    """Two authored 1000 N splits in one bucket must not become one 1000 N split.

    Re-sharing groups entries by phrase, total and direction; two identical
    groups would have had ONE total spread over all four entries — half the
    load. Their shares sum to twice the total, so they are left as written.
    """
    pkg = _area_package(tmp_path / "twice.aieng",
                        faces={"face_001": 1600.0, "face_002": 200.0},
                        mapped=["face_001", "face_002", "face_001", "face_002"],
                        shares=[800.0, 200.0, 800.0, 200.0], total=1000.0)

    result = normalize_cae_bindings(pkg)

    [group] = result["area_shares"]
    assert group["status"] == "not_recomputed"
    assert sum(entry["value_n"] for entry in _loads(pkg)) == pytest.approx(2000.0)


# --- The preflight's rebind preview claims only what the rebind repairs ---


def _moved_rib_package(tmp_path: Path) -> Path:
    return _package(
        tmp_path / "preview.aieng",
        topology=_topology("face_022", 49.0),
        load_target="LOAD_001",
        selector="rib_main top",
        mapping_face="face_012",
    )


def test_the_preview_recovers_a_moved_face_on_a_copy(tmp_path: Path) -> None:
    from app.runtime_registry.cae import _selector_rebind_preview

    pkg = _moved_rib_package(tmp_path)
    before = pkg.read_bytes()

    preview = _selector_rebind_preview(pkg, {"face_character_mismatches": []})

    assert preview["recovers_all"] is True, preview
    assert [r["face_id"] for r in preview["rebound"]] == ["face_022"]
    assert pkg.read_bytes() == before   # measured on a copy


def test_a_face_that_changed_character_is_not_claimed_recovered(tmp_path: Path) -> None:
    """A rebind repairs missing ids only; normalisation re-records signatures, so
    the copy cannot show a character change afterwards — the original verdict must."""
    from app.runtime_registry.cae import _selector_rebind_preview

    pkg = _moved_rib_package(tmp_path)

    preview = _selector_rebind_preview(
        pkg, {"face_character_mismatches": [{"face_id": "face_005"}]}
    )

    assert preview["recovers_all"] is False
    assert preview["character_changed"] == ["face_005"]


def test_an_unrelated_empty_nset_stays_broken_in_the_preview(tmp_path: Path) -> None:
    """The moved load face recovers; a BC whose set has no faces does not."""
    from app.runtime_registry.cae import _selector_rebind_preview

    pkg = _moved_rib_package(tmp_path)
    with zipfile.ZipFile(pkg) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    bcs = json.loads(members["simulation/cae_imports/parsed_boundary_conditions.json"])
    bcs["boundary_conditions"].append(
        {"id": "bc_002", "type": "fixed", "target": "BC_EMPTY", "dof_start": 1, "dof_end": 3, "value": 0}
    )
    mapping = json.loads(members["simulation/cae_mapping.json"])
    mapping["mappings"].append(
        {"cae_entity": "BC_EMPTY", "face_ids": [],
         "maps_to": {"cae_target_id": "bc_002", "role": "fixed_support"}}
    )
    members["simulation/cae_imports/parsed_boundary_conditions.json"] = json.dumps(bcs).encode()
    members["simulation/cae_mapping.json"] = json.dumps(mapping).encode()
    with zipfile.ZipFile(pkg, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    preview = _selector_rebind_preview(pkg, {"face_character_mismatches": []})

    assert preview["recovers_all"] is True, preview     # the moved face is recovered
    assert preview["nset_valid_after"] is False         # the empty set is not
