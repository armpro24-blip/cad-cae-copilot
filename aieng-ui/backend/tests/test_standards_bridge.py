"""Tests for the standards bridge BOM and standard-part utilities."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from test_api import _make_patch_settings


def _make_bom_package(pkg_path: Path, features: list[dict]) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    feature_graph = {
        "format_version": "0.1.0",
        "features": features,
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "bom-test"}))
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph))


def test_generate_bom_aggregates_standard_parts(tmp_path: Path) -> None:
    """BOM deduplicates standard parts by (name, type, material) and sums quantity."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_bolt_1",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "designation": "M6-1",
            "source_library": "bd_warehouse",
            "parameters": {},
        },
        {
            "id": "feat_bolt_2",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "designation": "M6-1",
            "source_library": "bd_warehouse",
            "parameters": {},
        },
        {
            "id": "feat_washer_1",
            "type": "standard_part",
            "name": "washer_M6",
            "canonical_type": "washer",
            "designation": "M6",
            "source_library": "bd_warehouse",
            "parameters": {},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["total_parts"] == 3
    assert result["unique_parts"] == 2

    bolt = next(
        item for item in result["items"]
        if item["part_type"] == "standard_part" and item["canonical_type"] == "screw"
    )
    assert bolt["quantity"] == 2
    assert bolt["designation"] == "M6-1"
    assert bolt["source_library"] == "bd_warehouse"

    washer = next(
        item for item in result["items"] if item["canonical_type"] == "washer"
    )
    assert washer["quantity"] == 1
    assert washer["designation"] == "M6"


def test_generate_bom_markdown_includes_quantities(tmp_path: Path) -> None:
    """Markdown BOM output includes each item with its aggregated quantity."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-md.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_bolt_1",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "designation": "M6-1",
            "source_library": "bd_warehouse",
            "parameters": {},
        },
        {
            "id": "feat_bolt_2",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "designation": "M6-1",
            "source_library": "bd_warehouse",
            "parameters": {},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path), fmt="markdown")
    assert result["status"] == "ok"
    assert "markdown" in result
    assert "mounting_bolt_M6" in result["markdown"]
    assert "| 2 |" in result["markdown"]


def test_generate_bom_csv_export_matches_schema(tmp_path: Path) -> None:
    """CSV export carries a stable header schema + one row per BOM line (#280)."""
    import csv
    import io
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-csv.aieng"
    _make_bom_package(pkg_path, [
        {"id": "b1", "type": "standard_part", "name": "mounting_bolt_M6",
         "canonical_type": "screw", "designation": "M6-1", "source_library": "bd_warehouse", "parameters": {}},
        {"id": "b2", "type": "standard_part", "name": "mounting_bolt_M6",
         "canonical_type": "screw", "designation": "M6-1", "source_library": "bd_warehouse", "parameters": {}},
        {"id": "p1", "type": "named_part", "name": "base_plate", "parameters": {"material": "Al6061-T6"}},
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path), fmt="csv")
    assert result["status"] == "ok"
    assert "csv" in result

    rows = list(csv.DictReader(io.StringIO(result["csv"])))
    assert [
        "line_no", "part_name", "part_type", "material", "quantity",
        "standard_part", "canonical_type", "designation", "source_library",
    ] == list(rows[0].keys())
    bolt = next(r for r in rows if r["part_name"] == "mounting_bolt_M6")
    assert bolt["quantity"] == "2"
    assert bolt["standard_part"] == "true"
    assert bolt["designation"] == "M6-1"
    plate = next(r for r in rows if r["part_name"] == "base_plate")
    assert plate["material"] == "Al6061-T6"
    assert plate["standard_part"] == "false"
    # line numbers are 1-based and contiguous
    assert [r["line_no"] for r in rows] == [str(i + 1) for i in range(len(rows))]


def test_generate_bom_json_export_is_erp_line_items(tmp_path: Path) -> None:
    """JSON export is a serialized ERP-style line-item document (#280)."""
    import json as _json
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-json.aieng"
    _make_bom_package(pkg_path, [
        {"id": "b1", "type": "standard_part", "name": "washer_M6",
         "canonical_type": "washer", "designation": "M6", "source_library": "bd_warehouse", "parameters": {}},
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path), fmt="json")
    assert result["status"] == "ok"
    assert "json" in result
    doc = _json.loads(result["json"])
    assert doc["total_parts"] == 1
    assert doc["unique_parts"] == 1
    line = doc["bill_of_materials"][0]
    assert line["line_no"] == 1
    assert line["part_name"] == "washer_M6"
    assert line["quantity"] == 1
    assert line["standard_part"] is True
    assert line["canonical_type"] == "washer"


def test_to_bom_frontend_payload_maps_to_camelcase_shape() -> None:
    """The frontend BOMData shape (camelCase) is mapped from the snake_case BOM result."""
    from app.standards_bridge import to_bom_frontend_payload

    result = {
        "status": "ok",
        "total_parts": 3,
        "unique_parts": 2,
        "items": [
            {"part_name": "mounting_bolt_M6", "part_type": "standard_part", "material": "", "quantity": 2,
             "standard_part": True, "canonical_type": "screw", "designation": "M6-1", "source_library": "bd_warehouse"},
            {"part_name": "base_plate", "part_type": "named_part", "material": "Al6061-T6", "quantity": 1},
        ],
    }
    payload = to_bom_frontend_payload(result, "proj-123", "2026-06-18T00:00:00+00:00")

    assert payload["projectId"] == "proj-123"
    assert payload["totalCount"] == 3
    assert payload["standardPartCount"] == 2
    assert payload["customPartCount"] == 1
    assert payload["generatedAt"] == "2026-06-18T00:00:00+00:00"

    bolt = next(i for i in payload["items"] if i["name"] == "mounting_bolt_M6")
    assert bolt["isStandardPart"] is True
    assert bolt["quantity"] == 2
    assert bolt["standardPartType"] == "screw"
    assert bolt["standardPartPreset"] == "M6-1"

    plate = next(i for i in payload["items"] if i["name"] == "base_plate")
    assert plate["isStandardPart"] is False
    assert plate["material"] == "Al6061-T6"
    assert plate["standardPartType"] is None
    # ids are unique so they are safe React keys
    assert len({i["id"] for i in payload["items"]}) == len(payload["items"])


def test_generate_bom_returns_error_when_package_missing(tmp_path: Path) -> None:
    """generate_bom returns a structured error when the package cannot be found."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    result = generate_bom(settings, project_id=None, package_path=str(tmp_path / "missing.aieng"))
    assert result["status"] == "error"
    assert result["code"] == "missing_package"


def test_generate_bom_warns_on_feature_missing_type(tmp_path: Path) -> None:
    """A feature with a name but no type is surfaced as a skipped_missing_type warning."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-typeless.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_untyped",
            "name": " Mystery bracket ",
            "parameters": {"material": "aluminium"},
        },
        {
            "id": "feat_bolt",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "parameters": {},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["warnings"] == [
        {
            "kind": "skipped_missing_type",
            "identifier": " Mystery bracket ",
            "message": "Feature ' Mystery bracket ' skipped because it has no type.",
        }
    ]
    assert result["unique_parts"] == 1
    assert result["items"][0]["part_name"] == "mounting_bolt_M6"
    assert result["limitations"] == "Best-effort semantic recognition; not a supplier BOM or validation claim."


def test_generate_bom_empty_feature_graph_returns_empty_bom(tmp_path: Path) -> None:
    """A package with an empty feature graph produces an empty but valid BOM."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-empty-fg.aieng"
    _make_bom_package(pkg_path, [])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["items"] == []
    assert result["total_parts"] == 0
    assert result["unique_parts"] == 0
    assert result["warnings"] == []


def test_generate_bom_corrupted_package_returns_read_error(tmp_path: Path) -> None:
    """A corrupted/non-zip file produces a package_read_error."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-corrupt.aieng"
    pkg_path.write_text("this is not a zip file", encoding="utf-8")

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "error"
    assert result["code"] == "package_read_error"


def test_generate_bom_warns_on_dedup_merge_and_keeps_different_materials_separate(tmp_path: Path) -> None:
    """Same-name/same-material parts are merged with a warning; same-name/different-material parts stay separate."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-dedup.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_bracket_alu_1",
            "type": "named_part",
            "name": "bracket",
            "parameters": {"material": "aluminium"},
        },
        {
            "id": "feat_bracket_alu_2",
            "type": "named_part",
            "name": "bracket",
            "parameters": {"material": "aluminium"},
        },
        {
            "id": "feat_bracket_steel",
            "type": "named_part",
            "name": "bracket",
            "parameters": {"material": "steel"},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["total_parts"] == 3
    assert result["unique_parts"] == 2

    aluminium_brackets = [i for i in result["items"] if i["material"] == "aluminium"]
    steel_brackets = [i for i in result["items"] if i["material"] == "steel"]
    assert len(aluminium_brackets) == 1
    assert aluminium_brackets[0]["quantity"] == 2
    assert len(steel_brackets) == 1
    assert steel_brackets[0]["quantity"] == 1

    merge_warnings = [w for w in result["warnings"] if w["kind"] == "dedup_merge"]
    assert len(merge_warnings) == 1
    assert merge_warnings[0]["part_name"] == "bracket"
    assert merge_warnings[0]["key"] == {
        "name": "bracket",
        "type": "named_part",
        "material": "aluminium",
    }
    assert merge_warnings[0]["merged_quantity"] == 2


def test_generate_bom_dedup_with_none_or_empty_material(tmp_path: Path) -> None:
    """Parts with None/empty material are deduped separately from parts with a material."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-empty-material.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_bolt_none",
            "type": "standard_part",
            "name": "bolt_M6",
            "canonical_type": "screw",
            "parameters": {"material": None},
        },
        {
            "id": "feat_bolt_empty",
            "type": "standard_part",
            "name": "bolt_M6",
            "canonical_type": "screw",
            "parameters": {"material": ""},
        },
        {
            "id": "feat_bolt_steel",
            "type": "standard_part",
            "name": "bolt_M6",
            "canonical_type": "screw",
            "parameters": {"material": "steel"},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["total_parts"] == 3
    assert result["unique_parts"] == 2
    materials = {item["material"]: item["quantity"] for item in result["items"]}
    assert materials.get("") == 2
    assert materials.get("steel") == 1

    merge_warnings = [w for w in result["warnings"] if w["kind"] == "dedup_merge"]
    assert len(merge_warnings) == 1
    assert merge_warnings[0]["key"]["material"] == ""
    assert merge_warnings[0]["merged_quantity"] == 2


def test_generate_bom_empty_warnings_when_nothing_skipped_or_merged(tmp_path: Path) -> None:
    """When no features are skipped and no dedup occurs, warnings is an empty list."""
    from app.standards_bridge import generate_bom

    settings = _make_patch_settings(tmp_path)
    pkg_path = tmp_path / "bom-clean.aieng"
    _make_bom_package(pkg_path, [
        {
            "id": "feat_bolt",
            "type": "standard_part",
            "name": "mounting_bolt_M6",
            "canonical_type": "screw",
            "parameters": {},
        },
        {
            "id": "feat_washer",
            "type": "standard_part",
            "name": "washer_M6",
            "canonical_type": "washer",
            "parameters": {},
        },
    ])

    result = generate_bom(settings, project_id=None, package_path=str(pkg_path))
    assert result["status"] == "ok"
    assert result["warnings"] == []
