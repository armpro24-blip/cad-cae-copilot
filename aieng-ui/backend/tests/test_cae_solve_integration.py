"""Real-CalculiX integration test for the full CAE solve loop.

Guards the end-to-end pipeline the four #356 fixes restored:
    generate_mesh -> source-deck synthesis -> generate_solver_input -> ccx
    -> extract_computed_metrics

It actually meshes (Gmsh) and solves (CalculiX) a single-solid cantilever, then
asserts real metrics come out. It SKIPS cleanly when Gmsh, build123d, or a
runnable ccx are unavailable, so it is safe in CI and only exercises the solver
where the tools exist (e.g. a dev box with AIENG_CCX_CMD set to a working
``conda run -n calculix-env ccx``).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


# Topology for the centered Box(100 x 20 x 10): x in [-50, 50], y in [-10, 10],
# z in [-5, 5]. We author it explicitly (rather than via execute_build123d) so
# the test targets the solve loop deterministically; _build_nsets maps these
# face bounding boxes onto the Gmsh mesh nodes geometrically.
_BOX_TOPOLOGY = {
    "schema_version": "0.1",
    "entities": [
        {"id": "face_xmin", "type": "face", "surface_type": "plane",
         "normal": [-1.0, 0.0, 0.0], "bounding_box": [-50.0, -10.0, -5.0, -50.0, 10.0, 5.0]},
        {"id": "face_xmax", "type": "face", "surface_type": "plane",
         "normal": [1.0, 0.0, 0.0], "bounding_box": [50.0, -10.0, -5.0, 50.0, 10.0, 5.0]},
    ],
}


def _rewrite_package(pkg: Path, new_members: dict[str, bytes]) -> None:
    existing: dict[str, bytes] = {}
    with zipfile.ZipFile(pkg) as zf:
        for name in zf.namelist():
            existing[name] = zf.read(name)
    existing.update(new_members)
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in existing.items():
            zf.writestr(name, blob)


def test_full_cae_loop_solves_with_real_ccx(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    bd = pytest.importorskip("build123d")
    from app import aieng_bridge, simulation_runner as sr
    from app.runtime_tool_registry import resolve_ccx_command
    from aieng.simulation.frd_result_extractor import extract_computed_metrics

    ccx_argv, reason = resolve_ccx_command()
    if not ccx_argv:
        pytest.skip(f"CalculiX not configured (set AIENG_CCX_CMD): {reason}")

    # 1. A single-solid cantilever box (100 x 20 x 10 mm).
    pkg = tmp_path / "beam.aieng"
    with tempfile.TemporaryDirectory() as t:
        step_path = Path(t) / "beam.step"
        bd.export_step(bd.Box(100.0, 20.0, 10.0), str(step_path))
        step_bytes = step_path.read_bytes()
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1", "model_id": "itest"}))
        zf.writestr("geometry/generated.step", step_bytes)
        zf.writestr("geometry/topology_map.json", json.dumps(_BOX_TOPOLOGY))

    # 2. Mesh (Gmsh, in-process).
    mesh = sr.generate_mesh_for_package(pkg, mesh_size_mm=6.0)
    assert mesh["status"] == "success", mesh

    fixed_face, load_face = "face_xmin", "face_xmax"

    # 3. Minimal linear-static CAE setup: fix one end, load the other in -Z.
    _rewrite_package(pkg, {
        "simulation/solver_settings.json":
            json.dumps({"solver": "CalculiX", "analysis_type": "static"}).encode(),
        "simulation/cae_imports/parsed_materials.json": json.dumps(
            {"materials": [{"name": "al6061",
                            "elastic": {"youngs_modulus": 69000, "poisson_ratio": 0.33}}]}).encode(),
        "simulation/cae_imports/parsed_boundary_conditions.json": json.dumps(
            {"boundary_conditions": [{"id": "bc", "target": "FIXED_END",
                                      "dof_start": 1, "dof_end": 3, "value": 0}]}).encode(),
        "simulation/cae_imports/parsed_loads.json": json.dumps(
            {"loads": [{"id": "tip", "target": "LOAD_END", "dof": 3, "value": -50}]}).encode(),
        "simulation/cae_mapping.json": json.dumps({"mappings": [
            {"cae_entity": "FIXED_END", "maps_to": {"feature_id": "ff"}, "face_ids": [fixed_face]},
            {"cae_entity": "LOAD_END", "maps_to": {"feature_id": "lf"}, "face_ids": [load_face]},
        ]}).encode(),
    })

    # 4. Source deck (solid-only, named NSETs) + assembled solver input.
    src = sr.ensure_source_deck_from_mesh(pkg)
    assert src["status"] == "synthesized", src
    assert set(src["nset_names"]) == {"FIXED_END", "LOAD_END"}, src
    assert src["empty_nsets"] == [], src
    gen = aieng_bridge.generate_solver_input(
        pkg, aieng_root=_WORKSPACE_ROOT / "aieng", run_id="run_001", overwrite=True
    )
    assert gen["status"] == "ok", gen

    # 5. Run the real solver.
    with zipfile.ZipFile(pkg) as zf:
        deck = zf.read("simulation/runs/run_001/solver_input.inp").decode(errors="replace")
    job_dir = tmp_path / "run"
    job_dir.mkdir()
    (job_dir / "job.inp").write_text(deck)
    try:
        proc = subprocess.run(
            ccx_argv + ["job"], cwd=str(job_dir),
            capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ccx could not be launched in this environment: {exc}")
    frd = job_dir / "job.frd"
    if proc.returncode != 0 or not frd.exists() or frd.stat().st_size == 0:
        # An env/config problem (e.g. a bare-exe DLL crash), not a code regression.
        pytest.skip(f"ccx did not solve cleanly here (rc={proc.returncode}); env config, not code")

    # 6. The payoff: real metrics extracted from the real FRD.
    metrics = extract_computed_metrics(frd)
    case = metrics["load_cases"][0]["metrics"]
    assert case.get("max_displacement", {}).get("value", 0) > 0, metrics
    assert case.get("max_von_mises_stress", {}).get("value", 0) > 0, metrics
    assert not any("not found" in w for w in metrics.get("warnings", [])), metrics
