"""Tests for ``aieng.simulation.dat_result_extractor`` (FEA breadth: modal + buckling).

The `.dat` parser is exercised against captured CalculiX-style output text — the
letter-spaced ``E I G E N V A L U E   O U T P U T`` / ``B U C K L I N G   F A C T O R``
block headers and fixed-column data rows — plus the package write-back contract.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.simulation.dat_result_extractor import (
    METRICS_PATH,
    extract_buckling_metrics,
    extract_dat_metrics,
    extract_modal_metrics,
    parse_buckling_factors,
    parse_eigenfrequencies,
    write_dat_metrics_package,
)


# A CalculiX 2.23 *FREQUENCY .dat block. Real column order is:
# mode, eigenvalue(omega^2), freq real part (rad/time), freq real part (cycles/time=Hz),
# imaginary part (rad/time, 0 for an undamped run). Hz is derived from the
# eigenvalue (sqrt(omega^2)/2pi), so this layout — including the trailing
# imaginary column that used to be misread as Hz — parses correctly.
_MODAL_DAT = """\

     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.7395882E+06   0.8599931E+03   0.1368751E+03   0.0000000E+00
      2   0.2903163E+08   0.5388101E+04   0.8575814E+03   0.0000000E+00
      3   0.5814411E+08   0.7625229E+04   0.1213601E+04   0.0000000E+00

"""

# A CalculiX *BUCKLE .dat block: columns are mode, buckling factor.
_BUCKLING_DAT = """\

     B U C K L I N G   F A C T O R   O U T P U T

 MODE NO       BUCKLING
               FACTOR

      1   0.4012345E+01
      2   0.1623457E+02
      3   0.4498765E+02

"""


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------


def test_parse_eigenfrequencies_reads_hz_column() -> None:
    freqs = parse_eigenfrequencies(_MODAL_DAT)
    assert len(freqs) == 3
    # Hz derived from the eigenvalue (sqrt(omega^2)/2pi) — equals ccx's CYCLES/TIME
    # column. Regression: the trailing imaginary 0.0 column must NOT be read as Hz
    # (that bug reported 0 Hz for every mode on real ccx 2.23).
    assert freqs[0] == pytest.approx(136.8751, rel=1e-4)
    assert freqs[1] == pytest.approx(857.5814, rel=1e-4)
    assert all(f > 0 for f in freqs)
    assert freqs == sorted(freqs)  # ascending modes


def test_parse_buckling_factors() -> None:
    factors = parse_buckling_factors(_BUCKLING_DAT)
    assert factors == pytest.approx([4.012345, 16.23457, 44.98765], rel=1e-5)


def test_parsers_return_empty_on_wrong_block() -> None:
    assert parse_eigenfrequencies(_BUCKLING_DAT) == []
    assert parse_buckling_factors(_MODAL_DAT) == []
    assert parse_eigenfrequencies("no relevant output here") == []


# ---------------------------------------------------------------------------
# computed_metrics builders
# ---------------------------------------------------------------------------


def test_extract_modal_metrics(tmp_path: Path) -> None:
    dat = tmp_path / "modal.dat"
    dat.write_text(_MODAL_DAT, encoding="utf-8")
    out = extract_modal_metrics(dat)
    m = out["load_cases"][0]["metrics"]
    assert m["first_natural_frequency_hz"]["value"] == pytest.approx(136.8751, rel=1e-4)
    assert len(m["natural_frequencies_hz"]["value"]) == 3
    assert m["natural_frequencies_hz"]["unit"] == "Hz"
    assert "linear" in m["first_natural_frequency_hz"]["note"].lower()
    assert out["metrics_source"]["tool"] == "dat_parser_v1"
    assert out["warnings"] == []


def test_extract_buckling_metrics_lowest_factor(tmp_path: Path) -> None:
    dat = tmp_path / "buckle.dat"
    dat.write_text(_BUCKLING_DAT, encoding="utf-8")
    out = extract_buckling_metrics(dat)
    m = out["load_cases"][0]["metrics"]
    assert m["lowest_buckling_factor"]["value"] == pytest.approx(4.012345, rel=1e-5)
    assert len(m["buckling_factors"]["value"]) == 3


def test_extract_metrics_warns_when_empty(tmp_path: Path) -> None:
    dat = tmp_path / "empty.dat"
    dat.write_text("nothing useful", encoding="utf-8")
    out = extract_modal_metrics(dat)
    assert out["load_cases"][0]["metrics"] == {}
    assert any("eigenvalue" in w.lower() for w in out["warnings"])


def test_extract_dat_metrics_dispatch(tmp_path: Path) -> None:
    dat = tmp_path / "m.dat"
    dat.write_text(_MODAL_DAT, encoding="utf-8")
    assert extract_dat_metrics(dat, "modal")["load_cases"][0]["id"] == "modal_001"
    with pytest.raises(ValueError):
        extract_dat_metrics(dat, "static")


# ---------------------------------------------------------------------------
# Package write-back
# ---------------------------------------------------------------------------


def _empty_package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "dat_test"}))
    return path


def test_write_dat_metrics_package_modal(tmp_path: Path) -> None:
    pkg = _empty_package(tmp_path / "p.aieng")
    dat = tmp_path / "modal.dat"
    dat.write_text(_MODAL_DAT, encoding="utf-8")

    metrics = write_dat_metrics_package(pkg, dat, "modal")
    assert metrics["load_cases"][0]["metrics"]["natural_frequencies_hz"]["unit"] == "Hz"
    with zipfile.ZipFile(pkg) as zf:
        assert METRICS_PATH in zf.namelist()
        written = json.loads(zf.read(METRICS_PATH))
    assert written["metrics_source"]["software"] == "CalculiX"
    assert "manifest.json" in zipfile.ZipFile(pkg).namelist()


def test_write_dat_metrics_package_buckling_overwrite_guard(tmp_path: Path) -> None:
    pkg = _empty_package(tmp_path / "p.aieng")
    dat = tmp_path / "buckle.dat"
    dat.write_text(_BUCKLING_DAT, encoding="utf-8")

    write_dat_metrics_package(pkg, dat, "buckling")
    with pytest.raises(FileExistsError):
        write_dat_metrics_package(pkg, dat, "buckling", overwrite=False)
    # overwrite=True succeeds
    write_dat_metrics_package(pkg, dat, "buckling", overwrite=True)
