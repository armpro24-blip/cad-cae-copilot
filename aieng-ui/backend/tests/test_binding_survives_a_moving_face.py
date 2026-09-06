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
