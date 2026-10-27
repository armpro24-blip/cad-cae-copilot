"""Extract high-magnitude field regions from CalculiX FRD results.

Phase 31 — Diagnostic full-field reasoning.

Partitions nodal DISP or S fields into spatial clusters of high-magnitude
values and reports each cluster's centroid, peak magnitude, and node count.

No visualization, no full-field serialization, no mesh interpolation.

## Why hand-rolled FRD parsing + spatial BFS instead of `pyvista`?

A reasonable off-the-shelf option for this layer is
[`pyvista`](https://github.com/pyvista/pyvista) (0.48+ has native FRD
reading) combined with `connectivity()` for region clustering, or
[`pyacvd`](https://github.com/pyvista/pyacvd) for surface re-meshing.
We chose to hand-roll the FRD reader and the distance-threshold BFS
clusterer for three reasons that are load-bearing for the Phase 31
honesty boundary:

1. **No mesh interpolation, no full-field serialization.** Phase 31
   explicitly forbids both. `pyvista` is excellent for both, which means
   importing it puts the surface area for "accidentally claim
   interpolated field values" one method call away. The hand-rolled
   path can only do what we actually contractually allow.
2. **Minimal runtime dependency.** `pyvista` pulls in VTK, NumPy, and
   a non-trivial native stack. AIENG's runtime today only needs
   `pyyaml` and `jsonschema`. Phase 31 is intentionally scoped to
   "≤ N high-magnitude clusters per field" — the surface is small
   enough that keeping it in-tree is cheaper than the dependency.
3. **Explicit fallback path for `feature_ref`.** Mapping clusters to
   feature IDs is done via the source-deck NSET membership plus
   `simulation/cae_mapping.json`, not via mesh-feature topology. That
   path is deterministic and observable; a `pyvista`-driven mapping
   would obscure the evidence trail.

If a future phase needs principal-stress decomposition, surface
re-meshing, or full-field interpolation, we should add `pyvista` /
`pyacvd` as an optional `[field-regions]` extra in `pyproject.toml`
(mirroring `[geometry]` for CadQuery) rather than pulling it into the
core runtime. Re-visit when Phase 36 closed-loop benchmark shows a
concrete need that the hand-rolled path cannot honestly express.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .. import FORMAT_VERSION
from ..schema_versions import FIELD_REGIONS_SCHEMA

FIELD_REGIONS_PATH = "results/field_regions.json"

# A percentile cut ALWAYS selects nodes, so "high-magnitude clusters" exist on
# every field — including a uniform one, where the "high" nodes sit within
# rounding of the median. Below this peak/median ratio the field is reported as
# having no concentration, so the clusters are not presented as hot spots.
NEAR_UNIFORM_PEAK_TO_MEDIAN = 1.1

_STRESS_FIELDS = ("S", "STRESS")
_DISP_FIELDS = ("DISP", "DISPLACEMENT")


class FieldRegionError(Exception):
    """Raised when field region extraction cannot proceed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_field_regions_package(
    package_path: str | Path,
    frd_path: str | Path,
    *,
    field: str = "S",
    metric: str = "von_mises",
    max_clusters: int = 3,
    threshold_percentile: float = 90.0,
    overwrite: bool = False,
    source_member: str | None = None,
) -> dict[str, Any]:
    """Extract high-magnitude field regions from an FRD file and write into a .aieng package.

    Args:
        package_path: Path to the `.aieng` package.
        frd_path: Path to the CalculiX `.frd` result file.
        field: FRD field name to analyse (``'S'`` for stress, ``'DISP'`` for displacement).
        metric: Metric to compute per node (``'von_mises'`` or ``'magnitude'``).
        max_clusters: Maximum number of clusters to return.
        threshold_percentile: Percentile cutoff (0–100). Only nodes above this
            percentile are considered for clustering.
        overwrite: Whether to overwrite an existing field_regions.json.
        source_member: The FRD's package path when it came from the package
            (``simulation/runs/<run_id>/outputs/result.frd``). It selects that
            run's own deck for node-set attribution and is recorded as
            ``source_frd``; a temp-file name says nothing about which run the
            regions describe.

    Returns:
        Dict with ``ok``, ``field_regions``, ``clusters``, ``warnings``.
    """
    pkg = Path(package_path)
    frd = Path(frd_path)
    if not pkg.exists():
        raise FileNotFoundError(f"package not found: {pkg}")
    if not frd.exists():
        raise FileNotFoundError(f"FRD file not found: {frd}")
    if pkg.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    # Parse FRD fields
    from .frd_result_extractor import parse_frd

    fields = parse_frd(frd)

    # Extract node coordinates from FRD mesh section
    coords = _extract_node_coords_from_frd(frd)
    if not coords:
        raise FieldRegionError("No node coordinates found in FRD file.")

    # Compute per-node scalar metric
    node_values, warnings = _compute_node_values(fields, field, metric, coords)
    if not node_values:
        raise FieldRegionError(
            f"Could not compute '{metric}' for field '{field}'. "
            f"Available fields: {list(fields.keys())}"
        )

    # Threshold: keep nodes above percentile
    threshold = _percentile_threshold(list(node_values.values()), threshold_percentile)
    statistics = _field_statistics(list(node_values.values()), threshold, threshold_percentile)
    if statistics["near_uniform"]:
        warnings.append(
            f"The field is near-uniform: peak {statistics['max']:.4g} is "
            f"{statistics['peak_to_median_ratio']} x the median {statistics['median']:.4g} "
            f"(< {NEAR_UNIFORM_PEAK_TO_MEDIAN}). The clusters below are the top "
            f"{100 - threshold_percentile:g}% of a flat field, not stress/displacement "
            "concentrations."
        )
    # With ties the percentile value can be the field's FLOOR (e.g. 90% of a
    # plate at one stress), and ">= threshold" then selected every node in the
    # field as "high". A node at the minimum is not high, whatever the
    # percentile says.
    floor = min(node_values.values())
    high_nodes = {
        nid for nid, val in node_values.items()
        if val >= threshold and (threshold > floor or val > floor)
    }
    if not high_nodes:
        warnings.append(
            f"No nodes exceeded the {threshold_percentile}th percentile "
            f"threshold ({threshold:.4f}). Returning empty clusters."
        )
        return _write_regions_package(
            pkg, [], source_member or frd.name, field, metric, warnings, overwrite=overwrite,
            statistics=statistics,
        )

    # Spatial clustering
    clusters_raw = _cluster_nodes(coords, high_nodes, _auto_threshold(coords))

    # Limit to max_clusters by merging smallest clusters
    clusters_raw = _limit_clusters(clusters_raw, max_clusters)

    # Cluster -> feature_id mapping from package evidence (source deck + cae_mapping).
    # Mapping is best-effort and honest: ambiguous or unmappable peaks remain None.
    feature_resolver = _build_feature_resolver(pkg, warnings, coords, source_member)

    # Build structured cluster descriptions
    clusters = []
    for idx, node_ids in enumerate(clusters_raw, start=1):
        peak_node = max(node_ids, key=lambda nid: node_values[nid])
        peak_val = node_values[peak_node]
        cx = sum(coords[nid][0] for nid in node_ids) / len(node_ids)
        cy = sum(coords[nid][1] for nid in node_ids) / len(node_ids)
        cz = sum(coords[nid][2] for nid in node_ids) / len(node_ids)
        median = statistics["median"]
        cae_target_ref, feature_ref = feature_resolver(peak_node)
        clusters.append(
            {
                "id": f"cluster_{idx:03d}",
                "location": {"x": round(cx, 6), "y": round(cy, 6), "z": round(cz, 6)},
                "magnitude": {
                    "value": round(peak_val, 4),
                    "unit": "MPa" if field.upper() in _STRESS_FIELDS else "mm",
                },
                "node_count": len(node_ids),
                # Where the peak itself sits; `location` is the cluster centroid.
                "peak_location": {
                    "node_id": peak_node,
                    "x": round(coords[peak_node][0], 6),
                    "y": round(coords[peak_node][1], 6),
                    "z": round(coords[peak_node][2], 6),
                },
                "feature_ref": feature_ref,
                "cae_target_ref": cae_target_ref,
                "peak_to_median_ratio": (
                    round(peak_val / median, 4) if median > 0 else None
                ),
            }
        )

    return _write_regions_package(
        pkg, clusters, source_member or frd.name, field, metric, warnings, overwrite=overwrite,
        statistics=statistics,
    )


# ---------------------------------------------------------------------------
# Node value computation
# ---------------------------------------------------------------------------


def _compute_node_values(
    fields: dict[str, dict[str, Any]],
    field: str,
    metric: str,
    coords: dict[int, tuple[float, float, float]],
) -> tuple[dict[int, float], list[str]]:
    """Return {node_id: scalar_value} and a warnings list."""
    warnings: list[str] = []
    node_values: dict[int, float] = {}

    # CalculiX names the stress field "STRESS" and displacement "DISP"; some
    # exports use "S". Accept the common aliases (mirrors frd_result_extractor).
    aliases = {
        "S": ("S", "STRESS"),
        "STRESS": ("STRESS", "S"),
        "DISP": ("DISP", "DISPLACEMENT"),
        "DISPLACEMENT": ("DISPLACEMENT", "DISP"),
    }
    field_data = None
    for name in aliases.get(field.upper(), (field.upper(),)):
        field_data = fields.get(name)
        if field_data:
            break
    if not field_data:
        return {}, warnings

    node_data = field_data.get("node_data", {})

    if metric == "von_mises" and field.upper() in _STRESS_FIELDS:
        from .field_derivation import stress_tensor_from_frd, von_mises

        components = field_data.get("components")
        for nid, vals in node_data.items():
            if nid not in coords:
                continue
            tensor = stress_tensor_from_frd(components, vals)
            if tensor is None or any(v is None for v in tensor):
                continue
            node_values[nid] = von_mises(tensor)

    elif metric == "magnitude" and field.upper() in _DISP_FIELDS:
        components = field_data.get("components", [])
        all_idx = next((i for i, c in enumerate(components) if c == "ALL"), None)
        d1_idx = next((i for i, c in enumerate(components) if c == "D1"), None)
        d2_idx = next((i for i, c in enumerate(components) if c == "D2"), None)
        d3_idx = next((i for i, c in enumerate(components) if c == "D3"), None)

        for nid, vals in node_data.items():
            if nid not in coords:
                continue
            if all_idx is not None and all_idx < len(vals) and vals[all_idx] is not None:
                v = abs(float(vals[all_idx]))
            elif (
                d1_idx is not None
                and d2_idx is not None
                and d3_idx is not None
                and all(idx < len(vals) and vals[idx] is not None for idx in (d1_idx, d2_idx, d3_idx))
            ):
                v = math.sqrt(
                    float(vals[d1_idx]) ** 2
                    + float(vals[d2_idx]) ** 2
                    + float(vals[d3_idx]) ** 2
                )
            else:
                continue
            node_values[nid] = v

    else:
        warnings.append(f"Unsupported metric '{metric}' for field '{field}'.")

    return node_values, warnings


# ---------------------------------------------------------------------------
# FRD mesh coordinate extraction
# ---------------------------------------------------------------------------


def _extract_node_coords_from_frd(frd_path: Path) -> dict[int, tuple[float, float, float]]:
    """Extract node coordinates from the mesh section of an FRD file.

    FRD record layouts differ between CalculiX output dialects: standard ccx
    leads each record with a single space + 2-char key (`` -1``) and an I10
    node field, while a wider dialect uses a 6-char key (``    -1``) with an
    I12 node field. The same layout detection as the metrics extractor is
    reused so both dialects parse.

    Real ccx output opens the nodal block with a ``2C`` header and closes it
    with ``-3`` — only ``-1`` records inside that block are node coordinates
    (the ``3C`` element block that follows also uses ``-1`` records). Files
    without a ``2C`` header fall back to the legacy rule: every ``-1`` record
    before the first ``-4`` result header is a node record.
    """
    try:
        text = frd_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    from .frd_result_extractor import _detect_frd_layout

    lines = text.splitlines()
    key_width, node_width = _detect_frd_layout(lines)

    coords: dict[int, tuple[float, float, float]] = {}
    saw_2c = False
    in_node_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("2C"):
            saw_2c = True
            in_node_block = True
            continue
        key = line[:key_width].strip()
        if key == "-4":
            break  # first result-block header: the mesh section is over
        if saw_2c and in_node_block and key == "-3":
            break  # explicit end of the 2C nodal block
        if key != "-1":
            continue
        if saw_2c and not in_node_block:
            continue

        node_id_str = line[key_width : key_width + node_width].strip()
        values = _slice_values(line, key_width + node_width, 3)
        if node_id_str and all(v is not None for v in values):
            try:
                coords[int(node_id_str)] = (float(values[0]), float(values[1]), float(values[2]))
            except ValueError:
                continue

    return coords


def _slice_values(line: str, start: int, count: int) -> list[float | None]:
    """Extract ``count`` sequential 12-character fixed-width floats."""
    values: list[float | None] = []
    pos = start
    for _ in range(count):
        chunk = line[pos : pos + 12].strip()
        if not chunk:
            values.append(None)
        else:
            try:
                values.append(float(chunk))
            except ValueError:
                values.append(None)
        pos += 12
    return values


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _percentile_threshold(values: list[float], percentile: float) -> float:
    """Return the threshold at the given percentile (0–100)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int((percentile / 100.0) * (len(sorted_vals) - 1))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def _field_statistics(
    values: list[float], threshold: float, percentile: float
) -> dict[str, Any]:
    """Context a reader needs to judge whether a "high" cluster is high at all."""
    ordered = sorted(values)
    n = len(ordered)
    median = (
        ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    ) if n else 0.0
    peak = ordered[-1] if n else 0.0
    ratio = round(peak / median, 4) if median > 0 else None
    return {
        "node_count": n,
        # Unrounded: a 1e-7 mm displacement field rounded to 6 decimals read
        # as all zeros beside a non-zero ratio.
        "min": ordered[0] if n else None,
        "median": median,
        "max": peak,
        "threshold_percentile": percentile,
        "threshold_value": threshold,
        "peak_to_median_ratio": ratio,
        # A zero median with a positive peak is a concentration by any measure.
        "near_uniform": ratio is not None and ratio < NEAR_UNIFORM_PEAK_TO_MEDIAN,
    }


def _auto_threshold(coords: dict[int, tuple[float, float, float]]) -> float:
    """Derive a spatial clustering threshold from the mesh bounding box.

    Uses 5 % of the bounding-box diagonal as the default distance threshold.
    """
    if not coords:
        return 1.0
    xs = [c[0] for c in coords.values()]
    ys = [c[1] for c in coords.values()]
    zs = [c[2] for c in coords.values()]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    diagonal = math.sqrt(dx * dx + dy * dy + dz * dz)
    return max(diagonal * 0.05, 1e-6)


def _cluster_nodes(
    coords: dict[int, tuple[float, float, float]],
    node_ids: set[int],
    threshold: float,
) -> list[list[int]]:
    """Group nodes into spatial clusters using distance-threshold BFS."""
    unvisited = set(node_ids)
    clusters: list[list[int]] = []

    while unvisited:
        seed = unvisited.pop()
        cluster = [seed]
        queue = [seed]
        while queue:
            current = queue.pop(0)
            cx, cy, cz = coords[current]
            # Nearest-neighbour scan (O(n²) but fine for small meshes)
            for nid in list(unvisited):
                nx, ny, nz = coords[nid]
                dist = math.sqrt((cx - nx) ** 2 + (cy - ny) ** 2 + (cz - nz) ** 2)
                if dist <= threshold:
                    unvisited.remove(nid)
                    cluster.append(nid)
                    queue.append(nid)
        clusters.append(cluster)

    # Sort by size descending
    clusters.sort(key=len, reverse=True)
    return clusters


def _limit_clusters(clusters: list[list[int]], max_clusters: int) -> list[list[int]]:
    """Merge smallest clusters until the count is within the limit."""
    if max_clusters <= 0 or len(clusters) <= max_clusters:
        return clusters

    while len(clusters) > max_clusters:
        # Sort by size ascending, merge two smallest
        clusters.sort(key=len)
        merged = clusters[0] + clusters[1]
        clusters = [merged] + clusters[2:]
        clusters.sort(key=len, reverse=True)

    return clusters


# ---------------------------------------------------------------------------
# Cluster -> feature_id mapping
# ---------------------------------------------------------------------------


_SOURCE_DECK_PATH = "simulation/cae_imports/source_solver_deck.inp"
_CAE_MAPPING_PATH = "simulation/cae_mapping.json"
_FEATURE_GRAPH_PATH = "graph/feature_graph.json"

_NO_REFS: tuple[str | None, str | None] = (None, None)


def _run_deck_member(source_member: str | None) -> str | None:
    """``simulation/runs/<id>/solver_input.inp`` for an FRD inside that run."""
    if not source_member:
        return None
    match = re.match(r"^simulation/runs/([^/]+)/", source_member.replace("\\", "/"))
    return f"simulation/runs/{match.group(1)}/solver_input.inp" if match else None


def _build_feature_resolver(
    pkg: Path,
    warnings: list[str],
    frd_coords: dict[int, tuple[float, float, float]] | None = None,
    source_member: str | None = None,
) -> Any:
    """Return a callable ``peak_node_id -> (cae_target_ref, feature_ref)``.

    * ``cae_target_ref`` — the CAE setup entity (boundary condition / load id)
      whose node set holds the peak, from the source deck's NSETs and
      ``simulation/cae_mapping.json``. That mapping records a CAE target id
      under ``maps_to.cae_target_id`` (historically misnamed ``feature_id``);
      it was never a CAD feature, so it is not reported as one.
    * ``feature_ref`` — the CAD feature, only when the evidence chain reaches
      one: the target's mapped ``face_ids`` all lie on exactly one named part
      in ``graph/feature_graph.json``.

    Either stays ``None`` when its evidence is missing or ambiguous, so the
    honesty boundary ("``feature_ref: null`` when mapping is unavailable")
    is preserved.

    The node sets must come from the mesh the FRD was solved on. The source
    deck is re-synthesised from the CURRENT mesh, so for an earlier run's FRD
    (after a remesh) its node ids meant other nodes: a cold-agent trial saw two
    different bolt holes both attributed to ``bc_001``. A run's own deck is used
    when the FRD came from that run, and in every case the deck's node
    coordinates must match the FRD's or no attribution is made.
    """
    deck_member = _run_deck_member(source_member)
    try:
        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
            if deck_member not in names:
                deck_member = _SOURCE_DECK_PATH
            deck_text = (
                zf.read(deck_member).decode("utf-8", errors="replace")
                if deck_member in names
                else None
            )
            mapping_raw = (
                json.loads(zf.read(_CAE_MAPPING_PATH))
                if _CAE_MAPPING_PATH in names
                else None
            )
            graph_bytes = zf.read(_FEATURE_GRAPH_PATH) if _FEATURE_GRAPH_PATH in names else None
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError):
        return lambda _node_id: _NO_REFS
    # The feature graph is optional evidence for feature_ref only: an unreadable
    # one must not also erase the cae_target_ref the deck + mapping establish.
    feature_graph: Any = None
    if graph_bytes is not None:
        try:
            feature_graph = json.loads(graph_bytes)
        except json.JSONDecodeError:
            warnings.append(
                f"{_FEATURE_GRAPH_PATH} is not valid JSON; cluster feature_ref stays null "
                "(cae_target_ref is unaffected)."
            )

    if deck_text is None or not isinstance(mapping_raw, dict):
        return lambda _node_id: _NO_REFS

    nset_to_target, target_faces = _build_nset_to_target(mapping_raw)
    if not nset_to_target:
        return lambda _node_id: _NO_REFS

    node_to_nsets = _build_node_to_nsets(deck_text)
    if not node_to_nsets:
        warnings.append(
            "simulation/cae_mapping.json exists but no NSET memberships were "
            "found in the source solver deck; cluster feature_ref stays null."
        )
        return lambda _node_id: _NO_REFS
    if frd_coords is not None and not _deck_matches_frd_mesh(
        deck_text, set(node_to_nsets), frd_coords
    ):
        warnings.append(
            f"The node sets in {deck_member} belong to a different mesh than this "
            "FRD (node coordinates differ), so clusters are not attributed to a CAE "
            "target: cae_target_ref and feature_ref stay null."
        )
        return lambda _node_id: _NO_REFS

    part_faces = _named_part_faces(feature_graph)

    def _resolve(node_id: int) -> tuple[str | None, str | None]:
        nsets = node_to_nsets.get(node_id, set())
        targets = set().union(*(nset_to_target.get(n, set()) for n in nsets))
        if len(targets) != 1:
            return _NO_REFS
        target = next(iter(targets))
        faces = target_faces.get(target) or set()
        owners = [fid for fid, owned in part_faces.items() if faces and faces <= owned]
        return target, (owners[0] if len(owners) == 1 else None)

    return _resolve


_NODE_OUTPUT_CARDS = ("*NODE FILE", "*NODE PRINT", "*NODE OUTPUT")


def _deck_matches_frd_mesh(
    deck_text: str,
    node_ids: set[int],
    frd_coords: dict[int, tuple[float, float, float]],
) -> bool:
    """Do the deck's coordinates for ``node_ids`` agree with the FRD's?

    The FRD writes coordinates to ~5 significant digits, so the tolerance is
    relative to the model size; a different mesh puts the same node id
    somewhere else entirely.
    """
    deck_coords = _parse_deck_node_coords(deck_text, node_ids)
    if not deck_coords or len(deck_coords) != len(node_ids):
        return False
    values = [c for xyz in frd_coords.values() for c in xyz]
    tolerance = max(1e-3, 1e-4 * (max(values) - min(values))) if values else 1e-3
    for nid, (x, y, z) in deck_coords.items():
        other = frd_coords.get(nid)
        if other is None:
            return False
        if max(abs(x - other[0]), abs(y - other[1]), abs(z - other[2])) > tolerance:
            return False
    return True


def _parse_deck_node_coords(
    deck_text: str, wanted: set[int]
) -> dict[int, tuple[float, float, float]]:
    """Coordinates of ``wanted`` nodes from the deck's ``*NODE`` blocks."""
    coords: dict[int, tuple[float, float, float]] = {}
    in_node = False
    for raw_line in deck_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper = line.upper()
            in_node = upper.startswith("*NODE") and not upper.startswith(_NODE_OUTPUT_CARDS)
            continue
        if not in_node:
            continue
        parts = line.split(",")
        try:
            nid = int(parts[0])
        except (ValueError, IndexError):
            continue
        if nid in wanted and len(parts) >= 4:
            try:
                coords[nid] = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                continue
    return coords


def _build_nset_to_target(
    mapping_raw: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(nset -> CAE target ids, CAE target id -> mapped face ids)``.

    One NSET may serve several targets (a load and a BC on the same faces);
    all are kept, so the resolver can see that such a node is ambiguous instead
    of the last mapping silently winning.
    """
    from aieng.simulation.cae_mapping_writer import mapping_target_id

    nset_to_target: dict[str, set[str]] = {}
    target_faces: dict[str, set[str]] = {}
    mappings = mapping_raw.get("mappings")
    if not isinstance(mappings, list):
        return nset_to_target, target_faces
    for m in mappings:
        if not isinstance(m, dict):
            continue
        cae_entity = m.get("cae_entity")
        target = mapping_target_id(m)
        if not isinstance(cae_entity, str) or target is None:
            continue
        nset_to_target.setdefault(cae_entity, set()).add(target)
        face_ids = m.get("face_ids")
        if isinstance(face_ids, list):
            target_faces.setdefault(target, set()).update(str(f) for f in face_ids)
    return nset_to_target, target_faces


def _named_part_faces(feature_graph: Any) -> dict[str, set[str]]:
    """Named-part feature id -> the topology ids it owns.

    ``geometry_refs`` is ``{faces: [...]}`` (preferred) or, per the schema's
    backward-compatible form, a plain list of topology ids. Either is evidence;
    extra non-face ids in the list form do not matter to a subset test.
    """
    out: dict[str, set[str]] = {}
    features = feature_graph.get("features") if isinstance(feature_graph, dict) else None
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict):
            continue
        if feature.get("type") not in {"named_part", "standard_part"}:
            continue
        refs = feature.get("geometry_refs")
        faces = refs.get("faces") if isinstance(refs, dict) else refs
        if isinstance(feature.get("id"), str) and isinstance(faces, list) and faces:
            out[feature["id"]] = {str(f) for f in faces}
    return out


def _build_node_to_nsets(deck_text: str) -> dict[int, set[str]]:
    """Parse *NSET blocks from a CalculiX-style deck and return node->nsets."""
    node_to_nsets: dict[int, set[str]] = {}
    current_nset: str | None = None

    for raw_line in deck_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper = line.upper()
            if upper.startswith("*NSET"):
                current_nset = _extract_nset_name(line)
            else:
                current_nset = None
            continue
        if current_nset is None:
            continue
        for token in line.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                node_id = int(token)
            except ValueError:
                continue
            node_to_nsets.setdefault(node_id, set()).add(current_nset)

    return node_to_nsets


def _extract_nset_name(keyword_line: str) -> str | None:
    """Extract the NSET= name from a ``*NSET, NSET=NAME[, ...]`` line."""
    for part in keyword_line.split(","):
        token = part.strip()
        upper = token.upper()
        if upper.startswith("NSET="):
            return token.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Package writeback
# ---------------------------------------------------------------------------


def _write_regions_package(
    path: Path,
    clusters: list[dict[str, Any]],
    frd_name: str,
    field: str,
    metric: str,
    warnings: list[str],
    overwrite: bool,
    statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically write ``results/field_regions.json`` into the .aieng package."""
    try:
        with zipfile.ZipFile(path, mode="r") as zf:
            names = set(zf.namelist())
            if FIELD_REGIONS_PATH in names and not overwrite:
                raise FileExistsError(
                    f"{FIELD_REGIONS_PATH} already exists; pass overwrite=true to replace it"
                )
            manifest = json.loads(zf.read("manifest.json"))
            members = _read_existing_members(zf)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"package is not a valid zip archive: {path}") from exc

    payload = {
        "schema_version": FIELD_REGIONS_SCHEMA,
        "format_version": FORMAT_VERSION,
        "source_frd": frd_name,
        "field": field,
        "metric": metric,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "warnings": warnings,
        **({"field_statistics": statistics} if statistics is not None else {}),
        "claim_policy": {
            "observational_only": True,
            "physical_correctness_not_claimed": True,
            "solver_execution_not_performed_by_aieng": True,
        },
    }
    payload_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()

    resources = manifest.setdefault("resources", {})
    results_resources = resources.setdefault("results", {})
    if not isinstance(results_resources, dict):
        results_resources = {}
        resources["results"] = results_resources
    results_resources["field_regions"] = FIELD_REGIONS_PATH

    manifest_json = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as fh:
        temp_path = Path(fh.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for info, data in members:
                zf.writestr(info, data)
            zf.writestr("manifest.json", manifest_json)
            zf.writestr(FIELD_REGIONS_PATH, payload_bytes)
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "ok": True,
        "out_path": FIELD_REGIONS_PATH,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "field_statistics": statistics,
        "warnings": warnings,
    }


def _read_existing_members(
    package: zipfile.ZipFile,
) -> list[tuple[zipfile.ZipInfo, bytes]]:
    skip = {"manifest.json", FIELD_REGIONS_PATH}
    seen: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info in package.infolist():
        if info.filename in skip or info.filename in seen:
            continue
        seen.add(info.filename)
        data = b"" if info.is_dir() else package.read(info.filename)
        members.append((info, data))
    return members
