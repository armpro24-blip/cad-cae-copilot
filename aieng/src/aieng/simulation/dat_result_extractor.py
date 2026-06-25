"""CalculiX `.dat` result parser — modal eigenfrequencies + linear buckling factors.

CalculiX writes nodal fields (DISP/S) to the `.frd` file, but the scalar outputs
of an eigenvalue analysis — the natural frequencies of a `*FREQUENCY` step and the
load factors of a `*BUCKLE` step — go to the `.dat` file instead. This module is
the `.dat` counterpart to :mod:`frd_result_extractor`: a tolerant text parser plus
``results/computed_metrics.json`` writers that share the same schema and package
write-back contract.

Honesty boundary: these are **linear eigenvalue results** — undamped natural
frequencies and linear (Euler) buckling factors. No damping, no prestress-modal,
no imperfection sensitivity; not a certification.

Pure text parsing (no numpy / OCC); `.dat` must be UTF-8 text.
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

_TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------------------
# Low-level block scanning
# ---------------------------------------------------------------------------

def _condensed(line: str) -> str:
    """Collapse a (possibly letter-spaced) header line to UPPER, no whitespace.

    CalculiX prints block titles letter-spaced, e.g. ``E I G E N V A L U E``;
    condensing makes them matchable as ``EIGENVALUE``.
    """
    return "".join(line.split()).upper()


def _floats(line: str) -> list[float]:
    """Parse all whitespace-separated tokens on a line as floats (skip non-numeric)."""
    out: list[float] = []
    for tok in line.split():
        try:
            out.append(float(tok))
        except ValueError:
            return out if out else []
    return out


def _data_rows(lines: list[str], start: int) -> list[list[float]]:
    """Collect consecutive numeric data rows beginning at/after ``start``.

    A data row starts with an integer mode number. Blank lines before the first
    data row are skipped (the header has trailing blanks); a blank line *after*
    data has begun, or a new letter-spaced section header, terminates the block.
    """
    rows: list[list[float]] = []
    for i in range(start, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            if rows:
                break
            continue
        vals = _floats(raw)
        # A genuine data row leads with an integer mode index.
        if len(vals) >= 2 and float(vals[0]).is_integer():
            rows.append(vals)
        elif rows:
            break
    return rows


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------

def parse_eigenfrequencies(dat_text: str) -> list[float]:
    """Return natural frequencies in Hz from a CalculiX ``*FREQUENCY`` `.dat`.

    The first two columns are stable across CalculiX versions: mode number, then
    the eigenvalue ``omega^2`` in (rad/time)^2. The natural frequency is derived
    from it directly as ``f = sqrt(eigenvalue) / 2pi`` — layout-independent and
    equal to ccx's own CYCLES/TIME column. (The trailing frequency columns have
    drifted: ccx 2.23 emits ``mode, eigenvalue, rad/time, cycles/time (Hz),
    imaginary`` — so reading a fixed Hz-column index picked up the trailing
    imaginary 0.0 and reported 0 Hz for every mode.) Returns an empty list if no
    eigenvalue block is present.
    """
    lines = dat_text.splitlines()
    freqs: list[float] = []
    for idx, line in enumerate(lines):
        if "EIGENVALUEOUTPUT" not in _condensed(line):
            continue
        for row in _data_rows(lines, idx + 1):
            if len(row) >= 2:
                eig = row[1]                      # eigenvalue omega^2 (stable column)
                freqs.append(math.sqrt(eig) / _TWO_PI if eig > 0 else 0.0)
        if freqs:
            break
    return freqs


def parse_buckling_factors(dat_text: str) -> list[float]:
    """Return linear buckling load factors from a CalculiX ``*BUCKLE`` `.dat`.

    The buckling table columns are: mode, buckling factor. Returns an empty list
    if no buckling-factor block is present.
    """
    lines = dat_text.splitlines()
    factors: list[float] = []
    for idx, line in enumerate(lines):
        if "BUCKLINGFACTOR" not in _condensed(line):
            continue
        for row in _data_rows(lines, idx + 1):
            if len(row) >= 2:
                factors.append(row[1])
        if factors:
            break
    return factors


# ---------------------------------------------------------------------------
# computed_metrics builders
# ---------------------------------------------------------------------------

_MODAL_NOTE = (
    "Linear undamped natural frequencies (CalculiX *FREQUENCY). No damping or "
    "prestress; not a certification."
)
_BUCKLING_NOTE = (
    "Linear (eigenvalue) buckling load factors (CalculiX *BUCKLE). Critical load "
    "= factor x applied reference load; linear theory, no imperfection sensitivity."
)


def _metrics_envelope(
    metrics: dict[str, Any],
    *,
    case_id: str,
    dat_path: Path,
    software: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": FRD_COMPUTED_METRICS_SCHEMA,
        "metrics_source": {
            "tool": "dat_parser_v1",
            "software": software,
            "source_files": [str(dat_path)],
        },
        "load_cases": [{"id": case_id, "metrics": metrics}],
        "warnings": warnings,
    }


def extract_modal_metrics(
    dat_path: Path,
    *,
    load_case_id: str = "modal_001",
    software: str = "CalculiX",
) -> dict[str, Any]:
    """Build a ``computed_metrics`` dict of natural frequencies from a `.dat`."""
    text = Path(dat_path).read_text(encoding="utf-8", errors="replace")
    freqs = parse_eigenfrequencies(text)
    metrics: dict[str, Any] = {}
    warnings: list[str] = []
    if freqs:
        metrics["natural_frequencies_hz"] = {
            "value": [round(f, 6) for f in freqs],
            "unit": "Hz",
            "note": _MODAL_NOTE,
        }
        metrics["first_natural_frequency_hz"] = {
            "value": round(freqs[0], 6),
            "unit": "Hz",
            "note": _MODAL_NOTE,
        }
    else:
        warnings.append(
            "No eigenvalue output found in .dat; natural frequencies not computed"
        )
    return _metrics_envelope(
        metrics, case_id=load_case_id, dat_path=Path(dat_path),
        software=software, warnings=warnings,
    )


def extract_buckling_metrics(
    dat_path: Path,
    *,
    load_case_id: str = "buckling_001",
    software: str = "CalculiX",
) -> dict[str, Any]:
    """Build a ``computed_metrics`` dict of buckling load factors from a `.dat`."""
    text = Path(dat_path).read_text(encoding="utf-8", errors="replace")
    factors = parse_buckling_factors(text)
    metrics: dict[str, Any] = {}
    warnings: list[str] = []
    if factors:
        metrics["buckling_factors"] = {
            "value": [round(f, 6) for f in factors],
            "unit": "dimensionless",
            "note": _BUCKLING_NOTE,
        }
        metrics["lowest_buckling_factor"] = {
            "value": round(min(factors, key=abs), 6),
            "unit": "dimensionless",
            "note": _BUCKLING_NOTE,
        }
    else:
        warnings.append(
            "No buckling-factor output found in .dat; buckling factors not computed"
        )
    return _metrics_envelope(
        metrics, case_id=load_case_id, dat_path=Path(dat_path),
        software=software, warnings=warnings,
    )


def extract_dat_metrics(
    dat_path: Path,
    analysis_type: str,
    *,
    load_case_id: str | None = None,
    software: str = "CalculiX",
) -> dict[str, Any]:
    """Dispatch `.dat` extraction by analysis type (``modal`` / ``buckling``)."""
    if analysis_type == "modal":
        return extract_modal_metrics(
            dat_path, load_case_id=load_case_id or "modal_001", software=software
        )
    if analysis_type == "buckling":
        return extract_buckling_metrics(
            dat_path, load_case_id=load_case_id or "buckling_001", software=software
        )
    raise ValueError(
        f"extract_dat_metrics supports modal/buckling, not {analysis_type!r}"
    )


# ---------------------------------------------------------------------------
# Package write-back (mirrors frd_result_extractor.write_computed_metrics_package)
# ---------------------------------------------------------------------------

def write_dat_metrics_package(
    package_path: Path,
    dat_path: Path,
    analysis_type: str,
    *,
    load_case_id: str | None = None,
    software: str = "CalculiX",
    overwrite: bool = True,
) -> dict[str, Any]:
    """Extract `.dat` metrics and write ``results/computed_metrics.json`` atomically."""
    metrics = extract_dat_metrics(
        Path(dat_path), analysis_type, load_case_id=load_case_id, software=software
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
