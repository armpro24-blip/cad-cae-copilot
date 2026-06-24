"""Simulation trigger: Gmsh mesh + CalculiX solve from AI preprocessing output.

Reads simulation/setup.yaml and simulation/cae_mapping.json from the .aieng package,
meshes the STEP geometry with Gmsh, generates a CalculiX input deck, runs the solver,
parses FRD results, and writes everything back atomically.

Graceful degradation: if Gmsh or CalculiX are not installed, returns
{"status": "tools_unavailable", "missing_tools": [...]} without raising.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import yaml

from .config import ensure_aieng_on_path
from .project_io import rebind_cae_faces, validate_cae_topology_references
from fastapi import HTTPException


# ── Tool availability ─────────────────────────────────────────────────────────

def _find_ccx() -> str | None:
    for candidate in ("ccx", "ccx_linux", "ccx2.21", "ccx_static"):
        cmd = shutil.which(candidate)
        if cmd:
            return cmd
    return None


def _gmsh_available() -> bool:
    try:
        import gmsh  # noqa: F401
        return True
    except ImportError:
        return False


def check_simulation_tools() -> dict[str, Any]:
    """Return availability status of Gmsh and CalculiX."""
    ccx = _find_ccx()
    gmsh_ok = _gmsh_available()
    missing = [t for t, ok in [("gmsh", gmsh_ok), ("ccx", ccx is not None)] if not ok]
    return {
        "gmsh": gmsh_ok,
        "calculix": ccx is not None,
        "calculix_cmd": ccx,
        "ready": len(missing) == 0,
        "missing": missing,
    }


# ── Package I/O helpers ───────────────────────────────────────────────────────

def _read_member(package_path: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            return zf.read(member)
    except Exception:
        pass
    return None


def _extract_step(package_path: Path, work_dir: Path) -> Path | None:
    """Extract the geometry STEP file to work_dir. Returns path or None."""
    for candidate in ("geometry/generated.step", "geometry/model.step", "geometry/part.step"):
        raw = _read_member(package_path, candidate)
        if raw:
            out = work_dir / "model.step"
            out.write_bytes(raw)
            return out
    return None


def _write_results_to_package(
    package_path: Path,
    solver_log: str,
    frd_bytes: bytes | None,
    summary: dict[str, Any],
    mesh_inp_bytes: bytes | None = None,
) -> None:
    """Atomically write solver_log, optional FRD/mesh, and results_summary into the package."""
    files: dict[str, bytes] = {
        "simulation/solver_log.txt": solver_log.encode(),
        "simulation/results_summary.json": json.dumps(summary, indent=2, ensure_ascii=False).encode(),
    }
    if frd_bytes:
        files["simulation/result.frd"] = frd_bytes
    if mesh_inp_bytes:
        files["simulation/mesh.inp"] = mesh_inp_bytes

    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with zipfile.ZipFile(package_path, "r") as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename not in files:
                    dst.writestr(item, src.read(item.filename))
            for archive_path, content_bytes in files.items():
                dst.writestr(archive_path, content_bytes)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Gmsh meshing ──────────────────────────────────────────────────────────────

class _suppress_offthread_signal:
    """Let gmsh run in a worker thread.

    ``gmsh.initialize()`` installs a SIGINT handler via ``signal.signal()``,
    which raises ``ValueError: signal only works in main thread of the main
    interpreter`` when gmsh is called off the main thread — e.g. from FastAPI's
    sync-endpoint threadpool or the MCP runtime. Temporarily no-op
    ``signal.signal`` (only when not on the main thread) so meshing succeeds;
    losing Ctrl-C interruptibility of the mesher is an acceptable trade for a
    background mesh.
    """

    def __enter__(self) -> "_suppress_offthread_signal":
        import signal
        import threading

        self._signal = signal
        self._orig = signal.signal
        if threading.current_thread() is not threading.main_thread():
            signal.signal = lambda *_args, **_kwargs: None  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._signal.signal = self._orig


def _mesh_with_gmsh(step_path: Path, work_dir: Path, mesh_size_mm: float) -> Path:
    """Mesh the STEP file with Gmsh and export a CalculiX-format .inp mesh."""
    import gmsh

    out_inp = work_dir / "mesh.inp"
    with _suppress_offthread_signal():
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("model")
        gmsh.merge(str(step_path))
        gmsh.model.occ.synchronize()

        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", mesh_size_mm)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", mesh_size_mm / 4.0)
        gmsh.option.setNumber("Mesh.Algorithm3D", 4)  # Frontal-Delaunay

        volumes = gmsh.model.getEntities(3)
        if not volumes:
            raise RuntimeError("No 3D volumes found in STEP — cannot mesh")
        gmsh.model.addPhysicalGroup(3, [v[1] for v in volumes], tag=1, name="EALL")

        # Surface physical groups needed for NSET construction later
        surfaces = gmsh.model.getEntities(2)
        for surf_dim, surf_tag in surfaces:
            gmsh.model.addPhysicalGroup(2, [surf_tag], tag=1000 + surf_tag, name=f"SURF{surf_tag}")

        gmsh.model.mesh.generate(3)
        gmsh.write(str(out_inp))
    finally:
        gmsh.finalize()

    return out_inp


# ── Node parsing ──────────────────────────────────────────────────────────────

def _parse_inp_nodes(inp_path: Path) -> dict[int, tuple[float, float, float]]:
    """Parse node coordinates from a Gmsh-generated CalculiX .inp file."""
    nodes: dict[int, tuple[float, float, float]] = {}
    in_node_section = False
    for line in inp_path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("*NODE"):
            in_node_section = True
            continue
        if stripped.startswith("*"):
            in_node_section = False
        if in_node_section and stripped and not stripped.startswith("**"):
            parts = [p.strip() for p in stripped.split(",")]
            if len(parts) >= 4:
                try:
                    nid = int(parts[0])
                    nodes[nid] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    pass
    return nodes


# ── Mesh preview: surface wireframe + element stats ───────────────────────────

# Corner-node face indices for common CalculiX 3D solid elements.
# Only the corner nodes are used for wireframe extraction; mid-side nodes
# (quadratic elements) are ignored so the overlay stays lightweight.
_ELEMENT_FACE_CORNERS: dict[str, tuple[tuple[int, ...], ...]] = {
    "C3D4": ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)),
    "C3D10": ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)),
    "C3D8": ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)),
    "C3D8R": ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)),
    "C3D8I": ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)),
    "C3D20": ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)),
    "C3D6": ((0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)),
    "C3D15": ((0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)),
    "C3D5": ((0, 1, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)),
    "C3D13": ((0, 1, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)),
}


def _element_corner_count(element_type: str) -> int | None:
    """Return the number of corner nodes for a supported element type, or None."""
    mapping: dict[str, int] = {
        "C3D4": 4, "C3D10": 4,
        "C3D8": 8, "C3D8R": 8, "C3D8I": 8, "C3D20": 8,
        "C3D6": 6, "C3D15": 6,
        "C3D5": 5, "C3D13": 5,
    }
    return mapping.get(element_type.upper())


def _parse_inp_elements(inp_path: Path) -> list[dict[str, Any]]:
    """Parse solid-element connectivity from a Gmsh-generated CalculiX .inp file.

    Returns a list of ``{id, type, nodes}`` dicts.  Element types that are not
    3D solids (e.g. *SHELL, *BEAM) are skipped.
    """
    elements: list[dict[str, Any]] = []
    current_type: str | None = None
    for line in inp_path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("*ELEMENT"):
            current_type = None
            for token in stripped.split(","):
                key_value = token.strip().split("=", 1)
                if len(key_value) == 2 and key_value[0].strip().upper() == "TYPE":
                    current_type = key_value[1].strip().upper()
            continue
        if stripped.startswith("*"):
            current_type = None
            continue
        if current_type and stripped and not stripped.startswith("**"):
            parts = [p.strip() for p in stripped.split(",")]
            if len(parts) < 2:
                continue
            corner_count = _element_corner_count(current_type)
            if corner_count is None:
                continue
            try:
                eid = int(parts[0])
                # Take only the corner-node columns; drop mid-side nodes.
                node_ids = [int(p) for p in parts[1 : 1 + corner_count]]
                if len(node_ids) == corner_count:
                    elements.append({"id": eid, "type": current_type, "nodes": node_ids})
            except ValueError:
                pass
    return elements


def _extract_surface_wireframe(
    nodes: dict[int, tuple[float, float, float]],
    elements: list[dict[str, Any]],
) -> tuple[list[list[float]], list[list[int]]]:
    """Extract the surface wireframe of a solid mesh.

    Returns ``(coords, edges)`` where ``coords`` are ``[x, y, z]`` surface node
    coordinates in model frame (mm) and ``edges`` are zero-based ``[i, j]`` pairs.
    """
    if not nodes or not elements:
        return [], []

    face_counts: dict[tuple[int, ...], int] = {}
    for element in elements:
        element_type = element["type"].upper()
        face_defs = _ELEMENT_FACE_CORNERS.get(element_type)
        if not face_defs:
            continue
        corner_ids = element["nodes"]
        for face in face_defs:
            face_node_ids = tuple(sorted(corner_ids[i] for i in face))
            face_counts[face_node_ids] = face_counts.get(face_node_ids, 0) + 1

    surface_faces = [face for face, count in face_counts.items() if count == 1]

    edge_set: set[tuple[int, int]] = set()
    for face in surface_faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            if a == b:
                continue
            edge_set.add((a, b) if a < b else (b, a))

    referenced_ids = {nid for edge in edge_set for nid in edge}
    if not referenced_ids:
        return [], []

    compact_map: dict[int, int] = {}
    coords: list[list[float]] = []
    for nid in sorted(referenced_ids):
        compact_map[nid] = len(coords)
        x, y, z = nodes[nid]
        coords.append([x, y, z])

    edges = [[compact_map[a], compact_map[b]] for a, b in sorted(edge_set)]
    return coords, edges


def _read_mesh_target_size_mm(package_path: Path) -> float | None:
    """Return the configured mesh target size from setup.yaml or results summary."""
    setup_raw = _read_member(package_path, "simulation/setup.yaml")
    if setup_raw:
        try:
            setup = yaml.safe_load(setup_raw)
            size = (setup.get("mesh") or {}).get("target_size_mm")
            if size is not None:
                return float(size)
        except Exception:
            pass
    results_raw = _read_member(package_path, "simulation/results_summary.json")
    if results_raw:
        try:
            summary = json.loads(results_raw)
            size = summary.get("mesh_size_mm")
            if size is not None:
                return float(size)
        except Exception:
            pass
    return None


def get_mesh_preview(package_path: Path) -> dict[str, Any]:
    """Read ``simulation/mesh.inp`` and return surface wireframe + element stats.

    The result is intentionally compact: a list of surface vertex coordinates and
    zero-based edge index pairs.  When ``mesh.inp`` is absent the response is
    ``{"available": False}`` so the UI can degrade cleanly.
    """
    package_path = Path(package_path)
    inp_bytes = _read_member(package_path, "simulation/mesh.inp")
    if not inp_bytes:
        return {"available": False}

    with tempfile.TemporaryDirectory(prefix="aieng_mesh_preview_") as tmp_str:
        work = Path(tmp_str)
        inp_path = work / "mesh.inp"
        inp_path.write_bytes(inp_bytes)

        nodes = _parse_inp_nodes(inp_path)
        elements = _parse_inp_elements(inp_path)

    if not elements:
        return {
            "available": True,
            "node_count": len(nodes),
            "element_count": 0,
            "element_type": None,
            "target_size_mm": _read_mesh_target_size_mm(package_path),
            "nodes": [],
            "edges": [],
            "quality": {"coarse_flag": False},
        }

    coords, edges = _extract_surface_wireframe(nodes, elements)
    element_type = elements[0]["type"]
    node_count = len(nodes)
    element_count = len(elements)
    target_size_mm = _read_mesh_target_size_mm(package_path)

    # Simple coarse-quality heuristic: fewer than 100 solid elements is treated
    # as a sanity-check warning rather than a rigorous mesh-quality verdict.
    coarse_flag = element_count < 100

    return {
        "available": True,
        "node_count": node_count,
        "element_count": element_count,
        "element_type": element_type,
        "target_size_mm": target_size_mm,
        "nodes": coords,
        "edges": edges,
        "quality": {
            "coarse_flag": coarse_flag,
            "note": "Mesh appears coarse (< 100 elements)" if coarse_flag else None,
        },
    }


# ── Mesh quality diagnostics (#279) ───────────────────────────────────────────

_TET_TYPES = ("C3D4", "C3D10")
# A tet is degenerate (flat/collapsed) when its volume is negligible relative to
# its edge length; "poor" when it is a sliver or highly stretched. Thresholds are
# scale-invariant (volume normalized by mean-edge cubed; aspect = max/min edge).
_DEGENERATE_VOL_RATIO = 1e-4
_POOR_VOL_RATIO = 0.02
_POOR_ASPECT_RATIO = 10.0


def _tet_aspect_and_volume_ratio(
    pts: list[tuple[float, float, float]],
) -> tuple[float, float]:
    """Return (edge aspect ratio, normalized volume ratio) for a 4-node tet.

    For a tetrahedron all six corner-pair distances are edges, so
    ``max_edge / min_edge`` is a clean aspect metric. The volume ratio
    ``vol / mean_edge**3`` is ~0.117 for a regular tet and → 0 for a flat one.
    """
    import itertools
    import math

    edges = [
        math.dist(pts[i], pts[j]) for i, j in itertools.combinations(range(4), 2)
    ]
    min_edge, max_edge = min(edges), max(edges)
    aspect = (max_edge / min_edge) if min_edge > 0 else float("inf")

    (ax, ay, az), (bx, by, bz), (cx, cy, cz), (dx, dy, dz) = pts
    u = (bx - ax, by - ay, bz - az)
    v = (cx - ax, cy - ay, cz - az)
    w = (dx - ax, dy - ay, dz - az)
    cross = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    vol = abs(cross[0] * w[0] + cross[1] * w[1] + cross[2] * w[2]) / 6.0
    mean_edge = sum(edges) / 6.0
    vol_ratio = (vol / (mean_edge ** 3)) if mean_edge > 0 else 0.0
    return aspect, vol_ratio


def compute_mesh_quality(
    nodes: dict[int, tuple[float, float, float]],
    elements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic element-quality verdict for a solid mesh (#279).

    Scoped to tetrahedral elements (C3D4/C3D10 — the dominant Gmsh → CalculiX
    output); other element types are counted and reported as ``quality_not_computed``.
    Honest boundary: this is an edge-ratio + normalized-volume heuristic, not a
    Jacobian/shape-function quality measure, and it does not validate surface-set
    coverage. ``verdict`` is ``fail`` (degenerate or broken elements present),
    ``warning`` (slivers / high aspect ratio), ``ok``, or ``unknown`` (no tets).
    """
    tets = [e for e in elements if str(e.get("type")) in _TET_TYPES]
    others = [e for e in elements if str(e.get("type")) not in _TET_TYPES]
    unsupported_types = sorted({str(e.get("type")) for e in others})

    base = {
        "element_count": len(elements),
        "tet_count": len(tets),
        "unsupported_element_count": len(others),
        "unsupported_element_types": unsupported_types,
        "thresholds": {
            "degenerate_volume_ratio": _DEGENERATE_VOL_RATIO,
            "poor_volume_ratio": _POOR_VOL_RATIO,
            "poor_aspect_ratio": _POOR_ASPECT_RATIO,
        },
        "metric": "tet edge-ratio aspect + normalized volume (heuristic)",
        "note": (
            "Element-quality heuristic for tetrahedra only; not a Jacobian quality "
            "measure and surface-set coverage is not validated."
        ),
    }

    if not tets:
        base.update({
            "verdict": "unknown",
            "degenerate_element_count": 0,
            "poor_element_count": 0,
            "broken_element_count": 0,
            "degenerate_element_ids": [],
            "max_aspect_ratio": None,
            "mean_aspect_ratio": None,
            "worst_element_id": None,
        })
        return base

    aspects: list[float] = []
    degenerate_ids: list[int] = []
    poor_ids: list[int] = []
    broken_ids: list[int] = []
    worst_id: int | None = None
    worst_aspect = -1.0

    for elem in tets:
        node_ids = elem.get("nodes") or []
        pts = [nodes.get(nid) for nid in node_ids[:4]]
        if len(pts) < 4 or any(p is None for p in pts):
            broken_ids.append(elem.get("id"))
            continue
        aspect, vol_ratio = _tet_aspect_and_volume_ratio(pts)  # type: ignore[arg-type]
        aspects.append(aspect)
        if vol_ratio < _DEGENERATE_VOL_RATIO:
            degenerate_ids.append(elem.get("id"))
        elif vol_ratio < _POOR_VOL_RATIO or aspect > _POOR_ASPECT_RATIO:
            poor_ids.append(elem.get("id"))
        if aspect > worst_aspect:
            worst_aspect = aspect
            worst_id = elem.get("id")

    if degenerate_ids or broken_ids:
        verdict = "fail"
    elif poor_ids:
        verdict = "warning"
    else:
        verdict = "ok"

    base.update({
        "verdict": verdict,
        "degenerate_element_count": len(degenerate_ids),
        "poor_element_count": len(poor_ids),
        "broken_element_count": len(broken_ids),
        "degenerate_element_ids": degenerate_ids[:50],
        "broken_element_ids": broken_ids[:50],
        "max_aspect_ratio": round(max(aspects), 4) if aspects else None,
        "mean_aspect_ratio": round(sum(aspects) / len(aspects), 4) if aspects else None,
        "worst_element_id": worst_id,
    })
    return base


def get_mesh_quality_diagnostics(package_path: Path) -> dict[str, Any]:
    """Read ``simulation/mesh.inp`` and return an element-quality verdict.

    ``{"available": False}`` when no mesh is present so callers can degrade
    cleanly (mirrors :func:`get_mesh_preview`).
    """
    package_path = Path(package_path)
    inp_bytes = _read_member(package_path, "simulation/mesh.inp")
    if not inp_bytes:
        return {"available": False}

    with tempfile.TemporaryDirectory(prefix="aieng_mesh_quality_") as tmp_str:
        inp_path = Path(tmp_str) / "mesh.inp"
        inp_path.write_bytes(inp_bytes)
        nodes = _parse_inp_nodes(inp_path)
        elements = _parse_inp_elements(inp_path)

    diagnostics = compute_mesh_quality(nodes, elements)
    diagnostics["available"] = True
    diagnostics["node_count"] = len(nodes)
    diagnostics["element_type"] = elements[0]["type"] if elements else None

    # Surface-set coverage: does each CAE load/BC face actually catch mesh nodes?
    cae_raw = _read_member(package_path, "simulation/cae_mapping.json")
    topo_raw = _read_member(package_path, "geometry/topology_map.json")
    cae_mapping = json.loads(cae_raw) if cae_raw else {"mappings": []}
    topology = json.loads(topo_raw) if topo_raw else {}
    set_coverage = compute_set_coverage(nodes, topology, cae_mapping)
    diagnostics["set_coverage"] = set_coverage
    diagnostics["overall_verdict"] = _combine_verdicts(
        diagnostics.get("verdict"), set_coverage.get("verdict")
    )
    return diagnostics


# ── Mesh generation (STEP -> Gmsh -> persisted mesh artifacts) ────────────────

def _write_members_to_package(package_path: Path, files: dict[str, bytes]) -> None:
    """Atomically add/replace the given members inside the .aieng package."""
    tmp = package_path.with_suffix(".meshtmp.aieng")
    try:
        with zipfile.ZipFile(package_path, "r") as src, \
             zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                if item.filename not in files:
                    dst.writestr(item, src.read(item.filename))
            for archive_path, content_bytes in files.items():
                dst.writestr(archive_path, content_bytes)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def generate_mesh_for_package(
    package_path: Path,
    *,
    mesh_size_mm: float | None = None,
) -> dict[str, Any]:
    """Mesh the package's STEP geometry with Gmsh and persist the FE mesh.

    Writes two artifacts into the ``.aieng`` package:

    - ``simulation/mesh.inp`` — the CalculiX-format mesh deck (``*NODE`` /
      ``*ELEMENT``), which lights up the existing mesh-preview, mesh-quality, and
      mesh-convergence readers (all of which read this path).
    - ``simulation/mesh/mesh_metadata.json`` — mesh stats + quality verdict, which
      flips the ``has_mesh`` preflight check (it scans the ``simulation/mesh/``
      prefix).

    This produces **only the FE mesh** — it does not build NSETs, bind boundary
    conditions/loads, assemble a solver deck, or run a solver. Those remain the
    job of the setup/deck/solver tools. Mesh size is taken from the ``mesh_size_mm``
    argument, else the package's configured target size, else a 2.5 mm default.

    Returns a status dict. Degrades honestly: ``tools_unavailable`` when Gmsh is
    not importable, ``no_geometry`` when the package has no STEP, ``mesh_failed``
    when Gmsh raises (e.g. non-manifold / no 3D volume).
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"status": "error", "code": "file_not_found",
                "message": f"Package not found: {package_path}"}

    if not _gmsh_available():
        return {
            "status": "tools_unavailable",
            "missing_tools": ["gmsh"],
            "message": "Gmsh is not importable in this environment; cannot mesh.",
        }

    size = float(
        mesh_size_mm
        if mesh_size_mm is not None
        else (_read_mesh_target_size_mm(package_path) or 2.5)
    )
    if size <= 0:
        return {"status": "error", "code": "bad_input",
                "message": f"mesh_size_mm must be positive, got {size}"}

    with tempfile.TemporaryDirectory(prefix="aieng_generate_mesh_") as tmp_str:
        work = Path(tmp_str)
        step_path = _extract_step(package_path, work)
        if step_path is None:
            return {
                "status": "no_geometry",
                "message": "No STEP geometry found in package — run CAD generation first.",
            }

        try:
            mesh_inp = _mesh_with_gmsh(step_path, work, size)
        except Exception as exc:  # noqa: BLE001 — surface the mesher error honestly
            return {
                "status": "mesh_failed",
                "code": "gmsh_error",
                "message": f"Gmsh meshing failed: {exc}",
                "target_size_mm": size,
            }

        mesh_bytes = mesh_inp.read_bytes()
        nodes = _parse_inp_nodes(mesh_inp)
        elements = _parse_inp_elements(mesh_inp)

    quality = compute_mesh_quality(nodes, elements)
    element_type = elements[0]["type"] if elements else None
    node_count = len(nodes)
    element_count = len(elements)

    if node_count == 0 or element_count == 0:
        return {
            "status": "mesh_failed",
            "code": "empty_mesh",
            "message": "Gmsh produced no solid elements for this geometry.",
            "target_size_mm": size,
        }

    metadata = {
        "schema_version": "0.1",
        "generator": "gmsh",
        "element_type": element_type,
        "node_count": node_count,
        "element_count": element_count,
        "target_size_mm": size,
        "min_size_mm": size / 4.0,
        "algorithm_3d": "frontal_delaunay",
        "quality": quality,
    }

    _write_members_to_package(
        package_path,
        {
            "simulation/mesh.inp": mesh_bytes,
            "simulation/mesh/mesh_metadata.json": json.dumps(metadata, indent=2).encode(),
        },
    )

    return {
        "status": "success",
        "node_count": node_count,
        "element_count": element_count,
        "element_type": element_type,
        "target_size_mm": size,
        "quality_verdict": quality.get("verdict"),
        "quality": quality,
        "written_artifacts": [
            "simulation/mesh.inp",
            "simulation/mesh/mesh_metadata.json",
        ],
    }


# ── Source solver deck synthesis (close the decoupled MCP solve loop) ─────────
#
# The staged MCP path (cae.generate_solver_input -> cae.run_solver) assembles its
# deck from simulation/cae_imports/source_solver_deck.inp, binding loads/BCs to
# NSETs *by name* (the cae_mapping cae_entity). A bare Gmsh mesh names surfaces
# SURF{tag}, not the cae_entity NSETs, so an imported source deck was required and
# a freshly-meshed model could not solve via MCP. These helpers synthesize that
# source deck from simulation/mesh.inp + topology + cae_mapping, using the same
# geometric face->node binding (_build_nsets) the legacy one-shot path uses.

SOURCE_SOLVER_DECK_PATH = "simulation/cae_imports/source_solver_deck.inp"


def build_source_deck_from_mesh(
    mesh_text: str,
    setup: dict[str, Any],
    nsets: dict[str, list[int]],
) -> tuple[str, list[str]]:
    """Build a CalculiX source deck: mesh + *SOLID SECTION + named *NSETs.

    This is the mesh-and-sets half of a deck — materials, BCs, loads, and the
    analysis step are added later by ``aieng.simulation.deck_generator``. The
    NSETs are named per ``cae_mapping`` cae_entity so the deck generator's
    BC/load ``target`` references resolve. Returns ``(deck_text, empty_nsets)``
    where ``empty_nsets`` lists cae_entities that caught no mesh nodes (so the
    caller can warn honestly instead of emitting a dangling NSET reference).
    """
    lines: list[str] = []
    # Preserve the mesh verbatim (Gmsh writes *NODE / *ELEMENT / *ELSET incl. EALL).
    # Defensive stop if the mesh source somehow carried solver sections.
    for line in mesh_text.splitlines():
        up = line.strip().upper()
        if up.startswith("*MATERIAL") or up.startswith("*BOUNDARY") or up.startswith("*STEP"):
            break
        lines.append(line)

    # Assign the volume element set (EALL, from _mesh_with_gmsh's physical group)
    # a solid section so the elements carry a material. Use the raw setup material
    # name so it matches the deck generator's *MATERIAL block (avoids a rename).
    mat_name = setup.get("material_name") or next(
        iter((setup.get("materials") or {}).keys()), "AIENG_MATERIAL"
    )
    lines.append(f"*SOLID SECTION, ELSET=EALL, MATERIAL={mat_name}")

    empty_nsets: list[str] = []
    for nset_name, node_ids in nsets.items():
        if not node_ids:
            empty_nsets.append(nset_name)
            continue
        lines.append(f"*NSET, NSET={nset_name}")
        for i in range(0, len(node_ids), 16):
            lines.append(", ".join(str(n) for n in node_ids[i : i + 16]))

    return "\n".join(lines) + "\n", empty_nsets


def ensure_source_deck_from_mesh(package_path: Path) -> dict[str, Any]:
    """Synthesize ``source_solver_deck.inp`` from a persisted mesh when absent.

    No-op (``status=exists``) when a source deck is already present — an
    explicitly imported deck always wins. Returns ``status=no_mesh`` when there is
    no ``simulation/mesh.inp`` to build from. On success writes the source deck and
    returns the NSET names it bound (and any that caught no nodes). Never runs a
    solver; safe to call before ``cae.generate_solver_input``.
    """
    package_path = Path(package_path)
    if _read_member(package_path, SOURCE_SOLVER_DECK_PATH):
        return {"created": False, "status": "exists"}

    mesh_bytes = _read_member(package_path, "simulation/mesh.inp")
    if not mesh_bytes:
        return {"created": False, "status": "no_mesh"}

    setup_raw = _read_member(package_path, "simulation/setup.yaml")
    setup = (yaml.safe_load(setup_raw) or {}) if setup_raw else {}
    cae_raw = _read_member(package_path, "simulation/cae_mapping.json")
    cae_mapping: dict[str, Any] = json.loads(cae_raw) if cae_raw else {"mappings": []}
    topo_raw = _read_member(package_path, "geometry/topology_map.json")
    topology: dict[str, Any] = json.loads(topo_raw) if topo_raw else {}

    with tempfile.TemporaryDirectory(prefix="aieng_source_deck_") as tmp_str:
        mesh_path = Path(tmp_str) / "mesh.inp"
        mesh_path.write_bytes(mesh_bytes)
        nodes = _parse_inp_nodes(mesh_path)

    nsets = (
        _build_nsets(nodes, topology, cae_mapping)
        if cae_mapping.get("mappings")
        else {}
    )
    deck_text, empty_nsets = build_source_deck_from_mesh(
        mesh_bytes.decode(errors="replace"), setup, nsets
    )
    _write_members_to_package(package_path, {SOURCE_SOLVER_DECK_PATH: deck_text.encode()})

    return {
        "created": True,
        "status": "synthesized",
        "source_deck_path": SOURCE_SOLVER_DECK_PATH,
        "nset_names": [n for n in nsets if nsets[n]],
        "empty_nsets": empty_nsets,
    }


_VERDICT_RANK = {"unknown": 0, "ok": 1, "warning": 2, "fail": 3}


def _combine_verdicts(*verdicts: str | None) -> str:
    """Return the most severe verdict (fail > warning > ok > unknown)."""
    best = "unknown"
    for v in verdicts:
        if _VERDICT_RANK.get(str(v), 0) > _VERDICT_RANK[best]:
            best = str(v)
    return best


# Minimum nodes for a surface set to define a meaningful boundary condition; a set
# that catches 1-2 nodes is undersampled (sparse), 0 is broken (empty).
_SPARSE_SET_MIN_NODES = 3


def compute_set_coverage(
    nodes: dict[int, tuple[float, float, float]],
    topology: dict[str, Any],
    cae_mapping: dict[str, Any],
) -> dict[str, Any]:
    """Validate that each CAE surface set resolves to actual mesh nodes (#279).

    A load/BC bound to a face that catches **zero** mesh nodes (``empty``) or to a
    ``@face`` id absent from the topology (``unresolved_face``) is silently dropped
    by the deck builder, producing a wrong/singular solve — so both fail the
    handoff. A set that catches only 1-2 nodes is ``sparse`` (warning). Reuses the
    same node-mapping (:func:`_nodes_on_face`) as the deck builder. ``unknown`` when
    no surface-set mappings exist (e.g. a pure-CAD project).
    """
    face_index: dict[str, dict[str, Any]] = {
        e["id"]: e
        for e in (topology.get("entities") or topology.get("faces") or [])
        if isinstance(e, dict) and e.get("type") == "face" and "id" in e
    }

    sets: list[dict[str, Any]] = []
    for mapping in cae_mapping.get("mappings") or []:
        cae_entity = mapping.get("cae_entity") or ""
        if not cae_entity:
            continue
        face_ids = [str(fid) for fid in (mapping.get("face_ids") or [])]
        missing = [fid for fid in face_ids if fid not in face_index]
        node_ids: set[int] = set()
        for fid in face_ids:
            entity = face_index.get(fid)
            if entity:
                node_ids.update(_nodes_on_face(nodes, entity))
        count = len(node_ids)
        if missing:
            status = "unresolved_face"
        elif count == 0:
            status = "empty"
        elif count < _SPARSE_SET_MIN_NODES:
            status = "sparse"
        else:
            status = "ok"
        sets.append({
            "cae_entity": cae_entity,
            "face_ids": face_ids,
            "missing_face_ids": missing,
            "resolved_node_count": count,
            "status": status,
        })

    empty = sum(1 for s in sets if s["status"] == "empty")
    unresolved = sum(1 for s in sets if s["status"] == "unresolved_face")
    sparse = sum(1 for s in sets if s["status"] == "sparse")

    if not sets:
        verdict = "unknown"
    elif empty or unresolved:
        verdict = "fail"
    elif sparse:
        verdict = "warning"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "set_count": len(sets),
        "empty_set_count": empty,
        "unresolved_set_count": unresolved,
        "sparse_set_count": sparse,
        "sparse_min_nodes": _SPARSE_SET_MIN_NODES,
        "sets": sets,
        "note": (
            "Surface-set coverage reuses the deck builder's face->node mapping; an "
            "empty/unresolved set would be silently dropped by the solver. Node "
            "membership is a bounding-box/plane heuristic, not exact face meshing."
        ),
    }


# ── Face → node mapping ───────────────────────────────────────────────────────

def _nodes_on_face(
    nodes: dict[int, tuple[float, float, float]],
    face_entity: dict[str, Any],
) -> list[int]:
    """Find node IDs that lie on a topology face.

    Strategy (in priority order):
    1. Cylinder: radial distance from inferred axis + Z-extent check.
    2. Plane with stored normal: point-to-plane distance using face normal and
       centroid as the plane origin, plus coarse AABB filter.
       Works for axis-aligned AND inclined/chamfered planes.
    3. Fallback (no normal stored): thin-dimension bounding-box heuristic
       (axis-aligned planes only — retained for backwards compatibility).
    """
    bbox = face_entity.get("bounding_box", [])
    if len(bbox) < 6:
        return []

    surface_type = face_entity.get("surface_type", "plane")
    # Tolerance: 2% of longest bbox dimension, minimum 0.5 mm.
    span = max(bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2], 1.0)
    tol = max(0.5, span * 0.02)
    # Free-form faces (loft/sweep/sphere) only carry a PROXY normal sampled at the
    # UV midpoint, so the tangent-plane band must be wider to capture a usable
    # patch of the curved surface near the pick. Approximate by design.
    is_freeform = bool(face_entity.get("freeform")) or (
        surface_type == "other" and face_entity.get("normal")
    )
    if is_freeform:
        tol = max(1.0, span * 0.10)

    # ── Cylinder ──────────────────────────────────────────────────────────────
    if surface_type == "cylinder":
        radius = float(face_entity.get("radius", 0.0))
        cx = (bbox[0] + bbox[3]) / 2.0
        cy = (bbox[1] + bbox[4]) / 2.0
        zmin, zmax = bbox[2], bbox[5]
        result = []
        for nid, (x, y, z) in nodes.items():
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if abs(dist - radius) < tol and zmin - tol <= z <= zmax + tol:
                result.append(nid)
        return result

    # ── Planar face — normal-vector path ──────────────────────────────────────
    raw_normal = face_entity.get("normal")
    if raw_normal and len(raw_normal) == 3:
        nx, ny, nz = float(raw_normal[0]), float(raw_normal[1]), float(raw_normal[2])
        mag = (nx * nx + ny * ny + nz * nz) ** 0.5
        if mag > 1e-9:
            nx, ny, nz = nx / mag, ny / mag, nz / mag
            # Use stored centroid; fall back to bbox midpoint
            cpt = face_entity.get("center") or [
                (bbox[0] + bbox[3]) / 2.0,
                (bbox[1] + bbox[4]) / 2.0,
                (bbox[2] + bbox[5]) / 2.0,
            ]
            px, py, pz = float(cpt[0]), float(cpt[1]), float(cpt[2])
            # Plane equation: dot(n, point) = d
            d = nx * px + ny * py + nz * pz
            result = []
            for nid, (x, y, z) in nodes.items():
                # Coarse AABB filter — rejects nodes far from face region
                if not (
                    bbox[0] - tol <= x <= bbox[3] + tol
                    and bbox[1] - tol <= y <= bbox[4] + tol
                    and bbox[2] - tol <= z <= bbox[5] + tol
                ):
                    continue
                # Exact point-to-plane distance
                if abs(nx * x + ny * y + nz * z - d) <= tol:
                    result.append(nid)
            return result

    # ── Fallback: thin-dimension bounding-box heuristic (axis-aligned only) ──
    dims = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
    thin = dims.index(min(dims))
    center = (bbox[thin] + bbox[thin + 3]) / 2.0
    result = []
    for nid, (x, y, z) in nodes.items():
        coords = (x, y, z)
        if abs(coords[thin] - center) > tol:
            continue
        in_plane = all(
            bbox[i] - tol <= coords[i] <= bbox[i + 3] + tol
            for i in range(3)
            if i != thin
        )
        if in_plane:
            result.append(nid)
    return result


def _build_nsets(
    nodes: dict[int, tuple[float, float, float]],
    topology: dict[str, Any],
    cae_mapping: dict[str, Any],
) -> dict[str, list[int]]:
    """Map cae_mapping face IDs to mesh node IDs via topology bounding boxes."""
    face_index: dict[str, dict[str, Any]] = {
        e["id"]: e
        for e in (topology.get("entities") or topology.get("faces") or [])
        if isinstance(e, dict) and e.get("type") == "face" and "id" in e
    }

    nsets: dict[str, list[int]] = {}
    for mapping in cae_mapping.get("mappings") or []:
        nset_name = mapping.get("cae_entity", "")
        if not nset_name:
            continue
        node_ids: set[int] = set()
        for fid in mapping.get("face_ids") or []:
            entity = face_index.get(fid)
            if entity:
                node_ids.update(_nodes_on_face(nodes, entity))
        nsets[nset_name] = sorted(node_ids)

    return nsets


def _unresolved_bc_load_faces(
    setup: dict[str, Any],
    cae_mapping: dict[str, Any],
    nsets: dict[str, list[int]],
) -> list[dict[str, Any]]:
    """Return loads/BCs whose mapped face(s) matched zero mesh nodes.

    A load or boundary condition whose target feature maps to an NSET that
    resolved to no nodes (face ↔ mesh mismatch) is silently dropped by the deck
    builder, so the solve would run with a missing constraint/load and produce a
    wrong (often singular) result. Surfacing these as @face hints lets the caller
    re-pick the face — or remesh — *before* paying for the solver run. Targets
    with no mapping at all are left to the normal completeness checks; this only
    flags the "selected a face but it caught no nodes" case.
    """
    feat_map: dict[str, tuple[str, list[str]]] = {}
    for m in cae_mapping.get("mappings") or []:
        fid = (m.get("maps_to") or {}).get("feature_id")
        if fid:
            feat_map[fid] = (m.get("cae_entity", ""), list(m.get("face_ids") or []))

    problems: list[dict[str, Any]] = []
    for kind, items in (
        ("boundary_condition", setup.get("boundary_conditions") or []),
        ("load", setup.get("loads") or []),
    ):
        for item in items:
            target = item.get("target_feature", "")
            nset_name, face_ids = feat_map.get(target, ("", []))
            if nset_name and not nsets.get(nset_name):
                problems.append({
                    "kind": kind,
                    "target_feature": target,
                    "cae_entity": nset_name,
                    "face_pointers": [f"@face:{fid}" for fid in face_ids],
                })
    return problems


# ── CalculiX deck generation ──────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """Make a name safe for CalculiX (alphanumeric + underscore, max 80 chars)."""
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", name)[:80]


def _build_calculix_deck(
    mesh_inp_text: str,
    setup: dict[str, Any],
    nsets: dict[str, list[int]],
    cae_mapping: dict[str, Any],
) -> str:
    """Build the full CalculiX input deck from the Gmsh mesh + AI preprocessing output."""
    lines: list[str] = []

    # ── Keep mesh sections from Gmsh verbatim (nodes + elements + elsets) ──
    # Stop if Gmsh somehow wrote material/BC/step sections (shouldn't happen).
    for line in mesh_inp_text.splitlines():
        up = line.strip().upper()
        if up.startswith("*MATERIAL") or up.startswith("*BOUNDARY") or up.startswith("*STEP"):
            break
        lines.append(line)

    # ── NALL / NSET definitions ──────────────────────────────────────────────
    if nsets:
        for nset_name, node_ids in nsets.items():
            if not node_ids:
                continue
            safe = _sanitize_name(nset_name)
            lines.append(f"*NSET, NSET={safe}")
            for i in range(0, len(node_ids), 16):
                lines.append(", ".join(str(n) for n in node_ids[i : i + 16]))

    # ── Material ─────────────────────────────────────────────────────────────
    mat_name = setup.get("material_name", "Al6061_T6")
    mat_safe = _sanitize_name(mat_name)
    mat_props = (setup.get("materials") or {}).get(mat_name) or {}
    E = float(mat_props.get("youngs_modulus_mpa", 69000))
    nu = float(mat_props.get("poisson_ratio", 0.33))
    # Convert kg/m³ → t/mm³ for consistent mm/N/MPa unit system
    rho_kg_m3 = float(mat_props.get("density_kg_m3", 2700))
    rho_t_mm3 = rho_kg_m3 * 1e-12

    lines += [
        f"*MATERIAL, NAME={mat_safe}",
        "*ELASTIC",
        f"{E:.1f}, {nu}",
        "*DENSITY",
        f"{rho_t_mm3:.6e}",
        f"*SOLID SECTION, ELSET=EALL, MATERIAL={mat_safe}",
        "",
    ]

    # ── STEP ─────────────────────────────────────────────────────────────────
    lines += ["*STEP", "*STATIC"]

    # Build feature_id → cae_entity index
    feat_to_nset: dict[str, str] = {}
    for m in cae_mapping.get("mappings") or []:
        fid = (m.get("maps_to") or {}).get("feature_id")
        if fid:
            feat_to_nset[fid] = m.get("cae_entity", "")

    # ── Boundary conditions ──────────────────────────────────────────────────
    bc_written = 0
    for bc in setup.get("boundary_conditions") or []:
        target = bc.get("target_feature", "")
        nset_name = feat_to_nset.get(target, "")
        safe = _sanitize_name(nset_name)
        if safe and nsets.get(nset_name):
            bc_type = bc.get("type", "fixed")
            if bc_type == "fixed":
                lines.append("*BOUNDARY")
                lines.append(f"{safe}, 1, 6")  # fix all 6 DOFs
                bc_written += 1

    # ── Loads ────────────────────────────────────────────────────────────────
    load_written = 0
    for ld in setup.get("loads") or []:
        target = ld.get("target_feature", "")
        nset_name = feat_to_nset.get(target, "")
        safe = _sanitize_name(nset_name)
        node_ids = nsets.get(nset_name) or []
        if safe and node_ids:
            value_n = float(ld.get("value_n") or 0.0)
            direction = ld.get("direction") or [0.0, 0.0, -1.0]
            n_nodes = len(node_ids)
            force_per_node = value_n / n_nodes if n_nodes else 0.0
            for dof, comp in enumerate(direction[:3], start=1):
                if abs(comp) > 1e-9:
                    lines.append("*CLOAD")
                    lines.append(f"{safe}, {dof}, {force_per_node * comp:.6f}")
            load_written += 1

    # ── Output requests ──────────────────────────────────────────────────────
    lines += [
        "*NODE FILE",
        "U",
        "*EL FILE",
        "S",
        "*END STEP",
    ]

    return "\n".join(lines) + "\n", bc_written, load_written


# ── CalculiX execution ────────────────────────────────────────────────────────

def _run_calculix(
    inp_path: Path,
    work_dir: Path,
    timeout: int = 180,
) -> tuple[int, str, Path | None]:
    """Run CalculiX. Returns (returncode, combined_log, frd_path or None)."""
    ccx_cmd = _find_ccx()
    if not ccx_cmd:
        raise RuntimeError("CalculiX (ccx) not found")

    result = subprocess.run(
        [ccx_cmd, inp_path.stem],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    log = result.stdout + "\n" + result.stderr
    frd = work_dir / f"{inp_path.stem}.frd"
    return result.returncode, log, frd if frd.exists() else None


# ── Solver failure diagnosis ──────────────────────────────────────────────────

def _diagnose_solver_log(log: str) -> list[str]:
    """Scan a CalculiX log for common failure patterns and return actionable messages."""
    diagnoses: list[str] = []
    up = log.upper()

    if "SINGULAR" in up or "ZERO PIVOT" in up or "ZERO DIAGONAL" in up:
        diagnoses.append(
            "Stiffness matrix is singular — the model may be under-constrained. "
            "Verify that all rigid-body modes are suppressed by the boundary conditions."
        )
    if "TOO MANY INCREMENTS" in up:
        diagnoses.append(
            "Solver did not converge within the increment limit — "
            "try a finer mesh or reduce the applied load magnitude."
        )
    if "DIVERGENCE" in up:
        diagnoses.append(
            "Solution diverged — check for unrealistically large loads or verify material properties."
        )
    if "NO ELEMENTS" in up or "EMPTY ELEMENT SET" in up:
        diagnoses.append(
            "An element set is empty — the face-to-node mapping may have failed. "
            "Check that the geometry STEP and topology_map.json are consistent."
        )
    if "ERROR IN FACE" in up or "INCONSISTENT ORIENTATION" in up:
        diagnoses.append(
            "Mesh has inconsistently oriented faces — the STEP geometry may have "
            "surface normal issues. Try regenerating the CAD model."
        )
    if not diagnoses:
        # Extract the first *ERROR line from the log as a fallback
        for line in log.splitlines():
            if "*ERROR" in line.upper() or "ERROR:" in line.upper():
                diagnoses.append(f"Solver reported: {line.strip()}")
                break
        if not diagnoses:
            diagnoses.append(
                "Solver exited with a non-zero return code — "
                "see simulation/solver_log.txt for the full output."
            )
    return diagnoses


# ── Result extraction ─────────────────────────────────────────────────────────

def _extract_metrics(frd_path: Path) -> dict[str, Any]:
    """Parse FRD file using the existing aieng simulation module."""
    ensure_aieng_on_path()
    from aieng.simulation.frd_result_extractor import extract_computed_metrics

    return extract_computed_metrics(frd_path)


def solve_package_static(
    package_path: Path,
    *,
    mesh_size_mm: float | None = None,
    timeout: int = 180,
    rebind_faces: bool = False,
    baseline_package_path: Path | str | None = None,
) -> dict[str, Any]:
    """Mesh + solve a package's CURRENT static geometry; return scalar metrics.

    Package-level counterpart to ``_run_simulation_core`` (no project/SSE/approval
    machinery) intended for batch use — e.g. a parametric sizing sweep that solves
    each dimension variant. Reuses the same proven leaf helpers: extract STEP →
    Gmsh mesh → NSETs from topology + cae_mapping → CalculiX deck → solve → parse
    FRD. Never mutates the package.

    Args:
        rebind_faces: If True and the package's CAE face references are stale,
            attempt a geometric rebind against ``baseline_package_path`` before
            refusing the solve. The baseline package is never mutated; a throwaway
            rebound copy is used internally.
        baseline_package_path: Package containing the original topology the CAE
            mapping was bound to. Required when ``rebind_faces`` is True and the
            current package's face references do not match its own topology.

    Returns ``{solver_executed, status, metrics, warnings, [error]}`` where
    ``metrics`` carries scalar ``max_von_mises_stress`` / ``max_displacement``.
    Degrades honestly (``solver_executed=False`` + a reason) when Gmsh/ccx are
    unavailable, the setup is missing, face references are stale, or the solve
    fails — it never reports an unsolved variant as success.
    """
    package_path = Path(package_path)
    tools = check_simulation_tools()
    if not tools["ready"]:
        return {
            "solver_executed": False,
            "status": "tools_unavailable",
            "metrics": {},
            "warnings": [],
            "error": f"required tools unavailable: {', '.join(tools['missing'])}",
        }

    setup_raw = _read_member(package_path, "simulation/setup.yaml")
    if not setup_raw:
        return {
            "solver_executed": False, "status": "no_setup", "metrics": {}, "warnings": [],
            "error": "simulation/setup.yaml not found — run CAE setup first",
        }
    setup = yaml.safe_load(setup_raw)
    cae_raw = _read_member(package_path, "simulation/cae_mapping.json")
    cae_mapping: dict[str, Any] = json.loads(cae_raw) if cae_raw else {"mappings": []}
    topo_raw = _read_member(package_path, "geometry/topology_map.json")
    topology: dict[str, Any] = json.loads(topo_raw) if topo_raw else {}

    # Stale face refs would silently drop loads/BCs → refuse rather than mis-solve.
    # Optionally try a geometric rebind first (e.g. parametric sizing sweep / design
    # study candidate where the baseline CAE mapping is carried onto a regenerated
    # topology). The baseline package is never mutated; rebound data lives in memory.
    rebind_report: dict[str, Any] | None = None
    topo_val = validate_cae_topology_references(package_path)
    if not topo_val.get("valid", True):
        if rebind_faces and baseline_package_path is not None:
            baseline_pkg = Path(baseline_package_path)
            old_topo_raw = _read_member(baseline_pkg, "geometry/topology_map.json")
            old_cae_raw = _read_member(baseline_pkg, "simulation/cae_mapping.json")
            if old_topo_raw and old_cae_raw:
                old_topology = json.loads(old_topo_raw)
                old_cae_mapping = json.loads(old_cae_raw)
                rebind_report = rebind_cae_faces(old_cae_mapping, old_topology, topology)
                if rebind_report.get("all_resolved"):
                    cae_mapping = rebind_report["cae_mapping"]
                    # Skip package-level validation; rebind all_resolved guarantees the
                    # new face_ids exist in ``topology``.
                    topo_val = {"valid": True}
                else:
                    return {
                        "solver_executed": False,
                        "status": "stale_topology_references",
                        "metrics": {},
                        "warnings": [],
                        "error": (
                            "CAE face references do not match current topology "
                            "and automatic rebind failed or was ambiguous."
                        ),
                        "rebind_report": rebind_report,
                    }
            else:
                return {
                    "solver_executed": False,
                    "status": "stale_topology_references",
                    "metrics": {},
                    "warnings": [],
                    "error": (
                        "CAE face references are stale and baseline topology/mapping "
                        "was not provided for rebind."
                    ),
                }
        else:
            return {
                "solver_executed": False, "status": "stale_topology_references", "metrics": {},
                "warnings": [], "error": "CAE face references do not match current topology",
            }

    size = float(mesh_size_mm or (setup.get("mesh") or {}).get("target_size_mm") or 2.5)
    with tempfile.TemporaryDirectory(prefix="aieng_sweep_solve_") as tmp_str:
        work = Path(tmp_str)
        step_path = _extract_step(package_path, work)
        if not step_path:
            return {
                "solver_executed": False, "status": "no_step", "metrics": {}, "warnings": [],
                "error": "no STEP geometry in package",
            }
        try:
            mesh_inp = _mesh_with_gmsh(step_path, work, size)
            nodes = _parse_inp_nodes(mesh_inp)
            nsets = _build_nsets(nodes, topology, cae_mapping)
            unresolved = _unresolved_bc_load_faces(setup, cae_mapping, nsets)
            if unresolved:
                return {
                    "solver_executed": False, "status": "unresolved_face_mapping", "metrics": {},
                    "warnings": [], "error": "load/BC face(s) matched zero mesh nodes",
                }
            mesh_text = mesh_inp.read_text(errors="replace")
            deck_text, _bc_count, _load_count = _build_calculix_deck(
                mesh_text, setup, nsets, cae_mapping
            )
            deck_inp = work / "aieng_run.inp"
            deck_inp.write_text(deck_text)
            returncode, _solver_log, frd_path = _run_calculix(deck_inp, work, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {
                "solver_executed": False, "status": "solve_error", "metrics": {}, "warnings": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        if returncode != 0 or not frd_path:
            return {
                "solver_executed": False, "status": "solver_error", "metrics": {}, "warnings": [],
                "error": f"CalculiX returned code {returncode} or produced no FRD",
            }
        extracted = _extract_metrics(frd_path)

    from aieng.converters.sizing_sweep import extract_static_metrics

    return {
        "solver_executed": True,
        "status": "success",
        "metrics": extract_static_metrics(extracted),
        # Full computed_metrics doc (carries metrics_source.software=CalculiX) — used
        # by the design-study candidate solver to write candidate-local results.
        "computed_metrics": extracted,
        "warnings": extracted.get("warnings") or [],
    }


# ── Orchestration ─────────────────────────────────────────────────────────────
#
# run_simulation (sync) and run_simulation_stream (SSE) share ONE pipeline,
# _run_simulation_core, so they cannot drift. The core is a generator that
# yields progress events ({"step": ...}) and a terminal {"step": "done",
# "result": {...}} event, and raises _SimAbort for contract-bearing aborts
# (confirmed gate, missing package/setup, stale-topology, unresolved faces).
# The sync wrapper drains the events, re-raising _SimAbort as the historical
# HTTPException; the stream wrapper formats every event as SSE and never raises.
#
# Note: the REST run-simulation[-stream] endpoints are the legacy embedded-agent
# path; the MCP `cae.run_solver` tool is the canonical agent-driven solver path.
# See docs/simulation_runner_audit.md.


def _sse(event: dict[str, Any]) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


class _SimAbort(Exception):
    """A pipeline abort that carries both response contracts.

    ``http_status`` / ``detail`` reproduce the historical HTTPException raised by
    the sync path; ``sse_event`` is the error event the streaming path emits.
    Keeping both on one object is what lets a single core serve both wrappers
    without the two diverging again.
    """

    def __init__(self, http_status: int, detail: Any, sse_event: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.http_status = http_status
        self.detail = detail
        self.sse_event = sse_event


def _run_simulation_core(
    settings: Any,
    project_id: str,
    payload: dict[str, Any],
) -> Generator[dict[str, Any], None, None]:
    """Canonical mesh → solve → parse pipeline shared by both REST entry points.

    Yields progress events and a terminal ``{"step": "done", "result": {...}}``
    event. Raises :class:`_SimAbort` for aborts that carry a response contract.

    Requires confirmed=true in payload (approval gate — this runs external
    processes). Writes simulation/solver_log.txt, simulation/result.frd, and
    simulation/results_summary.json into the .aieng package atomically.
    """
    from .project_io import get_project, resolve_project_path

    if not payload.get("confirmed"):
        raise _SimAbort(
            400,
            "confirmed=true is required — simulation runs external processes (Gmsh + CalculiX)",
            {"step": "error", "message": "confirmed=true is required"},
        )

    try:
        project = get_project(settings, project_id)
    except Exception as exc:
        raise _SimAbort(
            404,
            f"Project not found: {exc}",
            {"step": "error", "message": f"Project not found: {exc}"},
        )

    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path is None or not package_path.exists():
        raise _SimAbort(
            404,
            ".aieng package not found",
            {"step": "error", "message": ".aieng package not found"},
        )

    # ── Step 1: check tools (graceful degradation) ───────────────────────────
    yield {"step": "checking_tools", "message": "Checking Gmsh and CalculiX…"}
    tools = check_simulation_tools()
    if not tools["ready"]:
        result: dict[str, Any] = {
            "status": "tools_unavailable",
            "project_id": project_id,
            "missing_tools": tools["missing"],
            "message": f"Required tools not installed: {', '.join(tools['missing'])}. "
                       "Install Gmsh (pip install gmsh) and CalculiX (ccx).",
        }
        yield {"step": "done", "message": "Tools unavailable", "result": result}
        return

    # ── Prerequisites ─────────────────────────────────────────────────────────
    setup_raw = _read_member(package_path, "simulation/setup.yaml")
    if not setup_raw:
        raise _SimAbort(
            422,
            "simulation/setup.yaml not found — run AI preprocessing first",
            {"step": "error", "message": "simulation/setup.yaml not found — run AI preprocessing first"},
        )
    setup = yaml.safe_load(setup_raw)

    cae_raw = _read_member(package_path, "simulation/cae_mapping.json")
    cae_mapping: dict[str, Any] = json.loads(cae_raw) if cae_raw else {"mappings": []}

    topo_raw = _read_member(package_path, "geometry/topology_map.json")
    topology: dict[str, Any] = json.loads(topo_raw) if topo_raw else {}

    # Fail fast if the CAE face references are stale relative to current topology.
    # Enforced for BOTH the sync and the streaming path (previously the stream
    # path skipped this, so it could solve against stale face references and
    # report a wrong result as success).
    topology_validation = validate_cae_topology_references(package_path)
    if not topology_validation["valid"]:
        detail = {
            "code": "stale_topology_references",
            "message": (
                "Aborted before meshing: CAE face references do not match the "
                "current topology. Re-run AI preprocessing to refresh face "
                "references, or update simulation/cae_mapping.json manually."
            ),
            "topology_validation": topology_validation,
        }
        raise _SimAbort(422, detail, {"step": "error", **detail})

    mesh_size_mm = float(
        payload.get("mesh_size_mm")
        or (setup.get("mesh") or {}).get("target_size_mm")
        or 2.5
    )
    timeout = int(payload.get("timeout_s") or 180)

    with tempfile.TemporaryDirectory(prefix="aieng_sim_") as tmp_str:
        work_dir = Path(tmp_str)

        # ── Step 2: extract STEP ──────────────────────────────────────────────
        step_path = _extract_step(package_path, work_dir)
        if not step_path:
            raise _SimAbort(
                422,
                "No STEP file in package — run text-to-CAD generation first",
                {"step": "error", "message": "No STEP file in package — run text-to-CAD generation first"},
            )

        # ── Step 3: mesh ──────────────────────────────────────────────────────
        yield {"step": "meshing", "message": f"Generating mesh with Gmsh (target size {mesh_size_mm} mm)…"}
        mesh_inp = _mesh_with_gmsh(step_path, work_dir, mesh_size_mm)
        nodes = _parse_inp_nodes(mesh_inp)
        node_count = len(nodes)

        # ── Step 4: build NSETs from topology + cae_mapping ───────────────────
        face_count = len(cae_mapping.get("mappings") or [])
        yield {"step": "building_nsets", "message": f"Mapping {face_count} face(s) to mesh nodes ({node_count:,} nodes)…"}
        nsets = _build_nsets(nodes, topology, cae_mapping)
        empty_nsets = [k for k, v in nsets.items() if not v]

        # Fail fast: a load/BC whose face matched zero mesh nodes would be
        # silently dropped, yielding a wrong/singular solve. Abort with @face
        # hints before invoking CalculiX so the caller can re-pick or remesh.
        unresolved = _unresolved_bc_load_faces(setup, cae_mapping, nsets)
        if unresolved:
            hint_faces = ", ".join(
                fp for prob in unresolved for fp in prob["face_pointers"]
            ) or "(no face hints available)"
            detail = {
                "code": "unresolved_face_mapping",
                "message": (
                    "Aborted before solving: load/boundary-condition face(s) "
                    "matched zero mesh nodes. Re-pick the face(s) or reduce "
                    "mesh_size_mm, then retry."
                ),
                "unresolved": unresolved,
            }
            sse_event = {
                "step": "error",
                "code": "unresolved_face_mapping",
                "message": (
                    "Load/BC face(s) matched zero mesh nodes — aborted before "
                    f"solving. Re-pick or remesh: {hint_faces}"
                ),
                "unresolved": unresolved,
            }
            raise _SimAbort(422, detail, sse_event)

        # ── Generate solver deck ──────────────────────────────────────────────
        mesh_text = mesh_inp.read_text(errors="replace")
        deck_text, bc_count, load_count = _build_calculix_deck(
            mesh_text, setup, nsets, cae_mapping
        )
        deck_inp = work_dir / "aieng_run.inp"
        deck_inp.write_text(deck_text)

        # ── Step 5: solve ─────────────────────────────────────────────────────
        yield {"step": "solving", "message": f"Running CalculiX ({node_count:,} nodes)…"}
        returncode, solver_log, frd_path = _run_calculix(deck_inp, work_dir, timeout=timeout)

        # ── Step 6: parse results ─────────────────────────────────────────────
        yield {"step": "parsing", "message": "Parsing FRD results…"}
        warnings: list[str] = []
        if empty_nsets:
            warnings.append(f"NSETs with no matched nodes (face ID mismatch): {empty_nsets}")
        if bc_count == 0:
            warnings.append("No boundary conditions were applied — model may be unconstrained")
        if load_count == 0:
            warnings.append("No loads were applied")

        von_mises_max: float | None = None
        displacement_max: float | None = None
        frd_bytes: bytes | None = None
        full_metrics: dict[str, Any] = {}

        if frd_path:
            frd_bytes = frd_path.read_bytes()
            try:
                extracted = _extract_metrics(frd_path)
                load_cases = extracted.get("load_cases") or {}
                lc = next(iter(load_cases.values()), {}) if load_cases else {}
                von_mises_max = lc.get("max_von_mises_stress_mpa")
                displacement_max = lc.get("max_displacement_mm")
                warnings.extend(extracted.get("warnings") or [])
                full_metrics = extracted
            except Exception as exc:
                warnings.append(f"FRD result parsing failed: {exc}")

        status = "success" if returncode == 0 and frd_path else "solver_error"

        # ── Post-processing: verdict vs design targets ────────────────────────
        verdict: dict[str, Any] = {}
        if status == "success" and (von_mises_max is not None or displacement_max is not None):
            from . import post_processing

            targets_raw = _read_member(package_path, "task/design_targets.yaml")
            design_targets: list[dict[str, Any]] = []
            if targets_raw:
                try:
                    import yaml as _yaml
                    doc = _yaml.safe_load(targets_raw)
                    if isinstance(doc, dict):
                        design_targets = doc.get("targets") or []
                except Exception:
                    pass
            material_name = (setup.get("material_name") or setup.get("material") or "")
            verdict = post_processing.interpret_results(
                von_mises_max, displacement_max, design_targets, str(material_name)
            )

        results_summary: dict[str, Any] = {
            "schema_version": "0.1",
            "solver": "CalculiX",
            "status": status,
            "returncode": returncode,
            "node_count": node_count,
            "mesh_size_mm": mesh_size_mm,
            "bc_count": bc_count,
            "load_count": load_count,
            "von_mises_max_mpa": von_mises_max,
            "displacement_max_mm": displacement_max,
            "warnings": warnings,
            "full_metrics": full_metrics,
            "verdict": verdict,
        }

        # ── Write artifacts atomically ────────────────────────────────────────
        mesh_inp_bytes = mesh_inp.read_bytes()
        written = ["simulation/solver_log.txt", "simulation/results_summary.json", "simulation/mesh.inp"]
        if frd_bytes:
            written.append("simulation/result.frd")
        _write_results_to_package(package_path, solver_log, frd_bytes, results_summary, mesh_inp_bytes)

        result = {
            "status": status,
            "project_id": project_id,
            "returncode": returncode,
            "von_mises_max_mpa": von_mises_max,
            "displacement_max_mm": displacement_max,
            "node_count": node_count,
            "mesh_size_mm": mesh_size_mm,
            "written_artifacts": written,
            "warnings": warnings,
            "verdict": verdict,
        }
        if returncode != 0:
            result["solver_log_tail"] = solver_log[-2000:]
            result["diagnosis"] = _diagnose_solver_log(solver_log)

        done_msg = "Simulation complete" if status == "success" else f"Solver error (code {returncode})"
        yield {"step": "done", "message": done_msg, "result": result}


def run_simulation(
    settings: Any,
    project_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Mesh with Gmsh + solve with CalculiX + parse results, all in one call.

    Thin synchronous wrapper over :func:`_run_simulation_core`: drains the
    progress events and returns the terminal result dict. Pipeline aborts are
    re-raised as the historical ``HTTPException`` so the REST contract is
    unchanged.
    """
    result: dict[str, Any] | None = None
    try:
        for event in _run_simulation_core(settings, project_id, payload):
            if event.get("step") == "done":
                result = event.get("result")
    except _SimAbort as abort:
        raise HTTPException(status_code=abort.http_status, detail=abort.detail)

    if result is None:  # defensive — the core always yields a terminal event
        raise HTTPException(status_code=500, detail="Simulation produced no result")
    return result


def run_simulation_stream(
    settings: Any,
    project_id: str,
    payload: dict[str, Any],
) -> Generator[str, None, None]:
    """Streaming variant of run_simulation — yields SSE-formatted progress events.

    Events: checking_tools → meshing → building_nsets → solving → parsing →
            done (with full result) | error (on any failure).

    Thin SSE wrapper over :func:`_run_simulation_core`. The generator never
    raises; all failures are surfaced as SSE error events.
    """
    try:
        for event in _run_simulation_core(settings, project_id, payload):
            yield _sse(event)
    except _SimAbort as abort:
        yield _sse(abort.sse_event)
    except Exception as exc:
        yield _sse({"step": "error", "message": str(exc)})
