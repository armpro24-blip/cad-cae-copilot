from __future__ import annotations

import json
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator

from aieng import FORMAT_VERSION
from aieng.package import create_package, read_manifest
from aieng.standards.fastener_insertion import (
    FASTENER_INSERTION_REPORT_PATH,
    insert_fasteners_for_holes,
)


def _make_package(tmp_path: Path, *, known_stack: bool = True, with_axis: bool = True) -> Path:
    pkg = create_package("fastener_demo", tmp_path / "fastener_demo.aieng")
    feature_graph = {
        "format_version": FORMAT_VERSION,
        "features": [
            {
                "id": f"feat_hole_{idx:03d}",
                "type": "mounting_hole",
                "name": f"M6 clearance hole {idx}",
                "geometry_refs": {"faces": [f"face_hole_{idx:03d}"]},
                "parameters": {"diameter_mm": 6.6},
                "parameter_source": "mock",
                "parameter_confidence": "medium",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Test fixture semantic hole.",
                "intent": {"role": "mounting_or_passage_candidate"},
                "recognition": {"method": "test_fixture", "confidence": "medium"},
                "hole_metadata": _hole_metadata(idx, known_stack=known_stack, with_axis=with_axis),
            }
            for idx in range(1, 5)
        ],
    }
    with zipfile.ZipFile(pkg, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph, indent=2, sort_keys=True) + "\n")
    return pkg


def _make_threaded_package(
    tmp_path: Path,
    *,
    depth_mm: float | None = 8.0,
    thread_form: str = "metric",
) -> Path:
    pkg = create_package("threaded_fastener_demo", tmp_path / "threaded_fastener_demo.aieng")
    hole_metadata = {
        "diameter_mm": 6.0,
        "hole_depth_kind": "blind",
        "through": False,
        "axis": {
            "origin_mm": [0.0, 0.0, 0.0],
            "direction": [0.0, 0.0, 1.0],
            "origin_source": "test_fixture",
            "direction_source": "test_fixture",
        },
        "thread": {"designation": "M6", "pitch_mm": 1.0, "form": thread_form},
    }
    if depth_mm is not None:
        hole_metadata["depth_mm"] = depth_mm
    feature_graph = {
        "format_version": FORMAT_VERSION,
        "features": [
            {
                "id": "feat_tapped_hole_001",
                "type": "mounting_hole",
                "name": "M6 blind tapped hole",
                "geometry_refs": {"faces": ["face_tapped_hole_001"]},
                "parameters": {"diameter_mm": 6.0},
                "parameter_source": "mock",
                "parameter_confidence": "medium",
                "editable": True,
                "editability": "semantic_only",
                "writeback_strategy": "semantic_parameter_update_only",
                "editability_reason": "Test fixture semantic tapped hole.",
                "intent": {"role": "threaded_mounting_candidate"},
                "recognition": {"method": "test_fixture", "confidence": "medium"},
                "hole_metadata": hole_metadata,
            }
        ],
    }
    with zipfile.ZipFile(pkg, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("graph/feature_graph.json", json.dumps(feature_graph, indent=2, sort_keys=True) + "\n")
    return pkg


def _hole_metadata(idx: int, *, known_stack: bool, with_axis: bool) -> dict:
    metadata = {
        "diameter_mm": 6.6,
        "depth_mm": 9.0,
        "hole_depth_kind": "through",
        "through": True,
        "mating_stack": (
            {"status": "known", "thickness_mm": 9.0, "source": "test_fixture", "part_ids": ["plate_a", "plate_b"]}
            if known_stack
            else {"status": "unknown", "reason": "test fixture omitted stack"}
        ),
    }
    if with_axis:
        metadata["axis"] = {
            "origin_mm": [float(idx * 20), 10.0, 0.0],
            "direction": [0.0, 0.0, 1.0],
            "origin_source": "test_fixture",
            "direction_source": "test_fixture",
        }
    return metadata


def _read_json(pkg: Path, member: str) -> dict:
    with zipfile.ZipFile(pkg) as zf:
        return json.loads(zf.read(member))


def test_explicit_insertion_populates_four_m6_holes_with_screws_and_nuts(tmp_path):
    pkg = _make_package(tmp_path)

    report = insert_fasteners_for_holes(
        pkg,
        [f"feat_hole_{idx:03d}" for idx in range(1, 5)],
    )

    assert report["status"] == "ok"
    assert report["explicit_opt_in"] is True
    assert report["mutates_geometry"] is False
    assert report["inserted_count"] == 8
    assert report["blockers"] == []

    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    standard_parts = [feature for feature in feature_graph["features"] if feature["type"] == "standard_part"]
    screws = [feature for feature in standard_parts if feature["canonical_type"] == "screw"]
    nuts = [feature for feature in standard_parts if feature["canonical_type"] == "nut"]
    assert len(screws) == 4
    assert len(nuts) == 4
    assert all(feature["standard_part"] is True for feature in standard_parts)
    assert all(feature["source_library"] == "aieng.standards" for feature in standard_parts)
    assert all(feature["designation"] == "M6" for feature in standard_parts)
    assert screws[0]["parameters"]["length_mm"] == 16.0
    assert screws[0]["parameters"]["placement"]["axis_direction"] == [0.0, 0.0, 1.0]

    written_report = _read_json(pkg, FASTENER_INSERTION_REPORT_PATH)
    assert written_report["inserted_count"] == 8
    manifest = read_manifest(pkg)
    assert manifest["resources"]["graph"]["fastener_insertion_report"] == FASTENER_INSERTION_REPORT_PATH

    schema = json.loads((Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "feature_graph.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(feature_graph))
    assert errors == []


def test_insertion_reports_blocker_without_adding_parts_when_stack_unknown(tmp_path):
    pkg = _make_package(tmp_path, known_stack=False)

    report = insert_fasteners_for_holes(pkg, ["feat_hole_001"])

    assert report["status"] == "blocked"
    assert report["inserted_count"] == 0
    assert report["blockers"][0]["code"] == "unknown_stack_thickness"
    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    assert not any(feature["type"] == "standard_part" for feature in feature_graph["features"])


def test_insertion_reports_blocker_without_adding_parts_when_axis_unknown(tmp_path):
    pkg = _make_package(tmp_path, with_axis=False)

    report = insert_fasteners_for_holes(pkg, ["feat_hole_001"])

    assert report["status"] == "blocked"
    assert report["inserted_count"] == 0
    assert report["blockers"][0]["code"] == "insufficient_placement"
    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    assert not any(feature["type"] == "standard_part" for feature in feature_graph["features"])


def test_threaded_insertion_uses_depth_and_does_not_insert_nut(tmp_path):
    pkg = _make_threaded_package(tmp_path, depth_mm=8.0)

    report = insert_fasteners_for_holes(pkg, ["feat_tapped_hole_001"])

    assert report["status"] == "ok"
    assert report["inserted_count"] == 1
    assert report["inserted"][0]["kind"] == "threaded_screw"
    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    standard_parts = [feature for feature in feature_graph["features"] if feature["type"] == "standard_part"]
    assert len(standard_parts) == 1
    screw = standard_parts[0]
    assert screw["canonical_type"] == "screw"
    assert screw["designation"] == "M6"
    assert screw["parameters"]["length_mm"] == 8.0
    assert screw["parameters"]["length_mm"] <= 8.0
    assert screw["parameters"]["thread_engagement_depth_mm"] == 8.0
    assert screw["standard_part_metadata"]["threaded_hole"] is True


def test_threaded_insertion_requires_known_depth(tmp_path):
    pkg = _make_threaded_package(tmp_path, depth_mm=None)

    report = insert_fasteners_for_holes(pkg, ["feat_tapped_hole_001"])

    assert report["status"] == "blocked"
    assert report["blockers"][0]["code"] == "missing_thread_depth"
    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    assert not any(feature["type"] == "standard_part" for feature in feature_graph["features"])


def test_threaded_insertion_rejects_unsupported_thread_form(tmp_path):
    pkg = _make_threaded_package(tmp_path, thread_form="unc")

    report = insert_fasteners_for_holes(pkg, ["feat_tapped_hole_001"])

    assert report["status"] == "blocked"
    assert report["blockers"][0]["code"] == "no_match"
    assert "unsupported thread form" in report["blockers"][0]["planner_result"]["reasons"][0]
    feature_graph = _read_json(pkg, "graph/feature_graph.json")
    assert not any(feature["type"] == "standard_part" for feature in feature_graph["features"])
