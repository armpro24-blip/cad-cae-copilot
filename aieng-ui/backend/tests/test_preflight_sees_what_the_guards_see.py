"""The preflight must not report READY on the deck the solver will refuse.

`cae.prepare_solver_run` is step 3 of AGENTS.md's workflow E — the parametric
re-solve loop — and its documented job is to say what to do next. Two guards
were added downstream of it and it knew about neither:

- `cae.run_solver` refuses a deck built for another geometry revision
  (`stale_deck`, #532);
- `cae.generate_solver_input` refuses a mesh built for one (`stale_mesh`, #537).

So after a parameter edit, called the documented way (no `run_id`), it defaulted
to `run_001`, found that *baseline* deck present, and reported:

    ready_to_run: true
    missing_items: []
    recommended_next_calls: [{tool: cae.run_solver,
                              input_deck_path: simulation/runs/run_001/solver_input.inp}]

That deck is the pre-edit one. `stale_deck` refuses it, so no wrong number
escapes — the guard holds. But the answer handed to the agent is "ready", the
recommended action is the one guaranteed to be refused, and the way forward
(re-mesh, then a deck under a NEW run id) is never named. `safety-by-accident`
from the review lens, one layer up: the gate holds, and the tool whose job is
guidance points straight at it.

The missing-deck recommendation also carried `overwrite: True` on the caller's
`run_id` — which defaults to `run_001`. AGENTS.md calls `overwrite: true` "the
option that destroys the earlier result", and this response is the one an agent
follows literally.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

_TOPOLOGY = {
    "entities": [
        {"id": "body_001", "type": "solid", "name": "beam"},
        {"id": "face_001", "type": "face", "surface_type": "plane", "area": 1000.0,
         "normal": [-1.0, 0.0, 0.0], "body_id": "body_001"},
        {"id": "face_002", "type": "face", "surface_type": "plane", "area": 1000.0,
         "normal": [1.0, 0.0, 0.0], "body_id": "body_001"},
    ]
}


def _package(
    path: Path,
    *,
    mesh_revision: int | None = 0,
    deck_revision: int | None = 0,
    current_revision: int | None = None,
    with_deck: bool = True,
) -> Path:
    """A package the preflight can judge. `None` revisions record nothing."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam", "resources": {}}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY))
        zf.writestr("simulation/mesh/mesh.inp", "*NODE\n1,0,0,0\n")
        mesh_meta: dict[str, Any] = {"schema_version": "0.1", "nodes": 800}
        if mesh_revision is not None:
            mesh_meta["geometry_revision"] = mesh_revision
        zf.writestr("simulation/mesh/mesh_metadata.json", json.dumps(mesh_meta))
        zf.writestr("simulation/solver_settings.json",
                    json.dumps({"solver": "CalculiX", "analysis_type": "static"}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps(
            {"materials": [{"name": "Al6061-T6", "youngs_modulus_pa": 69e9,
                            "poisson_ratio": 0.33, "density_kg_m3": 2700}]}))
        zf.writestr("simulation/cae_imports/parsed_boundary_conditions.json", json.dumps(
            {"boundary_conditions": [{"id": "bc_001", "type": "fixed",
                                      "target": "@face:face_001",
                                      "dof_start": 1, "dof_end": 3, "value": 0}]}))
        zf.writestr("simulation/cae_imports/parsed_loads.json", json.dumps(
            {"loads": [{"id": "load_001", "type": "force", "target": "@face:face_002",
                        "value_n": 500, "direction": [0, 0, -1]}]}))
        if with_deck:
            zf.writestr("simulation/runs/run_001/solver_input.inp", "** deck\n")
            provenance: dict[str, Any] = {"run_id": "run_001"}
            if deck_revision is not None:
                provenance["geometry_revision"] = deck_revision
            zf.writestr("simulation/runs/run_001/deck_provenance.json",
                        json.dumps(provenance))
        if current_revision is not None:
            zf.writestr("state/revalidation_status.json",
                        json.dumps({"current_geometry_revision": current_revision}))
    return path


@pytest.fixture(scope="module")
def preflight(tmp_path_factory):
    """`cae.prepare_solver_run` bound to a throwaway platform root."""
    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings

    root = tmp_path_factory.mktemp("preflight")
    create_app(Settings(
        platform_root=root / "platform",
        workspace_root=root / "workspace",
        data_root=root / "data",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        sample_step=root / "workspace" / "sample.step",
    ))

    def run(pkg: Path, **extra: Any) -> dict[str, Any]:
        return runtime.invoke_tool(
            "cae.prepare_solver_run", {"package_path": str(pkg), **extra}
        )

    return run


def _tools(result: dict[str, Any]) -> list[str]:
    return [r.get("tool") for r in result.get("recommended_next_calls") or []]


def _rec(result: dict[str, Any], tool: str) -> dict[str, Any]:
    for entry in result.get("recommended_next_calls") or []:
        if entry.get("tool") == tool:
            return entry
    raise AssertionError(f"{tool} not recommended: {_tools(result)}")


# ── the measured case: a baseline deck after an edit ────────────────────────


def test_a_deck_from_before_the_edit_is_not_ready_to_run(preflight, tmp_path) -> None:
    result = preflight(_package(
        tmp_path / "edited.aieng", mesh_revision=1, deck_revision=0, current_revision=1
    ))

    assert result["ready_to_run"] is False, result
    assert any("stale_deck" in item for item in result["preflight"]["missing_items"]), (
        result["preflight"]["missing_items"]
    )
    assert result["preflight"]["stale_deck"] is True
    assert result["preflight"]["deck_geometry_revision"] == 0
    assert result["preflight"]["current_geometry_revision"] == 1


def test_it_recommends_a_free_run_id_and_never_the_baseline(preflight, tmp_path) -> None:
    result = preflight(_package(
        tmp_path / "edited.aieng", mesh_revision=1, deck_revision=0, current_revision=1
    ))

    deck = _rec(result, "cae.generate_solver_input")
    assert deck["input"]["run_id"] == "run_002", deck
    assert "overwrite" not in deck["input"], (
        "AGENTS.md calls overwrite:true the option that destroys the earlier result"
    )
    assert result["preflight"]["next_free_run_id"] == "run_002"


def test_it_does_not_recommend_running_the_deck_the_solver_will_refuse(
    preflight, tmp_path
) -> None:
    result = preflight(_package(
        tmp_path / "edited.aieng", mesh_revision=1, deck_revision=0, current_revision=1
    ))

    assert "cae.run_solver" not in _tools(result), _tools(result)


# ── the mesh half, and its ordering ─────────────────────────────────────────


def test_a_mesh_from_before_the_edit_is_reported_and_re_meshing_comes_first(
    preflight, tmp_path
) -> None:
    """A deck generated on a stale mesh is refused, so the re-mesh leads."""
    result = preflight(_package(
        tmp_path / "stale_mesh.aieng",
        mesh_revision=0, deck_revision=0, current_revision=1,
    ))

    assert result["ready_to_run"] is False
    assert any("stale_mesh" in item for item in result["preflight"]["missing_items"]), (
        result["preflight"]["missing_items"]
    )
    tools = _tools(result)
    assert "cae.generate_mesh" in tools, tools
    assert tools.index("cae.generate_mesh") < tools.index("cae.generate_solver_input"), (
        "generating a deck on the old mesh is refused, so re-mesh is the first step"
    )


# ── what must NOT change ───────────────────────────────────────────────────


def test_a_deck_built_for_the_current_geometry_is_left_alone(preflight, tmp_path) -> None:
    """The correct state: baseline solve of an unedited package.

    Revision 0 on every side, and no `revalidation_status.json` at all — which
    the readers treat as revision 0, not as unknown.
    """
    result = preflight(_package(tmp_path / "fresh.aieng"))

    assert result["preflight"]["stale_deck"] is False
    assert result["preflight"]["stale_mesh"] is False
    for item in result["preflight"]["missing_items"]:
        assert "stale_deck" not in item and "stale_mesh" not in item, item
    assert "cae.generate_mesh" not in _tools(result), _tools(result)


def test_a_package_recording_no_revisions_is_not_declared_stale(
    preflight, tmp_path
) -> None:
    """A deck and mesh written before revisions were recorded carry none.

    `None` means "cannot tell", never "they differ" — refusing on an unknown
    would block every package built before #532/#537.
    """
    result = preflight(_package(
        tmp_path / "legacy.aieng",
        mesh_revision=None, deck_revision=None, current_revision=3,
    ))

    assert result["preflight"]["stale_deck"] is False
    assert result["preflight"]["stale_mesh"] is False
    assert result["preflight"]["deck_geometry_revision"] is None
    for item in result["preflight"]["missing_items"]:
        assert "stale_deck" not in item and "stale_mesh" not in item, item


def test_a_missing_deck_still_recommends_generating_one(preflight, tmp_path) -> None:
    """The pre-existing recommendation survives — with a free id, no overwrite."""
    result = preflight(_package(tmp_path / "nodeck.aieng", with_deck=False))

    deck = _rec(result, "cae.generate_solver_input")
    assert deck["input"]["run_id"] == "run_001", "nothing to preserve yet"
    assert "overwrite" not in deck["input"], deck
