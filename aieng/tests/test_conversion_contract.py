"""Tests for the CAD/CAE-to-.aieng conversion contract (Phase 20)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.converters import (
    CAPABILITY_LEVEL_NAMES,
    CONVERTER_CLAIM_POLICY,
    available_converters,
    get_converter,
)
from aieng.converters.cli_runners import list_converter_capabilities
from aieng.validate import validate_package


SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas"


def test_capability_level_names_are_complete():
    assert CAPABILITY_LEVEL_NAMES == {
        0: "source_metadata",
        1: "geometry_topology",
        2: "object_registry",
        3: "feature_aware",
        4: "editability_metadata",
        5: "roundtrip_writeback_metadata",
    }


def test_converter_claim_policy_pins_boundary():
    assert CONVERTER_CLAIM_POLICY["best_effort_conversion"] is True
    assert CONVERTER_CLAIM_POLICY["missingness_explicit"] is True
    assert CONVERTER_CLAIM_POLICY["do_not_infer_missing_information"] is True
    assert CONVERTER_CLAIM_POLICY["unsupported_is_not_false"] is True
    assert CONVERTER_CLAIM_POLICY["external_tools_execute"] is True
    assert CONVERTER_CLAIM_POLICY["aieng_core_executes_external_tools"] is False
    assert CONVERTER_CLAIM_POLICY["aieng_core_executes_solvers_meshers_or_optimizers"] is False
    assert CONVERTER_CLAIM_POLICY["aieng_core_performs_cad_edits"] is False


def test_freecad_reference_converter_is_registered():
    assert "freecad_reference" in available_converters()
    converter = get_converter("freecad_reference")
    profile = converter.capability_profile()
    assert profile.converter_id == "freecad_reference"
    assert profile.source_system == "FreeCAD"
    levels = {level.level: level for level in profile.supported_levels}
    assert levels[0].supported is True
    assert levels[1].supported is False  # offline cannot reach L1
    assert levels[2].supported is True
    assert levels[3].supported is True
    assert levels[4].supported is True
    assert levels[5].supported is False  # writeback metadata reserved for future


def test_converter_capabilities_profiles_serialize_with_claim_policy():
    profiles = list_converter_capabilities()
    assert profiles, "at least one converter should be registered"
    for profile in profiles:
        policy = profile["claim_policy"]
        assert policy["best_effort_conversion"] is True
        assert policy["aieng_core_executes_solvers_meshers_or_optimizers"] is False
        assert policy["aieng_core_performs_cad_edits"] is False


def test_converter_capabilities_schema_validates_profile_dict():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (SCHEMAS_DIR / "converter_capabilities.schema.json").read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    for profile in list_converter_capabilities():
        errors = sorted(validator.iter_errors(profile), key=lambda error: list(error.path))
        assert not errors, f"profile failed schema: {[e.message for e in errors]}"


def test_conversion_manifest_schema_validates_minimal_payload():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (SCHEMAS_DIR / "conversion_manifest.schema.json").read_text(encoding="utf-8")
    )
    payload = {
        "format_version": "0.1.0",
        "manifest_id": "conversion_001",
        "generated_at_utc": "2026-05-13T00:00:00Z",
        "converter": {
            "converter_id": "freecad_reference",
            "source_system": "FreeCAD",
            "runtime_mode": "offline",
        },
        "source": {
            "filename": "sample.FCStd",
            "byte_size": 100,
        },
        "coverage_categories": [
            {"category": "geometry", "status": "missing"},
            {"category": "topology", "status": "missing"},
            {"category": "object_registry", "status": "complete"},
            {"category": "features", "status": "partial"},
        ],
        "emitted_resources": [],
        "unsupported_or_missing": [],
        "uncertainty_notes": [],
        "claim_policy": dict(CONVERTER_CLAIM_POLICY),
    }
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_conversion_manifest_schema_validates_optional_levels_shorthand():
    """declared/achieved capability levels remain valid optional shorthand."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (SCHEMAS_DIR / "conversion_manifest.schema.json").read_text(encoding="utf-8")
    )
    payload = {
        "format_version": "0.1.0",
        "manifest_id": "conversion_001",
        "generated_at_utc": "2026-05-13T00:00:00Z",
        "converter": {"converter_id": "test", "source_system": "X"},
        "source": {"filename": "x.stp", "byte_size": 1},
        "coverage_categories": [{"category": "geometry", "status": "partial"}],
        "declared_capability_levels": [{"level": 0, "name": "source_metadata"}],
        "achieved_capability_levels": [{"level": 0, "name": "source_metadata"}],
        "emitted_resources": [],
        "unsupported_or_missing": [],
        "uncertainty_notes": [],
        "claim_policy": dict(CONVERTER_CLAIM_POLICY),
    }
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_conversion_manifest_rejects_aieng_core_running_solver():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (SCHEMAS_DIR / "conversion_manifest.schema.json").read_text(encoding="utf-8")
    )
    payload = {
        "format_version": "0.1.0",
        "manifest_id": "conversion_001",
        "generated_at_utc": "2026-05-13T00:00:00Z",
        "converter": {
            "converter_id": "rogue",
            "source_system": "FreeCAD",
            "runtime_mode": "offline",
        },
        "source": {"filename": "x.FCStd", "byte_size": 1},
        "declared_capability_levels": [],
        "achieved_capability_levels": [],
        "emitted_resources": [],
        "unsupported_or_missing": [],
        "uncertainty_notes": [],
        "claim_policy": {
            **CONVERTER_CLAIM_POLICY,
            "aieng_core_executes_solvers_meshers_or_optimizers": True,
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_source_mode_converter_requires_conversion_manifest(tmp_path):
    """A package marked source_mode=converter without provenance/conversion_manifest.json
    must FAIL validation."""
    pkg = tmp_path / "broken.aieng"
    with zipfile.ZipFile(pkg, mode="w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "model_id": "x",
                    "format_version": "0.1.0",
                    "units": {"length": "mm", "mass": "kg", "force": "N", "stress": "MPa"},
                    "resources": {},
                    "created_by": {"tool": "aieng test", "created_at": "2026-05-13T00:00:00Z"},
                    "source_mode": "converter",
                },
                indent=2,
                sort_keys=True,
            ) + "\n",
        )
    report = validate_package(pkg)
    fails = [m for m in report.messages if m.level.value == "FAIL"]
    assert any("source_mode=converter requires" in m.text for m in fails)
