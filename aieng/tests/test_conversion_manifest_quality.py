"""Phase 20 closeout: conversion manifest quality and end-to-end smoke tests.

Verifies the adaptive conversion manifest contract end-to-end:

- all 15 coverage categories are present after an offline FreeCAD conversion;
- every status value is from the approved seven-value enum;
- missingness/unsupported/uncertain information is explicitly recorded, not silently omitted;
- legacy L-level fields remain optional (their absence does not break schema validation);
- the readiness information-state report uses coverage_categories as its primary source;
- the converted package passes the validator with zero FAILs.

FreeCAD installation is NOT required. The reference converter runs in offline mode
by parsing the FCStd zip and Document.xml directly.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.converters.cli_runners import convert_source, readiness_report_payload
from aieng.converters.freecad import FreeCADConverter
from aieng.validate import validate_package


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "sample_bracket.FCStd"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_sample_fcstd.py"

_EXPECTED_CATEGORIES = frozenset([
    "geometry",
    "topology",
    "object_registry",
    "stable_references",
    "features",
    "parameters",
    "assemblies",
    "materials",
    "loads",
    "boundary_conditions",
    "mesh",
    "solver_deck",
    "cad_cae_mappings",
    "editability_metadata",
    "writeback_metadata",
])

_VALID_STATUSES = frozenset([
    "complete",
    "partial",
    "inferred",
    "missing",
    "unsupported",
    "unavailable_in_source",
    "unknown",
])


@pytest.fixture(scope="module")
def fixture_fcstd() -> Path:
    if not FIXTURE.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("generate_sample_fcstd", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.generate(FIXTURE)
    assert FIXTURE.exists()
    return FIXTURE


@pytest.fixture(scope="module")
def converted_package(fixture_fcstd: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("qual") / "quality_target.aieng"
    convert_source(
        source_path=fixture_fcstd,
        out=out,
        model_id="quality_target",
        converter_id="freecad_reference",
        overwrite=True,
        runtime_mode="offline",
    )
    return out


@pytest.fixture(scope="module")
def coverage_categories(converted_package: Path) -> dict[str, dict]:
    with zipfile.ZipFile(converted_package) as archive:
        manifest = json.loads(archive.read("provenance/conversion_manifest.json"))
    return {entry["category"]: entry for entry in manifest["coverage_categories"]}


# ---------------------------------------------------------------------------
# 1. All 15 categories must be present
# ---------------------------------------------------------------------------

def test_all_15_coverage_categories_present(coverage_categories: dict):
    missing = _EXPECTED_CATEGORIES - set(coverage_categories.keys())
    assert not missing, f"Missing coverage categories: {sorted(missing)}"


def test_no_extra_coverage_categories(coverage_categories: dict):
    extra = set(coverage_categories.keys()) - _EXPECTED_CATEGORIES
    assert not extra, f"Unexpected coverage categories: {sorted(extra)}"


# ---------------------------------------------------------------------------
# 2. Status values must come from the approved enum
# ---------------------------------------------------------------------------

def test_all_coverage_statuses_are_valid(coverage_categories: dict):
    invalid = {
        cat: entry["status"]
        for cat, entry in coverage_categories.items()
        if entry["status"] not in _VALID_STATUSES
    }
    assert not invalid, f"Categories with invalid status: {invalid}"


# ---------------------------------------------------------------------------
# 3. Key offline-mode expectations
# ---------------------------------------------------------------------------

def test_geometry_is_missing_in_offline_mode(coverage_categories: dict):
    """Offline FCStd parsing produces no STEP geometry."""
    assert coverage_categories["geometry"]["status"] == "missing"


def test_topology_is_missing_in_offline_mode(coverage_categories: dict):
    """Stable face/edge/body IDs require OCC; not available offline."""
    assert coverage_categories["topology"]["status"] == "missing"


def test_object_registry_is_complete(coverage_categories: dict):
    """Document.xml object list is fully captured."""
    assert coverage_categories["object_registry"]["status"] == "complete"


def test_writeback_metadata_is_unsupported(coverage_categories: dict):
    """L5 roundtrip writeback is explicitly unsupported by the reference converter."""
    assert coverage_categories["writeback_metadata"]["status"] == "unsupported"


def test_features_are_partial(coverage_categories: dict):
    """Feature candidates from heuristics — partial, not confirmed."""
    assert coverage_categories["features"]["status"] == "partial"


def test_materials_loads_bcs_are_missing(coverage_categories: dict):
    """FEM inputs not present in FCStd Document.xml."""
    for cat in ("materials", "loads", "boundary_conditions"):
        assert coverage_categories[cat]["status"] == "missing", \
            f"Expected {cat} status=missing, got {coverage_categories[cat]['status']!r}"


# ---------------------------------------------------------------------------
# 4. Explicit missingness — not silently omitted
# ---------------------------------------------------------------------------

def test_missing_categories_record_missing_items_or_notes(coverage_categories: dict):
    """Categories with status=missing must record what is absent."""
    for cat, entry in coverage_categories.items():
        if entry["status"] == "missing":
            has_detail = bool(
                entry.get("missing_items") or entry.get("notes")
            )
            assert has_detail, (
                f"Category '{cat}' has status=missing but no missing_items or notes. "
                "Missingness must be explicit."
            )


def test_unsupported_categories_record_notes(coverage_categories: dict):
    """Categories with status=unsupported must explain why."""
    for cat, entry in coverage_categories.items():
        if entry["status"] == "unsupported":
            assert entry.get("notes"), (
                f"Category '{cat}' has status=unsupported but no notes. "
                "Unsupported status must be explained."
            )


def test_inferred_categories_record_inferred_items(coverage_categories: dict):
    """Categories with status=inferred or partial must explain what was inferred."""
    for cat, entry in coverage_categories.items():
        if entry["status"] in ("inferred", "partial"):
            has_detail = bool(
                entry.get("inferred_items") or entry.get("missing_items") or entry.get("notes")
            )
            assert has_detail, (
                f"Category '{cat}' has status={entry['status']!r} but no "
                "inferred_items/missing_items/notes."
            )


# ---------------------------------------------------------------------------
# 5. Resource paths: complete/partial categories must list emitted resources
# ---------------------------------------------------------------------------

def test_complete_categories_list_emitted_resources(coverage_categories: dict):
    for cat, entry in coverage_categories.items():
        if entry["status"] == "complete":
            assert entry.get("resources_emitted"), (
                f"Category '{cat}' is complete but lists no resources_emitted."
            )


# ---------------------------------------------------------------------------
# 6. L-level fields remain optional — schema accepts their absence
# ---------------------------------------------------------------------------

def test_legacy_level_fields_are_optional_in_schema(tmp_path: Path):
    """Conversion manifest without declared_capability_levels / achieved_capability_levels
    must still validate against the schema."""
    jsonschema = pytest.importorskip("jsonschema")
    from pathlib import Path as P
    schema_path = P(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "conversion_manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    from aieng.converters.base import CONVERTER_CLAIM_POLICY
    payload = {
        "format_version": "0.1.0",
        "manifest_id": "conversion_001",
        "generated_at_utc": "2026-05-13T00:00:00Z",
        "converter": {"converter_id": "test", "source_system": "X"},
        "source": {"filename": "x.stp", "byte_size": 1},
        "coverage_categories": [
            {"category": "geometry", "status": "missing"},
        ],
        # deliberately omitting declared_capability_levels and achieved_capability_levels
        "emitted_resources": [],
        "unsupported_or_missing": [],
        "uncertainty_notes": [],
        "claim_policy": dict(CONVERTER_CLAIM_POLICY),
    }
    jsonschema.Draft202012Validator(schema).validate(payload)


# ---------------------------------------------------------------------------
# 7. Readiness report uses coverage_categories as primary information source
# ---------------------------------------------------------------------------

def test_readiness_information_state_reads_coverage_categories(converted_package: Path):
    """The readiness information_state must reflect coverage_categories, not only
    completeness report categories."""
    report = readiness_report_payload(converted_package)
    info = report["information_state"]

    # topology was set to missing by the converter
    assert "topology" in info["missing"], (
        f"topology should appear in info['missing']; got {info}"
    )
    # object_registry was set to complete -> should appear in available
    assert "object_registry" in info["available"], (
        f"object_registry should appear in info['available']; got {info}"
    )
    # writeback_metadata was set to unsupported
    assert "writeback_metadata" in info["unsupported"], (
        f"writeback_metadata should appear in info['unsupported']; got {info}"
    )


def test_readiness_converter_section_exposes_coverage_categories(converted_package: Path):
    report = readiness_report_payload(converted_package)
    coverage = {c["category"]: c["status"] for c in report["converter"]["coverage_categories"]}
    assert set(coverage.keys()) == _EXPECTED_CATEGORIES, (
        f"readiness converter section coverage_categories missing categories: "
        f"{_EXPECTED_CATEGORIES - set(coverage.keys())}"
    )


# ---------------------------------------------------------------------------
# 8. Converted package passes validator with zero FAILs
# ---------------------------------------------------------------------------

def test_converted_package_zero_fails(converted_package: Path):
    result = validate_package(converted_package)
    fails = [m for m in result.messages if m.level.value == "FAIL"]
    assert not fails, f"Validator FAILs on converted package: {[m.text for m in fails]}"


# ---------------------------------------------------------------------------
# 9. Source file preserved unchanged
# ---------------------------------------------------------------------------

def test_source_fcstd_preserved_verbatim(fixture_fcstd: Path, converted_package: Path):
    with zipfile.ZipFile(converted_package) as archive:
        stored = archive.read("provenance/source.fcstd")
    assert stored == fixture_fcstd.read_bytes()


# ---------------------------------------------------------------------------
# 10. Smoke path: ConversionResult.coverage_categories mirrors manifest
# ---------------------------------------------------------------------------

def test_conversion_result_coverage_categories_populated(fixture_fcstd: Path):
    converter = FreeCADConverter()
    result = converter.convert(fixture_fcstd, model_id="smoke")
    cats = {c.category: c.status for c in result.coverage_categories}
    assert set(cats.keys()) == _EXPECTED_CATEGORIES
    assert all(s in _VALID_STATUSES for s in cats.values())
