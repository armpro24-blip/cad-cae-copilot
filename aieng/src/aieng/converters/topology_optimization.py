"""Topology optimization — contract + pluggable optimizer registry.

Topology optimization is a CAE-driven *generative* step that sits after analysis:
given a design space, load/support boundary conditions, and a volume budget, it
finds where material should be. Like solvers, the optimizer is a **pluggable
backend** behind a neutral contract — the built-in reference is a self-contained
2D SIMP (compliance minimization, pure numpy, no external solver/dependency).
Other optimizers (3D SIMP, ToPy, nTop, remote) can register and emit the same
neutral result.

Output: ``analysis/topology_optimization.json`` (optimizer provenance, objective
history, achieved volume fraction, density grid, honest limitations). The result is
also authored back into a Shape IR representation (``topology_result_to_shape_ir`` /
``write_shape_ir_from_topology_optimization``): the density field becomes one
``density_voxels`` node that the existing compilers expand into extruded voxel
geometry, so the optimized body re-compiles, meshes, and views like any other Shape
IR — and stays linked to its ``design_space_node``.

The problem itself can be DERIVED from the project's real CAE intent rather than a
preset: ``derive_topopt_problem_from_package`` reads the design-space bbox + face
geometry + supports/loads and projects them onto a 2D plane (the two largest
design-space dims) as explicit cell-based ``bcs.supports``/``bcs.loads``, which
``simp_2d`` consumes directly.

Honest scope of the built-in optimizer: 2D, plane-stress, linear-elastic, single
isotropic material, regular grid, coarse; 3D supports/loads are projected to the
plane and out-of-plane force is dropped. It is an observational design aid, not a
production optimizer.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from aieng import FORMAT_VERSION
from aieng.cae_setup_view import load_cae_setup
from aieng.simulation.cae_mapping_writer import mapping_target_id
from aieng.converters.shape_ir import _AXIS_INDEX, sample_periodic_catmull_rom

TOPOLOGY_OPTIMIZATION_PATH = "analysis/topology_optimization.json"
TOPOPT_CONTRACT_VERSION = "0.1"

_OPTIMIZER_REGISTRY: dict[str, dict[str, Any]] = {}


def register_optimizer(name: str, fn: Callable[[dict[str, Any]], dict[str, Any]], *,
                       version: str = "0.1", method: str = "", dimension: int = 0,
                       capability: dict[str, Any] | None = None) -> None:
    """Register an optimizer. ``fn(problem) -> optimizer_result`` dict.

    ``capability`` is an optional honest self-description (dimension, method, physics,
    mesh, material, backend, engineering_level, production_ready) echoed into the
    result's optimizer block so downstream tools know what they are looking at."""
    entry: dict[str, Any] = {"fn": fn, "version": version, "method": method, "dimension": dimension}
    if capability:
        entry["capability"] = dict(capability)
    _OPTIMIZER_REGISTRY[name] = entry


def available_optimizers() -> list[str]:
    return sorted(_OPTIMIZER_REGISTRY)


# ── 2D SIMP (self-contained reference optimizer) ─────────────────────────────

def _element_stiffness() -> np.ndarray:
    """8x8 element stiffness for a unit bilinear quad, plane stress, E=1, nu=0.3."""
    nu = 0.3
    k = np.array([
        1 / 2 - nu / 6, 1 / 8 + nu / 8, -1 / 4 - nu / 12, -1 / 8 + 3 * nu / 8,
        -1 / 4 + nu / 12, -1 / 8 - nu / 8, nu / 6, 1 / 8 - 3 * nu / 8,
    ])
    KE = 1 / (1 - nu ** 2) * np.array([
        [k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7]],
        [k[1], k[0], k[7], k[6], k[5], k[4], k[3], k[2]],
        [k[2], k[7], k[0], k[5], k[6], k[3], k[4], k[1]],
        [k[3], k[6], k[5], k[0], k[7], k[2], k[1], k[4]],
        [k[4], k[5], k[6], k[7], k[0], k[1], k[2], k[3]],
        [k[5], k[4], k[3], k[2], k[1], k[0], k[7], k[6]],
        [k[6], k[3], k[4], k[1], k[2], k[7], k[0], k[5]],
        [k[7], k[2], k[1], k[4], k[3], k[6], k[5], k[0]],
    ])
    return KE


def _boundary_conditions(nelx: int, nely: int, preset: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (fixed_dofs, force_vector) for a preset. 0-based dofs; 2 per node;
    node index = (nely+1)*elx + ely; dof = 2*node (x), 2*node+1 (y)."""
    ndof = 2 * (nelx + 1) * (nely + 1)
    F = np.zeros(ndof)

    def node(elx: int, ely: int) -> int:
        return (nely + 1) * elx + ely

    preset = (preset or "cantilever").lower()
    if preset == "mbb_beam":
        # left edge: x fixed; bottom-right corner: y fixed (roller); load down at top-left
        fixed = [2 * node(0, ely) for ely in range(nely + 1)]
        fixed.append(2 * node(nelx, nely) + 1)
        F[2 * node(0, 0) + 1] = -1.0
    else:  # cantilever (default): left edge fully clamped, downward tip load at right-middle
        fixed = []
        for ely in range(nely + 1):
            fixed.append(2 * node(0, ely))
            fixed.append(2 * node(0, ely) + 1)
        F[2 * node(nelx, nely // 2) + 1] = -1.0
    return np.array(sorted(set(fixed)), dtype=int), F


def _explicit_bcs(nelx: int, nely: int, supports: list, loads: list) -> tuple[np.ndarray, np.ndarray]:
    """Build (fixed_dofs, force_vector) from explicit cell-based BCs.

    A support/load entry carries ``cells`` = list of ``[i, j]`` (i = column = elx,
    j = row = ely). Support cells clamp both dofs of their 4 corner nodes; load
    cells distribute ``(fx, fy)`` evenly over the union of their corner nodes.
    This is what a derived problem (from real CAE supports/loads) feeds in instead
    of a preset.
    """
    ndof = 2 * (nelx + 1) * (nely + 1)
    F = np.zeros(ndof)

    def node(elx: int, ely: int) -> int:
        return (nely + 1) * elx + ely

    def corners(i: int, j: int) -> list[int]:
        i = min(max(int(i), 0), nelx - 1)
        j = min(max(int(j), 0), nely - 1)
        return [node(i, j), node(i + 1, j), node(i, j + 1), node(i + 1, j + 1)]

    fixed: set[int] = set()
    for s in supports or []:
        for cell in s.get("cells", []):
            for n in corners(cell[0], cell[1]):
                fixed.add(2 * n)
                fixed.add(2 * n + 1)

    for ld in loads or []:
        fx = float(ld.get("fx", 0.0))
        fy = float(ld.get("fy", 0.0))
        nodes: set[int] = set()
        for cell in ld.get("cells", []):
            nodes.update(corners(cell[0], cell[1]))
        if nodes and (fx or fy):
            per = 1.0 / len(nodes)
            for n in nodes:
                F[2 * n] += fx * per
                F[2 * n + 1] += fy * per

    return np.array(sorted(fixed), dtype=int), F


def _resolve_bcs(nelx: int, nely: int, bcs: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, str | None]:
    """Explicit supports/loads if present, else a named preset. Returns
    (fixed_dofs, force_vector, preset_name) — preset_name is None when explicit."""
    supports = bcs.get("supports")
    loads = bcs.get("loads")
    if supports or loads:
        fixed, F = _explicit_bcs(nelx, nely, supports or [], loads or [])
        if fixed.size and np.abs(F).sum() > 0:
            return fixed, F, None
        # degenerate explicit BCs (no support or no load) -> fall back to a preset
    preset = str(bcs.get("preset") or "cantilever")
    fixed, F = _boundary_conditions(nelx, nely, preset)
    return fixed, F, preset


def _grid_guidance_2d(problem: dict[str, Any], nely: int, nelx: int):
    """Extract the (preserve_mask, stiffness_weight_field, preserve_min_density) for a
    2D solve from problem['guidance_field']. Returns (None, None, 0.5) when guidance is
    off/absent/mis-shaped — so the classic behavior is untouched."""
    if not problem.get("use_result_guidance"):
        return None, None, 0.5
    gf = problem.get("guidance_field") or {}
    pmin = float((gf.get("options") or {}).get("preserve_min_density", 0.5))
    preserve = weight = None
    try:
        pa = np.asarray(gf.get("preserve_mask"), dtype=float)
        if pa.shape == (nely, nelx) and pa.any():
            preserve = pa > 0.5
    except Exception:  # noqa: BLE001
        pass
    try:
        wa = np.asarray(gf.get("stiffness_weight_field"), dtype=float)
        if wa.shape == (nely, nelx) and (wa != 1.0).any():
            weight = wa
    except Exception:  # noqa: BLE001
        pass
    return preserve, weight, pmin


def _grid_guidance_3d(problem: dict[str, Any], nx: int, ny: int, nz: int):
    """Flat (preserve_mask, stiffness_weight_field, preserve_min_density) for a 3D solve,
    indexed to eid = i + nx*j + nx*ny*k (C-order of the [nz][ny][nx] arrays)."""
    if not problem.get("use_result_guidance"):
        return None, None, 0.5
    gf = problem.get("guidance_field") or {}
    pmin = float((gf.get("options") or {}).get("preserve_min_density", 0.5))
    preserve = weight = None
    try:
        pa = np.asarray(gf.get("preserve_mask"), dtype=float)
        if pa.shape == (nz, ny, nx) and pa.any():
            preserve = (pa.reshape(-1) > 0.5)
    except Exception:  # noqa: BLE001
        pass
    try:
        wa = np.asarray(gf.get("stiffness_weight_field"), dtype=float)
        if wa.shape == (nz, ny, nx) and (wa != 1.0).any():
            weight = wa.reshape(-1)
    except Exception:  # noqa: BLE001
        pass
    return preserve, weight, pmin


def simp_2d(problem: dict[str, Any]) -> dict[str, Any]:
    """Classic 2D SIMP compliance minimization (OC update + sensitivity filter).

    problem.grid = {nelx, nely}; volfrac, penalty (default 3), rmin (default 1.5),
    max_iters (default 40), bcs.preset ('cantilever'|'mbb_beam'). Pure numpy
    (dense FE solve) — suitable for small/coarse design domains.
    """
    grid = problem.get("grid") or {}
    nelx = int(grid.get("nelx", 30))
    nely = int(grid.get("nely", 10))
    volfrac = float(problem.get("volfrac", 0.5))
    penal = float(problem.get("penalty", 3.0))
    rmin = float(problem.get("rmin", 1.5))
    max_iters = int(problem.get("max_iters", 40))
    bcs = problem.get("bcs") or {}

    KE = _element_stiffness()
    ndof = 2 * (nelx + 1) * (nely + 1)
    fixed, F, preset = _resolve_bcs(nelx, nely, bcs)
    bcs_source = "preset" if preset is not None else "explicit"
    free = np.setdiff1d(np.arange(ndof), fixed)

    # Optional result-guidance: preserve_mask floors protected cells; stiffness_weight_field
    # locally scales the sensitivity. Off / absent -> identical to the classic behavior.
    preserve_mask, weight_field, preserve_min = _grid_guidance_2d(problem, nely, nelx)
    guidance_consumed = preserve_mask is not None or weight_field is not None

    # element -> global dof map
    edof = np.zeros((nelx * nely, 8), dtype=int)
    for elx in range(nelx):
        for ely in range(nely):
            n1 = (nely + 1) * elx + ely
            n2 = (nely + 1) * (elx + 1) + ely
            e = elx * nely + ely
            edof[e] = [2 * n1, 2 * n1 + 1, 2 * n2, 2 * n2 + 1, 2 * n2 + 2, 2 * n2 + 3, 2 * n1 + 2, 2 * n1 + 3]

    # sensitivity filter weights (precomputed)
    x = np.full((nely, nelx), volfrac)
    ceil_r = int(np.ceil(rmin))
    history: list[float] = []
    change = 1.0
    it = 0
    while change > 0.01 and it < max_iters:
        it += 1
        # FE analysis (dense solve on free dofs)
        K = np.zeros((ndof, ndof))
        xpen = x ** penal
        for elx in range(nelx):
            for ely in range(nely):
                e = elx * nely + ely
                ed = edof[e]
                K[np.ix_(ed, ed)] += xpen[ely, elx] * KE
        U = np.zeros(ndof)
        U[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])

        # compliance + sensitivities
        c = 0.0
        dc = np.zeros((nely, nelx))
        for elx in range(nelx):
            for ely in range(nely):
                e = elx * nely + ely
                ue = U[edof[e]]
                ke_energy = float(ue @ KE @ ue)
                c += xpen[ely, elx] * ke_energy
                dc[ely, elx] = -penal * (x[ely, elx] ** (penal - 1)) * ke_energy
        history.append(round(c, 6))

        # sensitivity filtering
        dcf = np.zeros((nely, nelx))
        for i in range(nelx):
            for j in range(nely):
                s = 0.0
                acc = 0.0
                for k in range(max(i - ceil_r, 0), min(i + ceil_r + 1, nelx)):
                    for l in range(max(j - ceil_r, 0), min(j + ceil_r + 1, nely)):
                        fac = rmin - np.hypot(i - k, j - l)
                        if fac > 0:
                            s += fac
                            acc += fac * x[l, k] * dc[l, k]
                dcf[j, i] = acc / (x[j, i] * s) if s > 0 else dc[j, i]

        # guidance: bias sensitivity toward stiffness-sensitive cells; floor preserved cells.
        dc_eff = dcf if weight_field is None else dcf * weight_field
        lower = 0.001 if preserve_mask is None else np.where(preserve_mask, preserve_min, 0.001)

        # optimality-criteria update with bisection on the volume multiplier
        l1, l2, move = 1e-9, 1e9, 0.2
        xnew = x.copy()
        while (l2 - l1) / (l1 + l2) > 1e-3:
            lmid = 0.5 * (l1 + l2)
            xnew = np.clip(
                x * np.sqrt(np.maximum(-dc_eff, 0) / lmid),
                np.maximum(x - move, lower),
                np.minimum(x + move, 1.0),
            )
            if xnew.mean() - volfrac > 0:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.abs(xnew - x).max())
        x = xnew

    return {
        "density": [[round(float(v), 4) for v in row] for row in x.tolist()],
        "nelx": nelx, "nely": nely,
        "iterations": it,
        "compliance_history": history,
        "final_compliance": history[-1] if history else None,
        "achieved_volume_fraction": round(float(x.mean()), 4),
        "target_volume_fraction": volfrac,
        "bcs_preset": preset,
        "bcs_source": bcs_source,
        "guidance_consumed": guidance_consumed,
        "guidance_cells_preserved": int(preserve_mask.sum()) if preserve_mask is not None else 0,
        "guidance_cells_weighted": int((weight_field != 1.0).sum()) if weight_field is not None else 0,
    }


def precomputed_optimizer(problem: dict[str, Any]) -> dict[str, Any]:
    """Fake optimizer: ingests a precomputed density grid (problem['density']).
    Proves the optimizer layer is neutral — no solve is performed."""
    density = problem.get("density")
    if not (isinstance(density, list) and density and isinstance(density[0], list)):
        raise ValueError("precomputed optimizer requires problem['density'] as a 2D grid")
    nely = len(density)
    nelx = len(density[0])
    mean = float(np.mean(np.asarray(density, dtype=float)))
    return {
        "density": [[round(float(v), 4) for v in row] for row in density],
        "nelx": nelx, "nely": nely,
        "iterations": 0,
        "compliance_history": list(problem.get("compliance_history") or []),
        "final_compliance": problem.get("final_compliance"),
        "achieved_volume_fraction": round(mean, 4),
        "target_volume_fraction": problem.get("volfrac", round(mean, 4)),
        "bcs_preset": (problem.get("bcs") or {}).get("preset"),
    }


# ── 3D SIMP (experimental structured-voxel reference optimizer) ──────────────
# Self-contained 8-node hex (H8) SIMP on a structured voxel grid: pure numpy,
# dense FE solve, classic OC update + sensitivity filter. Experimental reference
# only — small grids, single linear-elastic isotropic material. NOT production.

def _hex8_stiffness(nu: float, E: float = 1.0) -> np.ndarray:
    """24x24 stiffness for a unit (1x1x1) 8-node trilinear hex, isotropic, 2x2x2 Gauss.

    Local node order (x,y,z natural coords ±1): 0(-,-,-) 1(+,-,-) 2(+,+,-) 3(-,+,-)
    4(-,-,+) 5(+,-,+) 6(+,+,+) 7(-,+,+); per node dofs (ux,uy,uz)."""
    nodes = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                      [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float)
    g = 1.0 / np.sqrt(3.0)
    gauss = [(sx * g, sy * g, sz * g) for sz in (-1, 1) for sy in (-1, 1) for sx in (-1, 1)]
    c = E / ((1 + nu) * (1 - 2 * nu))
    s = (1 - 2 * nu) / 2.0
    D = c * np.array([
        [1 - nu, nu, nu, 0, 0, 0],
        [nu, 1 - nu, nu, 0, 0, 0],
        [nu, nu, 1 - nu, 0, 0, 0],
        [0, 0, 0, s, 0, 0],
        [0, 0, 0, 0, s, 0],
        [0, 0, 0, 0, 0, s],
    ])
    KE = np.zeros((24, 24))
    inv_j = 2.0   # physical cube [0,1]: d/dx = 2 * d/dxi
    det_j = 1.0 / 8.0
    for xi, eta, ze in gauss:
        dN = np.zeros((3, 8))
        for a in range(8):
            xa, ya, za = nodes[a]
            dN[0, a] = 0.125 * xa * (1 + ya * eta) * (1 + za * ze)
            dN[1, a] = 0.125 * ya * (1 + xa * xi) * (1 + za * ze)
            dN[2, a] = 0.125 * za * (1 + xa * xi) * (1 + ya * eta)
        dNdx = dN * inv_j
        B = np.zeros((6, 24))
        for a in range(8):
            bx, by, bz = dNdx[:, a]
            B[0, 3 * a] = bx
            B[1, 3 * a + 1] = by
            B[2, 3 * a + 2] = bz
            B[3, 3 * a] = by
            B[3, 3 * a + 1] = bx
            B[4, 3 * a + 1] = bz
            B[4, 3 * a + 2] = by
            B[5, 3 * a] = bz
            B[5, 3 * a + 2] = bx
        KE += (B.T @ D @ B) * det_j
    return KE


def _hex_corner_nodes(ix: int, iy: int, iz: int, nnx: int, nny: int) -> list[int]:
    """Global node ids of the 8 corners of element (ix,iy,iz), in the H8 local order
    used by _hex8_stiffness. node(x,y,z) = x + nnx*y + nnx*nny*z (x fastest)."""
    def n(x: int, y: int, z: int) -> int:
        return x + nnx * y + nnx * nny * z
    return [n(ix, iy, iz), n(ix + 1, iy, iz), n(ix + 1, iy + 1, iz), n(ix, iy + 1, iz),
            n(ix, iy, iz + 1), n(ix + 1, iy, iz + 1), n(ix + 1, iy + 1, iz + 1), n(ix, iy + 1, iz + 1)]


def _voxel_corner_nodes(i: int, j: int, k: int, nx: int, ny: int, nz: int, nnx: int, nny: int) -> list[int]:
    i = min(max(int(i), 0), nx - 1)
    j = min(max(int(j), 0), ny - 1)
    k = min(max(int(k), 0), nz - 1)
    return _hex_corner_nodes(i, j, k, nnx, nny)


def _explicit_bcs_3d(nx: int, ny: int, nz: int, supports: list, loads: list) -> tuple[np.ndarray, np.ndarray]:
    """(fixed_dofs, force_vector) from explicit 3D cell-based BCs. Cells are [i,j,k];
    supports clamp the 3 dofs of a voxel's 8 corner nodes; loads distribute (fx,fy,fz)
    over the union of their corner nodes."""
    nnx, nny, nnz = nx + 1, ny + 1, nz + 1
    ndof = 3 * nnx * nny * nnz
    F = np.zeros(ndof)
    fixed: set[int] = set()
    for sup in supports or []:
        for cell in sup.get("cells", []):
            for n in _voxel_corner_nodes(cell[0], cell[1], cell[2], nx, ny, nz, nnx, nny):
                fixed.update((3 * n, 3 * n + 1, 3 * n + 2))
    for ld in loads or []:
        fx = float(ld.get("fx", 0.0))
        fy = float(ld.get("fy", 0.0))
        fz = float(ld.get("fz", 0.0))
        nodes: set[int] = set()
        for cell in ld.get("cells", []):
            nodes.update(_voxel_corner_nodes(cell[0], cell[1], cell[2], nx, ny, nz, nnx, nny))
        if nodes and (fx or fy or fz):
            per = 1.0 / len(nodes)
            for n in nodes:
                F[3 * n] += fx * per
                F[3 * n + 1] += fy * per
                F[3 * n + 2] += fz * per
    return np.array(sorted(fixed), dtype=int), F


def _resolve_bcs_3d(nx: int, ny: int, nz: int, bcs: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, str | None]:
    """Explicit 3D supports/loads if present, else the cantilever_3d preset
    (x=0 face fully clamped, -Y tip load at the far-end mid voxel)."""
    supports = bcs.get("supports")
    loads = bcs.get("loads")
    if supports or loads:
        fixed, F = _explicit_bcs_3d(nx, ny, nz, supports or [], loads or [])
        if fixed.size and np.abs(F).sum() > 0:
            return fixed, F, None
    nnx, nny, nnz = nx + 1, ny + 1, nz + 1
    ndof = 3 * nnx * nny * nnz
    F = np.zeros(ndof)
    fixed: set[int] = set()
    for iy in range(nny):
        for iz in range(nnz):
            n = 0 + nnx * iy + nnx * nny * iz   # x=0 face
            fixed.update((3 * n, 3 * n + 1, 3 * n + 2))
    nload = nx + nnx * (ny // 2) + nnx * nny * (nz // 2)
    F[3 * nload + 1] = -1.0
    return np.array(sorted(fixed), dtype=int), F, "cantilever_3d"


def _sensitivity_filter_3d(nx: int, ny: int, nz: int, rmin: float, x: np.ndarray, dc: np.ndarray) -> np.ndarray:
    """Classic mesh-independence sensitivity filter over a 3D voxel neighbourhood."""
    out = np.zeros_like(dc)
    r = int(np.ceil(rmin))

    def eid(i: int, j: int, k: int) -> int:
        return i + nx * j + nx * ny * k

    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                s = 0.0
                acc = 0.0
                for kk in range(max(k - r, 0), min(k + r + 1, nz)):
                    for jj in range(max(j - r, 0), min(j + r + 1, ny)):
                        for ii in range(max(i - r, 0), min(i + r + 1, nx)):
                            fac = rmin - float(np.sqrt((i - ii) ** 2 + (j - jj) ** 2 + (k - kk) ** 2))
                            if fac > 0:
                                e = eid(ii, jj, kk)
                                s += fac
                                acc += fac * x[e] * dc[e]
                e0 = eid(i, j, k)
                out[e0] = acc / (x[e0] * s) if s > 0 else dc[e0]
    return out


def simp_3d(problem: dict[str, Any]) -> dict[str, Any]:
    """Experimental 3D SIMP (H8 hex, dense numpy FE, OC update + sensitivity filter).

    problem.grid = {nx, ny, nz}; volume_fraction (or volfrac), penalty (3), rmin (1.5),
    max_iters (30). bcs: explicit {supports, loads} (3D cells) or the cantilever_3d
    preset. Small grids only. Returns density_3d as nested [nz][ny][nx]."""
    grid = problem.get("grid") or {}
    nx = int(grid.get("nx", 16))
    ny = int(grid.get("ny", 10))
    nz = int(grid.get("nz", 6))
    volfrac = float(problem.get("volume_fraction", problem.get("volfrac", 0.3)))
    penal = float(problem.get("penalty", 3.0))
    rmin = float(problem.get("rmin", 1.5))
    max_iters = int(problem.get("max_iters", 30))
    nu = float(problem.get("poisson_ratio", 0.3))

    KE = _hex8_stiffness(nu, 1.0)
    nnx, nny, nnz = nx + 1, ny + 1, nz + 1
    ndof = 3 * nnx * nny * nnz
    nele = nx * ny * nz

    def eid(i: int, j: int, k: int) -> int:
        return i + nx * j + nx * ny * k

    edof = np.zeros((nele, 24), dtype=int)
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                dofs: list[int] = []
                for n in _hex_corner_nodes(i, j, k, nnx, nny):
                    dofs += [3 * n, 3 * n + 1, 3 * n + 2]
                edof[eid(i, j, k)] = dofs

    fixed, F, preset = _resolve_bcs_3d(nx, ny, nz, problem.get("bcs") or {})
    free = np.setdiff1d(np.arange(ndof), fixed)

    # Optional result-guidance (flat, eid-indexed). Off/absent -> classic behavior.
    preserve_mask, weight_field, preserve_min = _grid_guidance_3d(problem, nx, ny, nz)
    guidance_consumed = preserve_mask is not None or weight_field is not None

    warnings: list[str] = []
    x = np.full(nele, volfrac)
    history: list[float] = []
    change = 1.0
    it = 0
    while change > 0.01 and it < max_iters:
        it += 1
        xpen = x ** penal
        K = np.zeros((ndof, ndof))
        for e in range(nele):
            ed = edof[e]
            K[np.ix_(ed, ed)] += xpen[e] * KE
        U = np.zeros(ndof)
        try:
            U[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
        except np.linalg.LinAlgError:
            warnings.append("singular stiffness (under-constrained BCs); stopping early")
            break
        c = 0.0
        dc = np.zeros(nele)
        for e in range(nele):
            ue = U[edof[e]]
            ce = float(ue @ KE @ ue)
            c += xpen[e] * ce
            dc[e] = -penal * (x[e] ** (penal - 1)) * ce
        history.append(round(c, 6))
        dc = _sensitivity_filter_3d(nx, ny, nz, rmin, x, dc)
        dc_eff = dc if weight_field is None else dc * weight_field
        lower = 0.001 if preserve_mask is None else np.where(preserve_mask, preserve_min, 0.001)
        l1, l2, move = 1e-9, 1e9, 0.2
        xnew = x.copy()
        while (l2 - l1) / (l1 + l2) > 1e-3:
            lmid = 0.5 * (l1 + l2)
            xnew = np.clip(
                x * np.sqrt(np.maximum(-dc_eff, 0) / lmid),
                np.maximum(x - move, lower), np.minimum(x + move, 1.0))
            if xnew.mean() - volfrac > 0:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.abs(xnew - x).max())
        x = xnew

    if it < 3:
        warnings.append("very few iterations completed — result may be unconverged")
    if nele < 200:
        warnings.append("coarse grid (<200 voxels) — result is indicative, not quantitative")
    if len(history) >= 2 and history[-1] >= history[0]:
        warnings.append("compliance did not decrease — check boundary conditions / grid")

    dens3 = [[[round(float(x[eid(i, j, k)]), 4) for i in range(nx)] for j in range(ny)] for k in range(nz)]
    return {
        "density_3d": dens3,
        "nx": nx, "ny": ny, "nz": nz,
        "iterations": it,
        "compliance_history": history,
        "final_compliance": history[-1] if history else None,
        "achieved_volume_fraction": round(float(x.mean()), 4),
        "target_volume_fraction": volfrac,
        "bcs_preset": preset,
        "bcs_source": "preset" if preset is not None else "explicit",
        "warnings": warnings,
        "guidance_consumed": guidance_consumed,
        "guidance_cells_preserved": int(preserve_mask.sum()) if preserve_mask is not None else 0,
        "guidance_cells_weighted": int((weight_field != 1.0).sum()) if weight_field is not None else 0,
    }


register_optimizer("simp_2d", simp_2d, version="0.1", method="SIMP", dimension=2)
register_optimizer("precomputed", precomputed_optimizer, version="0.1", method="precomputed", dimension=2)
register_optimizer(
    "simp_3d", simp_3d, version="0.1", method="SIMP", dimension=3,
    capability={
        "dimension": "3d", "method": "SIMP", "physics": "linear_elastic",
        "mesh": "structured_voxel_grid", "material": "single_material",
        "backend": "builtin_numpy", "engineering_level": "experimental_reference",
        "production_ready": False,
    },
)


# ── derive a problem from a project's CAE setup + geometry ───────────────────
# Connects "design space / supports / loads" (the project's real CAE intent) to
# the topology-optimization problem, instead of a hand-picked preset. The built-in
# optimizer is 2D, so 3D supports/loads are PROJECTED onto the plane of the two
# largest design-space dimensions; out-of-plane components are dropped (plane
# stress). This is honest 3D→2D — the projection + dropped components are recorded
# in the returned `derivation` block, and missing/degenerate data falls back to a
# preset with a warning rather than producing a silently-wrong problem.

def _read_member_text(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        return zf.read(name).decode("utf-8")
    except KeyError:
        return None


def _load_cae_setup(zf: zipfile.ZipFile) -> dict[str, Any]:
    """The package's CAE setup, whichever authoring path wrote it.

    Reading only ``simulation/setup.yaml`` here made the whole
    topology-optimization chain unreachable from the documented, key-free agent
    path (which writes ``simulation/cae_imports/parsed_*.json``) — and it failed
    invisibly, by falling back to the cantilever preset. See
    `aieng.cae_setup_view` for the two shapes.
    """
    return load_cae_setup(zf)


def _cannot_derive_2d(
    *,
    solid_node: Any,
    overall: list[float],
    warnings: list[str],
    grid: dict[str, Any],
    plane: dict[str, Any],
    frame: dict[str, Any],
    support_count: int,
    load_count: int,
    design_space_options: list[dict[str, Any]],
    out_of_plane_axis: str,
    out_of_plane_only: list[str],
    setup_present: bool,
) -> dict[str, Any]:
    """Say why the 2D problem could not be derived, instead of inventing one.

    The interesting case is not "no CAE setup" but a perfectly good one the 2D
    idealization cannot express. The projection plane is spanned by the two
    LARGEST dimensions, so for a plate or bracket the load that matters — the
    bending load, normal to the plate face — is always along the thinnest axis
    and is always dropped. Re-picking the plane does not rescue it either: a
    plane containing that load direction is only as tall as the plate is thick
    (a 6 mm plate on a 48-cell grid gives 2 cells), which is not a domain worth
    optimizing. Plane-stress simply cannot carry plate bending; ``dimension=3d``
    is the answer, and saying so beats substituting a textbook cantilever.
    """
    if out_of_plane_only:
        reason = (
            f"the load acts along {out_of_plane_axis}, the design space's thinnest axis — "
            "this is plate/shell bending, which a 2D plane-stress idealization cannot "
            "represent (the plane is spanned by the two largest dimensions, so the load "
            "has no in-plane component)"
        )
        recommendation = (
            "re-run with dimension='3d' (experimental structured-voxel SIMP), or supply an "
            "explicit 2D `problem` if you intend a different idealization"
        )
    elif not setup_present:
        reason = "the package has no CAE setup to derive supports and loads from"
        recommendation = (
            "author one first — cae.setup_static { material, fix, load } — or pass an "
            "explicit `problem`"
        )
    else:
        reason = (
            f"the CAE setup resolved to {support_count} support(s) and {load_count} load(s) "
            "on the design space; at least one of each is required"
        )
        others = [c["design_space_node"] for c in design_space_options
                  if c["design_space_node"] != solid_node]
        recommendation = (
            "the fixed faces and the loaded face must belong to the design space "
            f"({solid_node!r}, the largest solid); name another with design_space_node"
            + (f" — e.g. {', '.join(others)}" if others else "")
        )
    return {
        "status": "needs_user_input",
        "dimension": "2d",
        "reason": reason,
        "recommendation": recommendation,
        "diagnostics": warnings,
        "grid": grid,
        "plane": plane,
        "frame": frame,
        "design_space_node": solid_node,
        "design_space_bbox": overall,
        "support_count": support_count,
        "load_count": load_count,
        "out_of_plane_loads": out_of_plane_only,
        "design_space_candidates": design_space_options,
    }


def _bbox_center(bbox: Any) -> list[float] | None:
    if not (isinstance(bbox, list) and len(bbox) >= 6):
        return None
    return [(bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0, (bbox[2] + bbox[5]) / 2.0]


def _topology_entities(topology_map: Any) -> list[dict[str, Any]]:
    if isinstance(topology_map, list):
        return [e for e in topology_map if isinstance(e, dict)]
    if isinstance(topology_map, dict):
        ents = topology_map.get("entities") or topology_map.get("topology") or []
        return [e for e in ents if isinstance(e, dict)]
    return []


def _body_names(topology_map: Any) -> dict[str, str]:
    """body/solid id -> the name an engineer would recognise (else the id)."""
    names: dict[str, str] = {}
    for ent in _topology_entities(topology_map):
        if str(ent.get("type") or "").lower() not in {"solid", "body"}:
            continue
        ident = ent.get("id")
        if ident is None:
            continue
        names[str(ident)] = str(
            ent.get("name") or ent.get("source_ir_node") or ent.get("label") or ident
        )
    return names


WHOLE_MODEL_DESIGN_SPACE = "whole_model"


def _design_space_candidates(topology_map: Any) -> list[dict[str, Any]]:
    """Every design space a caller could legitimately name.

    One entry per solid, plus the whole-model envelope. The default — the
    largest single solid — excludes every other body, so on a two-part bracket a
    load applied to the rib lands outside the design space and cannot be mapped.
    Listing the alternatives turns that dead end into one round-trip.
    """
    candidates: list[dict[str, Any]] = []
    union: list[float] | None = None
    for ent in _topology_entities(topology_map):
        bb = ent.get("bounding_box") or ent.get("bbox")
        if not (isinstance(bb, list) and len(bb) >= 6):
            continue
        union = list(bb) if union is None else (
            [min(union[k], bb[k]) for k in range(3)]
            + [max(union[k + 3], bb[k + 3]) for k in range(3)]
        )
        if str(ent.get("type") or "").lower() not in {"solid", "body"}:
            continue
        node = str(ent.get("source_ir_node") or ent.get("name") or ent.get("id") or "")
        if node:
            candidates.append({"design_space_node": node, "kind": "solid", "bbox": list(bb)})
    if union is not None:
        candidates.append({
            "design_space_node": WHOLE_MODEL_DESIGN_SPACE,
            "kind": "model_envelope",
            "bbox": union,
            "note": "the envelope spanning every body — use when the load enters through "
                    "a different part than the one being optimized",
        })
    return candidates


def _resolve_design_space(
    topology_map: Any, requested: Any, default_bbox: Any, default_node: Any
) -> tuple[list[float] | None, Any, list[dict[str, Any]], str | None]:
    """(bbox, node, candidates, error) for an optionally caller-named design space."""
    candidates = _design_space_candidates(topology_map)
    if not requested:
        return default_bbox, default_node, candidates, None
    wanted = str(requested)
    for candidate in candidates:
        if candidate["design_space_node"] == wanted:
            return candidate["bbox"], wanted, candidates, None
    known = ", ".join(c["design_space_node"] for c in candidates) or "(none)"
    return None, wanted, candidates, (
        f"design_space_node {wanted!r} matches no body in this model; known: {known}"
    )


def _index_faces(topology_map: Any) -> tuple[dict[str, dict[str, Any]], list[float] | None, str | None]:
    """Return (faces_by_id, overall_bbox, largest_solid_source_node)."""
    faces: dict[str, dict[str, Any]] = {}
    union: list[float] | None = None
    best_solid: tuple[float, str] | None = None  # (volume, source_node)
    overall: list[float] | None = None
    for ent in _topology_entities(topology_map):
        et = str(ent.get("type") or "").lower()
        bb = ent.get("bounding_box") or ent.get("bbox")
        if isinstance(bb, list) and len(bb) >= 6:
            union = list(bb) if union is None else (
                [min(union[k], bb[k]) for k in range(3)] + [max(union[k + 3], bb[k + 3]) for k in range(3)]
            )
        if et == "face" and ent.get("id") is not None:
            faces[str(ent["id"])] = {
                "bbox": bb,
                "normal": ent.get("normal") or ent.get("proxy_normal"),
                "center": ent.get("center") or _bbox_center(bb),
                # kept so a refusal can name the body a face belongs to, rather
                # than only the face id the user never chose
                "body_id": ent.get("body_id"),
            }
        if et in {"solid", "body"} and isinstance(bb, list) and len(bb) >= 6:
            vol = max(bb[3] - bb[0], 0) * max(bb[4] - bb[1], 0) * max(bb[5] - bb[2], 0)
            node = str(ent.get("source_ir_node") or ent.get("name") or ent.get("id") or "")
            if best_solid is None or vol > best_solid[0]:
                best_solid = (vol, node)
                overall = list(bb)
    return faces, (overall or union), (best_solid[1] if best_solid else None)


def _feature_to_faces(cae_mapping: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in (cae_mapping or {}).get("mappings", []) or []:
        fid = mapping_target_id(m)
        if fid:
            out[fid] = [str(x) for x in (m.get("face_ids") or [])]
    return out


def _resolve_target_faces(target: Any, feat_to_faces: dict[str, list[str]], faces: dict[str, Any]) -> list[str]:
    t = str(target or "")
    if t in feat_to_faces:
        return [f for f in feat_to_faces[t] if f in faces]
    if t in faces:  # target is itself a face id
        return [t]
    if f"face_{t}" in faces:
        return [f"face_{t}"]
    return []


def _dedup_cells(cells: list[list[int]]) -> list[list[int]]:
    """Dedup grid cells, preserving dimensionality (2D [i,j] or 3D [i,j,k])."""
    seen: set[tuple[int, ...]] = set()
    out: list[list[int]] = []
    for c in cells:
        key = tuple(int(v) for v in c)
        if key not in seen:
            seen.add(key)
            out.append([int(v) for v in c])
    return out


TOPOPT_RESULT_GUIDANCE_PATH = "diagnostics/topology_optimization_result_guidance.json"

# Neutral CAE result artifacts (solver-agnostic). Used as GUIDANCE only — never as a
# replacement for the loads/supports/material/design-space taken from the CAE setup.
_CAE_RESULT_ARTIFACTS = {
    "computed_metrics": "analysis/computed_metrics.json",
    "field_regions": "analysis/field_regions.json",
    "cae_result_map": "analysis/cae_result_map.json",
    "object_registry": "registry/object_registry.json",
}


def _quantity_for(result_type: str | None) -> str:
    rt = str(result_type or "").lower()
    if "stress" in rt:
        return "von_mises_stress"
    if "disp" in rt or "deflect" in rt:
        return "displacement_magnitude"
    if "strain" in rt:
        return "strain"
    return rt or "unknown"


def _guidance_item(m: dict[str, Any], design_space_node: str | None, *, guidance: str | None = None) -> dict[str, Any]:
    """Preserve the neutral result-map fields verbatim + map back to the design space."""
    affected = m.get("affected_topology_entities") or []
    sin = m.get("source_ir_node")
    within = bool(design_space_node and (sin == design_space_node or design_space_node in affected))
    item = {
        "region_id": m.get("region_id"),
        "load_case_id": m.get("load_case_id"),
        "result_type": m.get("result_type"),
        "quantity": _quantity_for(m.get("result_type")),
        "value": m.get("value"),
        "unit": m.get("unit"),
        "value_range": m.get("value_range"),
        "location": m.get("location"),
        "affected_topology_entities": affected,
        "source_ir_node": sin,
        "node_linkage": m.get("node_linkage"),
        "mapping_method": m.get("mapping_method"),
        "confidence": m.get("confidence"),
        "within_design_space": within,
    }
    if guidance:
        item["guidance"] = guidance
    return item


def build_topopt_result_guidance(
    cae_result_map: dict[str, Any] | None,
    field_regions: dict[str, Any] | None,
    computed_metrics: dict[str, Any] | None,
    design_space_node: str | None,
    *,
    present: dict[str, bool] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Turn neutral CAE result artifacts into topology-optimization GUIDANCE.

    Returns ``(result_guidance, diagnostics, warnings)``. Solver-agnostic: reads only
    the neutral contract fields (result_type/value/unit/load_case_id/confidence/
    source_ir_node/mapping_method/...) — no CalculiX-specific names. The guidance never
    changes the optimizer's boundary conditions; it annotates which regions the analysis
    says are stressed / deflecting so a later step (or the user) can preserve/reinforce
    them. ``preserve_or_reinforce_regions`` ← stress hotspots; ``stiffness_sensitive_regions``
    ← deflection hotspots."""
    present = present or {}
    warnings: list[str] = []
    consumed: list[str] = []
    skipped: list[str] = []
    for key, path in _CAE_RESULT_ARTIFACTS.items():
        (consumed if present.get(key) else skipped).append(path)

    stress: list[dict[str, Any]] = []
    deflection: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []

    crm = cae_result_map or {}
    mapped = crm.get("mapped_results") if isinstance(crm.get("mapped_results"), list) else []
    for m in mapped:
        if not isinstance(m, dict):
            continue
        rt = str(m.get("result_type") or "").lower()
        if "stress" in rt:
            stress.append(_guidance_item(m, design_space_node))
        elif "disp" in rt or "deflect" in rt:
            deflection.append(_guidance_item(m, design_space_node))
    for u in (crm.get("unmapped_regions") if isinstance(crm.get("unmapped_regions"), list) else []):
        if isinstance(u, dict):
            unmapped.append({
                "region_id": u.get("region_id") or u.get("id"),
                "load_case_id": u.get("load_case_id"),
                "result_type": u.get("result_type"),
                "reason": u.get("reason") or "unmapped",
            })

    # If there is no mapped result map but raw field regions exist, surface them as
    # location-only hotspots that could not be tied to topology (honest, low confidence).
    used_field_regions = False
    if not mapped and isinstance(field_regions, dict):
        for r in (field_regions.get("regions") if isinstance(field_regions.get("regions"), list) else []):
            if not isinstance(r, dict):
                continue
            used_field_regions = True
            val = r.get("value") or {}
            stub = {
                "region_id": r.get("id"),
                "load_case_id": r.get("load_case_id"),
                "result_type": r.get("result_type"),
                "value": val.get("peak", val.get("max")),
                "value_range": {"min": val.get("min"), "max": val.get("max")},
                "unit": val.get("unit"),
                "location": r.get("center"),
                "affected_topology_entities": [],
                "source_ir_node": None,
                "node_linkage": "none",
                "mapping_method": "none",
                "confidence": "low",
            }
            rt = str(r.get("result_type") or "").lower()
            target = stress if "stress" in rt else (deflection if ("disp" in rt or "deflect" in rt) else None)
            if target is not None:
                target.append(_guidance_item(stub, design_space_node))
            unmapped.append({"region_id": r.get("id"), "load_case_id": r.get("load_case_id"),
                             "result_type": r.get("result_type"),
                             "reason": "field region not mapped to topology (no cae_result_map)"})

    available = bool(stress or deflection or unmapped)
    if not available:
        if not any(present.get(k) for k in _CAE_RESULT_ARTIFACTS):
            warnings.append("no CAE result artifacts present; deriving topology-optimization "
                            "problem from CAE setup only (loads/supports/material/design-space)")
        else:
            warnings.append("CAE result artifacts present but no mappable result regions found; "
                            "no result_guidance produced")
    elif unmapped and not (stress or deflection):
        warnings.append("CAE results present but none could be mapped to topology — see "
                        "unmapped_result_regions for diagnostics")

    # preserve_or_reinforce ← high stress; stiffness_sensitive ← high deflection.
    preserve = [{**dict(it), "guidance": "preserve_or_reinforce"} for it in stress]
    stiffness = [{**dict(it), "guidance": "increase_stiffness"} for it in deflection]

    global_extrema: list[dict[str, Any]] = []
    if isinstance(computed_metrics, dict):
        for lc in (computed_metrics.get("load_cases") if isinstance(computed_metrics.get("load_cases"), list) else []):
            for r in (lc.get("results") if isinstance(lc, dict) and isinstance(lc.get("results"), list) else []):
                if isinstance(r, dict):
                    global_extrema.append({
                        "load_case_id": lc.get("id"), "result_type": r.get("result_type"),
                        "metric": r.get("metric"), "max": r.get("max"), "min": r.get("min"),
                        "unit": r.get("unit"),
                    })

    guidance = {
        "available": available,
        "design_space_node": design_space_node,
        "stress_hotspots": stress,
        "deflection_hotspots": deflection,
        "stiffness_sensitive_regions": stiffness,
        "preserve_or_reinforce_regions": preserve,
        "unmapped_result_regions": unmapped,
        "global_extrema": global_extrema,
        "sources": {"consumed": consumed, "skipped": skipped},
        "advisory_only": True,
        "note": ("Engineering guidance from analysis results. ADVISORY: it annotates "
                 "stressed/deflecting regions; it does NOT set the optimizer's loads, "
                 "supports, material, or design space (those come from the CAE setup)."),
    }
    diagnostics = {
        "format": "aieng.topology_optimization_result_guidance",
        "schema_version": "0.1",
        "design_space_node": design_space_node,
        "consumed_artifacts": consumed,
        "skipped_artifacts": skipped,
        "used_field_regions_fallback": used_field_regions,
        "counts": {
            "mapped_results": len(mapped),
            "stress_hotspots": len(stress),
            "deflection_hotspots": len(deflection),
            "unmapped_result_regions": len(unmapped),
        },
        "confidence_distribution": _confidence_distribution(stress + deflection),
        "unmapped_result_regions": unmapped,
        "warnings": warnings,
    }
    return guidance, diagnostics, warnings


def _confidence_distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for it in items:
        c = str(it.get("confidence") or "unknown")
        dist[c] = dist.get(c, 0) + 1
    return dist


def _replace_members(package_path: Path, members: dict[str, bytes]) -> None:
    """Atomically write/replace several members in a .aieng zip in one rewrite."""
    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with (
            zipfile.ZipFile(package_path, "r") as src,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                if item.filename not in members:
                    dst.writestr(item, src.read(item.filename))
            for name, data in members.items():
                dst.writestr(name, data)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def collect_topopt_result_guidance(
    package_path: str | Path, design_space_node: str | None, *, write: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Read neutral CAE result artifacts from a package, build result_guidance, and
    (best-effort) write diagnostics/topology_optimization_result_guidance.json + stamp
    the conversion manifest with the topopt inputs provenance. Returns
    ``(result_guidance, warnings)``. Missing artifacts -> warning, never an error."""
    package_path = Path(package_path)
    present: dict[str, bool] = {}
    crm = fr = cm = None
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            for key, path in _CAE_RESULT_ARTIFACTS.items():
                present[key] = path in names
            crm = json.loads(zf.read(_CAE_RESULT_ARTIFACTS["cae_result_map"])) if present.get("cae_result_map") else None
            fr = json.loads(zf.read(_CAE_RESULT_ARTIFACTS["field_regions"])) if present.get("field_regions") else None
            cm = json.loads(zf.read(_CAE_RESULT_ARTIFACTS["computed_metrics"])) if present.get("computed_metrics") else None
    except Exception as exc:  # noqa: BLE001 - result guidance is advisory
        return ({"available": False, "error": f"{type(exc).__name__}: {exc}",
                 "sources": {"consumed": [], "skipped": list(_CAE_RESULT_ARTIFACTS.values())}},
                [f"could not read CAE result artifacts: {exc}"])

    guidance, diagnostics, warnings = build_topopt_result_guidance(
        crm, fr, cm, design_space_node, present=present)

    if write and package_path.exists():
        try:
            members: dict[str, bytes] = {
                TOPOPT_RESULT_GUIDANCE_PATH: (json.dumps(diagnostics, indent=2, sort_keys=True) + "\n").encode(),
            }
            # Stamp the conversion manifest: the topopt problem used CAE setup + (optionally)
            # CAE result guidance, with the artifact paths actually consumed.
            manifest: dict[str, Any] = {}
            with zipfile.ZipFile(package_path, "r") as zf:
                if "provenance/conversion_manifest.json" in zf.namelist():
                    try:
                        manifest = json.loads(zf.read("provenance/conversion_manifest.json"))
                    except Exception:
                        manifest = {}
            if not isinstance(manifest, dict):
                manifest = {}
            manifest.setdefault("format", "aieng.conversion_manifest")
            manifest["topopt_inputs"] = {
                "used": "cae_setup+cae_result_guidance" if guidance.get("available") else "cae_setup_only",
                "design_space_node": design_space_node,
                "result_guidance_available": bool(guidance.get("available")),
                "consumed_artifacts": guidance.get("sources", {}).get("consumed", []),
                "skipped_artifacts": guidance.get("sources", {}).get("skipped", []),
                "diagnostics_path": TOPOPT_RESULT_GUIDANCE_PATH,
                "note": "Loads/supports/material/design-space come from the CAE setup; CAE "
                        "results are advisory guidance only.",
            }
            members["provenance/conversion_manifest.json"] = (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            _replace_members(package_path, members)
        except Exception:  # noqa: BLE001 - best-effort diagnostics write
            pass
    return guidance, warnings


TOPOPT_GUIDANCE_FIELD_PATH = "analysis/topology_optimization_guidance_field.json"
_ENFORCEABLE_CONFIDENCE = {"high", "medium"}


def _guidance_cell(loc: Any, frame: dict[str, Any], dims: tuple[int, ...], is3d: bool) -> tuple[int, ...] | None:
    """Map a result hotspot's world ``location`` to a grid/voxel cell via the frame.
    Returns grid coords (i, j[, k]) or None if outside the grid / no location."""
    if isinstance(loc, dict):
        p = [float(loc.get("x", 0.0)), float(loc.get("y", 0.0)), float(loc.get("z", 0.0))]
    elif isinstance(loc, (list, tuple)) and len(loc) >= 3:
        p = [float(loc[0]), float(loc[1]), float(loc[2])]
    else:
        return None
    origin = frame.get("origin") or [0.0, 0.0, 0.0]
    cs = frame.get("cell_size") or [1.0, 1.0, 1.0]
    ui = _AXIS_INDEX.get(str(frame.get("u_axis", "x")), 0)
    vi = _AXIS_INDEX.get(str(frame.get("v_axis", "y")), 1)
    su = float(cs[0]) or 1.0
    sv = float(cs[1] if len(cs) > 1 else cs[0]) or 1.0
    i = int((p[ui] - float(origin[ui])) / su)
    j = int((p[vi] - float(origin[vi])) / sv)
    if is3d:
        wi = ({0, 1, 2} - {ui, vi}).pop()
        sw = float(cs[2] if len(cs) > 2 else cs[0]) or 1.0
        k = int((p[wi] - float(origin[wi])) / sw)
        if 0 <= i < dims[0] and 0 <= j < dims[1] and 0 <= k < dims[2]:
            return (i, j, k)
        return None
    if 0 <= i < dims[0] and 0 <= j < dims[1]:
        return (i, j)
    return None


def build_guidance_field(problem: dict[str, Any]) -> dict[str, Any]:
    """Map a problem's ``result_guidance`` into solver-neutral optimization-grid fields.

    Produces ``preserve_mask`` (cells to protect, from high/medium-confidence stress
    hotspots), ``stiffness_weight_field`` (local sensitivity multiplier, from deflection
    hotspots), and ``ignore_mask`` (low-confidence / unmapped regions — recorded, not
    enforced). 2D fields are shaped ``[nely][nelx]``; 3D ``[nz][ny][nx]`` (matching the
    optimizers' grids). Honest: low confidence and unmapped regions never enter the
    enforced masks; everything is diagnosed. Pure — no IO, no algorithm change."""
    grid = problem.get("grid") or {}
    frame = problem.get("frame") or (problem.get("derivation") or {}).get("frame") or {}
    rg = problem.get("result_guidance") or {}
    radius = max(int(problem.get("guidance_radius_cells", 1)), 0)
    mult = float(problem.get("stiffness_weight_multiplier", 2.0))
    pmin = float(problem.get("preserve_min_density", 0.5))

    is3d = "nx" in grid
    if is3d:
        nx, ny, nz = int(grid.get("nx", 1)), int(grid.get("ny", 1)), int(grid.get("nz", 1))
        dims: tuple[int, ...] = (nx, ny, nz)
        shape = (nz, ny, nx)
    else:
        nelx, nely = int(grid.get("nelx", 1)), int(grid.get("nely", 1))
        dims = (nelx, nely)
        shape = (nely, nelx)

    preserve = np.zeros(shape, dtype=float)
    weight = np.ones(shape, dtype=float)
    ignore = np.zeros(shape, dtype=float)

    def stamp(arr: np.ndarray, cell: tuple[int, ...], value: float, *, take_max: bool = False) -> int:
        n = 0
        if is3d:
            i, j, k = cell
            for kk in range(max(k - radius, 0), min(k + radius + 1, shape[0])):
                for jj in range(max(j - radius, 0), min(j + radius + 1, shape[1])):
                    for ii in range(max(i - radius, 0), min(i + radius + 1, shape[2])):
                        arr[kk, jj, ii] = max(arr[kk, jj, ii], value) if take_max else value
                        n += 1
        else:
            i, j = cell
            for jj in range(max(j - radius, 0), min(j + radius + 1, shape[0])):
                for ii in range(max(i - radius, 0), min(i + radius + 1, shape[1])):
                    arr[jj, ii] = max(arr[jj, ii], value) if take_max else value
                    n += 1
        return n

    diag = {"mapped": 0, "ignored": 0, "unmapped": [], "confidence_levels": {}}
    regions_prov: list[dict[str, Any]] = []

    def consider(items: list, kind: str) -> None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            conf = str(it.get("confidence") or "low").lower()
            diag["confidence_levels"][conf] = diag["confidence_levels"].get(conf, 0) + 1
            cell = _guidance_cell(it.get("location"), frame, dims, is3d)
            prov = {
                "region_id": it.get("region_id"), "kind": kind,
                "load_case_id": it.get("load_case_id"), "result_type": it.get("result_type"),
                "quantity": it.get("quantity"), "value": it.get("value"), "unit": it.get("unit"),
                "confidence": conf, "source_ir_node": it.get("source_ir_node"),
                "within_design_space": it.get("within_design_space"), "cell": list(cell) if cell else None,
            }
            if cell is None:
                diag["unmapped"].append({"region_id": it.get("region_id"), "kind": kind,
                                         "reason": "location outside design-space grid or missing"})
                prov["enforced"] = False
                regions_prov.append(prov)
                continue
            if conf in _ENFORCEABLE_CONFIDENCE:
                if kind == "preserve":
                    stamp(preserve, cell, 1.0)
                else:  # stiffness
                    stamp(weight, cell, mult, take_max=True)
                diag["mapped"] += 1
                prov["enforced"] = True
            else:  # low confidence: record + ignore, do not enforce
                stamp(ignore, cell, 1.0)
                diag["ignored"] += 1
                prov["enforced"] = False
            regions_prov.append(prov)

    consider(rg.get("preserve_or_reinforce_regions"), "preserve")
    consider(rg.get("stiffness_sensitive_regions"), "stiffness")

    diag["cells_preserved"] = int((preserve > 0.5).sum())
    diag["cells_weighted"] = int((weight != 1.0).sum())
    diag["cells_ignored"] = int((ignore > 0.5).sum())
    diag["unmapped_count"] = len(diag["unmapped"])

    return {
        "format": "aieng.topology_optimization_guidance_field",
        "schema_version": "0.1",
        "dimension": "3d" if is3d else "2d",
        "grid": dict(grid),
        "frame": frame,
        "options": {
            "use_result_guidance": True,
            "preserve_min_density": pmin,
            "stiffness_weight_multiplier": mult,
            "guidance_radius_cells": radius,
            "enforceable_confidence": sorted(_ENFORCEABLE_CONFIDENCE),
        },
        "preserve_mask": preserve.astype(int).tolist(),
        "stiffness_weight_field": np.round(weight, 4).tolist(),
        "ignore_mask": ignore.astype(int).tolist(),
        "diagnostics": diag,
        "provenance": {
            "design_space_node": rg.get("design_space_node") or problem.get("design_space_node"),
            "source_ir_node": problem.get("source_ir_node") or problem.get("design_space_node"),
            "consumed_artifacts": (rg.get("sources") or {}).get("consumed", []),
            "regions": regions_prov,
            "advisory_only": True,
        },
    }


def derive_topopt_problem_from_package(
    package_path: str | Path, *,
    dimension: str = "2d", resolution: int = 48, resolution_3d: int = 16,
    volfrac: float = 0.5, penalty: float = 3.0,
    rmin: float = 1.5, max_iters: int = 40, objective: str = "compliance_minimization",
    design_space_node: str | None = None,
) -> dict[str, Any]:
    """Derive a 2D topology-optimization problem from a project's CAE setup + geometry.

    With ``dimension="3d"`` the 3D derivation path is used instead (structured voxel
    grid, full 3D supports/loads, no projection); see
    ``derive_topopt_problem_3d_from_package``.

    Reads geometry/topology_map.json (design-space bbox + face geometry),
    simulation/cae_mapping.json (feature→face links) and the CAE setup
    (supports + loads). Projects the supports/loads onto the plane of the two
    largest design-space dimensions and maps them to grid cells, returning a
    ``problem`` with explicit ``bcs.supports``/``bcs.loads``. The ``derivation``
    block records the projection plane, the design-space frame (origin + cell
    size + thickness, so a later writeback can place the optimized body), source
    BC links, and warnings. If fewer than one support AND one load can be derived,
    the problem is REFUSED (`status: needs_user_input`) with the reason and the
    design spaces that could be named instead — never replaced by a preset.

    ``design_space_node`` names the body to optimize; the default is the largest
    single solid. Pass ``WHOLE_MODEL_DESIGN_SPACE`` for the envelope spanning
    every body — needed when the load enters through a different part.
    """
    if str(dimension) == "3d":
        return derive_topopt_problem_3d_from_package(
            package_path, resolution=resolution_3d, volfrac=volfrac, penalty=penalty,
            rmin=rmin, max_iters=max_iters, objective=objective,
            design_space_node=design_space_node)

    package_path = Path(package_path)
    warnings: list[str] = []
    with zipfile.ZipFile(package_path, "r") as zf:
        topo_raw = _read_member_text(zf, "geometry/topology_map.json")
        topology_map = json.loads(topo_raw) if topo_raw else {}
        cae_raw = _read_member_text(zf, "simulation/cae_mapping.json")
        cae_mapping = json.loads(cae_raw) if cae_raw else {}
        setup = _load_cae_setup(zf)

    faces, overall, solid_node = _index_faces(topology_map)
    overall, solid_node, design_space_options, ds_error = _resolve_design_space(
        topology_map, design_space_node, overall, solid_node)
    if ds_error:
        return {
            "status": "needs_user_input", "dimension": "2d", "reason": ds_error,
            "design_space_candidates": design_space_options,
        }
    if not overall:
        raise ValueError("cannot derive design space: no bounding box in geometry/topology_map.json")

    feat_to_faces = _feature_to_faces(cae_mapping)
    setup_bcs = setup.get("boundary_conditions") or []
    setup_loads = setup.get("loads") or []
    if not feat_to_faces and (setup_bcs or setup_loads):
        warnings.append("no simulation/cae_mapping.json — resolving BC/load targets as face ids directly")

    mins, maxs = overall[:3], overall[3:]
    ext = [max(maxs[k] - mins[k], 1e-9) for k in range(3)]
    u, v, w = sorted(range(3), key=lambda k: ext[k], reverse=True)
    axis = ["x", "y", "z"]
    nelx = max(int(resolution), 2)
    nely = max(int(round(resolution * ext[v] / ext[u])), 2)
    du, dv = ext[u] / nelx, ext[v] / nely

    def cell_of_point(p: list[float]) -> tuple[int, int]:
        i = int((p[u] - mins[u]) / ext[u] * nelx)
        j = int((p[v] - mins[v]) / ext[v] * nely)
        return min(max(i, 0), nelx - 1), min(max(j, 0), nely - 1)

    def cells_of_bbox(bb: Any) -> list[list[int]]:
        if not (isinstance(bb, list) and len(bb) >= 6):
            return []
        i0, j0 = cell_of_point([bb[0], bb[1], bb[2]])
        i1, j1 = cell_of_point([bb[3], bb[4], bb[5]])
        i0, i1 = sorted((i0, i1))
        j0, j1 = sorted((j0, j1))
        return [[i, j] for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    supports: list[dict[str, Any]] = []
    for bc in setup_bcs:
        fids = _resolve_target_faces(bc.get("target_feature"), feat_to_faces, faces)
        cells = _dedup_cells([c for fid in fids for c in cells_of_bbox(faces.get(fid, {}).get("bbox"))])
        if cells:
            supports.append({"cells": cells, "from": {
                "target_feature": bc.get("target_feature"), "type": bc.get("type"), "face_ids": fids}})
        else:
            warnings.append(f"support '{bc.get('target_feature')}' resolved to no faces/cells — skipped")

    loads: list[dict[str, Any]] = []
    out_of_plane_only: list[str] = []
    for ld in setup_loads:
        fids = _resolve_target_faces(ld.get("target_feature"), feat_to_faces, faces)
        cells = _dedup_cells([
            list(cell_of_point(faces[fid]["center"])) for fid in fids if faces.get(fid, {}).get("center")
        ])
        direction = ld.get("direction") or [0.0, 0.0, -1.0]
        # `or 1.0` turned an EXPLICIT 0 N into a 1 N reference load — the mirror of
        # the silent-zero-load defect: there, a real load became 0; here, a
        # deliberate 0 becomes a load that was never applied. Only a MISSING
        # magnitude defaults to the unit reference.
        raw_mag = ld.get("value_n")
        mag = 1.0 if raw_mag is None else float(raw_mag)
        fx, fy, fw = mag * float(direction[u]), mag * float(direction[v]), mag * float(direction[w])
        if cells and (fx or fy):
            loads.append({"cells": cells, "fx": fx, "fy": fy, "from": {
                "target_feature": ld.get("target_feature"), "value_n": mag, "direction": direction}})
            if abs(fw) > max(abs(fx), abs(fy)):
                warnings.append(
                    f"load '{ld.get('target_feature')}' is mostly out-of-plane ({axis[w]}); "
                    "the 2D problem keeps only the in-plane component")
        elif cells and abs(fw) > 0:
            # Only a load that DID land on cells, and whose force is entirely
            # out-of-plane, diagnoses the plate-bending case. One that resolved
            # to no cells is a binding problem and must not borrow that reason.
            out_of_plane_only.append(str(ld.get("target_feature")))
            warnings.append(
                f"load '{ld.get('target_feature')}' is purely out-of-plane ({axis[w]}); "
                "no in-plane component for the 2D problem — skipped")
        else:
            warnings.append(f"load '{ld.get('target_feature')}' resolved to no force/cells — skipped")

    bcs: dict[str, Any] = {"supports": supports, "loads": loads}
    derived = bool(supports and loads)
    if not derived:
        # Refuse rather than substitute a textbook problem. Returning
        # `status: ok` with the cantilever preset meant a real bracket was
        # optimized against a load case that was not its own — and the result
        # gets written back as the project's geometry by opt.writeback_to_shape_ir.
        # The 3D derivation in this same module already refuses this way.
        return _cannot_derive_2d(
            solid_node=solid_node, overall=overall, warnings=warnings,
            grid={"nelx": nelx, "nely": nely},
            plane={"u_axis": axis[u], "v_axis": axis[v], "out_of_plane_axis": axis[w]},
            frame={
                "origin": [mins[0], mins[1], mins[2]],
                "u_axis": axis[u], "v_axis": axis[v],
                "cell_size": [round(du, 6), round(dv, 6)], "thickness": round(ext[w], 6),
            },
            support_count=len(supports), load_count=len(loads),
            design_space_options=design_space_options,
            out_of_plane_axis=axis[w], out_of_plane_only=out_of_plane_only,
            # A setup that exists but resolved to nothing is a binding problem,
            # not a missing setup — telling the user to author one they already
            # have would send them the wrong way.
            setup_present=bool(setup),
        )

    # Advisory engineering guidance from neutral CAE result artifacts (if present).
    # The loads/supports/material/design-space above stay sourced from the CAE setup.
    result_guidance, guidance_warnings = collect_topopt_result_guidance(package_path, solid_node)
    warnings.extend(guidance_warnings)

    return {
        "grid": {"nelx": nelx, "nely": nely},
        "volfrac": volfrac, "penalty": penalty, "rmin": rmin, "max_iters": max_iters,
        "objective": objective,
        "bcs": bcs,
        "design_space_node": solid_node,
        "result_guidance": result_guidance,
        "derivation": {
            "source": "cae_setup+topology_map",
            "bc_source": "cae_setup",
            "result_guidance_source": "cae_result_artifacts" if result_guidance.get("available") else "none",
            "derived": derived,
            "plane": {"u_axis": axis[u], "v_axis": axis[v], "out_of_plane_axis": axis[w]},
            "design_space_bbox": overall,
            "frame": {
                "origin": [mins[0], mins[1], mins[2]],
                "u_axis": axis[u], "v_axis": axis[v],
                "cell_size": [round(du, 6), round(dv, 6)], "thickness": round(ext[w], 6),
            },
            "support_count": len(supports), "load_count": len(loads),
            "result_guidance_inputs": result_guidance.get("sources"),
            "warnings": warnings,
            "limitations": [
                "3D supports/loads are projected onto the plane of the two largest design-space "
                "dimensions; out-of-plane force components are dropped (plane-stress idealization).",
            ],
        },
    }


def _face_occupied_voxels(
    fbb: list[float], mins: list[float], ext: list[float], dims: tuple[int, int, int],
) -> list[list[int]]:
    """Every voxel the face's bbox overlaps, clamped to the grid.

    For a load that acts on a surface cutting through the domain rather than
    bounding it — an inclined gusset face, a pad partway up a wall. Returns []
    when the face lies entirely outside the design space, so "outside" stays
    distinguishable from "interior".
    """
    if not (isinstance(fbb, list) and len(fbb) >= 6):
        return []
    spans: list[range] = []
    for a in range(3):
        lo = (fbb[a] - mins[a]) / ext[a]
        hi = (fbb[a + 3] - mins[a]) / ext[a]
        if hi < -0.01 or lo > 1.01:
            return []                                      # outside on this axis
        i0 = min(max(int(lo * dims[a]), 0), dims[a] - 1)
        i1 = min(max(int(hi * dims[a]), 0), dims[a] - 1)
        spans.append(range(min(i0, i1), max(i0, i1) + 1))
    return [[i, j, k] for i in spans[0] for j in spans[1] for k in spans[2]]


def _face_boundary_voxels(
    fbb: list[float], mins: list[float], ext: list[float], dims: tuple[int, int, int],
    normal: Any = None,
) -> tuple[list[list[int]], int, int] | None:
    """Map a planar face to a layer of boundary voxels (no projection — full 3D).

    Finds the face's normal axis, checks the face sits on a design-space boundary
    (near min or max along that axis), and returns the voxel cells covering the
    face's footprint on that boundary layer. Returns None if the face is interior
    (not safely on a boundary) so the caller can ask the user.

    The normal axis comes from the face's RECORDED normal when the topology has
    one. Inferring it from the bounding box's thinnest extent is exact for an
    axis-aligned face and wrong for every inclined one: a triangular gusset's
    hypotenuse is thin along the rib's THICKNESS, not along its own normal, so
    the bbox heuristic read a 32°-inclined load face as a y-normal face sitting
    mid-model — interior, refused, and no choice of design space could fix it.
    """
    if not (isinstance(fbb, list) and len(fbb) >= 6):
        return None                                        # no bbox -> diagnose, don't crash
    ax: int | None = None
    if isinstance(normal, (list, tuple)) and len(normal) == 3:
        try:
            comps = [abs(float(c)) for c in normal]
        except (TypeError, ValueError):
            comps = []
        if comps and max(comps) > 0:
            ax = max(range(3), key=lambda a: comps[a])
    if ax is None:
        extents = [fbb[a + 3] - fbb[a] for a in range(3)]
        ax = min(range(3), key=lambda a: extents[a])       # fallback: thinnest extent
    coord = (fbb[ax] + fbb[ax + 3]) / 2.0
    rel = (coord - mins[ax]) / ext[ax]
    if not (-0.15 <= rel <= 1.15):
        # OUTSIDE the design space along its own normal, not on its boundary.
        # `min(rel, 1 - rel)` goes negative for rel > 1, which the interior test
        # below reads as "on a boundary" — so a load face 19 mm above a 6 mm
        # plate was silently mapped onto the plate's top layer.
        return None
    if min(rel, 1.0 - rel) > 0.15:                         # not near a boundary plane
        return None
    layer = 0 if rel < 0.5 else dims[ax] - 1
    others = [a for a in range(3) if a != ax]

    def crange(a: int) -> range:
        i0 = int((fbb[a] - mins[a]) / ext[a] * dims[a])
        i1 = int((fbb[a + 3] - mins[a]) / ext[a] * dims[a])
        i0 = min(max(i0, 0), dims[a] - 1)
        i1 = min(max(min(i1, dims[a] - 1), 0), dims[a] - 1)
        return range(min(i0, i1), max(i0, i1) + 1)

    a0, a1 = others
    cells: list[list[int]] = []
    for c0 in crange(a0):
        for c1 in crange(a1):
            cell = [0, 0, 0]
            cell[ax] = layer
            cell[a0] = c0
            cell[a1] = c1
            cells.append(cell)
    return cells, ax, layer


def derive_topopt_problem_3d_from_package(
    package_path: str | Path, *,
    resolution: int = 16, volfrac: float = 0.3, penalty: float = 3.0,
    rmin: float = 1.5, max_iters: int = 30, objective: str = "compliance_minimization",
    design_space_node: str | None = None,
) -> dict[str, Any]:
    """Derive a full-3D topology-optimization problem from a project's CAE + geometry.

    Builds a structured voxel grid over the design-space bbox (no 2D projection),
    maps fixed/support faces to boundary voxel layers and load faces to boundary
    voxel cells with the FULL 3D force vector. If a usable support AND load cannot
    be mapped, returns ``{"status": "needs_user_input", ...}`` with diagnostics
    rather than guessing."""
    package_path = Path(package_path)
    with zipfile.ZipFile(package_path, "r") as zf:
        topo_raw = _read_member_text(zf, "geometry/topology_map.json")
        topology_map = json.loads(topo_raw) if topo_raw else {}
        cae_raw = _read_member_text(zf, "simulation/cae_mapping.json")
        cae_mapping = json.loads(cae_raw) if cae_raw else {}
        setup = _load_cae_setup(zf)

    faces, overall, solid_node = _index_faces(topology_map)
    body_names = _body_names(topology_map)
    overall, solid_node, design_space_options, ds_error = _resolve_design_space(
        topology_map, design_space_node, overall, solid_node)
    if ds_error:
        return {
            "status": "needs_user_input", "dimension": "3d", "reason": ds_error,
            "design_space_candidates": design_space_options,
        }
    if not overall:
        raise ValueError("cannot derive design space: no bounding box in geometry/topology_map.json")

    feat_to_faces = _feature_to_faces(cae_mapping)
    setup_bcs = setup.get("boundary_conditions") or []
    setup_loads = setup.get("loads") or []

    mins, maxs = overall[:3], overall[3:]
    ext = [max(maxs[k] - mins[k], 1e-9) for k in range(3)]
    longest = max(ext)
    res = max(int(resolution), 2)
    nx = max(round(res * ext[0] / longest), 2)
    ny = max(round(res * ext[1] / longest), 2)
    nz = max(round(res * ext[2] / longest), 2)
    dims = (nx, ny, nz)
    cell_size = [round(ext[0] / nx, 6), round(ext[1] / ny, 6), round(ext[2] / nz, 6)]
    frame = {
        "origin": [mins[0], mins[1], mins[2]],
        "u_axis": "x", "v_axis": "y", "w_axis": "z",
        "cell_size": cell_size,
        "cell_size_x": cell_size[0], "cell_size_y": cell_size[1], "cell_size_z": cell_size[2],
    }

    diagnostics: list[str] = []
    supports: list[dict[str, Any]] = []
    for bc in setup_bcs:
        fids = _resolve_target_faces(bc.get("target_feature"), feat_to_faces, faces)
        cells: list[list[int]] = []
        for fid in fids:
            mapped = _face_boundary_voxels(
                faces.get(fid, {}).get("bbox") or [], mins, ext, dims,
                faces.get(fid, {}).get("normal"))
            if mapped:
                cells.extend(mapped[0])
            else:
                diagnostics.append(
                    f"support '{bc.get('target_feature')}' face {fid} is not on a design-space "
                    "boundary — cannot map to boundary voxels")
        cells = _dedup_cells(cells)
        if cells:
            supports.append({"cells": cells, "from": {
                "target_feature": bc.get("target_feature"), "type": bc.get("type"), "face_ids": fids}})
        elif not fids:
            diagnostics.append(f"support '{bc.get('target_feature')}' resolved to no faces")

    loads: list[dict[str, Any]] = []
    interior_mapped: list[str] = []
    warnings_3d: list[str] = []
    for ld in setup_loads:
        fids = _resolve_target_faces(ld.get("target_feature"), feat_to_faces, faces)
        cells = []
        for fid in fids:
            mapped = _face_boundary_voxels(
                faces.get(fid, {}).get("bbox") or [], mins, ext, dims,
                faces.get(fid, {}).get("normal"))
            if mapped:
                cells.extend(mapped[0])
                continue
            # A load does not have to sit on a boundary plane — it only has to
            # act on cells. Requiring a boundary layer refused the archetypal
            # case outright: a bracket's load enters through the rib's INCLINED
            # face, which spans the domain rather than bounding it. Fall back to
            # the voxels the face actually occupies, and record that it did.
            occupied = _face_occupied_voxels(
                faces.get(fid, {}).get("bbox") or [], mins, ext, dims)
            if occupied:
                cells.extend(occupied)
                interior_mapped.append(str(fid))
                continue
            # Genuinely outside the design space — name the body and the way out.
            owner_id = faces.get(fid, {}).get("body_id")
            owner = body_names.get(str(owner_id), owner_id) if owner_id else None
            owned = f" (on {owner})" if owner and owner != solid_node else ""
            diagnostics.append(
                f"load '{ld.get('target_feature')}' face {fid}{owned} lies outside the design "
                f"space '{solid_node}'. Name another with design_space_node — "
                f"e.g. {WHOLE_MODEL_DESIGN_SPACE} for the envelope spanning every body.")
        cells = _dedup_cells(cells)
        direction = ld.get("direction") or [0.0, 0.0, -1.0]
        # `or 1.0` turned an EXPLICIT 0 N into a 1 N reference load — the mirror of
        # the silent-zero-load defect: there, a real load became 0; here, a
        # deliberate 0 becomes a load that was never applied. Only a MISSING
        # magnitude defaults to the unit reference.
        raw_mag = ld.get("value_n")
        mag = 1.0 if raw_mag is None else float(raw_mag)
        fx, fy, fz = mag * float(direction[0]), mag * float(direction[1]), mag * float(direction[2])
        if cells and (fx or fy or fz):
            mapping = "occupied_cells" if any(f in interior_mapped for f in fids) else "boundary_layer"
            loads.append({"cells": cells, "fx": fx, "fy": fy, "fz": fz, "mapping": mapping, "from": {
                "target_feature": ld.get("target_feature"), "value_n": mag, "direction": direction}})
            if mapping == "occupied_cells":
                warnings_3d.append(
                    f"load '{ld.get('target_feature')}' acts on a face that cuts through the "
                    "design space rather than bounding it (an inclined or interior surface); "
                    "it is applied to the voxels the face occupies")
        elif not fids:
            diagnostics.append(f"load '{ld.get('target_feature')}' resolved to no faces")

    if not supports or not loads:
        return {
            "status": "needs_user_input",
            "dimension": "3d",
            "reason": "could not map a usable support AND load onto design-space boundary voxels",
            "diagnostics": diagnostics or [
                "no boundary_conditions/loads found in the CAE setup — define fixed supports "
                "and at least one load on design-space boundary faces, then retry"],
            "grid": {"nx": nx, "ny": ny, "nz": nz},
            "frame": frame,
            "design_space_node": solid_node,
            "design_space_bbox": overall,
            "support_count": len(supports), "load_count": len(loads),
            # The default design space is the largest single SOLID, so a load on
            # any other body is outside it by construction. Name the alternatives
            # rather than leaving the caller to guess what it may pass back.
            "design_space_candidates": design_space_options,
        }

    result_guidance, guidance_warnings = collect_topopt_result_guidance(package_path, solid_node)

    return {
        "status": "ok",
        "dimension": "3d",
        "grid": {"nx": nx, "ny": ny, "nz": nz},
        "volume_fraction": volfrac, "volfrac": volfrac,
        "penalty": penalty, "rmin": rmin, "max_iters": max_iters,
        "objective": objective,
        "bcs": {"supports": supports, "loads": loads},
        "design_space_node": solid_node,
        "source_ir_node": solid_node,
        "frame": frame,
        "result_guidance": result_guidance,
        "derivation": {
            "source": "cae_setup+topology_map",
            "bc_source": "cae_setup",
            "result_guidance_source": "cae_result_artifacts" if result_guidance.get("available") else "none",
            "derived": True,
            "dimension": "3d",
            "design_space_bbox": overall,
            "frame": frame,
            "support_count": len(supports), "load_count": len(loads),
            "result_guidance_inputs": result_guidance.get("sources"),
            "design_space_candidates": design_space_options,
            "warnings": diagnostics + warnings_3d + guidance_warnings,
            "limitations": [
                "Full-3D structured-voxel idealization: supports/loads are snapped to the nearest "
                "design-space boundary voxel layer; experimental reference, not production.",
            ],
        },
    }


def run_topology_optimization(problem: dict[str, Any], *, optimizer: str = "simp_2d") -> dict[str, Any]:
    """Run an optimizer and wrap its output in the neutral result contract."""
    requested = str(optimizer or "simp_2d")
    entry = _OPTIMIZER_REGISTRY.get(requested)
    fallback = entry is None
    if entry is None:
        entry = _OPTIMIZER_REGISTRY["simp_2d"]
        requested_name = "simp_2d"
    else:
        requested_name = requested

    # Result-guidance: when enabled and available, map result_guidance into grid fields
    # and hand them to the optimizer (preserve_mask / stiffness_weight_field). Advisory —
    # never alters BCs/material/design-space. Off/absent -> classic behavior unchanged.
    guidance_field: dict[str, Any] | None = None
    rg = problem.get("result_guidance") or {}
    if problem.get("use_result_guidance") and (problem.get("guidance_field") or rg.get("available")):
        guidance_field = problem.get("guidance_field") or build_guidance_field(problem)
        problem = {**problem, "guidance_field": guidance_field}

    opt = entry["fn"](problem)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    threshold = float(problem.get("threshold", 0.5))
    is_3d = "density_3d" in opt
    optimizer_block: dict[str, Any] = {
        "name": requested_name,
        "version": entry["version"],
        "method": entry["method"],
        "dimension": entry["dimension"],
        "requested": requested,
        "fallback": fallback,
    }
    if entry.get("capability"):
        optimizer_block["capability"] = entry["capability"]

    problem_block: dict[str, Any] = {
        "volfrac": opt.get("target_volume_fraction"),
        "penalty": problem.get("penalty", 3.0),
        "rmin": problem.get("rmin", 1.5),
        "bcs_preset": opt.get("bcs_preset"),
        "bcs_source": opt.get("bcs_source", "preset"),
        "design_space_node": problem.get("design_space_node"),
        "source_ir_node": problem.get("source_ir_node") or problem.get("design_space_node"),
        "load_case_id": problem.get("load_case_id"),
        "material": problem.get("material"),
        "constraints": problem.get("constraints"),
        "derivation": problem.get("derivation"),
        "result_guidance": problem.get("result_guidance"),
    }
    frame = problem.get("frame") or (problem.get("derivation") or {}).get("frame")
    result_block: dict[str, Any] = {
        "iterations": opt.get("iterations"),
        "final_compliance": opt.get("final_compliance"),
        "compliance_history": opt.get("compliance_history"),
        "objective_history": opt.get("compliance_history"),
        "achieved_volume_fraction": opt.get("achieved_volume_fraction"),
        "threshold": threshold,
    }

    if is_3d:
        dens3 = opt.get("density_3d") or []
        solid = sum(1 for plane in dens3 for row in plane for val in row if val >= threshold)
        problem_block["grid"] = {"nx": opt.get("nx"), "ny": opt.get("ny"), "nz": opt.get("nz")}
        result_block["solid_voxel_count"] = solid
        result_block["density_grid_3d"] = {
            "nx": opt.get("nx"), "ny": opt.get("ny"), "nz": opt.get("nz"), "values": dens3,
        }
        limitations = [
            "Experimental 3D SIMP: structured voxel grid, linear-elastic, single isotropic material.",
            "Coarse reference optimizer — not production-grade; voxelized result is a design suggestion.",
        ]
    else:
        density = opt.get("density") or []
        solid = sum(1 for row in density for v in row if v >= threshold)
        problem_block["grid"] = {"nelx": opt.get("nelx"), "nely": opt.get("nely")}
        result_block["solid_element_count"] = solid
        result_block["density_grid"] = {
            "nrows": opt.get("nely"), "ncols": opt.get("nelx"), "values": density,
        }
        limitations = [
            "2D plane-stress, linear-elastic, single isotropic material, regular grid.",
            "Coarse, observational design aid — not a production-grade optimizer.",
        ]

    guidance_consumed = bool(opt.get("guidance_consumed"))
    result_block["result_guidance_consumed"] = guidance_consumed
    if guidance_field is not None:
        result_block["guidance_field_summary"] = {
            "cells_preserved": opt.get("guidance_cells_preserved", 0),
            "cells_weighted": opt.get("guidance_cells_weighted", 0),
            "diagnostics": guidance_field.get("diagnostics"),
            "options": guidance_field.get("options"),
            "artifact_path": TOPOPT_GUIDANCE_FIELD_PATH,
        }

    return {
        "format": "aieng.topology_optimization",
        "schema_version": TOPOPT_CONTRACT_VERSION,
        "contract_version": TOPOPT_CONTRACT_VERSION,
        "generated_at_utc": now,
        "dimension": "3d" if is_3d else "2d",
        "optimizer": optimizer_block,
        "objective": str(problem.get("objective") or "compliance_minimization"),
        "frame": frame,
        "problem": problem_block,
        "result": result_block,
        "guidance_field": guidance_field,
        "provenance": {
            "optimizer_name": requested_name,
            "optimizer_version": entry["version"],
            "design_space_node": problem.get("design_space_node"),
            "source_ir_node": problem.get("source_ir_node") or problem.get("design_space_node"),
            "load_case_id": problem.get("load_case_id"),
            "contract_version": TOPOPT_CONTRACT_VERSION,
            "result_guidance_consumed": guidance_consumed,
            "result_guidance_available": bool(rg.get("available")),
            "result_guidance_artifacts": (rg.get("sources") or {}).get("consumed", []),
        },
        "limitations": limitations,
        "warnings": list(opt.get("warnings") or []),
    }


# ── writeback: optimization result → Shape IR representation ─────────────────
# Closes the generative loop. The optimizer emits a density field (analysis-level
# evidence); to make that field a first-class, re-compilable, viewable shape we
# author it back as ONE Shape IR ``density_voxels`` node. The existing compilers
# (build123d / manifold) expand it into extruded voxel geometry, so the optimized
# body flows through the same pipeline as any other Shape IR: compile → mesh/GLB,
# topology, verification, object_registry — and stays linked to its source node.

SHAPE_IR_PATH = "geometry/shape_ir.json"
SMOOTH_MESH_RECONSTRUCTION_PATH = "diagnostics/smooth_mesh_reconstruction.json"
# Method aliases that request a smooth marching-cubes mesh proxy for a 3D result.
_SMOOTH_MESH_METHODS = {"surface", "smooth_mesh", "marching_cubes"}


def _verts_bbox(verts: list[list[float]]) -> list[float] | None:
    if not verts:
        return None
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    zs = [p[2] for p in verts]
    return [round(min(xs), 5), round(min(ys), 5), round(min(zs), 5),
            round(max(xs), 5), round(max(ys), 5), round(max(zs), 5)]


def marching_cubes_surface(
    dens3: list, threshold: float, *,
    origin: list[float], cell_size: list[float],
) -> tuple[list[list[float]], list[list[int]]]:
    """Marching-cubes smooth surface of a 3D density field → (vertices, faces).

    Turns the blocky voxel field into a smooth triangle-mesh proxy (still mesh /
    lossy / not production CAD). Vertices are returned in WORLD coordinates (placed
    in the design-space frame via origin + per-axis cell size); triangle winding is
    flipped to outward-facing so the manifold built from it has positive volume.
    The grid is zero-padded so the surface closes. Returns ``([], [])`` if
    scikit-image is unavailable or no isosurface crosses the threshold (the caller
    can fall back to voxels)."""
    try:
        from skimage import measure  # optional dependency
    except Exception:
        return [], []
    arr = np.asarray(dens3, dtype=float)
    if arr.ndim != 3 or arr.size == 0 or float(arr.max()) < threshold:
        return [], []
    padded = np.pad(arr, 1, mode="constant", constant_values=0.0)
    try:
        v, f, _n, _val = measure.marching_cubes(padded, level=float(threshold))
    except (ValueError, RuntimeError):
        return [], []
    if len(v) == 0 or len(f) == 0:
        return [], []
    ox, oy, oz = float(origin[0]), float(origin[1]), float(origin[2])
    sx, sy, sz = float(cell_size[0]), float(cell_size[1]), float(cell_size[2])
    # padded array axes are (z, y, x); undo the pad and place at voxel-centre scale
    vw = np.column_stack([
        ox + (v[:, 2] - 0.5) * sx,
        oy + (v[:, 1] - 0.5) * sy,
        oz + (v[:, 0] - 0.5) * sz,
    ])
    fi = np.asarray(f, dtype=int)
    # Orient outward: skimage's triangle winding is field/version dependent, so pick
    # the winding that gives a positive signed volume (manifold needs outward normals).
    a, b, c = vw[fi[:, 0]], vw[fi[:, 1]], vw[fi[:, 2]]
    signed = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum())
    if signed < 0:
        fi = fi[:, ::-1]
    verts = [[float(round(x, 5)) for x in row] for row in vw]
    faces = [[int(t[0]), int(t[1]), int(t[2])] for t in fi]
    return verts, faces


def extract_density_contours(
    density: list[list[float]], threshold: float, *,
    origin_u: float, origin_v: float, su: float, sv: float, simplify_tol: float = 0.75,
) -> list[list[list[float]]]:
    """Marching-squares contours of a thresholded density field → in-plane polygons.

    Turns the blocky density grid into smooth boundary loops (the material's
    silhouette + internal holes) instead of axis-aligned voxels. Coordinates are
    returned in world (u, v) — each loop is a closed polygon in the design-space
    plane, aligned with the voxel footprint (cell centres). The grid is zero-padded
    so boundary loops close. Returns ``[]`` if scikit-image is unavailable or no
    contour crosses the threshold (the caller can fall back to voxels).
    """
    try:
        from skimage import measure  # optional dependency
    except Exception:
        return []
    arr = np.asarray(density, dtype=float)
    if arr.ndim != 2 or arr.size == 0:
        return []
    padded = np.pad(arr, 1, mode="constant", constant_values=0.0)
    polys: list[list[list[float]]] = []
    for loop in measure.find_contours(padded, level=float(threshold)):
        if simplify_tol > 0:
            loop = measure.approximate_polygon(loop, tolerance=simplify_tol)
        pts: list[list[float]] = []
        for rr, cc in loop:
            i, j = cc - 1, rr - 1  # undo the pad
            pts.append([round(origin_u + (i + 0.5) * su, 6), round(origin_v + (j + 0.5) * sv, 6)])
        if len(pts) >= 4:  # >=3 distinct + closing vertex
            polys.append(pts)
    return polys


def _inplane_envelope(topo_result: dict[str, Any], polygons: list, u_axis: str, v_axis: str
                      ) -> tuple[float, float, float, float] | None:
    """In-plane (u,v) bounds of the design space — the hard envelope a spline must
    not exceed. Only defined when the result carries a derivation design_space_bbox;
    without a real design space there is no envelope to protect, so returns None
    (and the spline is kept). ``polygons`` is unused but kept for signature symmetry."""
    bb = ((topo_result.get("problem") or {}).get("derivation") or {}).get("design_space_bbox")
    if not (isinstance(bb, list) and len(bb) >= 6):
        return None
    ui = _AXIS_INDEX.get(u_axis, 0)
    vi = _AXIS_INDEX.get(v_axis, 1)
    return (float(bb[ui]), float(bb[vi]), float(bb[ui + 3]), float(bb[vi + 3]))


def _spline_overshoots(polygons: list, env: tuple[float, float, float, float], tol: float = 1e-6) -> bool:
    """True if the periodic spline through any loop would leave the envelope. Uses
    the same Catmull-Rom sampling the mesh backend uses as a proxy for the B-Rep
    spline — both interpolating splines bulge at convex corners the same way."""
    umin, vmin, umax, vmax = env
    for poly in polygons:
        for p in sample_periodic_catmull_rom([[float(x[0]), float(x[1])] for x in poly], subdiv=8):
            if p[0] < umin - tol or p[0] > umax + tol or p[1] < vmin - tol or p[1] > vmax + tol:
                return True
    return False


def topology_result_to_shape_ir(
    topo_result: dict[str, Any], *,
    representation: str = "manifold_mesh",
    cell_size: tuple[float, float] | None = None,
    thickness: float | None = None,
    origin: tuple[float, float, float] | None = None,
    node_id: str | None = None,
    model_id: str | None = None,
    color: list[float] | None = None,
    use_frame: bool = True,
    method: str = "voxels",
    boundary: str = "polygon",
    simplify_tol: float | None = None,
) -> dict[str, Any]:
    """Author an optimization result into a Shape IR payload (one shape node).

    Pure: maps the neutral topology-optimization result → a Shape IR document.
    ``method`` selects the geometry the body becomes:
    - ``"voxels"`` (default): a ``density_voxels`` node — the thresholded grid as a
      union of extruded voxel boxes (blocky, exact, no extra dependency).
    - ``"contour"``: an ``extruded_region`` node — marching-squares boundary loops
      of the thresholded field, extruded; a smoother "design suggestion".
      ``boundary`` then chooses how those loops are interpreted: ``"polygon"``
      (straight segments) or ``"spline"`` (a closed periodic spline through the
      simplified points — a CAD-friendly curve in the B-Rep/NURBS runtime, a
      densified smooth polygon in the mesh runtime). Falls back to voxels (with a
      note) if no contour can be extracted.
    Either way it is a single labelled node; ``design_space_node`` is carried
    through so the optimized body stays linked to the design space it came from.

    Placement: when the result carries a derivation ``frame`` (origin + in-plane
    axes + cell size + thickness from ``derive_topopt_problem_from_package``) and
    ``use_frame`` is set, the voxels are placed in the design space's own
    coordinate frame — so the optimized body lands exactly where the design space
    is, on the correct plane. Explicit ``cell_size``/``thickness``/``origin`` args
    override the frame; without a frame they default to unit cells at the world
    origin (an abstract grid).
    """
    result = topo_result.get("result") or {}
    threshold = float(result.get("threshold", topo_result.get("threshold", 0.5)))
    design_space_node = (
        (topo_result.get("provenance") or {}).get("design_space_node")
        or (topo_result.get("problem") or {}).get("design_space_node")
    )
    nid = node_id or (f"optimized_{design_space_node}" if design_space_node else "optimized_topology")

    # ── 3D writeback: a 3D density field → one mesh node (manifold_mesh runtime) ──
    # method "voxels" = blocky union of solid cells; method "surface" = a smooth
    # marching-cubes proxy of the field (mesh / lossy / not production CAD). B-Rep/
    # NURBS reconstruction is a separate future milestone.
    is_3d = topo_result.get("dimension") == "3d" or "density_grid_3d" in result
    if is_3d:
        grid3 = result.get("density_grid_3d") or {}
        dens3 = grid3.get("values") or topo_result.get("density_3d") or []
        frame3 = (topo_result.get("frame")
                  or ((topo_result.get("problem") or {}).get("derivation") or {}).get("frame") or {})
        fo = frame3.get("origin") or [0.0, 0.0, 0.0]
        o3 = list(origin) if origin is not None else [float(fo[0]), float(fo[1]), float(fo[2])]
        fc = frame3.get("cell_size") or [1.0, 1.0, 1.0]
        if cell_size is not None:
            cs3 = [float(cell_size[0]), float(cell_size[1]),
                   float(cell_size[2] if len(cell_size) > 2 else cell_size[1])]
        else:
            cs3 = [float(fc[0]), float(fc[1] if len(fc) > 1 else fc[0]),
                   float(fc[2] if len(fc) > 2 else fc[0])]
        m3 = str(method or "voxels").lower()
        prov = topo_result.get("provenance") or {}
        problem_blk = topo_result.get("problem") or {}
        source_ir_node = prov.get("source_ir_node") or problem_blk.get("source_ir_node") or design_space_node
        # Preserve optimizer provenance + the analysis context the mesh proxy came from.
        src_opt = {
            "optimizer": (topo_result.get("optimizer") or {}).get("name"),
            "objective": topo_result.get("objective"),
            "design_space_node": design_space_node,
            "source_ir_node": source_ir_node,
            "load_case_id": prov.get("load_case_id") or problem_blk.get("load_case_id"),
            "final_compliance": result.get("final_compliance"),
            "achieved_volume_fraction": result.get("achieved_volume_fraction"),
            "target_volume_fraction": problem_blk.get("volfrac"),
            "threshold": threshold,
            "limitations": list(topo_result.get("limitations") or []),
            "dimension": "3d",
        }
        tags = ["preview", "design_suggestion", "lossy", "not_production_cad"]

        verts: list[list[float]] = []
        faces: list[list[int]] = []
        fallback_reason: str | None = None
        if m3 in _SMOOTH_MESH_METHODS:
            verts, faces = marching_cubes_surface(dens3, threshold, origin=o3, cell_size=cs3)
            if not (verts and faces):
                fallback_reason = "marching cubes produced no isosurface (empty field or scikit-image missing)"
                m3 = "voxels"   # honest fallback to blocky voxels
                src_opt["surface_fallback"] = fallback_reason

        # Honesty flags shared by every 3D topo-opt body (mesh preview, never B-Rep/CAD).
        honesty = {
            "representation_kind": "mesh", "geometry_kind": "mesh",
            "lossy": True, "preview_only": True, "cad_editable": False, "not_production_cad": True,
        }
        if m3 in _SMOOTH_MESH_METHODS:
            node3: dict[str, Any] = {
                "id": nid, "label": nid, "type": "smooth_mesh_proxy", "dimension": 3,
                "vertices": verts, "faces": faces, "origin": o3, "cell_size": cs3,
                "u_axis": str(frame3.get("u_axis", "x")), "v_axis": str(frame3.get("v_axis", "y")),
                "w_axis": str(frame3.get("w_axis", "z")), "placed_in_frame": bool(frame3),
                "iso_value": threshold, "smoothing": "marching_cubes",
                "vertex_count": len(verts), "triangle_count": len(faces),
                "bbox": _verts_bbox(verts),
                "tags": tags + ["smooth_mesh_proxy", "marching_cubes"],
                **honesty,
                "source_optimization": src_opt,
            }
            evidence = "smooth_mesh_preview"
        else:
            node3 = {
                "id": nid, "label": nid, "type": "density_voxels", "dimension": 3,
                "density_3d": dens3, "threshold": threshold, "cell_size": cs3, "origin": o3,
                "u_axis": str(frame3.get("u_axis", "x")), "v_axis": str(frame3.get("v_axis", "y")),
                "w_axis": str(frame3.get("w_axis", "z")), "placed_in_frame": bool(frame3),
                "tags": tags + ["voxelized"],
                **honesty,
                "source_optimization": src_opt,
            }
            evidence = "voxelized_preview"
        if color is not None:
            node3["color"] = list(color)
        return {
            "format": "aieng.shape_ir",
            "representation": representation or "manifold_mesh",
            "model_id": model_id or nid,
            "parts": [node3],
            "provenance": {
                "from_topology_optimization": True,
                "dimension": "3d",
                "optimizer": (topo_result.get("optimizer") or {}).get("name"),
                "design_space_node": design_space_node,
                "source_ir_node": source_ir_node,
                "load_case_id": src_opt.get("load_case_id"),
                "topopt_contract_version": topo_result.get("contract_version"),
                "evidence": evidence,
                "preview_only": True, "cad_editable": False,
            },
        }

    grid = result.get("density_grid") or {}
    density = grid.get("values") or topo_result.get("density") or []

    frame = ((topo_result.get("problem") or {}).get("derivation") or {}).get("frame") or {}
    use_frame = use_frame and bool(frame)
    f_cell = frame.get("cell_size") or [1.0, 1.0]
    u_axis = str(frame.get("u_axis", "x")) if use_frame else "x"
    v_axis = str(frame.get("v_axis", "y")) if use_frame else "y"

    if cell_size is not None:
        sx, sy = float(cell_size[0]), float(cell_size[1])
    elif use_frame:
        sx, sy = float(f_cell[0]), float(f_cell[1] if len(f_cell) > 1 else f_cell[0])
    else:
        sx, sy = 1.0, 1.0

    if thickness is not None:
        sz = float(thickness)
    elif use_frame:
        sz = float(frame.get("thickness", max(sx, sy)))
    else:
        sz = max(sx, sy)

    if origin is not None:
        o = [float(origin[0]), float(origin[1]), float(origin[2])]
    elif use_frame:
        fo = frame.get("origin") or [0.0, 0.0, 0.0]
        o = [float(fo[0]), float(fo[1]), float(fo[2])]
    else:
        o = [0.0, 0.0, 0.0]

    source_optimization = {
        "optimizer": (topo_result.get("optimizer") or {}).get("name"),
        "objective": topo_result.get("objective"),
        "design_space_node": design_space_node,
        "final_compliance": result.get("final_compliance"),
        "achieved_volume_fraction": result.get("achieved_volume_fraction"),
    }

    method = str(method or "voxels").lower()
    boundary = "spline" if str(boundary).lower() == "spline" else "polygon"
    # A spline through every marching-squares vertex would wiggle; simplify more
    # aggressively so the curve has a few well-placed through-points.
    if simplify_tol is None:
        simplify_tol = 1.5 if boundary == "spline" else 0.75
    polygons: list[list[list[float]]] = []
    if method == "contour":
        ui = _AXIS_INDEX.get(u_axis, 0)
        vi = _AXIS_INDEX.get(v_axis, 1)
        polygons = extract_density_contours(
            density, threshold, origin_u=o[ui], origin_v=o[vi], su=sx, sv=sy, simplify_tol=simplify_tol)
        if not polygons:
            method = "voxels"  # honest fallback when no contour could be extracted
            source_optimization["contour_fallback"] = "no contour extracted (empty field or skimage missing)"
        elif boundary == "spline":
            # Geometry safety: a periodic spline can bulge past the design-space
            # envelope. If it would, fall back to the polygon (which stays within its
            # own points) rather than emitting a body that pokes outside the envelope.
            env = _inplane_envelope(topo_result, polygons, u_axis, v_axis)
            if env is not None and _spline_overshoots(polygons, env):
                boundary = "polygon"
                source_optimization["spline_fallback"] = (
                    "periodic spline would overshoot the design-space envelope; "
                    "using polygon boundary for geometry safety")

    if method == "contour":
        node: dict[str, Any] = {
            "id": nid, "label": nid, "type": "extruded_region",
            "polygons": polygons, "boundary": boundary, "thickness": sz, "origin": o,
            "u_axis": u_axis, "v_axis": v_axis, "placed_in_frame": use_frame,
            "source_optimization": source_optimization,
        }
    else:
        node = {
            "id": nid, "label": nid, "type": "density_voxels",
            "density": density, "threshold": threshold,
            "cell_size": [sx, sy], "thickness": sz, "origin": o,
            "u_axis": u_axis, "v_axis": v_axis, "placed_in_frame": use_frame,
            "source_optimization": source_optimization,
        }
    if color is not None:
        node["color"] = list(color)

    return {
        "format": "aieng.shape_ir",
        "representation": representation,
        "model_id": model_id or nid,
        "parts": [node],
        "provenance": {
            "from_topology_optimization": True,
            "optimizer": (topo_result.get("optimizer") or {}).get("name"),
            "design_space_node": design_space_node,
            "topopt_contract_version": topo_result.get("contract_version"),
        },
    }


def write_shape_ir_from_topology_optimization(
    package_path: str | Path, *,
    representation: str = "manifold_mesh",
    cell_size: tuple[float, float] | None = None,
    thickness: float | None = None,
    origin: tuple[float, float, float] | None = None,
    node_id: str | None = None,
    color: list[float] | None = None,
    use_frame: bool = True,
    method: str = "voxels",
    boundary: str = "polygon",
    simplify_tol: float | None = None,
) -> dict[str, Any]:
    """Read a package's topology-optimization result and (re)write geometry/shape_ir.json.

    Reads ``analysis/topology_optimization.json``, authors the Shape IR document,
    and writes it as ``geometry/shape_ir.json`` (replacing any existing member).
    ``method`` chooses ``"voxels"`` (blocky union) or ``"contour"`` (marching-squares
    boundary, extruded); for a contour, ``boundary`` chooses ``"polygon"`` or
    ``"spline"`` (smooth periodic curve). By default (``use_frame``) the optimized
    body is placed in the design space's own coordinate frame when the result
    carries a derivation frame. Does NOT compile — the caller (workbench) recompiles
    via the representation's runtime to produce mesh/GLB + topology + verification +
    registry. Returns the Shape IR payload.
    """
    package_path = Path(package_path)
    with zipfile.ZipFile(package_path, "r") as zf:
        if TOPOLOGY_OPTIMIZATION_PATH not in zf.namelist():
            raise FileNotFoundError(
                f"{TOPOLOGY_OPTIMIZATION_PATH} not in package — run topology optimization first"
            )
        topo_result = json.loads(zf.read(TOPOLOGY_OPTIMIZATION_PATH))

    payload = topology_result_to_shape_ir(
        topo_result, representation=representation, cell_size=cell_size,
        thickness=thickness, origin=origin, node_id=node_id, color=color, use_frame=use_frame,
        method=method, boundary=boundary, simplify_tol=simplify_tol,
    )
    members = {SHAPE_IR_PATH: (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()}
    # Neutral mesh export (#149/#204): a smooth-mesh result ships a standalone OBJ
    # so downstream mesh→NURBS tools (AMRTO/PYTOCAD) can consume it directly —
    # reconstructed/lossy mesh, not production CAD.
    from .mesh_obj_export import TOPOLOGY_RESULT_MESH_OBJ_PATH, topology_result_mesh_obj

    _obj_text = topology_result_mesh_obj(payload)
    if _obj_text is not None:
        members[TOPOLOGY_RESULT_MESH_OBJ_PATH] = _obj_text.encode("utf-8")
    # When a smooth (marching-cubes) mesh was requested, write honest reconstruction
    # diagnostics (iso value, grid shape, vertex/face counts, bbox, frame placement,
    # fallback reason if it degraded to voxels).
    if str(method).lower() in _SMOOTH_MESH_METHODS:
        diag = _smooth_mesh_reconstruction_diagnostics(payload, topo_result)
        members[SMOOTH_MESH_RECONSTRUCTION_PATH] = (json.dumps(diag, indent=2, sort_keys=True) + "\n").encode()
    _replace_members(package_path, members)
    return payload


def _smooth_mesh_reconstruction_diagnostics(payload: dict[str, Any], topo_result: dict[str, Any]) -> dict[str, Any]:
    """Honest diagnostics for the 3D smooth-mesh reconstruction (mesh preview, not CAD)."""
    node = (payload.get("parts") or [{}])[0]
    result = topo_result.get("result") or {}
    grid3 = result.get("density_grid_3d") or {}
    is_smooth = node.get("type") == "smooth_mesh_proxy"
    src_opt = node.get("source_optimization") or {}
    return {
        "format": "aieng.smooth_mesh_reconstruction",
        "schema_version": "0.1",
        "method": "marching_cubes",
        "succeeded": is_smooth,
        "fallback": None if is_smooth else "density_voxels",
        "fallback_reason": src_opt.get("surface_fallback"),
        "iso_value": result.get("threshold"),
        "input_grid_shape": {"nx": grid3.get("nx"), "ny": grid3.get("ny"), "nz": grid3.get("nz")},
        "vertex_count": node.get("vertex_count", 0),
        "face_count": node.get("triangle_count", 0),
        "bbox": node.get("bbox"),
        "frame_placement_applied": bool(node.get("placed_in_frame")),
        "representation_kind": "mesh",
        "geometry_kind": "mesh",
        "preview_only": True,
        "cad_editable": False,
        "not_production_cad": True,
        "design_space_node": node.get("source_optimization", {}).get("design_space_node"),
        "source_ir_node": node.get("source_optimization", {}).get("source_ir_node"),
        "load_case_id": src_opt.get("load_case_id"),
        "optimizer": src_opt.get("optimizer"),
        "limitations": src_opt.get("limitations") or topo_result.get("limitations") or [],
    }


def write_topology_optimization(
    package_path: str | Path, problem: dict[str, Any], *, optimizer: str = "simp_2d",
) -> dict[str, Any]:
    """Run optimization and write analysis/topology_optimization.json into a package.

    When result-guidance was consumed, the (bulky) guidance grid fields are written to
    a separate analysis/topology_optimization_guidance_field.json; the main result keeps
    only a compact guidance_field_summary."""
    result = run_topology_optimization(problem, optimizer=optimizer)
    package_path = Path(package_path)
    if not package_path.exists():
        return result
    # Split the bulky guidance field out into its own artifact; keep the main result lean.
    guidance_field = result.pop("guidance_field", None)
    members: dict[str, bytes] = {
        TOPOLOGY_OPTIMIZATION_PATH: (json.dumps(result, indent=2, sort_keys=True) + "\n").encode(),
    }
    if guidance_field is not None:
        members[TOPOPT_GUIDANCE_FIELD_PATH] = (
            json.dumps(guidance_field, indent=2, sort_keys=True) + "\n").encode()
    _replace_members(package_path, members)
    return result
