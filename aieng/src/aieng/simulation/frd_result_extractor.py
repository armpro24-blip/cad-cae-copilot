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

METRICS_PATH = "results/computed_metrics.json"
_BINARY_FRD_UNSUPPORTED = (
    "binary or non-UTF-8 FRD files are unsupported by frd_parser_v1; "
    "provide a CalculiX text FRD export before extracting computed metrics"
)


# ---------------------------------------------------------------------------
# FRD parser
# ---------------------------------------------------------------------------

def _detect_frd_layout(lines: list[str]) -> tuple[int, int]:
    """Detect the FRD record key/node field widths from the first tag line.

    Returns ``(key_width, node_width)``. Standard CalculiX output leads each
    record with a single space + 2-char key (" -4", key_width=3) and an I10 node
    field (node_width=10); a wider dialect uses key_width=6 / node_width=12.
    Defaults to the wide layout when no tag line is found.
    """
    for line in lines:
        stripped = line.lstrip()
        if stripped[:2] in ("-1", "-2", "-3", "-4", "-5"):
            lead = len(line) - len(stripped)
            key_width = lead + 2
            node_width = 12 if key_width >= 6 else 10
            return key_width, node_width
    return 6, 12


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

    while i < len(lines):
        line = lines[i]

        if _key(line) == "-4":
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
                        current_node_id = int(ln[key_width:value_start])
                    except (ValueError, IndexError):
                        i += 1
                        continue
                    node_data[current_node_id] = _slice_values(ln, value_start, n_components)

                elif t == "-2" and current_node_id is not None:
                    # Continuation line for the same node; -2 lines also carry the
                    # node-ID field before the extra values.
                    already = len(node_data.get(current_node_id, []))
                    remaining = n_components - already
                    if remaining > 0:
                        more = _slice_values(ln, value_start, remaining)
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
            metrics["max_displacement"] = {"value": round(max_disp, 6), "unit": "mm"}
        else:
            warnings.append(
                "DISP field present but no valid displacement values could be extracted"
            )
    elif not thermal_only:
        warnings.append("DISP field not found in FRD file; max_displacement not computed")

    # --- Max von Mises stress -----------------------------------------------
    if stress_dataset:
        node_data = stress_dataset["node_data"]
        max_vm: float | None = None
        for vals in node_data.values():
            if len(vals) < 6 or any(v is None for v in vals[:6]):
                continue
            sxx, syy, szz = float(vals[0]), float(vals[1]), float(vals[2])
            sxy, sxz, syz = float(vals[3]), float(vals[4]), float(vals[5])
            vm = math.sqrt(
                0.5 * (
                    (sxx - syy) ** 2
                    + (syy - szz) ** 2
                    + (szz - sxx) ** 2
                    + 6.0 * (sxy ** 2 + sxz ** 2 + syz ** 2)
                )
            )
            if max_vm is None or vm > max_vm:
                max_vm = vm

        if max_vm is not None:
            metrics["max_von_mises_stress"] = {"value": round(max_vm, 4), "unit": "MPa"}
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
            metrics["max_temperature"] = {"value": round(max(temps), 4), "unit": "K"}
            metrics["min_temperature"] = {"value": round(min(temps), 4), "unit": "K"}
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


def extract_computed_metrics(
    frd_path: Path,
    *,
    load_case_id: str = "load_case_001",
    load_case_ids: list[str] | None = None,
    software: str = "CalculiX",
) -> dict[str, Any]:
    """Extract scalar extrema from a CalculiX FRD file.

    Returns a dict matching the ``results/computed_metrics.json`` schema
    (version ``"0.1"``).  Multi-step FRDs produce one load case per step.

    Args:
        frd_path: Path to the CalculiX ``.frd`` file.
        load_case_id: Load case ID for a single-step result. Ignored when
            ``load_case_ids`` is supplied or when the FRD contains multiple steps.
        load_case_ids: Optional explicit load-case ids, one per FRD step.
        software: Name of the solver software (used in metrics_source).

    Returns:
        Dict with ``schema_version``, ``metrics_source``, ``load_cases``,
        and ``warnings``.
    """
    fields = parse_frd_steps(frd_path)
    disp_steps = fields.get("DISP", [])
    # CalculiX names the stress field "STRESS"; some exports use "S". Accept both.
    stress_steps = fields.get("S") or fields.get("STRESS") or []
    # Steady-state heat transfer emits nodal temperature as NDTEMP (some exports
    # use TEMP).
    temp_steps = fields.get("NDTEMP") or fields.get("TEMP") or []
    n_steps = max(len(disp_steps), len(stress_steps), len(temp_steps), 1)

    warnings: list[str] = []
    load_cases: list[dict[str, Any]] = []

    for step_index in range(n_steps):
        disp = disp_steps[step_index] if step_index < len(disp_steps) else None
        stress = stress_steps[step_index] if step_index < len(stress_steps) else None
        temp = temp_steps[step_index] if step_index < len(temp_steps) else None
        case_metrics = _extract_step_metrics(disp, stress, warnings, temp)
        case_id = _load_case_id_for_step(
            step_index,
            provided_id=load_case_id,
            provided_ids=load_case_ids,
            n_steps=n_steps,
        )
        load_cases.append({"id": case_id, "metrics": case_metrics})

    return {
        "schema_version": FRD_COMPUTED_METRICS_SCHEMA,
        "metrics_source": {
            "tool": "frd_parser_v1",
            "software": software,
            "source_files": [str(frd_path)],
        },
        "load_cases": load_cases,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Package write-back
# ---------------------------------------------------------------------------

def write_computed_metrics_package(
    package_path: Path,
    frd_path: Path,
    *,
    load_case_id: str = "load_case_001",
    software: str = "CalculiX",
    overwrite: bool = True,
) -> dict[str, Any]:
    """Extract FRD metrics and write ``results/computed_metrics.json`` into
    the package atomically.

    Args:
        package_path: Path to the ``.aieng`` package.
        frd_path: Path to the CalculiX ``.frd`` file to parse.
        load_case_id: Load case ID for the metrics.
        software: Solver software name.
        overwrite: Replace existing ``computed_metrics.json`` if present.

    Returns:
        The computed metrics dict (same as :func:`extract_computed_metrics`).
    """
    metrics = extract_computed_metrics(
        frd_path, load_case_id=load_case_id, software=software
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
