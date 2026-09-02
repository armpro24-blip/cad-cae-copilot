"""Tests for the AI preprocessing module."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.config import Settings
from app.project_io import compute_topology_hash

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _make_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


def _make_project(settings: Settings, name: str, package: str | None) -> tuple[str, Path | None]:
    from app.main import default_project, project_dir, save_project

    project = save_project(settings, default_project(name))
    project_id = project["id"]
    pkg_path = None
    if package:
        pkg_path = project_dir(settings, project_id) / package
        project["aieng_file"] = package
        save_project(settings, project)
    return project_id, pkg_path


_TOPOLOGY_MAP: dict[str, Any] = {
    "format_version": "0.1",
    "entities": [
        {
            "id": "body_001",
            "type": "solid",
            "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 10.0],
        },
        {
            "id": "face_001",
            "type": "face",
            "surface_type": "plane",
            "area": 5000.0,
            "normal": [0.0, 0.0, -1.0],
            "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 0.0],
            "body_id": "body_001",
        },
        {
            "id": "face_002",
            "type": "face",
            "surface_type": "plane",
            "area": 5000.0,
            "normal": [0.0, 0.0, 1.0],
            "bounding_box": [0.0, 0.0, 10.0, 100.0, 50.0, 10.0],
            "body_id": "body_001",
        },
        {
            "id": "face_003",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "bounding_box": [8.0, 8.0, 0.0, 12.0, 12.0, 10.0],
            "body_id": "body_001",
        },
        {
            "id": "face_004",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "bounding_box": [88.0, 8.0, 0.0, 92.0, 12.0, 10.0],
            "body_id": "body_001",
        },
    ],
}

_FEATURE_GRAPH: dict[str, Any] = {
    "features": [
        {
            "id": "feat_base_001",
            "type": "base_plate",
            "name": "Main plate",
            "geometry_refs": {"faces": ["face_001", "face_002"]},
            "parameters": {"length_mm": 100.0, "width_mm": 50.0, "thickness_mm": 10.0},
            "intent": {"role": "structural_base"},
        },
        {
            "id": "feat_hole_001",
            "type": "mounting_hole",
            "name": "Left bolt hole",
            "geometry_refs": {"faces": ["face_003"]},
            "parameters": {"diameter_mm": 8.0},
            "intent": {"role": "mounting_candidate"},
        },
        {
            "id": "feat_hole_002",
            "type": "mounting_hole",
            "name": "Right bolt hole",
            "geometry_refs": {"faces": ["face_004"]},
            "parameters": {"diameter_mm": 8.0},
            "intent": {"role": "mounting_candidate"},
        },
    ]
}


def _build_test_package(pkg_path: Path) -> None:
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps(_TOPOLOGY_MAP))
        zf.writestr("graph/feature_graph.json", json.dumps(_FEATURE_GRAPH))


# ── unit tests for geometry context builder ──────────────────────────────────

def test_build_geometry_context_extracts_bounding_box(tmp_path: Path) -> None:
    from app.geometry_providers import build_geometry_context

    pkg = tmp_path / "test.aieng"
    _build_test_package(pkg)
    ctx = build_geometry_context(pkg)
    text = ctx.to_llm_text()

    assert "100.0" in text
    assert "BOUNDING BOX" in text


def test_build_geometry_context_lists_faces(tmp_path: Path) -> None:
    from app.geometry_providers import build_geometry_context

    pkg = tmp_path / "test.aieng"
    _build_test_package(pkg)
    ctx = build_geometry_context(pkg)
    text = ctx.to_llm_text()

    assert "face_001" in text
    assert "plane" in text
    assert "cylinder" in text
    assert "radius=4.00mm" in text


def test_build_geometry_context_lists_features(tmp_path: Path) -> None:
    from app.geometry_providers import build_geometry_context

    pkg = tmp_path / "test.aieng"
    _build_test_package(pkg)
    ctx = build_geometry_context(pkg)
    text = ctx.to_llm_text()

    assert "feat_base_001" in text
    assert "feat_hole_001" in text
    assert "mounting_hole" in text


def test_build_geometry_context_empty_package(tmp_path: Path) -> None:
    from app.geometry_providers import build_geometry_context

    pkg = tmp_path / "empty.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    ctx = build_geometry_context(pkg)
    text = ctx.to_llm_text()

    assert "Data source" in text


# ── unit tests for _fea_setup_to_setup_yaml ──────────────────────────────────

def test_fea_setup_to_setup_yaml_basic() -> None:
    from app.ai_preprocessing import _fea_setup_to_setup_yaml

    fea = {
        "material": "Al6061-T6",
        "material_reason": "lightweight, common bracket material",
        "boundary_conditions": [
            {
                "id": "bc_001",
                "target_feature_id": "feat_hole_001",
                "target_face_ids": ["face_003"],
                "target_description": "Left bolt hole",
                "type": "fixed",
                "reason": "bolted to frame",
            }
        ],
        "loads": [
            {
                "id": "load_001",
                "target_feature_id": "feat_base_001",
                "target_face_ids": ["face_002"],
                "target_description": "Top face",
                "type": "force",
                "value_n": 500.0,
                "direction": [0.0, 0.0, -1.0],
                "reason": "downward load",
            }
        ],
        "mesh": {"target_size_mm": 2.5, "refinement_note": "refine near holes"},
        "analysis_type": "static_structural",
        "assumptions": ["linear elastic"],
        "warnings": [],
    }

    result = _fea_setup_to_setup_yaml(fea)

    assert result["material_name"] == "Al6061-T6"
    assert "Al6061-T6" in result["materials"]
    assert result["materials"]["Al6061-T6"]["youngs_modulus_mpa"] == 69000
    assert len(result["boundary_conditions"]) == 1
    assert result["boundary_conditions"][0]["target_feature"] == "feat_hole_001"
    assert result["loads"][0]["value_n"] == 500.0
    assert result["mesh"]["target_size_mm"] == 2.5
    assert result["ai_generated"] is True


def test_fea_setup_to_cae_mapping() -> None:
    from app.ai_preprocessing import _fea_setup_to_cae_mapping

    fea = {
        "boundary_conditions": [
            {
                "target_feature_id": "feat_hole_001",
                "target_description": "Left bolt hole",
                "type": "fixed",
                "target_face_ids": ["face_003"],
            }
        ],
        "loads": [
            {
                "target_feature_id": "feat_base_001",
                "target_description": "Top face",
                "type": "force",
                "value_n": 500.0,
                "direction": [0.0, 0.0, -1.0],
                "target_face_ids": ["face_002"],
            }
        ],
    }

    result = _fea_setup_to_cae_mapping(fea)

    assert result["ai_generated"] is True
    mappings = result["mappings"]
    assert len(mappings) == 2
    feature_ids = [m["maps_to"]["cae_target_id"] for m in mappings]
    assert "feat_hole_001" in feature_ids
    assert "feat_base_001" in feature_ids


def test_build_user_prompt_includes_brep_digest() -> None:
    from app.ai_preprocessing import _build_user_prompt

    prompt = _build_user_prompt(
        geometry_context="GEOM",
        task_description="fixed at holes",
        material_hint=None,
        mesh_hint=None,
        material_catalog="Al6061-T6",
        brep_digest="B-Rep Graph Digest\n- @face:face_001 support",
    )

    assert "B-REP POINTER DIGEST" in prompt
    assert "@face:face_001" in prompt
    assert "target_pointers" in prompt


def test_validate_resolves_group_pointer_to_face_ids() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [
            {
                "id": "bc_001",
                "target_pointers": ["@group:mounting_group"],
                "target_face_ids": [],
            }
        ],
        "loads": [],
    }
    entity_index = {
        "mounting_group": {
            "kind": "group",
            "members": ["face_003", "face_004"],
        }
    }
    normalized, warnings = _validate_and_normalize_fea_setup(
        setup, _VALID_FACES, _KNOWN_MATERIALS, entity_index
    )

    assert normalized["boundary_conditions"][0]["target_face_ids"] == ["face_003", "face_004"]
    assert normalized["boundary_conditions"][0]["selection_source"] == "brep_pointer"
    assert warnings == []


def test_pointer_only_mapping_uses_selection_key() -> None:
    from app.ai_preprocessing import _fea_setup_to_cae_mapping, _fea_setup_to_setup_yaml

    fea = {
        "material": "Al6061-T6",
        "boundary_conditions": [
            {
                "id": "bc_001",
                "target_feature_id": None,
                "target_pointers": ["@group:mounting_group"],
                "target_face_ids": ["face_003", "face_004"],
                "target_description": "mounting hole group",
                "type": "fixed",
            }
        ],
        "loads": [],
        "mesh": {},
    }

    setup_yaml = _fea_setup_to_setup_yaml(fea)
    mapping = _fea_setup_to_cae_mapping(fea)

    assert setup_yaml["boundary_conditions"][0]["target_feature"] == "group_mounting_group"
    assert setup_yaml["boundary_conditions"][0]["target_pointers"] == ["@group:mounting_group"]
    assert mapping["mappings"][0]["maps_to"]["cae_target_id"] == "group_mounting_group"
    assert mapping["mappings"][0]["maps_to"]["target_pointers"] == ["@group:mounting_group"]
    assert mapping["mappings"][0]["face_ids"] == ["face_003", "face_004"]



# ── integration test: endpoint returns 400 when task_description missing ─────

def test_ai_preprocessing_endpoint_missing_task(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    project_id, pkg_path = _make_project(settings, "test-project", "test.aieng")
    assert pkg_path is not None
    _build_test_package(pkg_path)

    resp = client.post(f"/api/projects/{project_id}/ai-preprocessing", json={})
    assert resp.status_code == 400
    assert "task_description" in resp.text


def test_ai_preprocessing_endpoint_no_package(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    from app.main import default_project, save_project
    project = save_project(settings, default_project("no-pkg"))
    project_id = project["id"]

    resp = client.post(
        f"/api/projects/{project_id}/ai-preprocessing",
        json={"task_description": "fixed at bolt holes, 500N downward"},
    )
    assert resp.status_code == 404


# ── integration test: endpoint with mocked Claude call ───────────────────────

_MOCK_CLAUDE_RESPONSE = {
    "material": "Al6061-T6",
    "material_reason": "Typical bracket material, good strength-to-weight",
    "boundary_conditions": [
        {
            "id": "bc_001",
            "target_feature_id": "feat_hole_001",
            "target_face_ids": ["face_003"],
            "target_description": "Left mounting hole - bolt connection",
            "type": "fixed",
            "reason": "Bolt holes are the fixed support as described",
        },
        {
            "id": "bc_002",
            "target_feature_id": "feat_hole_002",
            "target_face_ids": ["face_004"],
            "target_description": "Right mounting hole - bolt connection",
            "type": "fixed",
            "reason": "Both bolt holes provide fixed support",
        },
    ],
    "loads": [
        {
            "id": "load_001",
            "target_feature_id": "feat_base_001",
            "target_face_ids": ["face_002"],
            "target_description": "Top surface of plate - downward load",
            "type": "force",
            "value_n": 500.0,
            "direction": [0.0, 0.0, -1.0],
            "reason": "500N downward load applied on top face",
        }
    ],
    "mesh": {
        "target_size_mm": 2.5,
        "refinement_note": "Refine around bolt holes (radius ~4mm)",
        "reason": "Medium mesh appropriate for this 100x50x10mm plate",
    },
    "analysis_type": "static_structural",
    "assumptions": ["Linear elastic analysis", "Isotropic material", "Small deformations"],
    "warnings": [],
}


def test_ai_preprocessing_endpoint_dry_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "bracket-test", "bracket.aieng")
    assert pkg_path is not None
    _build_test_package(pkg_path)

    with patch("app.ai_preprocessing.call_claude_for_fea_setup", return_value=_MOCK_CLAUDE_RESPONSE):
        resp = client.post(
            f"/api/projects/{project_id}/ai-preprocessing",
            json={
                "task_description": "Plate fixed at two bolt holes, 500N downward on top face",
                "material_hint": "aluminum",
                "mesh_hint": "medium",
                "write_files": False,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fea_setup"]["material"] == "Al6061-T6"
    assert len(data["fea_setup"]["boundary_conditions"]) == 2
    assert data["setup_yaml"]["ai_generated"] is True
    assert data["written_artifacts"] == []


def test_ai_preprocessing_endpoint_writes_files(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "bracket-write", "bracket_w.aieng")
    assert pkg_path is not None
    _build_test_package(pkg_path)

    with patch("app.ai_preprocessing.call_claude_for_fea_setup", return_value=_MOCK_CLAUDE_RESPONSE):
        resp = client.post(
            f"/api/projects/{project_id}/ai-preprocessing",
            json={
                "task_description": "Plate fixed at two bolt holes, 500N downward",
                "write_files": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "simulation/setup.yaml" in data["written_artifacts"]
    assert "simulation/cae_mapping.json" in data["written_artifacts"]

    # verify artifacts are in the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/setup.yaml" in names
        assert "simulation/cae_mapping.json" in names

        setup = yaml.safe_load(zf.read("simulation/setup.yaml"))
        assert setup["material_name"] == "Al6061-T6"
        assert setup["ai_generated"] is True
        assert len(setup["boundary_conditions"]) == 2

        mapping = json.loads(zf.read("simulation/cae_mapping.json"))
        assert mapping["ai_generated"] is True
        assert len(mapping["mappings"]) >= 2

        # The topology hash binds the CAE setup to the geometry it was generated against.
        topology = json.loads(zf.read("geometry/topology_map.json"))
        expected_hash = compute_topology_hash(topology)
        assert setup.get("topology_hash") == expected_hash
        assert mapping.get("topology_hash") == expected_hash


# ── validation and normalization tests ───────────────────────────────────────

_KNOWN_MATERIALS = {"Al6061-T6", "Al7075-T6", "Steel-1045", "Ti-6Al-4V", "Nylon-PA66"}
_VALID_FACES = {"face_001", "face_002", "face_003", "face_004"}


def test_validate_normalizes_unknown_material() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {"material": "UnknownAlloy-X99", "boundary_conditions": [], "loads": []}
    normalized, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert normalized["material"] == "Al6061-T6"
    assert any("UnknownAlloy-X99" in w for w in warnings)
    assert any("falling back" in w for w in warnings)


def test_validate_accepts_valid_material() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {"material": "Steel-1045", "boundary_conditions": [], "loads": []}
    normalized, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert normalized["material"] == "Steel-1045"
    assert not any("material" in w.lower() for w in warnings)


def test_validate_flags_unknown_face_ids() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [
            {"id": "bc_001", "target_face_ids": ["face_999"], "type": "fixed"}
        ],
        "loads": [
            {"id": "load_001", "target_face_ids": ["face_888"], "value_n": 100.0}
        ],
    }
    _, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert any("face_999" in w for w in warnings)
    assert any("face_888" in w for w in warnings)


def test_validate_skips_face_check_when_no_topology() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [
            {"id": "bc_001", "target_face_ids": ["face_999"], "type": "fixed"}
        ],
        "loads": [],
    }
    # Empty valid_face_ids means topology not available — no warnings expected.
    _, warnings = _validate_and_normalize_fea_setup(setup, set(), _KNOWN_MATERIALS)

    assert not any("face_999" in w for w in warnings)


def test_validate_warns_nonpositive_load() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [],
        "loads": [{"id": "load_001", "target_face_ids": [], "value_n": -50.0}],
    }
    _, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert any("not positive" in w for w in warnings)


def test_validate_warns_extreme_load() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [],
        "loads": [{"id": "load_001", "target_face_ids": [], "value_n": 2e9}],
    }
    _, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert any("GN" in w for w in warnings)


def test_validate_detects_nset_collision() -> None:
    from app.ai_preprocessing import _validate_and_normalize_fea_setup

    # Two BCs with feature IDs that truncate to the same 16-char NSET name.
    long_prefix = "very_long_feature_name_"
    feat_a = long_prefix + "A"
    feat_b = long_prefix + "B"

    setup = {
        "material": "Al6061-T6",
        "boundary_conditions": [
            {"id": "bc_001", "target_feature_id": feat_a, "target_face_ids": []},
            {"id": "bc_002", "target_feature_id": feat_b, "target_face_ids": []},
        ],
        "loads": [],
    }
    _, warnings = _validate_and_normalize_fea_setup(setup, _VALID_FACES, _KNOWN_MATERIALS)

    assert any("collision" in w.lower() for w in warnings)


def test_load_valid_face_ids_from_package(tmp_path: Path) -> None:
    from app.ai_preprocessing import _load_valid_face_ids

    pkg = tmp_path / "test.aieng"
    _build_test_package(pkg)
    face_ids = _load_valid_face_ids(pkg)

    assert "face_001" in face_ids
    assert "face_002" in face_ids
    assert "face_003" in face_ids
    assert "face_004" in face_ids
    assert "body_001" not in face_ids  # solids excluded


def test_load_valid_face_ids_empty_package(tmp_path: Path) -> None:
    from app.ai_preprocessing import _load_valid_face_ids

    pkg = tmp_path / "empty.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
    face_ids = _load_valid_face_ids(pkg)

    assert face_ids == set()


def test_write_both_to_package_atomic(tmp_path: Path) -> None:
    from app.ai_preprocessing import _write_both_to_package

    pkg = tmp_path / "test.aieng"
    _build_test_package(pkg)

    _write_both_to_package(pkg, {
        "simulation/setup.yaml": b"material: Al6061-T6\n",
        "simulation/cae_mapping.json": b'{"mappings": []}',
    })

    with zipfile.ZipFile(pkg, "r") as zf:
        names = zf.namelist()
        assert "simulation/setup.yaml" in names
        assert "simulation/cae_mapping.json" in names
        assert zf.read("simulation/setup.yaml") == b"material: Al6061-T6\n"
        # original members preserved
        assert "geometry/topology_map.json" in names


def test_ai_preprocessing_response_includes_validation_warnings(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)

    project_id, pkg_path = _make_project(settings, "bracket-val", "bracket_val.aieng")
    assert pkg_path is not None
    _build_test_package(pkg_path)

    bad_response = dict(_MOCK_CLAUDE_RESPONSE)
    bad_response = {**_MOCK_CLAUDE_RESPONSE, "material": "UnknownMaterial-X99"}

    with patch("app.ai_preprocessing.call_claude_for_fea_setup", return_value=bad_response):
        resp = client.post(
            f"/api/projects/{project_id}/ai-preprocessing",
            json={"task_description": "fixed bolt holes, 500N load", "write_files": False},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "validation_warnings" in data
    assert any("UnknownMaterial-X99" in w for w in data["validation_warnings"])
    # Material should be normalized in the output
    assert data["fea_setup"]["material"] == "Al6061-T6"


# ── material catalog tests ────────────────────────────────────────────────────

def test_material_catalog_has_common_materials() -> None:
    import sys
    aieng_src = _WORKSPACE_ROOT / "aieng" / "src"
    if str(aieng_src) not in sys.path:
        sys.path.insert(0, str(aieng_src))
    from aieng.context.materials import MATERIALS, get_material, list_materials_for_llm

    assert "Al6061-T6" in MATERIALS
    assert "Steel-1045" in MATERIALS
    assert "Ti-6Al-4V" in MATERIALS
    assert "Nylon-PA66" in MATERIALS

    steel = get_material("Steel-1045")
    assert steel["youngs_modulus_mpa"] == 205000

    catalog = list_materials_for_llm()
    assert "Steel-1045" in catalog
    assert "205000" in catalog
