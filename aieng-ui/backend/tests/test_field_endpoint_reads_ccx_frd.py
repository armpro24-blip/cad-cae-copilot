"""The viewer's result fields, read from REAL CalculiX output.

`GET /api/projects/{id}/fields/{name}` derives principal stresses, Tresca and
the shear components per node from the FRD stress block. CalculiX writes that
block as SXX SYY SZZ SXY SYZ SZX; the derivation read the last two columns as
(Sxz, Syz). Von Mises is blind to that swap, which is why every earlier test —
built on a hand-written FRD whose shear columns were zero — stayed green while
the S1/S2/S3/Tresca fields were wrong on every real package (measured on the
workspace's ccx packages: up to 29% of peak von Mises per node).

The FRDs used here are unedited CalculiX 2.23 output from
`aieng/tests/fixtures/frd_ccx_2_23/`.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.services.platform_logic import _extract_frd_field_data

_AIENG_ROOT = Settings.from_env().aieng_root
_CCX = Path(__file__).resolve().parents[3] / "aieng" / "tests" / "fixtures" / "frd_ccx_2_23"


def _package(tmp_path: Path, frd_name: str) -> Path:
    pkg = tmp_path / f"{frd_name}.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        zf.writestr(
            "simulation/runs/run_001/outputs/result.frd",
            (_CCX / f"{frd_name}.frd").read_bytes(),
        )
    return pkg


def _stress_rows(frd_name: str) -> dict[int, tuple[float, ...]]:
    """Node -> (SXX, SYY, SZZ, SXY, SYZ, SZX), read independently of the app."""
    rows: dict[int, tuple[float, ...]] = {}
    in_stress = False
    for line in (_CCX / f"{frd_name}.frd").read_text().splitlines():
        if line.startswith(" -4"):
            in_stress = line.split()[1] == "STRESS"
        elif line.startswith(" -3"):
            in_stress = False
        elif in_stress and line.startswith(" -1"):
            rows[int(line[3:13])] = tuple(float(line[13 + 12 * i: 25 + 12 * i]) for i in range(6))
    return rows


def _principals(row: tuple[float, ...]) -> np.ndarray:
    sxx, syy, szz, sxy, syz, szx = row
    m = np.array([[sxx, sxy, szx], [sxy, syy, syz], [szx, syz, szz]])
    return np.linalg.eigvalsh(m)[::-1]


@pytest.mark.parametrize("field, index", [("s1", 0), ("s2", 1), ("s3", 2)])
def test_principal_fields_match_eigenvalues_of_the_ccx_tensor(
    tmp_path: Path, field: str, index: int
) -> None:
    rows = _stress_rows("static")
    assert len(rows) == 20
    data = _extract_frd_field_data(_package(tmp_path, "static"), field, _AIENG_ROOT)
    assert data is not None
    expected = [_principals(rows[nid])[index] for nid in sorted(rows)]
    assert data["values"] == pytest.approx(expected, abs=1e-9)


def test_tresca_and_shear_component_fields(tmp_path: Path) -> None:
    rows = _stress_rows("static")
    pkg = _package(tmp_path, "static")
    tresca = _extract_frd_field_data(pkg, "tresca", _AIENG_ROOT)
    syz = _extract_frd_field_data(pkg, "syz", _AIENG_ROOT)
    sxz = _extract_frd_field_data(pkg, "sxz", _AIENG_ROOT)
    order = sorted(rows)
    assert tresca["values"] == pytest.approx(
        [_principals(rows[n])[0] - _principals(rows[n])[2] for n in order], abs=1e-9
    )
    assert syz["values"] == pytest.approx([rows[n][4] for n in order])
    assert sxz["values"] == pytest.approx([rows[n][5] for n in order])


def test_a_step_the_result_does_not_have_is_named_not_silently_replaced(tmp_path: Path) -> None:
    pkg = _package(tmp_path, "multistep")
    asked = _extract_frd_field_data(pkg, "von_mises", _AIENG_ROOT, load_case_id="load_case_005")
    last = _extract_frd_field_data(pkg, "von_mises", _AIENG_ROOT, load_case_id="load_case_002")
    # The viewer still gets a field, but the response says it is step 2, not 5.
    assert asked["values"] == last["values"]
    assert any("step 5" in w and "showing step 2" in w for w in asked["warnings"])
    assert last["warnings"] == []
