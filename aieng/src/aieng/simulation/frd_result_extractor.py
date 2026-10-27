"""CalculiX FRD result file parser.

Parses CalculiX FRD text format (binary FRD is not supported here).
Extracts DISP (nodal displacement) and S (stress tensor) fields,
computes scalar extrema, and writes results/computed_metrics.json
into a .aieng package.

Limitations:
- Only DISP and S fields are processed.
- Continuation lines (-2) are supported for completeness but rarely needed
  since DISP (4 components) and S (6 components) each fit in one -1 line.
- Binary FRD format is not supported; file must be UTF-8 text.
"""
from __future__ import annotations

import json
import math
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ..schema_versions import FRD_COMPUTED_METRICS_SCHEMA
from .field_derivation import stress_tensor_from_frd, von_mises

METRICS_PATH = "results/computed_metrics.json"

# What the extrema ARE, stated with the number. CalculiX computes stresses at
# the integration points, extrapolates them to the nodes and averages over the
# elements sharing each node before writing the .frd (ccx manual, *EL FILE), and
# von Mises is then formed from those averaged components here. The peak of an
# averaged nodal field is lower than the integration-point / unaveraged peak at
# a concentration, and it moves with the mesh — see cae.mesh_convergence.
_STRESS_BASIS = (
    "max over nodes of von Mises formed from CalculiX nodal stresses "
    "(integration-point values extrapolated to nodes and averaged over adjacent elements)"
)
_DISP_BASIS = "max over nodes of the nodal displacement magnitude sqrt(D1^2+D2^2+D3^2)"
# CalculiX has no temperature unit: NDTEMP is in whatever scale the applied
# temperature boundary conditions used. Stamping "K" invented one — a setup
# written in degrees Celsius (100 at the root) was reported as 100 K.
_TEMP_UNIT = "as_applied"
_TEMP_BASIS = (
    "max/min over nodes of NDTEMP, in the same scale (degC or K) as the applied "
    "temperature boundary conditions; CalculiX carries no temperature unit"
)
_BINARY_FRD_UNSUPPORTED = (
    "binary or non-UTF-8 FRD files are unsupported by frd_parser_v1; "
    "provide a CalculiX text FRD export before extracting computed metrics"
)


# ---------------------------------------------------------------------------
# FRD parser
# ---------------------------------------------------------------------------

def _format_indicator(lines: list[str]) -> int | None:
    """The FORMAT field of the first ``2C`` / ``100C`` header, or None.

    CalculiX GraphiX manual, "Result Format": FORMAT is 0 = short ASCII
    (data records ``(1X,I2,I5,6E12.5)``), 1 = long ASCII (``(1X,I2,I10,6E12.5)``),
    2 = binary. It is the last integer on both header records.
    """
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("2C") or stripped.startswith("100C"):
            token = stripped.split()[-1] if stripped.split() else ""
            try:
                return int(token)
            except ValueError:
                return None
    return None


def _detect_frd_layout(lines: list[str]) -> tuple[int, int]:
    """Detect the FRD record key/node field widths.

    Returns ``(key_width, node_width)``. CalculiX leads each record with a single
    space + 2-char key (" -4", key_width=3) followed by the node number: I10 in
    the long format ccx writes, I5 in the short format (read from the header's
    FORMAT indicator — guessing I10 for a short-format file read the first value
    into the node id and silently dropped every record). A wider dialect uses
    key_width=6 / node_width=12. Defaults to the wide layout when no tag line is
    found.
    """
    for line in lines:
        stripped = line.lstrip()
        if stripped[:2] in ("-1", "-2", "-3", "-4", "-5"):
            lead = len(line) - len(stripped)
            key_width = lead + 2
            if key_width >= 6:
                return key_width, 12
            return key_width, 5 if _format_indicator(lines) == 0 else 10
    return 6, 12


def _parse_step_header(line: str) -> dict[str, Any] | None:
    """Parse a ``100C`` nodal-results header record (fixed columns).

    Layout (cgx manual): ``'  100','C',6A1,E12.5,I12,20A1,I2,I5,10A1,I2`` =
    KEY, CODE, SETNAME, VALUE, NUMNOD, TEXT, ICTYPE, NUMSTP, ANALYS, FORMAT.
    ICTYPE: 0 static, 1 time step, 2 frequency, 3 load step, 4 user named.
    VALUE is the step time, the eigenfrequency (cycles/time) of a modal step, or
    the buckling factor of a ccx ``*BUCKLE`` mode.
    """
    if not line.strip().startswith("100C"):
        return None
    start = line.index("100C") + 4  # column after CODE
    body = line[start:]
    try:
        value = float(body[6:18])
    except ValueError:
        value = None
    def _int(text: str) -> int | None:
        try:
            return int(text)
        except ValueError:
            return None
    return {
        "value": value,
        "analysis_code": _int(body[50:52]),
        "step_number": _int(body[52:57]),
        "analysis_text": body[57:67].strip() or None,
        "format": _int(body[67:69]),
    }


def _parse_frd_text(text: str) -> list[dict[str, Any]]:
    """Parse FRD text and return a flat list of datasets in file order.

    Each dataset is a dict with:
      - ``field_name``: upper-case field name (e.g. ``'DISP'``, ``'S'``)
      - ``components``: list of component name strings
      - ``node_data``: dict mapping node ID (int) to list of float | None
      - ``step_index``: sequential dataset index within this field
    """
    lines = text.splitlines()
    datasets: list[dict[str, Any]] = []
    i = 0
    field_occurrence: dict[str, int] = {}

    # FRD record layout differs between CalculiX output dialects: standard ccx
    # writes a 1-space-led key (" -4"/" -1") with an I10 node field
    # (key_width=3, node_width=10, values from col 13), while some exports use a
    # wider key (key_width=6, node_width=12, values from col 18). Detect the
    # widths once so both parse correctly.
    key_width, node_width = _detect_frd_layout(lines)
    value_start = key_width + node_width

    def _key(text_line: str) -> str:
        return text_line[:key_width].strip()

    step_header: dict[str, Any] | None = None

    while i < len(lines):
        line = lines[i]

        header = _parse_step_header(line)
        if header is not None:
            step_header = header
            i += 1
            continue

        if _key(line) == "-4":
            # The FRD spec gives every result block its own FORMAT flag (on its
            # 100C header), independent of the coordinate block's: a file may
            # store coordinates short (I5) and results long (I10). Read node ids
            # with the width THIS block declares.
            block_value_start = value_start
            if key_width == 3 and step_header and step_header.get("format") in (0, 1):
                block_value_start = key_width + (5 if step_header["format"] == 0 else 10)
            # Field header: "<key>  FIELDNAME  n_components  ..."
            parts = line[key_width:].split()
            if not parts:
                i += 1
                continue
            field_name = parts[0].strip().upper()
            try:
                n_components = int(parts[1]) if len(parts) >= 2 else 0
            except ValueError:
                n_components = 0

            # Read component names from consecutive -5 lines
            i += 1
            component_names: list[str] = []
            while i < len(lines) and _key(lines[i]) == "-5":
                cparts = lines[i][key_width:].split()
                component_names.append(cparts[0].upper() if cparts else "")
                i += 1

            # Read per-node data from -1 and -2 lines
            node_data: dict[int, list[float | None]] = {}
            current_node_id: int | None = None

            while i < len(lines):
                ln = lines[i]
                t = _key(ln)

                if t == "-3":
                    i += 1
                    break

                if t == "-1":
                    try:
                        current_node_id = int(ln[key_width:block_value_start])
                    except (ValueError, IndexError):
                        i += 1
                        continue
                    node_data[current_node_id] = _slice_values(
                        ln, block_value_start, n_components
                    )

                elif t == "-2" and current_node_id is not None:
                    # Continuation line for the same node; -2 lines also carry the
                    # node-ID field before the extra values.
                    already = len(node_data.get(current_node_id, []))
                    remaining = n_components - already
                    if remaining > 0:
                        more = _slice_values(ln, block_value_start, remaining)
                        node_data.setdefault(current_node_id, []).extend(more)

                i += 1

            if field_name and node_data:
                occurrence = field_occurrence.get(field_name, 0)
                datasets.append(
                    {
                        "field_name": field_name,
                        "components": component_names,
                        "node_data": node_data,
                        "step_index": occurrence,
                        # The 100C header this block belongs to (None for files
                        # without one). Lets callers group DISP/STRESS of the
                        # same step and recognise eigenmode blocks.
                        "step": step_header,
                    }
                )
                field_occurrence[field_name] = occurrence + 1

        else:
            i += 1

    return datasets


def _read_frd_text(frd_path: Path) -> str:
    try:
        raw = frd_path.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(f"FRD file not found or unreadable: {frd_path}") from exc
    if b"\x00" in raw:
        raise ValueError(_BINARY_FRD_UNSUPPORTED)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(_BINARY_FRD_UNSUPPORTED) from exc


def parse_frd_steps(frd_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Parse a CalculiX FRD text file and return per-field, per-step data.

    Args:
        frd_path: Path to the .frd file.

    Returns:
        Dict keyed by field name; each value is a list of datasets, one per
        step/analysis increment in file order.  Modal/buckling mode shapes and
        multi-step static results therefore appear as successive list entries.
    """
    text = _read_frd_text(frd_path)
    datasets = _parse_frd_text(text)
    fields: dict[str, list[dict[str, Any]]] = {}
    for dataset in datasets:
        fields.setdefault(dataset["field_name"], []).append(dataset)
    return fields


def parse_frd(frd_path: Path) -> dict[str, dict[str, Any]]:
    """Parse a CalculiX FRD text file and return per-field node data.

    Backward-compatible convenience wrapper: returns the **first** dataset for
    each field name. For multi-step files use :func:`parse_frd_steps`.

    Args:
        frd_path: Path to the .frd file.

    Returns:
        Dict keyed by field name (e.g. ``'DISP'``, ``'S'``), each containing:
          - ``components``: list of component name strings
          - ``node_data``: dict mapping node ID (int) to list of float | None
    """
    fields = parse_frd_steps(frd_path)
    return {
        field_name: datasets[0] if datasets else {}
        for field_name, datasets in fields.items()
    }


def _slice_values(line: str, start: int, count: int) -> list[float | None]:
    """Slice up to ``count`` fixed-width 12-char values from ``line[start:]``."""
    values: list[float | None] = []
    pos = start
    while len(values) < count:
        chunk = line[pos:pos + 12] if pos + 12 <= len(line) else line[pos:]
        chunk = chunk.strip()
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
# Extrema computation
# ---------------------------------------------------------------------------

def _extract_step_metrics(
    disp_dataset: dict[str, Any] | None,
    stress_dataset: dict[str, Any] | None,
    warnings: list[str],
    temp_dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return scalar metrics for one FRD step, appending any warnings."""
    metrics: dict[str, Any] = {}
    # A steady-state thermal step carries NDTEMP but no DISP/S — don't emit the
    # structural "field not found" warnings in that case.
    thermal_only = temp_dataset is not None and not disp_dataset and not stress_dataset

    # --- Max displacement ---------------------------------------------------
    if disp_dataset:
        components = disp_dataset["components"]
        node_data = disp_dataset["node_data"]

        all_idx = next((i for i, c in enumerate(components) if c == "ALL"), None)
        d1_idx = next((i for i, c in enumerate(components) if c == "D1"), None)
        d2_idx = next((i for i, c in enumerate(components) if c == "D2"), None)
        d3_idx = next((i for i, c in enumerate(components) if c == "D3"), None)

        max_disp: float | None = None
        for vals in node_data.values():
            if all_idx is not None and all_idx < len(vals) and vals[all_idx] is not None:
                v: float = abs(float(vals[all_idx]))
            elif (
                d1_idx is not None
                and d2_idx is not None
                and d3_idx is not None
                and all(
                    idx < len(vals) and vals[idx] is not None
                    for idx in (d1_idx, d2_idx, d3_idx)
                )
            ):
                v = math.sqrt(
                    float(vals[d1_idx]) ** 2
                    + float(vals[d2_idx]) ** 2
                    + float(vals[d3_idx]) ** 2
                )
            else:
                continue
            if max_disp is None or v > max_disp:
                max_disp = v

        if max_disp is not None:
            metrics["max_displacement"] = {
                "value": round(max_disp, 6), "unit": "mm", "basis": _DISP_BASIS,
            }
        else:
            warnings.append(
                "DISP field present but no valid displacement values could be extracted"
            )
    elif not thermal_only:
        warnings.append("DISP field not found in FRD file; max_displacement not computed")

    # --- Max von Mises stress -----------------------------------------------
    if stress_dataset:
        node_data = stress_dataset["node_data"]
        components = stress_dataset.get("components")
        max_vm: float | None = None
        for vals in node_data.values():
            tensor = stress_tensor_from_frd(components, vals)
            if tensor is None or any(v is None for v in tensor):
                continue
            vm = von_mises(tensor)
            if max_vm is None or vm > max_vm:
                max_vm = vm

        if max_vm is not None:
            metrics["max_von_mises_stress"] = {
                "value": round(max_vm, 4), "unit": "MPa", "basis": _STRESS_BASIS,
            }
        else:
            warnings.append(
                "S field present but no valid stress tensor values could be extracted"
            )
    elif not thermal_only:
        warnings.append(
            "S (stress tensor) field not found in FRD file; "
            "max_von_mises_stress not computed"
        )

    # --- Temperature (steady-state thermal) ---------------------------------
    if temp_dataset:
        node_data = temp_dataset["node_data"]
        temps = [
            float(vals[0])
            for vals in node_data.values()
            if vals and vals[0] is not None
        ]
        if temps:
            metrics["max_temperature"] = {
                "value": round(max(temps), 4), "unit": _TEMP_UNIT, "basis": _TEMP_BASIS,
            }
            metrics["min_temperature"] = {
                "value": round(min(temps), 4), "unit": _TEMP_UNIT, "basis": _TEMP_BASIS,
            }
        else:
            warnings.append(
                "NDTEMP field present but no valid temperature values could be extracted"
            )

    return metrics


def _load_case_id_for_step(
    step_index: int,
    *,
    provided_id: str,
    provided_ids: list[str] | None,
    n_steps: int,
) -> str:
    """Pick a load-case id for a given step."""
    if provided_ids is not None and step_index < len(provided_ids):
        return provided_ids[step_index]
    if n_steps == 1:
        return provided_id
    return f"load_case_{step_index + 1:03d}"


_BUCKLING_TYPES = ("buckling", "buckle")


def _step_groups(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group FRD blocks into analysis steps: ``[{step, fields: {name: dataset}}]``.

    When every block carries its ``100C`` header the blocks are grouped by that
    header (step number, step value, analysis type) — the solver's own statement
    of which DISP belongs with which STRESS. Files without headers fall back to
    pairing the n-th block of each field.
    """
    if datasets and all(d.get("step") for d in datasets):
        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        for d in datasets:
            h = d["step"]
            key = (h.get("step_number"), h.get("value"), h.get("analysis_code"))
            group = groups.setdefault(key, {"step": h, "fields": {}})
            group["fields"].setdefault(d["field_name"], d)
        return list(groups.values())
    by_field: dict[str, list[dict[str, Any]]] = {}
    for d in datasets:
        by_field.setdefault(d["field_name"], []).append(d)
    n = max((len(v) for v in by_field.values()), default=0)
    return [
        {"step": None, "fields": {name: v[i] for name, v in by_field.items() if i < len(v)}}
        for i in range(n)
    ]


def _eigenmode_kind(step: dict[str, Any] | None, analysis_type: str | None) -> str | None:
    """``"modal"`` / ``"buckling"`` when this step holds an eigenmode, else None.

    ICTYPE 2 is "frequency" in the FRD spec, so a modal mode is recognised from
    the file alone. CalculiX writes ``*BUCKLE`` modes as ICTYPE 4 ("user
    named") with VALUE = the buckling factor, after a VALUE 0 block holding the
    real stress state under the reference load (measured on ccx output). ICTYPE
    4 is not specific to buckling, so a buckling mode is recognised only when
    the caller says the analysis IS buckling.
    """
    if not step:
        return None
    if step.get("analysis_code") == 2:
        return "modal"
    if (
        (analysis_type or "").lower() in _BUCKLING_TYPES
        and step.get("analysis_code") == 4
        and step.get("value") not in (None, 0.0)
    ):
        return "buckling"
    return None


def _eigen_load_case(kind: str, values: list[float]) -> dict[str, Any]:
    """One load case carrying the eigenvalues, in the `.dat` extractor's shape."""
    from .dat_result_extractor import _BUCKLING_NOTE, _MODAL_NOTE

    basis = "VALUE of each mode's FRD step header (100C record)"
    if kind == "modal":
        return {
            "id": "modal_001",
            "metrics": {
                "natural_frequencies_hz": {
                    "value": [round(v, 6) for v in values], "unit": "Hz",
                    "note": _MODAL_NOTE, "basis": basis,
                },
                "first_natural_frequency_hz": {
                    "value": round(values[0], 6), "unit": "Hz",
                    "note": _MODAL_NOTE, "basis": basis,
                },
            },
        }
    return {
        "id": "buckling_001",
        "metrics": {
            "buckling_factors": {
                "value": [round(v, 6) for v in values], "unit": "dimensionless",
                "note": _BUCKLING_NOTE, "basis": basis,
            },
            "lowest_buckling_factor": {
                "value": round(min(values, key=abs), 6), "unit": "dimensionless",
                "note": _BUCKLING_NOTE, "basis": basis,
            },
        },
    }


def extract_computed_metrics(
    frd_path: Path,
    *,
    load_case_id: str = "load_case_001",
    load_case_ids: list[str] | None = None,
    software: str = "CalculiX",
    source_files: list[str] | None = None,
    run_id: str | None = None,
    analysis_type: str | None = None,
) -> dict[str, Any]:
    """Extract scalar extrema from a CalculiX FRD file.

    Returns a dict matching the ``results/computed_metrics.json`` schema
    (version ``"0.1"``).  Multi-step FRDs produce one load case per step.

    Eigenmode steps (modal, and buckling when ``analysis_type`` says so) are not
    reported as ``max_displacement`` / ``max_von_mises_stress``: a mode shape is a
    normalised eigenvector, so its amplitude in "mm" and its "stress" in "MPa"
    are not physical results. Their eigenvalues are reported instead, under the
    same metric names the ``.dat`` extractor uses.

    Args:
        frd_path: Path to the CalculiX ``.frd`` file.
        load_case_id: Load case ID for a single-step result. Ignored when
            ``load_case_ids`` is supplied or when the FRD contains multiple steps.
        load_case_ids: Optional explicit load-case ids, one per FRD step.
        software: Name of the solver software (used in metrics_source).
        source_files: In-package artifact paths to record instead of the
            filesystem path the FRD was staged at. A temp path is useless to
            anyone who opens the package later.
        run_id: The solver run these metrics came from, recorded so an exported
            package can still say which run supports which number.
        analysis_type: The analysis the run performed, when known (e.g. from
            ``solver_run.json``). Needed to recognise buckling modes.

    Returns:
        Dict with ``schema_version``, ``metrics_source``, ``load_cases``,
        and ``warnings``.
    """
    datasets = _parse_frd_text(_read_frd_text(frd_path))
    groups = _step_groups(datasets) or [{"step": None, "fields": {}}]

    warnings: list[str] = []
    # (FRD step index, group) — the index into `load_case_ids` is the step's
    # position in the FILE, so an eigenmode step removed from the physical
    # list must not shift the explicit ids of the steps after it.
    physical: list[tuple[int, dict[str, Any]]] = []
    eigen: dict[str, list[float]] = {}
    for frd_step_index, group in enumerate(groups):
        step = group["step"] or {}
        kind = _eigenmode_kind(group["step"], analysis_type)
        if kind is not None:
            if step.get("value") is not None:
                eigen.setdefault(kind, []).append(float(step["value"]))
            continue
        physical.append((frd_step_index, group))
        if (
            analysis_type is None
            and step.get("analysis_code") == 4
            and step.get("value") not in (None, 0.0)
        ):
            warnings.append(
                f"FRD step {step.get('step_number')} is a 'user named' step (ICTYPE 4) "
                f"with value {step.get('value')}; CalculiX writes *BUCKLE mode shapes "
                "this way. Its magnitudes are reported as-is because the analysis "
                "type was not given — if this is a buckling run they are normalised "
                "mode shapes, not physical results."
            )
    for kind, values in eigen.items():
        warnings.append(
            f"{len(values)} {kind} eigenmode step(s): their DISP/STRESS blocks are "
            "normalised mode shapes, so they are not reported as max_displacement / "
            "max_von_mises_stress; the eigenvalues are reported instead."
        )

    load_cases: list[dict[str, Any]] = []
    n_steps = len(physical)
    for step_index, (frd_step_index, group) in enumerate(physical):
        fields = group["fields"]
        disp = fields.get("DISP")
        # CalculiX names the stress field "STRESS"; some exports use "S".
        stress = fields.get("S") or fields.get("STRESS")
        # Steady-state heat transfer emits nodal temperature as NDTEMP (some
        # exports use TEMP).
        temp = fields.get("NDTEMP") or fields.get("TEMP")
        case_metrics = _extract_step_metrics(disp, stress, warnings, temp)
        if load_case_ids is not None and frd_step_index < len(load_case_ids):
            case_id = load_case_ids[frd_step_index]
        else:
            case_id = _load_case_id_for_step(
                step_index, provided_id=load_case_id, provided_ids=None, n_steps=n_steps,
            )
        load_cases.append({"id": case_id, "metrics": case_metrics})
    for kind, values in eigen.items():
        load_cases.append(_eigen_load_case(kind, values))

    # `frd_path` is wherever the caller staged the file — usually a temp
    # directory that no longer exists by the time anyone reads the package. A
    # caller that knows the in-package artifact and the run should say so, or
    # an exported package cannot answer "which run produced this number".
    metrics_source: dict[str, Any] = {
        "tool": "frd_parser_v1",
        "software": software,
        "source_files": list(source_files) if source_files else [str(frd_path)],
    }
    if run_id:
        metrics_source["run_id"] = run_id
    return {
        "schema_version": FRD_COMPUTED_METRICS_SCHEMA,
        "metrics_source": metrics_source,
        "load_cases": load_cases,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Package write-back
# ---------------------------------------------------------------------------

def _package_analysis_type(package_path: Path, run_id: str | None) -> str | None:
    """The analysis type the package records for the run ``run_id`` produced.

    Only for a run-bound FRD: an external FRD (no ``run_id``) is not described
    by the CURRENT settings — a buckling result read under ``static`` settings
    would turn mode shapes back into physical numbers and silence the
    unknown-analysis warning — so it stays unknown.
    """
    if not run_id:
        return None
    candidates = [f"simulation/runs/{run_id}/solver_run.json", "simulation/solver_settings.json"]
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = set(zf.namelist())
            for member in candidates:
                if member not in names:
                    continue
                data = json.loads(zf.read(member))
                value = data.get("analysis_type") if isinstance(data, dict) else None
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError):
        return None
    return None


def write_computed_metrics_package(
    package_path: Path,
    frd_path: Path,
    *,
    load_case_id: str = "load_case_001",
    software: str = "CalculiX",
    overwrite: bool = True,
    source_files: list[str] | None = None,
    run_id: str | None = None,
    analysis_type: str | None = None,
) -> dict[str, Any]:
    """Extract FRD metrics and write ``results/computed_metrics.json`` into
    the package atomically.

    Args:
        package_path: Path to the ``.aieng`` package.
        frd_path: Path to the CalculiX ``.frd`` file to parse.
        load_case_id: Load case ID for the metrics.
        software: Solver software name.
        overwrite: Replace existing ``computed_metrics.json`` if present.
        analysis_type: The analysis the FRD came from. When omitted for a
            run-bound FRD (``run_id`` given) it is read from the package — the
            run's own ``solver_run.json`` first (what was actually solved), then
            ``simulation/solver_settings.json`` — because ccx writes ``*BUCKLE``
            modes as a generic step type. An external FRD stays unknown.

    Returns:
        The computed metrics dict (same as :func:`extract_computed_metrics`).
    """
    if analysis_type is None:
        analysis_type = _package_analysis_type(package_path, run_id)
    metrics = extract_computed_metrics(
        frd_path, load_case_id=load_case_id, software=software,
        source_files=source_files, run_id=run_id, analysis_type=analysis_type,
    )
    metrics_bytes = (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode()

    with zipfile.ZipFile(package_path, "r") as zf:
        existing = set(zf.namelist())
        if not overwrite and METRICS_PATH in existing:
            raise FileExistsError(
                f"{METRICS_PATH} already exists; use overwrite=True to replace"
            )
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in existing else {}
        members: list[tuple[zipfile.ZipInfo, bytes]] = []
        seen: set[str] = set()
        for info in zf.infolist():
            if info.filename in seen or info.filename in (METRICS_PATH, "manifest.json"):
                continue
            seen.add(info.filename)
            data = b"" if info.is_dir() else zf.read(info.filename)
            members.append((info, data))

    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".aieng", dir=package_path.parent
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out:
            out.writestr("manifest.json", manifest_bytes)
            for info, data in members:
                out.writestr(info, data)
            out.writestr(METRICS_PATH, metrics_bytes)
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return metrics
