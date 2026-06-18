"""Tests for the ASCII VTU result importer (#279 D1/D2)."""

from __future__ import annotations

import zipfile
from pathlib import Path


_ASCII_VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <Points>
        <DataArray type="Float64" NumberOfComponents="3" format="ascii">
          0 0 0  1 0 0  0 1 0  0 0 1
        </DataArray>
      </Points>
      <PointData>
        <DataArray type="Float64" Name="von_mises" format="ascii">
          10 20 30 40
        </DataArray>
        <DataArray type="Float64" Name="displacement" NumberOfComponents="3" format="ascii">
          0 0 0  3 4 0  0 0 5  1 2 2
        </DataArray>
      </PointData>
      <Cells>
        <DataArray type="Int64" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int64" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">10</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
"""

_BINARY_VTU = """<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="1.0" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="2" NumberOfCells="0">
      <Points>
        <DataArray type="Float64" NumberOfComponents="3" format="appended" offset="0"/>
      </Points>
    </Piece>
  </UnstructuredGrid>
  <AppendedData encoding="raw">_garbagebytes</AppendedData>
</VTKFile>
"""


def _make_vtu_package(pkg_path: Path, vtu_text: str) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("simulation/result.vtu", vtu_text)


def test_parse_vtu_points_and_point_data() -> None:
    from app.vtu_importer import parse_vtu

    parsed = parse_vtu(_ASCII_VTU)
    assert parsed["available"] is True
    assert len(parsed["points"]) == 4
    assert parsed["points"][1] == (1.0, 0.0, 0.0)
    assert parsed["point_data"]["von_mises"]["num_components"] == 1
    assert parsed["point_data"]["von_mises"]["values"] == [10.0, 20.0, 30.0, 40.0]
    assert parsed["point_data"]["displacement"]["num_components"] == 3


def test_parse_vtu_appended_binary_is_unavailable() -> None:
    from app.vtu_importer import parse_vtu

    parsed = parse_vtu(_BINARY_VTU)
    # Appended/binary payloads are not supported — degrade honestly, never crash.
    assert parsed["available"] is False
    assert "ascii" in (parsed.get("reason") or "").lower()


def test_parse_vtu_malformed_component_count_skips_array() -> None:
    from app.vtu_importer import parse_vtu

    malformed = _ASCII_VTU.replace(
        'Name="von_mises" format="ascii"',
        'Name="von_mises" NumberOfComponents="many" format="ascii"',
    )

    parsed = parse_vtu(malformed)

    assert parsed["available"] is True
    assert "von_mises" not in parsed["point_data"]
    assert parsed["point_data"]["displacement"]["num_components"] == 3


def test_extract_vtu_field_scalar(tmp_path: Path) -> None:
    from app.vtu_importer import extract_vtu_field

    pkg = tmp_path / "vtu_scalar.aieng"
    _make_vtu_package(pkg, _ASCII_VTU)

    field = extract_vtu_field(pkg, "von_mises")
    assert field is not None
    assert field["values"] == [10.0, 20.0, 30.0, 40.0]
    assert field["min_value"] == 10.0
    assert field["max_value"] == 40.0
    assert len(field["node_coords"]) == 4
    assert field["source"] == "vtu"


def test_extract_vtu_field_vector_magnitude(tmp_path: Path) -> None:
    from app.vtu_importer import extract_vtu_field

    pkg = tmp_path / "vtu_vec.aieng"
    _make_vtu_package(pkg, _ASCII_VTU)

    # displacement magnitudes: |(0,0,0)|=0, |(3,4,0)|=5, |(0,0,5)|=5, |(1,2,2)|=3
    field = extract_vtu_field(pkg, "disp_magnitude")
    assert field is not None
    assert field["values"] == [0.0, 5.0, 5.0, 3.0]
    assert field["max_value"] == 5.0


def test_extract_vtu_field_missing_field_is_none(tmp_path: Path) -> None:
    from app.vtu_importer import extract_vtu_field

    pkg = tmp_path / "vtu_missing.aieng"
    _make_vtu_package(pkg, _ASCII_VTU)

    assert extract_vtu_field(pkg, "temperature") is None


def test_extract_vtu_field_no_vtu_is_none(tmp_path: Path) -> None:
    from app.vtu_importer import extract_vtu_field

    pkg = tmp_path / "no_vtu.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")

    assert extract_vtu_field(pkg, "von_mises") is None
