"""A deck may not be generated on a mesh built for other geometry.

Found by a cold-start agent trial — an agent given only the MCP tools and the
docs, with no knowledge of this codebase. It could not find anything that
refuses a stale MESH, noticed that the docs guard the stale DECK emphatically,
and reported the asymmetry. Measured on the reference beam: edit 10 -> 20 mm,
generate a FRESH deck for run_002, skip the re-mesh, solve.

    max_displacement:     2.480533 -> 2.480533  (0.0%)
    max_von_mises_stress: 175.746  -> 175.746   (0.0%)
    geometry_changed: true   binding_count_changed: false   warnings: []

0.0% change on a doubled thickness, and every existing guard passed:
`stale_deck` cannot fire because the deck IS new, `cae.prepare_solver_run`
returned ok, and `cae.compare_runs` correctly reported `geometry_changed: true`
because the geometry really did change — so none of them could tell. The mesh
was the stale artifact and nothing looked at it. Same wrong answer as #532, one
layer down.

Reading the docs adversarially found a defect in the code, because the docs
record what the guarantees are meant to be: where they were asymmetric, the
code was too.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.runtime_registry.cae import _stale_mesh_revisions

_MESH_META = "simulation/mesh/mesh_metadata.json"
_STATUS = "state/revalidation_status.json"


def _package(
    path: Path, *, mesh_revision: object, current_revision: object
) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        if mesh_revision is not ...:
            meta: dict = {"schema_version": "0.1", "generator": "gmsh"}
            if mesh_revision is not None:
                meta["geometry_revision"] = mesh_revision
            zf.writestr(_MESH_META, json.dumps(meta))
        if current_revision is not None:
            zf.writestr(
                _STATUS, json.dumps({"current_geometry_revision": current_revision})
            )
    return path


def test_a_mesh_from_before_the_edit_is_reported_stale(tmp_path: Path) -> None:
    """The measured case: mesh at revision 0, package at 1."""
    pkg = _package(tmp_path / "stale.aieng", mesh_revision=0, current_revision=1)

    assert _stale_mesh_revisions(pkg) == (0, 1)


def test_a_mesh_for_the_current_geometry_is_not_stale(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "fresh.aieng", mesh_revision=1, current_revision=1)

    assert _stale_mesh_revisions(pkg) is None


def test_a_baseline_mesh_before_any_edit_is_not_stale(tmp_path: Path) -> None:
    """Revision 0 with no status file at all — the first solve of a new part.

    This is where the first attempt at this fix failed. The mesh writer returned
    `None` for a package with no `revalidation_status.json`, while the deck's
    own `_current_geometry_revision` returns 0 there. The check then compared
    "unknown vs 1" and stayed silent on exactly the sequence it exists for:
    baseline, edit, re-solve. Both sides now say 0.
    """
    pkg = _package(tmp_path / "baseline.aieng", mesh_revision=0, current_revision=None)

    assert _stale_mesh_revisions(pkg) is None


def test_a_mesh_with_no_recorded_revision_cannot_be_checked(tmp_path: Path) -> None:
    """Absent evidence keeps the old behaviour rather than refusing every package.

    The `face_signatures` discipline: a mesh written before meshes recorded a
    revision carries none, and refusing on an unknown would block every package
    already on disk.
    """
    pkg = _package(tmp_path / "legacy.aieng", mesh_revision=None, current_revision=1)

    assert _stale_mesh_revisions(pkg) is None


def test_a_package_with_no_mesh_at_all_is_not_stale(tmp_path: Path) -> None:
    """"No mesh" is a different refusal, raised elsewhere as missing setup."""
    pkg = _package(tmp_path / "nomesh.aieng", mesh_revision=..., current_revision=1)

    assert _stale_mesh_revisions(pkg) is None


def test_the_mesh_writer_and_the_deck_agree_on_what_revision_zero_means() -> None:
    """The two conventions must match or the guard is silent where it matters.

    Asserted as an invariant across the two modules rather than as a literal,
    because it was their DISAGREEMENT that made the first fix a no-op.
    """
    import zipfile as _zipfile
    import tempfile

    from aieng.simulation.deck_generator import _current_geometry_revision
    from app.simulation_runner import _package_geometry_revision

    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "empty.aieng"
        with _zipfile.ZipFile(pkg, "w") as zf:
            zf.writestr("manifest.json", "{}")

        writer_says = _package_geometry_revision(pkg)
        with _zipfile.ZipFile(pkg) as zf:
            deck_says = _current_geometry_revision(zf)

    assert writer_says == deck_says == 0, (
        f"mesh writer says {writer_says}, deck says {deck_says} — a mismatch "
        "makes the stale-mesh check silent on a baseline-then-edit sequence"
    )


def test_the_tool_refuses_by_name_not_as_missing_setup(tmp_path: Path) -> None:
    """`missing_setup` is not branchable: the mesh is not missing, it is old.

    The core generator also refuses this, but as `missing_setup` — and this repo
    has already paid for a code an agent cannot branch on. The pre-check runs
    before any deck work, so a package needs nothing but the two revisions to
    prove the wiring.
    """
    from app.main import create_app, default_project, project_dir, save_project
    from app import runtime
    from tests.test_api import _make_patch_settings

    settings = _make_patch_settings(tmp_path)
    create_app(settings)
    project = save_project(settings, default_project("stale-mesh"))
    pid = project["id"]
    pkg = project_dir(settings, pid) / "b.aieng"
    _package(pkg, mesh_revision=0, current_revision=1)
    project["aieng_file"] = "b.aieng"
    save_project(settings, project)

    result = runtime.invoke_tool("cae.generate_solver_input", {"project_id": pid})

    assert result["code"] == "stale_mesh", result
    assert result["mesh_geometry_revision"] == 0
    assert result["current_geometry_revision"] == 1
    assert "0.0% change on a doubled thickness" in result["message"]
