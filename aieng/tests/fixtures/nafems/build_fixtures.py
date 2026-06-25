"""NAFEMS-style V&V fixture builder.

Generates linear-static reference cases as runnable ``.aieng`` packages:

* ``tension_rod``                  — axial tension of a square rod.
* ``cantilever_end_load``          — end-loaded cantilever beam.
* ``cantilever_udl``               — uniformly distributed downward load on cantilever.
* ``fixed_fixed_udl``              — fixed-fixed beam under uniformly distributed load.
* ``fixed_fixed_center_load``      — fixed-fixed beam with a center point load.
* ``cantilever_midspan_load``      — cantilever with a point load at mid-span.
* ``cantilever_end_load_lateral``  — end-loaded cantilever bending about the weak axis.

Each package contains the minimal artifacts required by
:mod:`aieng.simulation.deck_generator`:

* ``manifest.json``
* ``simulation/setup.yaml``
* ``simulation/cae_imports/source_solver_deck.inp``
* ``simulation/cae_mapping.json``

The mesh is a coarse C3D8 hexahedral grid that balances CI runtime against the
±10 % tolerance band documented in ``aieng/docs/nafems_vv_cases.md``.
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any

import yaml


# Shared material properties — steel.
STEEL = {
    "youngs_modulus_mpa": 210000.0,
    "poisson_ratio": 0.3,
    "density_kg_m3": 7850.0,
}

# Steel in a CONSISTENT mm–tonne–s unit system: E in N/mm² (MPa), density in
# tonne/mm³ (7850 kg/m³ = 7.85e-9 t/mm³). The deck generator emits the density
# value verbatim, so an eigenvalue (modal/buckling) case must use the consistent
# value for the reported frequencies (Hz) to be physically meaningful. Static
# cases do not use density and keep the kg/m³ value above.
STEEL_CONSISTENT = {
    "youngs_modulus_mpa": 210000.0,
    "poisson_ratio": 0.3,
    "density_kg_m3": 7.85e-9,  # tonne/mm³ (consistent mm–t–s units)
}

# Schema/format constants.
FORMAT_VERSION = "0.1.0"


def _build_manifest(model_id: str) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "format_version": FORMAT_VERSION,
        "units": {"length": "mm", "mass": "kg", "force": "N", "stress": "MPa"},
        "resources": {"simulation": {}, "results": {}},
        "created_by": {"tool": "nafems_fixture_builder", "created_at": "2026-01-01T00:00:00Z"},
    }


def _build_setup(
    *,
    bc_target_feature: str,
    load_target_feature: str,
    load_value_n: float,
    load_direction: list[int | float],
    material: dict[str, Any] | None = None,
    include_load: bool = True,
) -> dict[str, Any]:
    """Return a minimal setup.yaml for a single fixed-face + single-load case.

    When ``include_load`` is False (e.g. a modal analysis), the ``loads`` key is
    omitted entirely so the deck generator does not require a load.
    """
    setup: dict[str, Any] = {
        "materials": {"Steel": material or STEEL},
        "boundary_conditions": [
            {
                "id": "bc_fixed",
                "target_feature": bc_target_feature,
                "type": "fixed",
            },
        ],
    }
    if include_load:
        setup["loads"] = [
            {
                "id": "load_main",
                "target_feature": load_target_feature,
                "value_n": load_value_n,
                "direction": load_direction,
            },
        ]
    return setup


def _build_cae_mapping(*mappings: tuple[str, str]) -> dict[str, Any]:
    """Map feature IDs to NSET names in the source deck."""
    return {
        "mappings": [
            {"cae_entity": nset, "maps_to": {"feature_id": fid}}
            for nset, fid in mappings
        ],
    }


def _generate_hex_mesh(
    *,
    lx: float,
    ly: float,
    lz: float,
    nx: int,
    ny: int,
    nz: int,
) -> tuple[list[tuple[int, float, float, float]], list[tuple[int, int, int, int, int, int, int, int]], dict[str, set[int]]]:
    """Generate a structured hexahedral mesh for a rectangular prism.

    Returns ``(nodes, elements, nsets)`` where ``nsets`` keys are ``N_FIX``,
    ``N_LOAD``, and ``N_TOP`` (top face at Z=lz).
    """
    if nx <= 0 or ny <= 0 or nz <= 0:
        raise ValueError("element counts must be positive")

    dx, dy, dz = lx / nx, ly / ny, lz / nz

    nodes: list[tuple[int, float, float, float]] = []
    for k in range(nz + 1):
        z = k * dz
        for j in range(ny + 1):
            y = j * dy
            for i in range(nx + 1):
                x = i * dx
                nodes.append((len(nodes) + 1, x, y, z))

    node_id = lambda i, j, k: k * (nx + 1) * (ny + 1) + j * (nx + 1) + i + 1

    elements: list[tuple[int, int, int, int, int, int, int, int, int]] = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                n1 = node_id(i, j, k)
                n2 = node_id(i + 1, j, k)
                n3 = node_id(i + 1, j + 1, k)
                n4 = node_id(i, j + 1, k)
                n5 = node_id(i, j, k + 1)
                n6 = node_id(i + 1, j, k + 1)
                n7 = node_id(i + 1, j + 1, k + 1)
                n8 = node_id(i, j + 1, k + 1)
                elements.append((len(elements) + 1, n1, n2, n3, n4, n5, n6, n7, n8))

    nsets: dict[str, set[int]] = {
        "N_FIX": set(),
        "N_LOAD": set(),
        "N_TOP": set(),
    }
    for nid, x, y, z in nodes:
        if math.isclose(x, 0.0, abs_tol=1e-9):
            nsets["N_FIX"].add(nid)
        if math.isclose(x, lx, abs_tol=1e-9):
            nsets["N_LOAD"].add(nid)
        if math.isclose(z, lz, abs_tol=1e-9):
            nsets["N_TOP"].add(nid)

    return nodes, elements, nsets


def _format_nset(name: str, node_ids: set[int], width: int = 16) -> list[str]:
    """Format a CalculiX *NSET block from a set of node IDs."""
    lines = [f"*NSET, NSET={name}"]
    ids = sorted(node_ids)
    for i in range(0, len(ids), width):
        lines.append(", ".join(str(nid) for nid in ids[i : i + width]))
    return lines


def _write_source_deck(
    *,
    nodes: list[tuple[int, float, float, float]],
    elements: list[tuple[int, int, int, int, int, int, int, int, int]],
    nsets: dict[str, set[int]],
    active_nsets: list[str],
) -> str:
    """Assemble a CalculiX-compatible source solver deck."""
    lines: list[str] = [
        "*NODE",
    ]
    for nid, x, y, z in nodes:
        lines.append(f"{nid}, {x:.6f}, {y:.6f}, {z:.6f}")

    lines.append("*ELEMENT, TYPE=C3D8, ELSET=EALL")
    for eid, n1, n2, n3, n4, n5, n6, n7, n8 in elements:
        lines.append(f"{eid}, {n1}, {n2}, {n3}, {n4}, {n5}, {n6}, {n7}, {n8}")

    for name in active_nsets:
        lines.extend(_format_nset(name, nsets[name]))

    # EALL is already defined by the *ELEMENT, ELSET=EALL card above; re-list it
    # explicitly but chunk to <=16 ids per line — CalculiX rejects a card line
    # with more than 16 entries ("*ERROR in splitline"), which silently broke
    # every real-ccx NAFEMS case once a mesh exceeded 16 elements.
    lines.append("*ELSET, ELSET=EALL")
    eids = [eid for eid, *_ in elements]
    for i in range(0, len(eids), 16):
        lines.append(", ".join(str(eid) for eid in eids[i : i + 16]))

    lines.append("*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL")
    lines.append("1.0")
    return "\n".join(lines) + "\n"



def _build_package(
    out_path: Path,
    *,
    model_id: str,
    bc_target_feature: str,
    load_target_feature: str,
    load_value_n: float,
    load_direction: list[int | float],
    cae_mapping: dict[str, Any],
    source_deck: str,
    material: dict[str, Any] | None = None,
    include_load: bool = True,
    solver_settings: dict[str, Any] | None = None,
) -> Path:
    """Write an ``.aieng`` package with the standard simulation artifacts.

    ``solver_settings`` (e.g. ``{"analysis_type": "modal", "num_modes": 6}``) is
    written to ``simulation/solver_settings.json`` when provided; ``include_load``
    / ``material`` flow into the setup (modal cases omit the load and use a
    consistent-units density).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    setup = _build_setup(
        bc_target_feature=bc_target_feature,
        load_target_feature=load_target_feature,
        load_value_n=load_value_n,
        load_direction=load_direction,
        material=material,
        include_load=include_load,
    )

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(_build_manifest(model_id), indent=2))
        zf.writestr("simulation/", b"")
        zf.writestr("simulation/cae_imports/", b"")
        zf.writestr("simulation/setup.yaml", yaml.safe_dump(setup))
        zf.writestr("simulation/cae_mapping.json", json.dumps(cae_mapping, indent=2))
        zf.writestr("simulation/cae_imports/source_solver_deck.inp", source_deck)
        if solver_settings is not None:
            zf.writestr("simulation/solver_settings.json", json.dumps(solver_settings, indent=2))

    return out_path


def _both_ends_fixed_nset(nodes: list[tuple[int, float, float, float]], lx: float) -> set[int]:
    """Return node IDs on both X=0 and X=Lx faces."""
    return {
        nid for nid, x, _, _ in nodes
        if math.isclose(x, 0.0, abs_tol=1e-9) or math.isclose(x, lx, abs_tol=1e-9)
    }


def _top_center_node(
    nodes: list[tuple[int, float, float, float]], lx: float, ly: float, lz: float
) -> set[int]:
    """Return the single top-face (Z=Lz) center node."""
    candidates = [
        (nid, abs(x - lx / 2.0) + abs(y - ly / 2.0))
        for nid, x, y, z in nodes
        if math.isclose(z, lz, abs_tol=1e-9)
    ]
    if not candidates:
        return set()
    return {min(candidates, key=lambda item: item[1])[0]}


def _top_midspan_nset(
    nodes: list[tuple[int, float, float, float]], lx: float, lz: float
) -> set[int]:
    """Return the top-face (Z=Lz) line of nodes at mid-span (X=Lx/2).

    Requires an even ``nx`` so a node plane lands exactly on X=Lx/2.
    """
    return {
        nid
        for nid, x, _, z in nodes
        if math.isclose(z, lz, abs_tol=1e-9) and math.isclose(x, lx / 2.0, abs_tol=1e-9)
    }


def build_tension_rod_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build the ``tension_rod`` NAFEMS-style reference case."""
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 10.0
    nx, ny, nz = mesh_divisions or (20, 2, 2)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    # Total 1000 N tensile load in +X on the X=L end face. The deck generator
    # distributes this total over the N_LOAD face nodes — do NOT pre-divide.
    load_per_node = 1000.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_LOAD"],
    )

    return _build_package(
        out_path,
        model_id="nafems_tension_rod",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[1, 0, 0],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_LOAD", "feat_load")),
        source_deck=source_deck,
    )


def build_cantilever_end_load_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build the ``cantilever_end_load`` NAFEMS-style reference case."""
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    # Total 100 N in -Z on the X=L end face (deck generator distributes the total).
    load_per_node = 100.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_LOAD"],
    )

    return _build_package(
        out_path,
        model_id="nafems_cantilever_end_load",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[0, 0, -1],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_LOAD", "feat_load")),
        source_deck=source_deck,
    )


def build_cantilever_udl_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build the ``cantilever_udl`` NAFEMS-style reference case."""
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    # Total 100 N over all top-face (Z=lz) nodes (deck generator distributes the total).
    load_per_node = 100.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_TOP"],
    )

    return _build_package(
        out_path,
        model_id="nafems_cantilever_udl",
        bc_target_feature="feat_fix",
        load_target_feature="feat_top",
        load_value_n=load_per_node,
        load_direction=[0, 0, -1],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_TOP", "feat_top")),
        source_deck=source_deck,
    )


def build_fixed_fixed_udl_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build a fixed-fixed beam under uniformly distributed load.

    Both ends (X=0 and X=Lx) are fully fixed; the total 100 N load is distributed
    over the top face (Z=Lz). This is a regression case only, not a certification.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    nsets["N_FIX"] = _both_ends_fixed_nset(nodes, lx)
    # Total 100 N over the top face (deck generator distributes the total).
    load_per_node = 100.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_TOP"],
    )

    return _build_package(
        out_path,
        model_id="nafems_fixed_fixed_udl",
        bc_target_feature="feat_fix",
        load_target_feature="feat_top",
        load_value_n=load_per_node,
        load_direction=[0, 0, -1],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_TOP", "feat_top")),
        source_deck=source_deck,
    )


def build_fixed_fixed_center_load_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build a fixed-fixed beam with a center point load on the top face.

    Both ends are fully fixed; the 100 N point load is applied to the single
    top-center node. Stress at the load point is mesh-sensitive, so the reference
    focuses on mid-span displacement. This is a regression case only.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    nsets["N_FIX"] = _both_ends_fixed_nset(nodes, lx)
    nsets["N_CENTER"] = _top_center_node(nodes, lx, ly, lz)
    load_per_node = 100.0  # single node takes the full load

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_CENTER"],
    )

    return _build_package(
        out_path,
        model_id="nafems_fixed_fixed_center_load",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[0, 0, -1],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_CENTER", "feat_load")),
        source_deck=source_deck,
    )


def build_cantilever_midspan_load_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build a cantilever with a point load applied at mid-span (X=Lx/2).

    Fixed at X=0; the total 100 N load (-Z) is distributed over the top-face
    line of nodes at mid-span. Max displacement is at the free tip; max bending
    stress is at the fixed root (M = P*a, a = Lx/2). ``nx`` must be even so a
    node plane lands on mid-span. Regression case only, not a certification.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    if nx % 2 != 0:
        raise ValueError("cantilever_midspan_load requires an even nx for a mid-span node plane")
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    nsets["N_MID"] = _top_midspan_nset(nodes, lx, lz)
    # Total 100 N over the mid-span top line (deck generator distributes the total).
    load_per_node = 100.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_MID"],
    )

    return _build_package(
        out_path,
        model_id="nafems_cantilever_midspan_load",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[0, 0, -1],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_MID", "feat_load")),
        source_deck=source_deck,
    )


def build_cantilever_end_load_lateral_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build an end-loaded cantilever bending about the WEAK (Z) axis.

    Same geometry as ``cantilever_end_load`` but the 100 N end-face load acts in
    -Y, so bending uses I = Lz*Ly^3/12 (weak axis) instead of Lx*... . This
    exercises a different load direction and moment of inertia through the same
    pipeline. Regression case only, not a certification.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    # Total 100 N in -Y on the X=L end face (deck generator distributes the total).
    load_per_node = 100.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_LOAD"],
    )

    return _build_package(
        out_path,
        model_id="nafems_cantilever_end_load_lateral",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[0, -1, 0],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_LOAD", "feat_load")),
        source_deck=source_deck,
    )


def build_cantilever_modal_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build a clamped-free cantilever MODAL case (first bending natural frequency).

    Same beam as ``cantilever_end_load`` (L=100, section 10×20 mm) but with NO
    load and a ``*FREQUENCY`` step. The fundamental mode is weak-axis bending
    (I = Lz·Ly³/12 = 1666.67 mm⁴). Consistent mm–t–s units → frequency in Hz.
    Regression case only, not a certification.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX"],
    )

    return _build_package(
        out_path,
        model_id="nafems_cantilever_modal",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=0.0,
        load_direction=[0, 0, 0],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix")),
        source_deck=source_deck,
        material=STEEL_CONSISTENT,
        include_load=False,
        solver_settings={"analysis_type": "modal", "num_modes": 6},
    )


def build_column_buckling_fixture(
    out_path: Path | str,
    mesh_divisions: tuple[int, int, int] | None = None,
) -> Path:
    """Build a clamped-free column linear BUCKLING case (Euler critical load).

    A slender column (L=100, section 10×20 mm) clamped at X=0, with a 1000 N
    axial compressive reference load (-X) on the free X=L face and a ``*BUCKLE``
    step. The lowest buckling mode is weak-axis (I = 1666.67 mm⁴); fixed-free
    effective-length factor K=2. The reported buckling factor λ₁ = P_cr / 1000 N.
    Regression case only, not a certification.
    """
    out_path = Path(out_path)
    lx, ly, lz = 100.0, 10.0, 20.0
    nx, ny, nz = mesh_divisions or (20, 4, 4)
    nodes, elements, nsets = _generate_hex_mesh(lx=lx, ly=ly, lz=lz, nx=nx, ny=ny, nz=nz)

    # Total 1000 N compressive (-X) reference load on the X=L end face (deck generator distributes the total).
    load_per_node = 1000.0

    source_deck = _write_source_deck(
        nodes=nodes,
        elements=elements,
        nsets=nsets,
        active_nsets=["N_FIX", "N_LOAD"],
    )

    return _build_package(
        out_path,
        model_id="nafems_column_buckling",
        bc_target_feature="feat_fix",
        load_target_feature="feat_load",
        load_value_n=load_per_node,
        load_direction=[-1, 0, 0],
        cae_mapping=_build_cae_mapping(("N_FIX", "feat_fix"), ("N_LOAD", "feat_load")),
        source_deck=source_deck,
        material=STEEL_CONSISTENT,
        solver_settings={"analysis_type": "buckling", "num_factors": 5},
    )


# Catalog of supported cases for runners/tests.
CASE_BUILDERS: dict[str, Any] = {
    "tension_rod": build_tension_rod_fixture,
    "cantilever_end_load": build_cantilever_end_load_fixture,
    "cantilever_udl": build_cantilever_udl_fixture,
    "fixed_fixed_udl": build_fixed_fixed_udl_fixture,
    "fixed_fixed_center_load": build_fixed_fixed_center_load_fixture,
    "cantilever_midspan_load": build_cantilever_midspan_load_fixture,
    "cantilever_end_load_lateral": build_cantilever_end_load_lateral_fixture,
    "cantilever_modal": build_cantilever_modal_fixture,
    "column_buckling": build_column_buckling_fixture,
}


def build_all_fixtures(out_dir: Path | str) -> dict[str, Path]:
    """Build all NAFEMS-style fixtures into ``out_dir``.

    Returns a mapping from case_id to ``.aieng`` path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        case_id: builder(out_dir / f"{case_id}.aieng")
        for case_id, builder in CASE_BUILDERS.items()
    }
