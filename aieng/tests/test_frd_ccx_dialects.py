"""FRD parsing against the format spec and against real CalculiX output.

Two sources of truth, deliberately:

* ``fixtures/frd_ccx_2_23/*.frd`` are UNEDITED output of CalculiX 2.23 for the
  decks beside them (``*.inp``: a 4x1x1 C3D8 steel cantilever, 100x10x10 mm,
  run as static, two-step static, modal, buckling and steady heat transfer).
  The eigenvalues asserted below are the ones ccx printed to its own ``.dat``
  for the same runs.
* a writer that follows the CalculiX GraphiX manual "Result Format" records
  literally — ``100C`` header ``'  100','C',6A1,E12.5,I12,20A1,I2,I5,10A1,I2``
  and data records ``(1X,I2,I5,6E12.5)`` short / ``(1X,I2,I10,6E12.5)`` long —
  for the dialects ccx itself does not produce (short format, huge node ids).

Earlier fixtures were hand-written in the reader's own shape (a 6-character key,
an I12 node field, ``SXZ`` before ``SYZ``), which is how the parser could pass
every test while misreading real files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aieng.simulation.frd_result_extractor import (
    _detect_frd_layout,
    extract_computed_metrics,
    parse_frd_steps,
)
from aieng.simulation.field_region_extractor import _extract_node_coords_from_frd

CCX = Path(__file__).parent / "fixtures" / "frd_ccx_2_23"


def _metrics(result: dict) -> dict[str, dict[str, object]]:
    return {
        case["id"]: {name: entry["value"] for name, entry in case["metrics"].items()}
        for case in result["load_cases"]
    }


# --- real CalculiX 2.23 output ------------------------------------------------


def test_real_static_frd_component_names_and_layout() -> None:
    lines = (CCX / "static.frd").read_text().splitlines()
    assert _detect_frd_layout(lines) == (3, 10)
    stress = parse_frd_steps(CCX / "static.frd")["STRESS"][0]
    assert stress["components"] == ["SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"]
    assert stress["step"]["analysis_code"] == 0
    assert len(stress["node_data"]) == 20


def test_real_static_metrics_state_their_basis() -> None:
    result = extract_computed_metrics(CCX / "static.frd")
    case = result["load_cases"][0]["metrics"]
    assert set(case) == {"max_displacement", "max_von_mises_stress"}
    assert "averaged" in case["max_von_mises_stress"]["basis"]
    assert case["max_von_mises_stress"]["unit"] == "MPa"


def test_real_two_step_frd_gives_one_load_case_per_step() -> None:
    result = extract_computed_metrics(CCX / "multistep.frd")
    cases = _metrics(result)
    assert list(cases) == ["load_case_001", "load_case_002"]
    # step 2 applies half the load of step 1, in another direction: linear
    # elastic, so the peak magnitudes halve exactly (to FRD print precision).
    ratio = cases["load_case_002"]["max_displacement"] / cases["load_case_001"]["max_displacement"]
    assert ratio == pytest.approx(0.5, rel=1e-4)


def test_real_modal_frd_reports_frequencies_not_mode_shape_amplitudes() -> None:
    result = extract_computed_metrics(CCX / "modal.frd")
    cases = _metrics(result)
    # Before: four "load cases" with max_displacement ~ hundreds of "mm" and
    # max_von_mises_stress ~ 1e5 "MPa" — the amplitude of a mass-normalised
    # eigenvector, on a part with no load at all.
    assert "load_case_001" not in cases
    assert all("max_displacement" not in m for m in cases.values())
    freqs = cases["modal_001"]["natural_frequencies_hz"]
    # ccx .dat for the same run: 0.1566868E+04, 0.1566868E+04, 0.8070768E+04, 0.9990872E+04
    assert freqs == pytest.approx([1566.868, 1566.868, 8070.768, 9990.872], rel=1e-6)
    assert cases["modal_001"]["first_natural_frequency_hz"] == pytest.approx(1566.868, rel=1e-6)
    assert any("normalised mode shapes" in w for w in result["warnings"])


def test_real_buckling_frd_with_analysis_type() -> None:
    result = extract_computed_metrics(CCX / "buckling.frd", analysis_type="buckling")
    cases = _metrics(result)
    # the VALUE-0 block is the real stress state under the reference load
    assert set(cases["load_case_001"]) == {"max_displacement", "max_von_mises_stress"}
    # ccx .dat for the same run: 0.1558898E+03, 0.1558898E+03, 0.1469311E+04
    assert cases["buckling_001"]["buckling_factors"] == pytest.approx(
        [155.8898, 155.8898, 1469.311], rel=1e-6
    )
    assert cases["buckling_001"]["lowest_buckling_factor"] == pytest.approx(155.8898, rel=1e-6)
    assert len(cases) == 2


def test_real_buckling_frd_without_analysis_type_is_qualified() -> None:
    # ICTYPE 4 is "user named" in the spec, so without being told the analysis
    # the reader does not reclassify — but it says what it could be.
    result = extract_computed_metrics(CCX / "buckling.frd")
    assert len(result["load_cases"]) == 4
    assert sum("ICTYPE 4" in w for w in result["warnings"]) == 3


@pytest.mark.parametrize(
    "member, payload",
    [
        ("simulation/runs/run_001/solver_run.json", {"state": "completed", "analysis_type": "buckling"}),
        ("simulation/solver_settings.json", {"analysis_type": "buckling"}),
    ],
)
def test_package_extraction_reads_the_analysis_type_it_records(
    tmp_path: Path, member: str, payload: dict
) -> None:
    """`cae.extract_solver_results` passes no analysis_type; the package records one.
    Without reading it, a buckling FRD's mode shapes became a static result."""
    import json
    import zipfile

    from aieng.simulation.frd_result_extractor import write_computed_metrics_package

    pkg = tmp_path / "buckle.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"resources": {}}))
        zf.writestr(member, json.dumps(payload))
    result = write_computed_metrics_package(pkg, CCX / "buckling.frd", run_id="run_001")
    cases = _metrics(result)
    assert cases["buckling_001"]["lowest_buckling_factor"] == pytest.approx(155.8898, rel=1e-6)
    assert len(cases) == 2  # the reference-load state + the factors, no mode shapes


def test_real_thermal_frd() -> None:
    cases = _metrics(extract_computed_metrics(CCX / "thermal.frd"))
    # the deck fixes 100 at the root face and 20 at the tip face
    assert cases == {"load_case_001": {"max_temperature": 100.0, "min_temperature": 20.0}}


# --- spec-literal writer: dialects ccx does not emit -------------------------


def _e(v: float) -> str:
    return f"{v:12.5E}"


def _frd(fmt: int, nodes: dict[int, tuple[float, float, float]], steps: list[dict]) -> str:
    """Write an FRD by the cgx manual's record formats. ``fmt`` 0 short, 1 long."""
    nw = 5 if fmt == 0 else 10
    lines = [
        "    1C".ljust(72),
        f"    2C{'':18s}{len(nodes):12d}{'':37s}{fmt:1d}",
    ]
    lines += [f" -1{nid:{nw}d}{_e(x)}{_e(y)}{_e(z)}" for nid, (x, y, z) in nodes.items()]
    lines.append(" -3")
    for step in steps:
        # each result block carries its own FORMAT flag (default: the file's)
        sfmt = step.get("fmt", fmt)
        snw = 5 if sfmt == 0 else 10
        header = (
            f"  100CL{100 + step['numstp']:5d}{_e(step['value'])}{len(nodes):12d}"
            f"{'':20s}{step['ictype']:2d}{step['numstp']:5d}{step.get('analys', ''):10s}{sfmt:2d}"
        )
        for name, comps, data in step["blocks"]:
            lines.append(header)
            lines.append(f" -4  {name:8s}{len(comps):4d}    1")
            for c in comps:
                lines.append(f" -5  {c:8s}    1    2    1    0")
            for nid, vals in data.items():
                lines.append(f" -1{nid:{snw}d}" + "".join(_e(v) for v in vals))
            lines.append(" -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


_STRESS = ["SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"]
_DISP = ["D1", "D2", "D3", "ALL"]


def _static_steps(nid_a: int, nid_b: int) -> list[dict]:
    return [{
        "numstp": 1, "value": 1.0, "ictype": 0,
        "blocks": [
            # negative values glue together in fixed columns: "-1.00000E+00-2.00000E+00"
            ("DISP", _DISP, {nid_a: [-1.0, -2.0, -2.0], nid_b: [0.0, 0.0, 0.0]}),
            ("STRESS", _STRESS, {nid_a: [-100.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                                 nid_b: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}),
        ],
    }]


@pytest.mark.parametrize("fmt", [0, 1])
def test_short_and_long_format_parse_identically(fmt: int, tmp_path: Path) -> None:
    # Before: the short (I5) format was read with an I10 node field, so the first
    # value bled into the node id and every record was silently dropped.
    path = tmp_path / f"fmt{fmt}.frd"
    path.write_text(_frd(fmt, {7: (0.0, 0.0, 0.0), 12345: (1.0, -2.5, 3.0)},
                         _static_steps(7, 12345)))
    cases = _metrics(extract_computed_metrics(path))
    assert cases["load_case_001"]["max_displacement"] == pytest.approx(3.0)
    assert cases["load_case_001"]["max_von_mises_stress"] == pytest.approx(100.0)
    assert _extract_node_coords_from_frd(path)[12345] == (1.0, -2.5, 3.0)


def test_large_node_ids_in_long_format(tmp_path: Path) -> None:
    big = 1_234_567_890  # fits I10; would not fit the short I5 field
    path = tmp_path / "big.frd"
    path.write_text(_frd(1, {1: (0.0, 0.0, 0.0), big: (5.0, 0.0, 0.0)}, _static_steps(1, big)))
    disp = parse_frd_steps(path)["DISP"][0]["node_data"]
    assert disp[big][:3] == [0.0, 0.0, 0.0] and disp[1][:3] == [-1.0, -2.0, -2.0]
    assert big in _extract_node_coords_from_frd(path)


def test_blocks_pair_by_step_header_not_by_occurrence(tmp_path: Path) -> None:
    # Step 1 wrote displacements only; step 2 wrote both. Pairing the n-th DISP
    # with the n-th STRESS (the old rule) attributes step 2's stress to step 1.
    steps = [
        {"numstp": 1, "value": 1.0, "ictype": 0,
         "blocks": [("DISP", _DISP, {1: [1.0, 0.0, 0.0]})]},
        {"numstp": 2, "value": 2.0, "ictype": 0,
         "blocks": [("DISP", _DISP, {1: [2.0, 0.0, 0.0]}),
                    ("STRESS", _STRESS, {1: [50.0, 0.0, 0.0, 0.0, 0.0, 0.0]})]},
    ]
    path = tmp_path / "pair.frd"
    path.write_text(_frd(1, {1: (0.0, 0.0, 0.0)}, steps))
    cases = _metrics(extract_computed_metrics(path))
    assert "max_von_mises_stress" not in cases["load_case_001"]
    assert cases["load_case_002"] == {"max_displacement": 2.0, "max_von_mises_stress": 50.0}


def test_result_block_width_comes_from_its_own_header(tmp_path: Path) -> None:
    # Coordinates in the short format (2C FORMAT 0), results in the long format
    # (100C FORMAT 1): the spec gives each block its own flag. Reading the
    # result node ids with the coordinate block's I5 width dropped them.
    steps = [dict(step, fmt=1) for step in _static_steps(7, 12345)]
    path = tmp_path / "mixed.frd"
    path.write_text(_frd(0, {7: (0.0, 0.0, 0.0), 12345: (1.0, -2.5, 3.0)}, steps))
    cases = _metrics(extract_computed_metrics(path))
    assert cases["load_case_001"]["max_displacement"] == pytest.approx(3.0)
    assert cases["load_case_001"]["max_von_mises_stress"] == pytest.approx(100.0)
    assert _extract_node_coords_from_frd(path)[12345] == (1.0, -2.5, 3.0)


def test_explicit_load_case_ids_stay_on_their_frd_step(tmp_path: Path) -> None:
    # static, modal (ICTYPE 2), static: the third step's id is load_case_ids[2].
    # Filtering the eigenmode out before numbering handed it load_case_ids[1].
    steps = [
        {"numstp": 1, "value": 1.0, "ictype": 0,
         "blocks": [("DISP", _DISP, {1: [1.0, 0.0, 0.0]})]},
        {"numstp": 2, "value": 1234.5, "ictype": 2, "analys": "MODAL",
         "blocks": [("DISP", _DISP, {1: [9.0, 0.0, 0.0]})]},
        {"numstp": 3, "value": 2.0, "ictype": 0,
         "blocks": [("DISP", _DISP, {1: [2.0, 0.0, 0.0]})]},
    ]
    path = tmp_path / "mixed_steps.frd"
    path.write_text(_frd(1, {1: (0.0, 0.0, 0.0)}, steps))
    cases = _metrics(extract_computed_metrics(path, load_case_ids=["pull", "mode", "push"]))
    assert cases["pull"]["max_displacement"] == pytest.approx(1.0)
    assert cases["push"]["max_displacement"] == pytest.approx(2.0)
    assert cases["modal_001"]["natural_frequencies_hz"] == pytest.approx([1234.5])
    assert "mode" not in cases


def test_parsing_does_not_need_numpy() -> None:
    # aieng declares no numpy dependency; the FRD parser (and compare_runs) must
    # import and run without it — numpy is needed only for principal stresses.
    import os
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['numpy'] = None\n"
        "from pathlib import Path\n"
        "from aieng.simulation.frd_result_extractor import extract_computed_metrics\n"
        "import aieng.cae_run_comparison\n"
        f"m = extract_computed_metrics(Path({str(CCX / 'static.frd')!r}))\n"
        "print(m['load_cases'][0]['metrics']['max_von_mises_stress']['value'])\n"
    )
    src = Path(__file__).resolve().parents[1] / "src"
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
        stdin=subprocess.DEVNULL, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert float(proc.stdout.strip()) > 0
