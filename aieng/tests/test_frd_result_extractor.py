"""Tests for aieng.simulation.frd_result_extractor.

All tests use synthetic FRD text — no real CalculiX solver is required.

FRD format recap:
  - '    -4  FIELDNAME  n_components  ...' — field header
  - '    -5  COMPNAME  ...'               — component definition
  - '    -1  <12-char node_id>  <12-char values...>' — per-node data
  - '    -2  <12-char node_id>  <12-char values...>' — continuation
  - '    -3'                              — end of field
  - '9999'                                — end of file
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pytest

from aieng.simulation.frd_result_extractor import (
    extract_computed_metrics,
    parse_frd,
    write_computed_metrics_package,
)
from aieng.schema_versions import FRD_COMPUTED_METRICS_SCHEMA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _frd_value(v: float) -> str:
    """Format a float as a 12-char FRD scientific notation field."""
    return f"{v:12.5E}"


def _node_line(node_id: int, values: list[float]) -> str:
    tag = "    -1"
    node_str = f"{node_id:12d}"
    vals_str = "".join(_frd_value(v) for v in values)
    return tag + node_str + vals_str


def _make_frd(
    disp_nodes: dict[int, list[float]] | None,
    stress_nodes: dict[int, list[float]] | None,
) -> str:
    """Build a minimal FRD text string with optional DISP and S fields."""
    lines = [
        "    1C                                                                         1",
        "    1UCUT.......................                                                2",
    ]

    if disp_nodes is not None:
        lines += [
            "    -4  DISP        4    1",
            "    -5  D1          1    2    1    0",
            "    -5  D2          1    2    2    0",
            "    -5  D3          1    2    3    0",
            "    -5  ALL         1    2    0    1",
        ]
        for nid, vals in disp_nodes.items():
            lines.append(_node_line(nid, vals))
        lines.append("    -3")

    if stress_nodes is not None:
        lines += [
            "    -4  S           6    1",
            "    -5  SXX         1    4    1    1",
            "    -5  SYY         1    4    2    1",
            "    -5  SZZ         1    4    3    1",
            "    -5  SXY         1    4    4    1",
            "    -5  SXZ         1    4    5    1",
            "    -5  SYZ         1    4    6    1",
        ]
        for nid, vals in stress_nodes.items():
            lines.append(_node_line(nid, vals))
        lines.append("    -3")

    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def _make_multistep_frd(steps: list[dict[str, dict[int, list[float]]]]) -> str:
    """Build a minimal FRD with multiple DISP/S steps.

    Each step dict may contain ``disp`` and/or ``stress`` node maps.
    """
    lines = [
        "    1C                                                                         1",
        "    1UCUT.......................                                                2",
    ]
    for step in steps:
        disp_nodes = step.get("disp")
        stress_nodes = step.get("stress")
        if disp_nodes is not None:
            lines += [
                "    -4  DISP        4    1",
                "    -5  D1          1    2    1    0",
                "    -5  D2          1    2    2    0",
                "    -5  D3          1    2    3    0",
                "    -5  ALL         1    2    0    1",
            ]
            for nid, vals in disp_nodes.items():
                lines.append(_node_line(nid, vals))
            lines.append("    -3")
        if stress_nodes is not None:
            lines += [
                "    -4  S           6    1",
                "    -5  SXX         1    4    1    1",
                "    -5  SYY         1    4    2    1",
                "    -5  SZZ         1    4    3    1",
                "    -5  SXY         1    4    4    1",
                "    -5  SXZ         1    4    5    1",
                "    -5  SYZ         1    4    6    1",
            ]
            for nid, vals in stress_nodes.items():
                lines.append(_node_line(nid, vals))
            lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def _write_frd(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "job.frd"
    p.write_text(content, encoding="utf-8")
    return p


def _make_package(pkg_path: Path) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))


def _vm(sxx, syy, szz, sxy, sxz, syz) -> float:
    return math.sqrt(
        0.5 * (
            (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
            + 6.0 * (sxy ** 2 + sxz ** 2 + syz ** 2)
        )
    )


# ---------------------------------------------------------------------------
# parse_frd
# ---------------------------------------------------------------------------

class TestParseFrd:
    def test_disp_field_parsed_correctly(self, tmp_path: Path) -> None:
        disp = {
            1: [1.0, 0.0, 0.0, 1.0],
            2: [2.0, 0.0, 0.0, 2.0],
        }
        frd = _write_frd(tmp_path, _make_frd(disp, None))
        fields = parse_frd(frd)
        assert "DISP" in fields
        nd = fields["DISP"]["node_data"]
        assert abs(nd[1][3] - 1.0) < 1e-5
        assert abs(nd[2][3] - 2.0) < 1e-5

    def test_stress_field_parsed_correctly(self, tmp_path: Path) -> None:
        stress = {
            1: [100.0, 50.0, 30.0, 20.0, 10.0, 5.0],
        }
        frd = _write_frd(tmp_path, _make_frd(None, stress))
        fields = parse_frd(frd)
        assert "S" in fields
        nd = fields["S"]["node_data"]
        assert abs(nd[1][0] - 100.0) < 1e-3
        assert abs(nd[1][3] - 20.0) < 1e-3

    def test_component_names_captured(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [0.0, 0.0, 0.0, 0.0]}, None))
        fields = parse_frd(frd)
        assert fields["DISP"]["components"] == ["D1", "D2", "D3", "ALL"]

    def test_stress_component_names_captured(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd(None, {1: [0.0] * 6}))
        fields = parse_frd(frd)
        assert fields["S"]["components"] == ["SXX", "SYY", "SZZ", "SXY", "SXZ", "SYZ"]

    def test_both_fields_parsed(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, {1: [100.0] * 6}))
        fields = parse_frd(frd)
        assert "DISP" in fields
        assert "S" in fields

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_frd(tmp_path / "missing.frd")

    def test_binary_frd_is_rejected_honestly(self, tmp_path: Path) -> None:
        frd = tmp_path / "binary.frd"
        frd.write_bytes(b"\x00\x01not-a-text-frd\x00\xff")

        with pytest.raises(ValueError, match="binary or non-UTF-8 FRD files are unsupported"):
            parse_frd(frd)

    def test_empty_frd_returns_no_fields(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, " 9999\n")
        fields = parse_frd(frd)
        assert fields == {}

    def test_multiple_nodes_parsed(self, tmp_path: Path) -> None:
        disp = {i: [float(i), 0.0, 0.0, float(i)] for i in range(1, 6)}
        frd = _write_frd(tmp_path, _make_frd(disp, None))
        fields = parse_frd(frd)
        assert len(fields["DISP"]["node_data"]) == 5

    def test_parse_frd_steps_multiple_disp_steps(self, tmp_path: Path) -> None:
        from aieng.simulation.frd_result_extractor import parse_frd_steps

        frd = _write_frd(
            tmp_path,
            _make_multistep_frd(
                [
                    {"disp": {1: [1.0, 0.0, 0.0, 1.0]}},
                    {"disp": {1: [2.0, 0.0, 0.0, 2.0]}},
                ]
            ),
        )
        fields = parse_frd_steps(frd)
        assert "DISP" in fields
        assert len(fields["DISP"]) == 2
        assert fields["DISP"][0]["node_data"][1][3] == pytest.approx(1.0, abs=1e-5)
        assert fields["DISP"][1]["node_data"][1][3] == pytest.approx(2.0, abs=1e-5)

    def test_parse_frd_backward_compatible_single_step(self, tmp_path: Path) -> None:
        frd = _write_frd(
            tmp_path,
            _make_multistep_frd(
                [
                    {"disp": {1: [1.0, 0.0, 0.0, 1.0]}, "stress": {1: [100.0] * 6}},
                ]
            ),
        )
        fields = parse_frd(frd)
        assert "DISP" in fields
        assert "S" in fields
        assert fields["DISP"]["node_data"][1][3] == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------------------
# extract_computed_metrics
# ---------------------------------------------------------------------------

def _ccx_node_line(node_id: int, values: list[float]) -> str:
    """A node-data line in standard CalculiX layout: ' -1' + I10 node + E12.5 values."""
    return " -1" + f"{node_id:10d}" + "".join(f"{v:12.5E}" for v in values)


def _make_ccx_frd(disp_nodes, stress_nodes) -> str:
    """Build an FRD in the layout real CalculiX (2.x) emits: a single leading
    space + 2-char key (key_width=3), an I10 node field, and the stress field
    named 'STRESS' (not 'S')."""
    lines = ["    1C", "    1UCUT"]
    if disp_nodes is not None:
        lines += [" -4  DISP        4    1", " -5  D1          1    2    1    0",
                  " -5  D2          1    2    2    0", " -5  D3          1    2    3    0",
                  " -5  ALL         1    2    0    1"]
        lines += [_ccx_node_line(n, v) for n, v in disp_nodes.items()]
        lines.append(" -3")
    if stress_nodes is not None:
        lines += [" -4  STRESS      6    1", " -5  SXX         1    4    1    1",
                  " -5  SYY         1    4    2    1", " -5  SZZ         1    4    3    1",
                  " -5  SXY         1    4    4    1", " -5  SXZ         1    4    5    1",
                  " -5  SYZ         1    4    6    1"]
        lines += [_ccx_node_line(n, v) for n, v in stress_nodes.items()]
        lines.append(" -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


class TestCalculiXStandardFrdLayout:
    """Regression: real CalculiX FRD uses a 1-space key (key_width=3), I10 node,
    and names the stress field 'STRESS'. The parser previously hard-coded the
    wide layout (key_width=6) + field name 'S', so it silently extracted nothing
    from real ccx output."""

    def test_disp_and_stress_extracted_from_ccx_layout(self, tmp_path: Path) -> None:
        disp = {1: [0.0, 0.0, 0.0, 0.0], 2: [3.0, 4.0, 0.0, 5.0]}  # max |U| = 5.0
        stress = {1: [100.0, 50.0, 30.0, 20.0, 10.0, 5.0], 2: [0.0] * 6}
        frd = _write_frd(tmp_path, _make_ccx_frd(disp, stress))
        result = extract_computed_metrics(frd)
        lc = result["load_cases"][0]
        assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
        expected_vm = _vm(100.0, 50.0, 30.0, 20.0, 10.0, 5.0)
        assert abs(lc["metrics"]["max_von_mises_stress"]["value"] - expected_vm) < 1e-2
        assert not any("not found" in w for w in result.get("warnings", []))

    def test_ccx_layout_parses_node_values(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_ccx_frd({7: [1.5, 0.0, 0.0, 1.5]}, None))
        fields = parse_frd(frd)
        assert "DISP" in fields
        assert abs(fields["DISP"]["node_data"][7][0] - 1.5) < 1e-5


class TestExtractComputedMetrics:
    def test_max_displacement_from_all_component(self, tmp_path: Path) -> None:
        disp = {
            1: [1.0, 0.0, 0.0, 1.0],
            2: [3.0, 0.0, 0.0, 3.0],   # max ALL = 3.0
            3: [0.5, 0.5, 0.0, 0.707],
        }
        frd = _write_frd(tmp_path, _make_frd(disp, None))
        result = extract_computed_metrics(frd)
        lc = result["load_cases"][0]
        assert "max_displacement" in lc["metrics"]
        assert abs(lc["metrics"]["max_displacement"]["value"] - 3.0) < 1e-4
        assert lc["metrics"]["max_displacement"]["unit"] == "mm"

    def test_max_displacement_computed_from_components_when_no_all(self, tmp_path: Path) -> None:
        # Provide D1/D2/D3 only (no ALL component) by omitting the 4th value
        # We'll manually construct FRD without ALL component
        lines = [
            "    -4  DISP        3    1",
            "    -5  D1          1    2    1    0",
            "    -5  D2          1    2    2    0",
            "    -5  D3          1    2    3    0",
            _node_line(1, [3.0, 4.0, 0.0]),   # magnitude = 5.0
            _node_line(2, [1.0, 0.0, 0.0]),
            "    -3",
            " 9999",
        ]
        frd_text = "\n".join(lines) + "\n"
        frd = _write_frd(tmp_path, frd_text)
        result = extract_computed_metrics(frd)
        lc = result["load_cases"][0]
        assert "max_displacement" in lc["metrics"]
        assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4

    def test_max_von_mises_stress_correct(self, tmp_path: Path) -> None:
        stress = {
            1: [100.0, 50.0, 30.0, 20.0, 10.0, 5.0],
            2: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0],  # higher von Mises
        }
        frd = _write_frd(tmp_path, _make_frd(None, stress))
        result = extract_computed_metrics(frd)
        lc = result["load_cases"][0]
        assert "max_von_mises_stress" in lc["metrics"]
        expected = _vm(200.0, 100.0, 50.0, 10.0, 0.0, 0.0)
        assert abs(lc["metrics"]["max_von_mises_stress"]["value"] - round(expected, 4)) < 0.01
        assert lc["metrics"]["max_von_mises_stress"]["unit"] == "MPa"

    def test_schema_version_and_structure(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, {1: [100.0] * 6}))
        result = extract_computed_metrics(frd)
        assert result["schema_version"] == FRD_COMPUTED_METRICS_SCHEMA
        assert result["metrics_source"]["tool"] == "frd_parser_v1"
        assert isinstance(result["load_cases"], list)
        assert len(result["load_cases"]) == 1

    def test_load_case_id_passed_through(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        result = extract_computed_metrics(frd, load_case_id="lc_custom_001")
        assert result["load_cases"][0]["id"] == "lc_custom_001"

    def test_warning_when_disp_missing(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd(None, {1: [100.0] * 6}))
        result = extract_computed_metrics(frd)
        assert any("DISP" in w for w in result["warnings"])
        assert "max_displacement" not in result["load_cases"][0]["metrics"]

    def test_warning_when_stress_missing(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        result = extract_computed_metrics(frd)
        assert any("S" in w for w in result["warnings"])
        assert "max_von_mises_stress" not in result["load_cases"][0]["metrics"]

    def test_software_name_in_metrics_source(self, tmp_path: Path) -> None:
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        result = extract_computed_metrics(frd, software="MyFEMSolver")
        assert result["metrics_source"]["software"] == "MyFEMSolver"

    def test_multi_step_computed_metrics(self, tmp_path: Path) -> None:
        frd = _write_frd(
            tmp_path,
            _make_multistep_frd(
                [
                    {"disp": {1: [1.0, 0.0, 0.0, 1.0]}},
                    {"disp": {1: [3.0, 0.0, 0.0, 3.0]}},
                ]
            ),
        )
        result = extract_computed_metrics(frd)
        assert len(result["load_cases"]) == 2
        assert result["load_cases"][0]["id"] == "load_case_001"
        assert result["load_cases"][1]["id"] == "load_case_002"
        assert result["load_cases"][0]["metrics"]["max_displacement"]["value"] == pytest.approx(
            1.0, abs=1e-4
        )
        assert result["load_cases"][1]["metrics"]["max_displacement"]["value"] == pytest.approx(
            3.0, abs=1e-4
        )

    def test_explicit_load_case_ids_multi_step(self, tmp_path: Path) -> None:
        frd = _write_frd(
            tmp_path,
            _make_multistep_frd(
                [
                    {"disp": {1: [1.0, 0.0, 0.0, 1.0]}},
                    {"disp": {1: [2.0, 0.0, 0.0, 2.0]}},
                ]
            ),
        )
        result = extract_computed_metrics(frd, load_case_ids=["mode_1", "mode_2"])
        assert [lc["id"] for lc in result["load_cases"]] == ["mode_1", "mode_2"]


# ---------------------------------------------------------------------------
# write_computed_metrics_package
# ---------------------------------------------------------------------------

class TestWriteComputedMetricsPackage:
    def test_writes_metrics_into_package(self, tmp_path: Path) -> None:
        pkg = tmp_path / "test.aieng"
        _make_package(pkg)
        disp = {1: [1.0, 0.0, 0.0, 1.0], 2: [5.0, 0.0, 0.0, 5.0]}
        stress = {1: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0]}
        frd = _write_frd(tmp_path, _make_frd(disp, stress))

        result = write_computed_metrics_package(pkg, frd)

        with zipfile.ZipFile(pkg, "r") as zf:
            assert "results/computed_metrics.json" in zf.namelist()
            written = json.loads(zf.read("results/computed_metrics.json"))

        assert written["schema_version"] == FRD_COMPUTED_METRICS_SCHEMA
        lc = written["load_cases"][0]
        assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
        # Return value matches written
        assert result["schema_version"] == FRD_COMPUTED_METRICS_SCHEMA

    def test_preserves_existing_entries(self, tmp_path: Path) -> None:
        pkg = tmp_path / "test.aieng"
        _make_package(pkg)
        # Add an extra file
        with zipfile.ZipFile(pkg, "a") as zf:
            zf.writestr("simulation/solver_settings.json", '{"solver": "ccx"}')

        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        write_computed_metrics_package(pkg, frd)

        with zipfile.ZipFile(pkg, "r") as zf:
            names = set(zf.namelist())
        assert "simulation/solver_settings.json" in names
        assert "manifest.json" in names
        assert "results/computed_metrics.json" in names

    def test_no_duplicate_entries(self, tmp_path: Path) -> None:
        pkg = tmp_path / "test.aieng"
        _make_package(pkg)
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        write_computed_metrics_package(pkg, frd)
        write_computed_metrics_package(pkg, frd, overwrite=True)

        with zipfile.ZipFile(pkg, "r") as zf:
            names = zf.namelist()
        assert names.count("results/computed_metrics.json") == 1

    def test_refuses_overwrite_when_false(self, tmp_path: Path) -> None:
        pkg = tmp_path / "test.aieng"
        _make_package(pkg)
        frd = _write_frd(tmp_path, _make_frd({1: [1.0, 0.0, 0.0, 1.0]}, None))
        write_computed_metrics_package(pkg, frd)
        with pytest.raises(FileExistsError):
            write_computed_metrics_package(pkg, frd, overwrite=False)

    def test_binary_frd_does_not_write_computed_metrics(self, tmp_path: Path) -> None:
        pkg = tmp_path / "test.aieng"
        _make_package(pkg)
        frd = tmp_path / "binary.frd"
        frd.write_bytes(b"\x00\x01not-a-text-frd\x00\xff")

        with pytest.raises(ValueError, match="binary or non-UTF-8 FRD files are unsupported"):
            write_computed_metrics_package(pkg, frd)

        with zipfile.ZipFile(pkg, "r") as zf:
            assert "results/computed_metrics.json" not in zf.namelist()
