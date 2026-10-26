"""Reference implementations the vectorized SIMP solvers are checked against.

Not collected by pytest (no ``test_`` prefix). Three independent references:

* ``legacy_simp_2d`` / ``legacy_simp_3d`` -- the dense, loop-based solvers that
  ``aieng.converters.topology_optimization`` shipped before the sparse rewrite,
  copied verbatim (only renamed). Same algorithm as the new code when it is run
  with ``emin=0, xmin=1e-3`` (classic SIMP with a density floor), so the two
  must agree to round-off.
* ``top88`` -- a line-by-line Python port of ``top88.m`` from Andreassen,
  Clausen, Schevenels, Lazarov & Sigmund (2011), "Efficient topology
  optimization in MATLAB using 88 lines of code", Struct. Multidisc. Optim.
  43:1-16 (code as distributed by DTU TopOpt group). Half-MBB beam, modified
  SIMP (E0=1, Emin=1e-9), ft=1 sensitivity / ft=2 density filter, OC update.
  Kept deliberately dense-free but un-optimized so it stays readable against
  the MATLAB original; it builds KE from the paper's A11/A12/B11/B12 blocks,
  not from the module's helper, so it is an independent check of KE too.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

from aieng.converters.topology_optimization import (
    _element_stiffness,
    _grid_guidance_2d,
    _grid_guidance_3d,
    _hex8_stiffness,
    _hex_corner_nodes,
    _resolve_bcs,
    _resolve_bcs_3d,
)


# ── verbatim copy of the pre-rewrite dense 2D solver ─────────────────────────

def legacy_simp_2d(problem: dict[str, Any]) -> dict[str, Any]:
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
    free = np.setdiff1d(np.arange(ndof), fixed)

    preserve_mask, weight_field, preserve_min = _grid_guidance_2d(problem, nely, nelx)

    edof = np.zeros((nelx * nely, 8), dtype=int)
    for elx in range(nelx):
        for ely in range(nely):
            n1 = (nely + 1) * elx + ely
            n2 = (nely + 1) * (elx + 1) + ely
            e = elx * nely + ely
            edof[e] = [2 * n1, 2 * n1 + 1, 2 * n2, 2 * n2 + 1, 2 * n2 + 2, 2 * n2 + 3, 2 * n1 + 2, 2 * n1 + 3]

    x = np.full((nely, nelx), volfrac)
    ceil_r = int(np.ceil(rmin))
    history: list[float] = []
    change = 1.0
    it = 0
    while change > 0.01 and it < max_iters:
        it += 1
        K = np.zeros((ndof, ndof))
        xpen = x ** penal
        for elx in range(nelx):
            for ely in range(nely):
                e = elx * nely + ely
                ed = edof[e]
                K[np.ix_(ed, ed)] += xpen[ely, elx] * KE
        U = np.zeros(ndof)
        U[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])

        c = 0.0
        dc = np.zeros((nely, nelx))
        for elx in range(nelx):
            for ely in range(nely):
                e = elx * nely + ely
                ue = U[edof[e]]
                ke_energy = float(ue @ KE @ ue)
                c += xpen[ely, elx] * ke_energy
                dc[ely, elx] = -penal * (x[ely, elx] ** (penal - 1)) * ke_energy
        history.append(c)

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

        dc_eff = dcf if weight_field is None else dcf * weight_field
        lower = 0.001 if preserve_mask is None else np.where(preserve_mask, preserve_min, 0.001)

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

    return {"x": x, "iterations": it, "compliance_history": history, "final_change": change}


# ── verbatim copy of the pre-rewrite dense 3D solver ─────────────────────────

def _legacy_sensitivity_filter_3d(nx, ny, nz, rmin, x, dc):
    out = np.zeros_like(dc)
    r = int(np.ceil(rmin))

    def eid(i, j, k):
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


def legacy_simp_3d(problem: dict[str, Any]) -> dict[str, Any]:
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

    def eid(i, j, k):
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
    preserve_mask, weight_field, preserve_min = _grid_guidance_3d(problem, nx, ny, nz)

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
        U[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
        c = 0.0
        dc = np.zeros(nele)
        for e in range(nele):
            ue = U[edof[e]]
            ce = float(ue @ KE @ ue)
            c += xpen[e] * ce
            dc[e] = -penal * (x[e] ** (penal - 1)) * ce
        history.append(c)
        dc = _legacy_sensitivity_filter_3d(nx, ny, nz, rmin, x, dc)
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

    return {"x": x, "iterations": it, "compliance_history": history, "final_change": change}


# ── top88 (Andreassen et al. 2011), half-MBB beam ────────────────────────────

def top88(nelx: int, nely: int, volfrac: float, penal: float, rmin: float, ft: int,
          max_loop: int = 10_000) -> dict[str, Any]:
    E0, Emin, nu = 1.0, 1e-9, 0.3
    A11 = np.array([[12, 3, -6, -3], [3, 12, 3, 0], [-6, 3, 12, -3], [-3, 0, -3, 12]], float)
    A12 = np.array([[-6, -3, 0, 3], [-3, -6, -3, -6], [0, -3, -6, 3], [3, -6, 3, -6]], float)
    B11 = np.array([[-4, 3, -2, 9], [3, -4, -9, 4], [-2, -9, -4, -3], [9, 4, -3, -4]], float)
    B12 = np.array([[2, -3, 4, -9], [-3, 2, 9, -2], [4, 9, 2, 3], [-9, -2, 3, 2]], float)
    KE = 1 / (1 - nu ** 2) / 24 * (np.block([[A11, A12], [A12.T, A11]])
                                   + nu * np.block([[B11, B12], [B12.T, B11]]))
    # MATLAB 1-based: nodenrs = reshape(1:(1+nelx)*(1+nely),1+nely,1+nelx)
    nodenrs = np.arange(1, (1 + nelx) * (1 + nely) + 1).reshape((1 + nely, 1 + nelx), order="F")
    edofVec = (2 * nodenrs[:-1, :-1] + 1).reshape(nelx * nely, order="F")
    edofMat = edofVec[:, None] + np.array([0, 1, 2 * nely + 2, 2 * nely + 3, 2 * nely + 0,
                                           2 * nely + 1, -2, -1])[None, :]
    edofMat = edofMat - 1  # to 0-based dofs
    iK = np.kron(edofMat, np.ones((8, 1), dtype=int)).ravel()
    jK = np.kron(edofMat, np.ones((1, 8), dtype=int)).ravel()
    ndof = 2 * (nely + 1) * (nelx + 1)
    F = np.zeros(ndof)
    F[2 - 1] = -1.0
    U = np.zeros(ndof)
    fixeddofs = np.union1d(np.arange(1, 2 * (nely + 1) + 1, 2), [2 * (nelx + 1) * (nely + 1)]) - 1
    freedofs = np.setdiff1d(np.arange(ndof), fixeddofs)
    # PREPARE FILTER (loops kept as in the original)
    iH, jH, sH = [], [], []
    rr = int(np.ceil(rmin)) - 1
    for i1 in range(1, nelx + 1):
        for j1 in range(1, nely + 1):
            e1 = (i1 - 1) * nely + j1
            for i2 in range(max(i1 - rr, 1), min(i1 + rr, nelx) + 1):
                for j2 in range(max(j1 - rr, 1), min(j1 + rr, nely) + 1):
                    e2 = (i2 - 1) * nely + j2
                    iH.append(e1 - 1)
                    jH.append(e2 - 1)
                    sH.append(max(0.0, rmin - np.sqrt((i1 - i2) ** 2 + (j1 - j2) ** 2)))
    H = coo_matrix((sH, (iH, jH)), shape=(nelx * nely, nelx * nely)).tocsr()
    Hs = np.asarray(H.sum(axis=1)).ravel()
    # x(:) in MATLAB is column-major over (nely, nelx): flat vectors below are that order.
    x = np.full(nelx * nely, volfrac)
    xPhys = x.copy()
    loop = 0
    change = 1.0
    history: list[float] = []
    while change > 0.01 and loop < max_loop:
        loop += 1
        # sK = reshape(KE(:)*(Emin+xPhys(:)'.^penal*(E0-Emin)),64*nelx*nely,1)
        sK = (KE.ravel(order="F")[:, None] * (Emin + xPhys ** penal * (E0 - Emin))[None, :]).ravel(order="F")
        K = coo_matrix((sK, (iK, jK)), shape=(ndof, ndof)).tocsc()
        K = (K + K.T) / 2
        U[freedofs] = spsolve(K[freedofs, :][:, freedofs], F[freedofs])
        Ue = U[edofMat]
        ce = np.sum((Ue @ KE) * Ue, axis=1)
        c = float(np.sum((Emin + xPhys ** penal * (E0 - Emin)) * ce))
        history.append(c)
        dc = -penal * (E0 - Emin) * xPhys ** (penal - 1) * ce
        dv = np.ones(nelx * nely)
        if ft == 1:
            dc = H @ (x * dc) / Hs / np.maximum(1e-3, x)
        elif ft == 2:
            dc = H @ (dc / Hs)
            dv = H @ (dv / Hs)
        l1, l2, move = 0.0, 1e9, 0.2
        while (l2 - l1) / (l1 + l2) > 1e-3:
            lmid = 0.5 * (l2 + l1)
            xnew = np.maximum(0.0, np.maximum(x - move, np.minimum(1.0, np.minimum(
                x + move, x * np.sqrt(-dc / dv / lmid)))))
            if ft == 1:
                xPhys = xnew
            elif ft == 2:
                xPhys = (H @ xnew) / Hs
            if xPhys.sum() > volfrac * nelx * nely:
                l1 = lmid
            else:
                l2 = lmid
        change = float(np.max(np.abs(xnew - x)))
        x = xnew
    return {
        "xPhys": xPhys.reshape((nely, nelx), order="F"),
        "iterations": loop,
        "compliance_history": history,
        "final_change": change,
    }
