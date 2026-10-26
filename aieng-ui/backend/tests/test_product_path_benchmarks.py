"""Authoritative benchmarks run through the PRODUCT path, not around it.

The NAFEMS suite in `aieng/src/aieng/nafems_verification.py` solves hand-written
C3D8 decks, so it never touches the path users take: build123d geometry ->
`cae.setup_static` / `cae.apply_setup_patch` -> `cae.generate_mesh` (gmsh,
C3D10) -> `cae.generate_solver_input` -> `cae.run_solver`. That is how the
default path once ran 2x off while the suite stayed green. Every case here goes
through `runtime.invoke_tool`, the surface a connected agent drives, and is
checked against a closed form or a published reference value.

Reference values and tolerances (measured value at the time of writing in
brackets; tolerance chosen so a real regression fails and mesh noise does not):

- Uniform tension patch test, symmetry rollers on three faces: sigma_xx = F/A
  at EVERY node, u_x(L) = F L / (E A) — exact for any mesh. Tol 0.1 %
  [0.001 %]. Before this module's fixes: peak von Mises +295 %, u_x +11.4 %.
- End-loaded cantilever via `cae.setup_static`: sigma_xx at mid-span top fibre
  = M c / I (Saint-Venant flexure, exact away from the ends), tol 1 %
  [-0.02 %]; tip deflection vs Timoshenko F L^3/(3EI) + F L/(k G A), k = 5/6,
  tol 2 % [-0.76 %; the fully clamped face is stiffer than beam theory].
- NAFEMS LE1 elliptic membrane (NAFEMS, "The Standard NAFEMS Benchmarks",
  rev. 3, 1990, test LE1): sigma_yy at D = 92.7 MPa, tol 2 % [+0.02 %]. Needs
  a PRESSURE on the curved outer edge, which the product could not express.
- Cantilever first bending frequency vs Euler-Bernoulli
  f1 = (1.8751^2 / 2 pi) sqrt(EI / (rho A L^4)), L/h = 20, tol 1 % [+0.06 %].
- 1D steady conduction, T fixed at x = 0 and a total heat flux Q on x = L:
  T(x) = T0 + Q x / (k A), exact for any mesh. Tol 0.01 K [5e-4 K].
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

pytest.importorskip("build123d", reason="the benchmarks need the real CAD stack")
pytest.importorskip("gmsh", reason="the benchmarks need the real mesher")


def _ccx_available() -> bool:
    from app.simulation_runner import _find_ccx

    try:
        return bool(_find_ccx())
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory):
    if not _ccx_available():
        pytest.skip("CalculiX is not runnable here; the gate counts this as a failure")
    from app import runtime
    from app.app_factory import create_app
    from app.config import Settings

    root = tmp_path_factory.mktemp("benchmarks")
    previous_cwd = Path.cwd()
    os.chdir(root)  # `.aieng_cache` is CWD-relative; keep it private to this run
    try:
        workspace = root / "workspace"
        settings = Settings(
            platform_root=root / "platform", workspace_root=workspace,
            data_root=root / "data", aieng_root=_WORKSPACE_ROOT / "aieng",
            sample_step=workspace / "sample.step",
        )
        create_app(settings)
        yield {"runtime": runtime, "settings": settings}
    finally:
        os.chdir(previous_cwd)


def _build(bench, code: str, name: str) -> tuple[str, Path]:
    from app.main import default_project, save_project
    from app.project_io import project_dir

    pid = save_project(bench["settings"], default_project(name))["id"]
    built = bench["runtime"].invoke_tool(
        "cad.execute_build123d",
        {"project_id": pid, "code": code, "timeout": 300, "thumbnail": False},
    )
    assert built["status"] == "ok", built
    return pid, project_dir(bench["settings"], pid) / f"{pid}.aieng"


def _member(pkg: Path, name: str) -> bytes:
    with zipfile.ZipFile(pkg) as zf:
        return zf.read(name)


def _faces(pkg: Path) -> list[dict]:
    topo = json.loads(_member(pkg, "geometry/topology_map.json"))
    return [e for e in topo.get("entities", []) if e.get("type") == "face"]


def _plane(pkg: Path, axis: int, value: float) -> str:
    hits = [
        f["id"] for f in _faces(pkg)
        if f.get("surface_type") == "plane" and abs(abs(f["normal"][axis]) - 1) < 1e-6
        and abs(f["center"][axis] - value) < 1e-6
    ]
    assert len(hits) == 1, hits
    return hits[0]


def _write_setup(bench, pid: str, material: dict, bcs: list, loads: list,
                 analysis_type: str = "static") -> None:
    from aieng.simulation.cae_setup_writer import authored_setup_document

    patches = [
        {"action_type": "create_file", "path": "simulation/solver_settings.json",
         "content": {"solver": "CalculiX", "analysis_type": analysis_type}},
        {"action_type": "create_file", "path": "simulation/cae_imports/parsed_materials.json",
         "content": authored_setup_document("materials", [material], authored_by="benchmark")},
        {"action_type": "create_file",
         "path": "simulation/cae_imports/parsed_boundary_conditions.json",
         "content": authored_setup_document("boundary_conditions", bcs, authored_by="benchmark")},
        {"action_type": "create_file", "path": "simulation/cae_imports/parsed_loads.json",
         "content": authored_setup_document("loads", loads, authored_by="benchmark")},
    ]
    result = bench["runtime"].invoke_tool(
        "cae.apply_setup_patch", {"project_id": pid, "patches": patches}
    )
    assert result.get("status") == "ok", result


def _solve(bench, pid: str, mesh_size_mm: float) -> dict:
    tool = bench["runtime"].invoke_tool
    meshed = tool("cae.generate_mesh", {"project_id": pid, "mesh_size_mm": mesh_size_mm})
    assert meshed["status"] == "completed", meshed
    deck = tool("cae.generate_solver_input", {"project_id": pid, "run_id": "run_001"})
    assert deck["status"] == "completed", deck
    # Generous timeout: the default 120 s is meant for interactive use, and a
    # loaded CI runner (or a parallel suite) must not turn a benchmark into a
    # timeout. The errors/diagnosis are named in the message if it still fails.
    solved = tool("cae.run_solver", {
        "project_id": pid, "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        "timeout_seconds": 900,
    })
    assert solved["status"] == "completed", (
        solved.get("errors"), solved.get("diagnosis"), solved.get("warnings")
    )
    return {"mesh": meshed, "deck": deck, "solve": solved}


def _frd(pkg: Path) -> dict:
    from aieng.simulation.frd_result_extractor import parse_frd_steps

    with zipfile.ZipFile(pkg) as zf:
        name = next(n for n in zf.namelist()
                    if n.startswith("simulation/runs/run_001/") and n.endswith(".frd"))
        data = zf.read(name)
    path = Path(tempfile.mkdtemp(prefix="aieng_bench_")) / "result.frd"
    path.write_bytes(data)
    fields = parse_frd_steps(path)
    return {name: datasets[0]["node_data"] for name, datasets in fields.items()}


def _nodes(pkg: Path) -> dict[int, tuple[float, float, float]]:
    from aieng.simulation.surface_loads import parse_mesh

    return parse_mesh(_member(pkg, "simulation/mesh/mesh.inp").decode())[0]


def _stress(fields: dict) -> dict:
    return fields.get("STRESS") or fields["S"]


def _von_mises(s: list[float]) -> float:
    sxx, syy, szz, sxy, syz, szx = s[:6]
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3 * (sxy ** 2 + syz ** 2 + szx ** 2))


_STEEL = {"name": "STEEL", "elastic": {"youngs_modulus": 210000.0, "poisson_ratio": 0.3}}


def _roller(name: str, face: str, dof: int) -> dict:
    return {"id": name, "type": "symmetry", "target": f"@face:{face}",
            "dof_start": dof, "dof_end": dof, "value": 0}


@pytest.mark.parametrize("load_form", ["force", "pressure"])
def test_uniform_tension_patch_test_is_exact(bench, load_form: str) -> None:
    length, width, thick, force = 100.0, 20.0, 10.0, 2000.0
    pid, pkg = _build(bench, (
        "from build123d import *\n"
        f"bar = Box({length}, {width}, {thick}, align=(Align.MIN, Align.MIN, Align.MIN))\n"
        "bar.label = 'bar'\nresult = Compound(children=[bar])\n"
    ), f"patch_{load_form}")
    end = _plane(pkg, 0, length)
    load = (
        {"id": "pull", "type": "force", "target": f"@face:{end}", "value_n": force,
         "direction": [1, 0, 0]}
        if load_form == "force" else
        # Tension = a NEGATIVE pressure (positive pushes into the face).
        {"id": "pull", "type": "pressure", "target": f"@face:{end}",
         "pressure_mpa": -force / (width * thick)}
    )
    _write_setup(bench, pid, _STEEL, [
        _roller("sym_x", _plane(pkg, 0, 0.0), 1),
        _roller("sym_y", _plane(pkg, 1, 0.0), 2),
        _roller("sym_z", _plane(pkg, 2, 0.0), 3),
    ], [load])
    run = _solve(bench, pid, 5.0)
    assert not [w for w in run["deck"].get("warnings") or [] if "split equally" in w]

    fields, exact = _frd(pkg), force / (width * thick)
    stresses = _stress(fields).values()
    worst = max(abs(s[0] - exact) / exact for s in stresses)
    assert worst < 1e-3, f"sigma_xx deviates {worst:.3%} from F/A somewhere"
    assert max(_von_mises(s) for s in stresses) == pytest.approx(exact, rel=1e-3)
    tip = max(u[0] for u in fields["DISP"].values())
    assert tip == pytest.approx(exact * length / 210000.0, rel=1e-3)


def test_cantilever_matches_beam_theory(bench) -> None:
    length, width, height, force = 200.0, 20.0, 20.0, 100.0
    young, poisson = 210000.0, 0.3
    pid, pkg = _build(bench, (
        "from build123d import *\nBEAM_LENGTH = 200.0\n"
        f"beam = Box(BEAM_LENGTH, {width}, {height}, align=(Align.MIN, Align.MIN, Align.MIN))\n"
        "beam.label = 'beam'\nresult = Compound(children=[beam])\n"
    ), "cantilever")
    setup = bench["runtime"].invoke_tool("cae.setup_static", {
        "project_id": pid,
        "material": {"name": "STEEL", "youngs_modulus_mpa": young, "poisson_ratio": poisson},
        "fix": "left", "load": {"at": "right", "force_n": force, "direction": "-Z"},
    })
    assert setup["status"] == "ok", setup
    run = _solve(bench, pid, 4.0)
    # A solid square bar is its own wall; the accuracy band must say so.
    assert run["mesh"]["accuracy"]["band"] == "reliable", run["mesh"]["accuracy"]

    fields, nodes = _frd(pkg), _nodes(pkg)
    inertia, area = width * height ** 3 / 12, width * height
    shear_modulus = young / (2 * (1 + poisson))
    timoshenko = (force * length ** 3 / (3 * young * inertia)
                  + force * length / ((5 / 6) * shear_modulus * area))
    tip = [abs(fields["DISP"][n][2]) for n, p in nodes.items() if abs(p[0] - length) < 1e-6]
    assert sum(tip) / len(tip) == pytest.approx(timoshenko, rel=0.02)

    stress = _stress(fields)
    top_mid = [stress[n][0] for n, p in nodes.items()
               if abs(p[0] - length / 2) < 1.0 and abs(p[2] - height) < 1e-6]
    assert top_mid, "no nodes on the top fibre at mid-span"
    sigma = force * (length / 2) * (height / 2) / inertia
    assert sum(top_mid) / len(top_mid) == pytest.approx(sigma, rel=0.01)


def test_nafems_le1_elliptic_membrane(bench) -> None:
    half_thickness = 50.0  # the z = 0 face is the mid-plane: a symmetry plane
    pid, pkg = _build(bench, (
        "from build123d import *\n"
        "sk = (Ellipse(3250, 2750) - Ellipse(2000, 1000)) & "
        "Rectangle(4000, 4000, align=(Align.MIN, Align.MIN))\n"
        f"plate = extrude(sk, amount={half_thickness}); plate.label = 'membrane'\n"
        "result = Compound(children=[plate])\n"
    ), "nafems_le1")
    outer = [f["id"] for f in _faces(pkg)
             if f.get("surface_type") != "plane" and f["bounding_box"][3] > 3000]
    assert len(outer) == 1
    _write_setup(bench, pid, _STEEL, [
        _roller("sym_x", _plane(pkg, 0, 0.0), 1),
        _roller("sym_y", _plane(pkg, 1, 0.0), 2),
        _roller("sym_z", _plane(pkg, 2, 0.0), 3),
    ], [{"id": "tension", "type": "pressure", "target": f"@face:{outer[0]}",
         "pressure_mpa": -10.0}])
    _solve(bench, pid, 60.0)

    stress, nodes = _stress(_frd(pkg)), _nodes(pkg)
    at_d = sorted((p[2], stress[n][1]) for n, p in nodes.items()
                  if abs(p[0] - 2000.0) < 1e-3 and abs(p[1]) < 1e-3)
    assert len(at_d) >= 2
    # Plane stress is the through-thickness average of the 3D field.
    average = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(at_d, at_d[1:]))
    assert average / half_thickness == pytest.approx(92.7, rel=0.02)


def test_cantilever_first_frequency(bench) -> None:
    from aieng.simulation.dat_result_extractor import parse_eigenfrequencies

    length, side, young, density = 200.0, 10.0, 210000.0, 7850.0
    pid, pkg = _build(bench, (
        "from build123d import *\n"
        f"beam = Box({length}, {side}, {side}, align=(Align.MIN, Align.MIN, Align.MIN))\n"
        "beam.label = 'beam'\nresult = Compound(children=[beam])\n"
    ), "modal")
    setup = bench["runtime"].invoke_tool("cae.setup_static", {
        "project_id": pid, "analysis_type": "modal", "fix": "left",
        "material": {"name": "STEEL", "youngs_modulus_mpa": young, "poisson_ratio": 0.3,
                     "density_kg_m3": density},
    })
    assert setup["status"] == "ok", setup
    _solve(bench, pid, 2.5)
    frequencies = parse_eigenfrequencies(
        _member(pkg, "simulation/runs/run_001/outputs/result.dat").decode()
    )
    inertia = side ** 4 / 12
    euler_bernoulli = 1.87510407 ** 2 / (2 * math.pi) * math.sqrt(
        young * inertia / (density * 1e-12 * side * side * length ** 4)
    )
    assert frequencies[0] == pytest.approx(euler_bernoulli, rel=0.01)


def test_one_dimensional_conduction_is_exact(bench) -> None:
    length, area, conductivity, flux, t0 = 100.0, 200.0, 200.0, 40000.0, 100.0
    pid, pkg = _build(bench, (
        "from build123d import *\n"
        f"bar = Box({length}, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN))\n"
        "bar.label = 'bar'\nresult = Compound(children=[bar])\n"
    ), "conduction")
    material = {"name": "AL", "elastic": {"youngs_modulus": 70000.0, "poisson_ratio": 0.33},
                "conductivity": conductivity}
    _write_setup(bench, pid, material, [
        {"id": "hot", "type": "temperature", "target": f"@face:{_plane(pkg, 0, 0.0)}",
         "dof_start": 11, "dof_end": 11, "value": t0},
    ], [
        {"id": "flux", "type": "heat_flux", "target": f"@face:{_plane(pkg, 0, length)}",
         "dof": 11, "value": flux},
    ], analysis_type="thermal")
    _solve(bench, pid, 5.0)

    temperature, nodes = _frd(pkg)["NDTEMP"], _nodes(pkg)
    worst = max(abs(temperature[n][0] - (t0 + flux * p[0] / (conductivity * area)))
                for n, p in nodes.items())
    assert worst < 0.01, f"temperature off the exact linear field by {worst:.3g} K"


def test_a_force_spread_over_faces_is_shared_by_area(bench) -> None:
    """One total force on two faces is one traction: each face carries its area share."""
    pid, pkg = _build(bench, (
        "from build123d import *\n"
        "blk = Box(40, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN)); blk.label = 'blk'\n"
        "result = Compound(children=[blk])\n"
    ), "area_share")
    big, small = _plane(pkg, 2, 10.0), _plane(pkg, 0, 40.0)  # 800 mm² and 200 mm²
    setup = bench["runtime"].invoke_tool("cae.setup_static", {
        "project_id": pid, "material": "Al6061-T6", "fix": "left",
        "load": {"at": f"@face:{big} @face:{small}", "force_n": 1000, "direction": "-Z"},
    })
    assert setup["status"] == "ok", setup
    loads = json.loads(_member(pkg, "simulation/cae_imports/parsed_loads.json"))["loads"]
    shares = {entry["target"].split(":", 1)[1]: entry["value_n"] for entry in loads}
    assert shares[big] == pytest.approx(800.0) and shares[small] == pytest.approx(200.0)
    assert setup["load_share_basis"] == "area" and setup["warnings"] == []

    # A face with no recorded area cannot be area-shared: the equal fallback is
    # reported and warned about instead of passing as `status: ok` alone.
    topo_path = "geometry/topology_map.json"
    topology = json.loads(_member(pkg, topo_path))
    for entity in topology["entities"]:
        if entity.get("id") == small:
            entity.pop("area", None)
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(pkg) as zf:
        for name in zf.namelist():
            members[name] = zf.read(name)
    members[topo_path] = json.dumps(topology).encode()
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    fallback = bench["runtime"].invoke_tool("cae.setup_static", {
        "project_id": pid, "material": "Al6061-T6", "fix": "left",
        "load": {"at": f"@face:{big} @face:{small}", "force_n": 1000, "direction": "-Z"},
    })
    assert fallback["status"] == "ok", fallback
    assert fallback["load_share_basis"] == "equal"
    assert any("EQUALLY" in w for w in fallback["warnings"])
