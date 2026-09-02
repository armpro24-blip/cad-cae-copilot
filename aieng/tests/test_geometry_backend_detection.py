from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aieng.cli import main
from aieng.geometry.backend import OCCGeometryBackend, detect_occ_runtime
from aieng.graph.feature_graph import recognize_features_package
from aieng.geometry.step_importer import import_step_package
from aieng.validation.completeness_writer import write_completeness_report_package

FAKE_STEP_CONTENT = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def imported_package(tmp_path):
    step_path = tmp_path / "bracket.step"
    step_path.write_bytes(FAKE_STEP_CONTENT)
    package_path = tmp_path / "bracket_001.aieng"
    import_step_package(step_path, package_path)
    return package_path


# ---------------------------------------------------------------------------
# detect_occ_runtime: return shape
# ---------------------------------------------------------------------------

def test_detect_occ_runtime_returns_dict():
    result = detect_occ_runtime()
    assert isinstance(result, dict)


def test_detect_occ_runtime_has_available_key():
    result = detect_occ_runtime()
    assert "available" in result


def test_detect_occ_runtime_has_provider_key():
    result = detect_occ_runtime()
    assert "provider" in result


def test_detect_occ_runtime_has_message_key():
    result = detect_occ_runtime()
    assert "message" in result


def test_detect_occ_runtime_available_is_bool():
    result = detect_occ_runtime()
    assert isinstance(result["available"], bool)


def test_detect_occ_runtime_message_is_str():
    result = detect_occ_runtime()
    assert isinstance(result["message"], str)


def test_detect_occ_runtime_provider_is_str_or_none():
    result = detect_occ_runtime()
    assert result["provider"] is None or isinstance(result["provider"], str)


# ---------------------------------------------------------------------------
# detect_occ_runtime: behaviour when no OCC is installed (monkeypatched)
# ---------------------------------------------------------------------------

def test_detect_occ_runtime_not_available_when_find_spec_returns_none():
    with patch("importlib.util.find_spec", return_value=None):
        result = detect_occ_runtime()
    assert result["available"] is False
    assert result["provider"] is None
    assert len(result["message"]) > 0


def test_detect_occ_runtime_message_contains_install_hint_when_unavailable():
    with patch("importlib.util.find_spec", return_value=None):
        result = detect_occ_runtime()
    msg = result["message"].lower()
    assert "install" in msg or "not found" in msg or "no supported" in msg


# ---------------------------------------------------------------------------
# detect_occ_runtime: behaviour when pythonocc-core is simulated as installed
# ---------------------------------------------------------------------------

def test_detect_occ_runtime_detects_pythonocc():
    def fake_find_spec(name):
        if name == "OCC":
            return MagicMock()
        return None

    with patch("importlib.util.find_spec", side_effect=fake_find_spec):
        result = detect_occ_runtime()

    assert result["available"] is True
    assert result["provider"] == "pythonocc-core"
    assert result["message"]


# ---------------------------------------------------------------------------
# detect_occ_runtime: behaviour when OCP is simulated as installed
# ---------------------------------------------------------------------------

def test_detect_occ_runtime_detects_ocp():
    def fake_find_spec(name):
        if name == "OCP":
            return MagicMock()
        return None

    with patch("importlib.util.find_spec", side_effect=fake_find_spec):
        result = detect_occ_runtime()

    assert result["available"] is True
    assert result["provider"] == "OCP"


def test_detect_occ_runtime_prefers_ocp_over_pythonocc():
    # Phase 7B.2: OCP is checked first because we implement OCP extraction.
    with patch("importlib.util.find_spec", return_value=MagicMock()):
        result = detect_occ_runtime()
    assert result["provider"] == "OCP"


# ---------------------------------------------------------------------------
# OCCGeometryBackend.extract_topology: Phase 7B.2 behaviour
# ---------------------------------------------------------------------------

def test_occ_backend_raises_when_no_runtime():
    with patch("importlib.util.find_spec", return_value=None):
        backend = OCCGeometryBackend()
        with pytest.raises(NotImplementedError) as exc_info:
            backend.extract_topology(b"unused")
    msg = str(exc_info.value)
    assert "OCC" in msg
    assert "install" in msg.lower() or "dependency" in msg.lower()


def test_occ_backend_raises_for_pythonocc_not_ocp():
    # When only pythonocc-core is detected (not OCP), raise NotImplementedError.
    def fake_find_spec(name):
        if name == "OCC":
            return MagicMock()
        return None

    with patch("importlib.util.find_spec", side_effect=fake_find_spec):
        backend = OCCGeometryBackend()
        with pytest.raises(NotImplementedError) as exc_info:
            backend.extract_topology(b"unused")
    msg = str(exc_info.value)
    assert "pythonocc-core" in msg
    assert "OCP" in msg or "cadquery" in msg.lower()


def test_occ_backend_no_runtime_message_contains_install_hint():
    with patch("importlib.util.find_spec", return_value=None):
        backend = OCCGeometryBackend()
        with pytest.raises(NotImplementedError) as exc_info:
            backend.extract_topology(b"unused")
    msg = str(exc_info.value).lower()
    assert "cadquery" in msg or "install" in msg


# ---------------------------------------------------------------------------
# Module import does not require OCC
# ---------------------------------------------------------------------------

def test_backend_module_import_does_not_require_occ():
    import aieng.geometry.backend  # noqa: F401  — must not raise on import


def test_detect_occ_runtime_importable_without_occ():
    from aieng.geometry.backend import detect_occ_runtime as fn  # noqa: F401
    assert callable(fn)


# ---------------------------------------------------------------------------
# aieng geometry-backends CLI command
# ---------------------------------------------------------------------------

def test_geometry_backends_command_exits_zero(capsys):
    assert main(["geometry-backends"]) == 0


def test_geometry_backends_lists_mock_as_available(capsys):
    main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "mock" in output
    assert "available" in output


def test_geometry_backends_lists_occ(capsys):
    main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "occ" in output


def test_geometry_backends_occ_shows_not_available_when_no_runtime(capsys):
    with patch("importlib.util.find_spec", return_value=None):
        main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "occ" in output
    assert "not available" in output


def test_geometry_backends_occ_shows_detected_when_pythonocc_present(capsys):
    def fake_find_spec(name):
        if name == "OCC":
            return MagicMock()
        return None

    with patch("importlib.util.find_spec", side_effect=fake_find_spec):
        main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "occ" in output
    assert "detected" in output or "pythonocc-core" in output


def test_geometry_backends_occ_shows_ocp_extraction_when_ocp_present(capsys):
    def fake_find_spec(name):
        if name == "OCP":
            return MagicMock()
        return None

    with patch("importlib.util.find_spec", side_effect=fake_find_spec):
        main(["geometry-backends"])
    output = capsys.readouterr().out
    assert "occ" in output
    assert "OCP" in output or "detected" in output


# ---------------------------------------------------------------------------
# extract-topology --backend occ fails clearly when no runtime (Phase 7B.2)
# ---------------------------------------------------------------------------

def test_extract_topology_occ_fails_with_exit_code_2_when_no_runtime(tmp_path, capsys):
    package_path = imported_package(tmp_path)
    with patch("importlib.util.find_spec", return_value=None):
        assert main(["extract-topology", str(package_path), "--backend", "occ"]) == 2


def test_extract_topology_occ_prints_fail_to_stderr_when_no_runtime(tmp_path, capsys):
    package_path = imported_package(tmp_path)
    with patch("importlib.util.find_spec", return_value=None):
        main(["extract-topology", str(package_path), "--backend", "occ"])
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "OCC" in captured.err or "install" in captured.err.lower()


def test_extract_topology_mock_still_works_after_7b1(tmp_path, capsys):
    package_path = imported_package(tmp_path)
    assert main(["extract-topology", str(package_path), "--backend", "mock"]) == 0
    output = capsys.readouterr().out
    assert "PASS" in output


def test_extract_topology_auto_uses_mock_when_ocp_unavailable(tmp_path):
    package_path = imported_package(tmp_path)
    with patch("aieng.cli.detect_occ_runtime", return_value={"available": False, "provider": None, "message": "none"}):
        with patch("aieng.cli.extract_topology_package", return_value=package_path) as mocked_extract:
            assert main(["extract-topology", str(package_path)]) == 0
    assert mocked_extract.call_args.kwargs["backend"] == "mock"


def test_extract_topology_auto_uses_occ_when_ocp_available(tmp_path):
    package_path = imported_package(tmp_path)
    with patch("aieng.cli.detect_occ_runtime", return_value={"available": True, "provider": "OCP", "message": "ok"}):
        with patch("aieng.cli.extract_topology_package", return_value=package_path) as mocked_extract:
            assert main(["extract-topology", str(package_path)]) == 0
    assert mocked_extract.call_args.kwargs["backend"] == "occ"


def test_occ_real_step_extraction_sets_completeness_real_geometry_true(tmp_path):
    pytest.importorskip("OCP.STEPControl", reason="OCP/CadQuery not installed")

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer

    try:
        from OCP.STEPControl import STEPControl_AsIs
    except ImportError:
        from OCP.STEPControl import STEPControl_StepModelType
        STEPControl_AsIs = STEPControl_StepModelType.STEPControl_AsIs

    step_path = tmp_path / "real_box.step"
    writer = STEPControl_Writer()
    writer.Transfer(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), STEPControl_AsIs)
    assert writer.Write(str(step_path)) == IFSelect_RetDone

    package_path = tmp_path / "real_box.aieng"
    import_step_package(step_path, package_path)
    assert main(["extract-topology", str(package_path), "--backend", "occ"]) == 0

    write_completeness_report_package(package_path)
    with zipfile.ZipFile(package_path) as zf:
        report = json.loads(zf.read("validation/completeness_report.json"))

    assert report["real_geometry_extraction"] is True


def test_occ_real_bracket_feature_recognition_produces_core_candidate_types(tmp_path):
    pytest.importorskip("OCP.STEPControl", reason="OCP/CadQuery not installed")

    real_step = Path("examples/real_bracket.step")
    if not real_step.exists():
        pytest.skip("examples/real_bracket.step missing")

    package_path = tmp_path / "real_bracket.aieng"
    import_step_package(real_step, package_path)
    assert main(["extract-topology", str(package_path), "--backend", "occ"]) == 0

    recognize_features_package(package_path)
    with zipfile.ZipFile(package_path) as zf:
        feature_graph = json.loads(zf.read("graph/feature_graph.json"))

    feature_types = {feature.get("type") for feature in feature_graph.get("features", []) if isinstance(feature, dict)}
    assert "base_plate" in feature_types
    assert "mounting_hole" in feature_types
    assert "mounting_hole_pattern" in feature_types


# ---------------------------------------------------------------------------
# pyproject.toml geometry extra
# ---------------------------------------------------------------------------

def test_pyproject_toml_has_geometry_extra():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "geometry" in text


# ---------------------------------------------------------------------------
# Phase 7B.2: schema accepts OCP-style topology metadata (no OCP required)
# ---------------------------------------------------------------------------

def test_topology_schema_accepts_ocp_metadata():
    """OCP topology_map output must conform to the existing schema."""
    import json
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "topology_map.schema.json").read_text(encoding="utf-8"))
    ocp_map = {
        "format_version": "0.1.0",
        "metadata": {
            "extraction_backend": "occ",
            "runtime_provider": "OCP",
            "extraction_mode": "parsed_from_step",
            "real_step_parsing": True,
            "source_geometry": "geometry/normalized.step",
            "phase": "7B.2",
            "limitations": ["experimental"],
        },
        "entities": [
            {"id": "body_001", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 10.0, 20.0, 30.0]},
            {
                "id": "face_001",
                "type": "face",
                "surface_type": "plane",
                "area": 200.0,
                "bounding_box": [0.0, 0.0, 30.0, 10.0, 20.0, 30.0],
                "normal": [0.0, 0.0, 1.0],
                "body_id": "body_001",
            },
        ],
    }
    errors = list(Draft202012Validator(schema).iter_errors(ocp_map))
    assert errors == [], f"Schema rejected OCP-style topology map: {errors}"


def test_topology_schema_accepts_ocp_metadata_with_cylinder():
    """Cylinder face fields (radius, axis) must also conform."""
    import json
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "topology_map.schema.json").read_text(encoding="utf-8"))
    ocp_map = {
        "format_version": "0.1.0",
        "metadata": {
            "extraction_backend": "occ",
            "runtime_provider": "OCP",
            "extraction_mode": "parsed_from_step",
            "real_step_parsing": True,
            "source_geometry": "geometry/normalized.step",
        },
        "entities": [
            {"id": "body_001", "type": "solid"},
            {
                "id": "face_001",
                "type": "face",
                "surface_type": "cylinder",
                "area": 62.8,
                "radius": 5.0,
                "axis": [0.0, 0.0, 1.0],
                "body_id": "body_001",
            },
            {"id": "edge_001", "type": "edge"},
        ],
    }
    errors = list(Draft202012Validator(schema).iter_errors(ocp_map))
    assert errors == [], f"Schema rejected OCP cylinder face: {errors}"


# ---------------------------------------------------------------------------
# Phase 7B.2: OCP imports are lazy (no OCP imported at module level)
# ---------------------------------------------------------------------------

def test_ocp_not_imported_at_module_level():
    """Importing aieng.geometry.backend must not trigger OCP imports."""
    import sys
    ocp_keys_before = {k for k in sys.modules if k.startswith("OCP.")}
    import aieng.geometry.backend  # noqa: F401
    ocp_keys_after = {k for k in sys.modules if k.startswith("OCP.")}
    assert ocp_keys_after == ocp_keys_before, (
        f"Importing backend triggered OCP imports: {ocp_keys_after - ocp_keys_before}"
    )


def test_occ_backend_extract_topology_does_not_import_ocp_before_call():
    """Creating an OCCGeometryBackend instance must not import OCP."""
    import sys
    ocp_keys_before = {k for k in sys.modules if k.startswith("OCP.")}
    OCCGeometryBackend()
    ocp_keys_after = {k for k in sys.modules if k.startswith("OCP.")}
    assert ocp_keys_after == ocp_keys_before


# ---------------------------------------------------------------------------
# Capability probe (Issue #59)
# ---------------------------------------------------------------------------

def test_geometry_capability_probe_returns_bool():
    from _geometry_capability import has_working_occ_step_backend
    result = has_working_occ_step_backend()
    assert isinstance(result, bool)


def _ocp_importable() -> bool:
    """Whether OCP is actually available in the running environment."""
    try:
        import OCP  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return False
    return True


def test_geometry_capability_probe_matches_environment():
    """The probe must AGREE with the environment, in both directions.

    This previously hard-coded ``is False`` on the assumption that OCP is never
    installed. That holds for the bare core library but not for any environment
    carrying build123d (which requires OCP) — so the test asserted the
    environment rather than the code, and failed on every dev machine and any
    CI runner with the CAD stack installed.
    """
    from _geometry_capability import has_working_occ_step_backend

    result = has_working_occ_step_backend()
    if _ocp_importable():
        # OCP present: the probe may still be False if OCP cannot parse a real
        # STEP (a broken install) — but it must never be False while the deeper
        # parser check succeeds.
        from _geometry_capability import _ocp_can_parse_real_step

        if _ocp_can_parse_real_step():
            assert result is True
    else:
        assert result is False


def test_geometry_capability_probe_uses_real_bracket_step():
    """The parser check reflects whether OCP can actually read a real STEP."""
    from _geometry_capability import _ocp_can_parse_real_step

    result = _ocp_can_parse_real_step()
    assert isinstance(result, bool)
    if not _ocp_importable():
        assert result is False
