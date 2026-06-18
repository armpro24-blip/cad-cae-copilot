"""Tests for text-to-CAD generation (build123d backend)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.config import Settings

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


def _make_project(settings: Settings, name: str) -> str:
    from app.main import default_project, save_project
    project = save_project(settings, default_project(name))
    return project["id"]


def _load_topology_map(settings: Settings, project_id: str) -> dict:
    # execute_build123d_code returns a compact topology_summary and writes the
    # full topology_map to the package; read it from there.
    from app.main import project_dir
    from app.project_io import get_project
    project = get_project(settings, project_id)
    pkg_path = project_dir(settings, project_id) / project["aieng_file"]
    with zipfile.ZipFile(pkg_path, "r") as zf:
        return json.loads(zf.read("geometry/topology_map.json"))


# ── sample topology ───────────────────────────────────────────────────────────

_SAMPLE_TOPOLOGY: dict = {
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
            "center": [50.0, 25.0, 0.0],
            "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 0.0],
        },
        {
            "id": "face_002",
            "type": "face",
            "surface_type": "plane",
            "area": 5000.0,
            "normal": [0.0, 0.0, 1.0],
            "center": [50.0, 25.0, 10.0],
            "bounding_box": [0.0, 0.0, 10.0, 100.0, 50.0, 10.0],
        },
        {
            "id": "face_003",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "center": [10.0, 10.0, 5.0],
            "bounding_box": [8.0, 8.0, 0.0, 12.0, 12.0, 10.0],
        },
        {
            "id": "face_004",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "center": [90.0, 10.0, 5.0],
            "bounding_box": [88.0, 8.0, 0.0, 92.0, 12.0, 10.0],
        },
        {
            "id": "face_005",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "center": [10.0, 40.0, 5.0],
            "bounding_box": [8.0, 38.0, 0.0, 12.0, 42.0, 10.0],
        },
        {
            "id": "face_006",
            "type": "face",
            "surface_type": "cylinder",
            "area": 251.3,
            "radius": 4.0,
            "center": [90.0, 40.0, 5.0],
            "bounding_box": [88.0, 38.0, 0.0, 92.0, 42.0, 10.0],
        },
    ],
}

_SAMPLE_CODE = """\
with BuildPart() as bp:
    Box(100, 50, 10)
    with Locations((10, 10, 0), (90, 10, 0), (10, 40, 0), (90, 40, 0)):
        Hole(radius=4, depth=10)
result = bp.part
"""


# ── _coerce_code ──────────────────────────────────────────────────────────────

def test_coerce_code_strips_python_fence() -> None:
    from app.cad_generation import _coerce_code

    raw = "```python\nresult = Box(10, 10, 10)\n```"
    assert _coerce_code(raw) == "result = Box(10, 10, 10)"


def test_coerce_code_strips_plain_fence() -> None:
    from app.cad_generation import _coerce_code

    raw = "```\nresult = Box(10, 10, 10)\n```"
    assert _coerce_code(raw) == "result = Box(10, 10, 10)"


def test_coerce_code_plain_text_unchanged() -> None:
    from app.cad_generation import _coerce_code

    raw = "result = Box(10, 10, 10)"
    assert _coerce_code(raw) == raw


# ── _topology_to_feature_graph ────────────────────────────────────────────────

def test_feature_graph_detects_bolt_pattern() -> None:
    from app.cad_generation import _topology_to_feature_graph

    fg = _topology_to_feature_graph(_SAMPLE_TOPOLOGY)
    patterns = [f for f in fg["features"] if f["type"] == "mounting_hole_pattern"]
    assert len(patterns) == 1
    assert patterns[0]["parameters"]["count"] == 4
    assert set(patterns[0]["geometry_refs"]["faces"]) == {
        "face_003", "face_004", "face_005", "face_006"
    }


def test_feature_graph_detects_base_plate() -> None:
    from app.cad_generation import _topology_to_feature_graph

    fg = _topology_to_feature_graph(_SAMPLE_TOPOLOGY)
    plates = [f for f in fg["features"] if f["type"] == "base_plate"]
    assert len(plates) == 1
    assert plates[0]["geometry_refs"]["faces"] == ["face_001"]


def test_feature_graph_empty_topology() -> None:
    from app.cad_generation import _topology_to_feature_graph

    fg = _topology_to_feature_graph({})
    assert fg["features"] == []


def test_feature_graph_surfaces_named_parts() -> None:
    """Named solids (from build123d .label) become named_part features the agent
    can reference, with id derived from the body id for stability."""
    from app.cad_generation import _topology_to_feature_graph

    topo = {
        "entities": [
            {"id": "body_001", "type": "solid", "name": "fuselage", "bounding_box": [0, 0, 0, 40, 40, 10]},
            {"id": "body_002", "type": "solid", "name": "motor_pod_FL", "bounding_box": [50, 0, 0, 56, 0, 30]},
            {"id": "face_001", "type": "face", "surface_type": "plane", "body_id": "body_001"},
            {"id": "face_002", "type": "face", "surface_type": "cylinder", "radius": 3.0, "body_id": "body_002"},
        ]
    }
    fg = _topology_to_feature_graph(topo)
    named = [f for f in fg["features"] if f["type"] == "named_part"]
    assert [f["name"] for f in named] == ["fuselage", "motor_pod_FL"]
    fuselage = next(f for f in named if f["name"] == "fuselage")
    assert fuselage["id"] == "feat_body_001"
    assert fuselage["geometry_refs"]["body"] == "body_001"
    assert fuselage["geometry_refs"]["faces"] == ["face_001"]


def test_feature_graph_recognizes_external_fillets_not_hole_pattern() -> None:
    """A box with equal-radius quarter-round edge fillets must surface as `fillet`
    features, not be swept into a mounting_hole/bore pattern by the cylinder
    grouping heuristic (#297 Phase 2)."""
    from app.cad_generation import _topology_to_feature_graph

    topo = {
        "entities": [
            {"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 100, 60, 40], "volume": 230000.0},
            {"id": "face_side_x", "type": "face", "surface_type": "plane", "area": 4000.0, "normal": [1.0, 0.0, 0.0], "bounding_box": [100, 0, 0, 100, 60, 40], "adjacent_entity_ids": ["face_fil_1", "face_fil_2", "face_fil_3", "face_fil_4"]},
            {"id": "face_side_y", "type": "face", "surface_type": "plane", "area": 6000.0, "normal": [0.0, 1.0, 0.0], "bounding_box": [0, 60, 0, 100, 60, 40], "adjacent_entity_ids": ["face_fil_1", "face_fil_2", "face_fil_3", "face_fil_4"]},
            # Four identical vertical-edge fillets (quarter cylinders, cross ~ 6x6).
            {"id": "face_fil_1", "type": "face", "surface_type": "cylinder", "radius": 6.0, "axis": [0, 0, 1], "area": 377.0, "bounding_box": [0, 0, 0, 6, 6, 40], "adjacent_entity_ids": ["face_side_x", "face_side_y"]},
            {"id": "face_fil_2", "type": "face", "surface_type": "cylinder", "radius": 6.0, "axis": [0, 0, 1], "area": 377.0, "bounding_box": [94, 0, 0, 100, 6, 40], "adjacent_entity_ids": ["face_side_x", "face_side_y"]},
            {"id": "face_fil_3", "type": "face", "surface_type": "cylinder", "radius": 6.0, "axis": [0, 0, 1], "area": 377.0, "bounding_box": [0, 54, 0, 6, 60, 40], "adjacent_entity_ids": ["face_side_x", "face_side_y"]},
            {"id": "face_fil_4", "type": "face", "surface_type": "cylinder", "radius": 6.0, "axis": [0, 0, 1], "area": 377.0, "bounding_box": [94, 54, 0, 100, 60, 40], "adjacent_entity_ids": ["face_side_x", "face_side_y"]},
        ]
    }
    fg = _topology_to_feature_graph(topo)
    fillets = [f for f in fg["features"] if f["type"] == "fillet"]
    assert len(fillets) == 4
    fillet_faces = {ref for f in fillets for ref in f["geometry_refs"]["faces"]}
    assert fillet_faces == {"face_fil_1", "face_fil_2", "face_fil_3", "face_fil_4"}
    # The fillet cylinders must NOT be grouped as holes/bores.
    for f in fg["features"]:
        if f["type"] in {"mounting_hole_pattern", "mounting_hole", "bore"}:
            assert not (set(f["geometry_refs"].get("faces", [])) & fillet_faces)


def test_feature_graph_surfaces_standard_parts_with_provenance() -> None:
    from app.cad_generation import _named_parts_from_feature_graph, _topology_to_feature_graph

    topo = {
        "entities": [
            {
                "id": "body_001",
                "type": "solid",
                "name": "mounting_bolt_M6",
                "bounding_box": [-3, -3, 0, 3, 3, 12],
                "standard_part": True,
                "source_library": "bd_warehouse",
                "source_module": "bd_warehouse.fastener",
                "source_class": "SocketHeadCapScrew",
                "canonical_type": "screw",
                "designation": "M6-1",
                "object_label": "mounting_bolt_M6",
                "detection_method": "bd_warehouse_type",
                "confidence": "high",
            },
            {
                "id": "face_001",
                "type": "face",
                "surface_type": "cylinder",
                "radius": 3.0,
                "body_id": "body_001",
            },
        ]
    }

    fg = _topology_to_feature_graph(topo)

    standard = [f for f in fg["features"] if f["type"] == "standard_part"]
    assert len(standard) == 1
    screw = standard[0]
    assert screw["name"] == "mounting_bolt_M6"
    assert screw["standard_part"] is True
    assert screw["source_library"] == "bd_warehouse"
    assert screw["canonical_type"] == "screw"
    assert screw["designation"] == "M6-1"
    assert screw["recognition"] == {"method": "bd_warehouse_type", "confidence": "high"}
    assert screw["geometry_refs"] == {"body": "body_001", "faces": ["face_001"]}
    assert _named_parts_from_feature_graph(fg) == ["mounting_bolt_M6"]
    assert fg["metadata"]["standard_parts"]["count"] == 1
    assert fg["metadata"]["standard_parts"]["by_canonical_type"] == {"screw": 1}


def test_feature_graph_unnamed_solid_has_no_named_part() -> None:
    from app.cad_generation import _topology_to_feature_graph

    topo = {"entities": [{"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]}]}
    fg = _topology_to_feature_graph(topo)
    assert [f for f in fg["features"] if f["type"] == "named_part"] == []


def test_feature_graph_detects_hollow_body_candidate() -> None:
    from app.cad_generation import _slim_feature_graph_for_response, _topology_to_feature_graph

    topo = {
        "entities": [
            {
                "id": "body_001",
                "type": "solid",
                "name": "housing_shell",
                "bounding_box": [0, 0, 0, 100, 80, 60],
                "volume": 42000.0,
            },
            *[
                {
                    "id": f"face_{idx:03d}",
                    "type": "face",
                    "surface_type": "plane",
                    "body_id": "body_001",
                    "area": 100.0,
                }
                for idx in range(1, 9)
            ],
        ]
    }

    fg = _topology_to_feature_graph(topo, source_code="body = housing(100, 80, 60, wall=4)", model_kind="mechanical")
    hollow = [f for f in fg["features"] if f["type"] == "hollow_body"]

    assert len(hollow) == 1
    assert hollow[0]["intent"]["role"] == "shell"
    assert hollow[0]["parameters"]["bbox_fill_ratio"] < 0.1
    assert hollow[0]["recognition"]["method"] == "bbox_volume_fill_ratio"
    assert hollow[0]["recognition"]["confidence"] == "high"
    slim = _slim_feature_graph_for_response(fg)
    slim_hollow = next(f for f in slim["features"] if f["type"] == "hollow_body")
    assert slim_hollow["geometry_refs"]["body"] == "body_001"
    assert slim_hollow["geometry_refs"]["face_count"] == 8


def test_feature_graph_does_not_mark_solid_block_as_hollow_body() -> None:
    from app.cad_generation import _topology_to_feature_graph

    topo = {
        "entities": [
            {
                "id": "body_001",
                "type": "solid",
                "name": "solid_block",
                "bounding_box": [0, 0, 0, 10, 10, 10],
                "volume": 1000.0,
            },
            *[
                {"id": f"face_{idx:03d}", "type": "face", "surface_type": "plane", "body_id": "body_001"}
                for idx in range(1, 7)
            ],
        ]
    }

    fg = _topology_to_feature_graph(topo, model_kind="mechanical")

    assert [f for f in fg["features"] if f["type"] == "hollow_body"] == []


def test_feature_graph_two_hole_groups() -> None:
    from app.cad_generation import _topology_to_feature_graph

    topo = {
        "entities": [
            {"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 100, 100, 20]},
            {"id": "f1", "type": "face", "surface_type": "cylinder", "radius": 4.0, "area": 251.3, "center": [10, 10, 10]},
            {"id": "f2", "type": "face", "surface_type": "cylinder", "radius": 4.0, "area": 251.3, "center": [90, 10, 10]},
            {"id": "f3", "type": "face", "surface_type": "cylinder", "radius": 8.0, "area": 502.6, "center": [50, 50, 10]},
            {"id": "f4", "type": "face", "surface_type": "cylinder", "radius": 8.0, "area": 502.6, "center": [50, 80, 10]},
        ]
    }
    fg = _topology_to_feature_graph(topo)
    types = [f["type"] for f in fg["features"]]
    # Both groups have 2 cylinders → both become "mounting_hole"
    assert types.count("mounting_hole") == 2


# ── Build123dBackend.can_generate ─────────────────────────────────────────────

def test_build123d_backend_can_generate_true() -> None:
    from app.cad_generation import Build123dBackend

    backend = Build123dBackend(MagicMock())
    fake_module = MagicMock()
    with patch.dict("sys.modules", {"build123d": fake_module}):
        assert backend.can_generate() is True


def test_build123d_backend_cannot_generate_without_build123d() -> None:
    from app.cad_generation import Build123dBackend

    backend = Build123dBackend(MagicMock())
    with patch.dict("sys.modules", {"build123d": None}):
        assert backend.can_generate() is False


# ── _execute_build123d_code ───────────────────────────────────────────────────

def test_execute_build123d_code_success(tmp_path: Path) -> None:
    from app.cad_generation import _execute_build123d_code

    fake_step = b"ISO-10303-21;"
    fake_stl = b"solid result\nendsolid result"
    fake_glb = b"glTF\x02\x00\x00\x00"
    fake_topo = json.dumps(_SAMPLE_TOPOLOGY)

    def _fake_run(cmd, **kwargs):
        result_mock = MagicMock()
        result_mock.returncode = 0
        result_mock.stderr = ""
        out_step = Path(cmd[2])
        out_topo = Path(cmd[3])
        out_stl = Path(cmd[4])
        out_glb = Path(cmd[5])
        out_step.write_bytes(fake_step)
        out_topo.write_text(fake_topo, encoding="utf-8")
        out_stl.write_bytes(fake_stl)
        out_glb.write_bytes(fake_glb)
        return result_mock

    with patch("app.cad_generation.subprocess.run", side_effect=_fake_run):
        step_bytes, stl_bytes, glb_bytes, topo, _mesh_meta = _execute_build123d_code("result = Box(10, 10, 10)")

    assert step_bytes == fake_step
    assert stl_bytes == fake_stl
    assert glb_bytes == fake_glb
    assert topo["format_version"] == "0.1"


def test_execute_build123d_code_failure() -> None:
    from app.cad_generation import _execute_build123d_code

    fail_mock = MagicMock()
    fail_mock.returncode = 1
    fail_mock.stderr = "NameError: name 'result' is not defined"

    with patch("app.cad_generation.subprocess.run", return_value=fail_mock):
        with pytest.raises(RuntimeError, match="build123d execution failed"):
            _execute_build123d_code("Box(10, 10, 10)  # missing result =")


# ── CAD execution resource limits + timeout failure mode (#182) ────────────────

def test_resource_limits_from_env_defaults(monkeypatch) -> None:
    from app.cad_generation import (
        _CAD_DEFAULT_MAX_FILE_MB,
        _CAD_DEFAULT_MAX_MEMORY_MB,
        _resource_limits_from_env,
    )

    for var in ("AIENG_CAD_MAX_MEMORY_MB", "AIENG_CAD_MAX_CPU_SECONDS", "AIENG_CAD_MAX_FILE_MB"):
        monkeypatch.delenv(var, raising=False)

    limits = _resource_limits_from_env(timeout=60)
    assert limits.max_memory_mb == _CAD_DEFAULT_MAX_MEMORY_MB
    assert limits.max_file_mb == _CAD_DEFAULT_MAX_FILE_MB
    # CPU default sits above the wall-clock timeout so wall-clock fires first,
    # with the CPU cap as a hard backstop for pure-CPU spins.
    assert limits.max_cpu_seconds > 60


def test_resource_limits_from_env_overrides(monkeypatch) -> None:
    from app.cad_generation import _resource_limits_from_env

    monkeypatch.setenv("AIENG_CAD_MAX_MEMORY_MB", "1024")
    monkeypatch.setenv("AIENG_CAD_MAX_CPU_SECONDS", "5")
    monkeypatch.setenv("AIENG_CAD_MAX_FILE_MB", "64")
    limits = _resource_limits_from_env(timeout=60)
    assert limits.max_memory_mb == 1024
    assert limits.max_cpu_seconds == 5
    assert limits.max_file_mb == 64


def test_resource_limits_from_env_disable(monkeypatch) -> None:
    from app.cad_generation import _resource_limits_from_env

    # 0 / negative / garbage disables that particular cap (treated as unlimited).
    monkeypatch.setenv("AIENG_CAD_MAX_MEMORY_MB", "0")
    monkeypatch.setenv("AIENG_CAD_MAX_CPU_SECONDS", "-1")
    monkeypatch.setenv("AIENG_CAD_MAX_FILE_MB", "not-a-number")
    limits = _resource_limits_from_env(timeout=60)
    assert limits.max_memory_mb == 0
    assert limits.max_cpu_seconds == 0
    # Garbage falls back to the default rather than disabling.
    assert limits.max_file_mb > 0


def test_resource_limit_preamble_contains_setrlimit() -> None:
    from app.cad_generation import CadResourceLimits, _build_resource_limit_preamble

    preamble = _build_resource_limit_preamble(
        CadResourceLimits(max_memory_mb=2048, max_cpu_seconds=90, max_file_mb=256)
    )
    assert "RLIMIT_AS" in preamble
    assert "RLIMIT_CPU" in preamble
    assert "RLIMIT_FSIZE" in preamble
    # Must degrade gracefully where the resource module is unavailable (Windows).
    assert "import resource" in preamble
    # The configured caps are baked into the child preamble.
    assert "2048" in preamble and "90" in preamble and "256" in preamble


def test_resource_limit_preamble_injected_into_runner(monkeypatch) -> None:
    """The runner script handed to the subprocess must carry the rlimit preamble."""
    from app.cad_generation import _execute_build123d_code

    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        # cmd[1] is the runner script path written to the temp dir.
        captured["runner"] = Path(cmd[1]).read_text(encoding="utf-8")
        result_mock = MagicMock()
        result_mock.returncode = 0
        result_mock.stderr = ""
        Path(cmd[2]).write_bytes(b"ISO-10303-21;")
        Path(cmd[3]).write_text(json.dumps(_SAMPLE_TOPOLOGY), encoding="utf-8")
        Path(cmd[4]).write_bytes(b"solid result\nendsolid result")
        Path(cmd[5]).write_bytes(b"glTF")
        return result_mock

    monkeypatch.setenv("AIENG_CAD_MAX_MEMORY_MB", "777")
    with patch("app.cad_generation.subprocess.run", side_effect=_fake_run):
        _execute_build123d_code("result = Box(10, 10, 10)", timeout=60)

    runner = captured["runner"]
    assert "setrlimit" in runner
    assert "777" in runner  # configured memory cap baked into the child
    # Preamble must precede the build123d import so limits apply before heavy allocs.
    assert runner.index("setrlimit") < runner.index("import build123d")


def test_execute_build123d_code_timeout() -> None:
    """A subprocess timeout surfaces as a clean RuntimeError, not a raw TimeoutExpired."""
    from app.cad_generation import _execute_build123d_code

    def _raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 60))

    with patch("app.cad_generation.subprocess.run", side_effect=_raise_timeout):
        with pytest.raises(RuntimeError, match="timed out"):
            _execute_build123d_code("result = Box(10, 10, 10)", timeout=3)


@pytest.mark.skipif(
    importlib.util.find_spec("resource") is None,
    reason="POSIX resource limits unavailable (e.g. Windows); wall-clock timeout is the guard there",
)
def test_cpu_resource_limit_enforced(monkeypatch) -> None:
    """On POSIX, a CPU-time cap kills a pure-CPU runaway before the wall clock."""
    from app.cad_generation import _execute_build123d_code

    # Cap CPU at 2s; keep the wall-clock timeout high so we prove the *CPU* cap
    # (not the wall clock) terminates the runaway.
    monkeypatch.setenv("AIENG_CAD_MAX_CPU_SECONDS", "2")
    runaway = "x = 0\nwhile True:\n    x += 1\nresult = Box(1, 1, 1)"
    with pytest.raises(RuntimeError):
        _execute_build123d_code(runaway, timeout=120)


def test_execute_build123d_code_with_bd_warehouse_fastener() -> None:
    """#3: bd_warehouse standard-parts modules are pre-bound in the runner
    namespace, so agent code can produce spec-compliant ISO/DIN/ANSI parts
    (a real M6 socket-head cap screw) instead of approximating with primitives.
    Runs the real subprocess; skipped when build123d/bd_warehouse are absent."""
    pytest.importorskip("build123d")
    pytest.importorskip("bd_warehouse")
    from app.cad_generation import _execute_build123d_code, _topology_to_feature_graph

    code = (
        "screw = fastener.SocketHeadCapScrew('M6-1', length=12, simple=True)\n"
        "screw.label = 'mounting_bolt_M6'\n"
        "result = screw\n"
    )
    step_bytes, stl_bytes, _glb_bytes, topo, _mesh_meta = _execute_build123d_code(code)
    assert step_bytes[:13] == b"ISO-10303-21;"
    assert stl_bytes
    assert isinstance(topo, dict) and topo.get("entities")
    screw_body = next(
        entity for entity in topo["entities"]
        if entity.get("type") == "solid" and entity.get("name") == "mounting_bolt_M6"
    )
    assert screw_body["standard_part"] is True
    assert screw_body["source_library"] == "bd_warehouse"
    assert screw_body["source_module"] == "bd_warehouse.fastener"
    assert screw_body["source_class"] == "SocketHeadCapScrew"
    assert screw_body["canonical_type"] == "screw"
    assert screw_body["designation"] == "M6-1"

    fg = _topology_to_feature_graph(topo, source_code=code)
    standard = next(f for f in fg["features"] if f["type"] == "standard_part")
    assert standard["name"] == "mounting_bolt_M6"
    assert standard["source_library"] == "bd_warehouse"
    assert standard["canonical_type"] == "screw"


def test_execute_build123d_bd_warehouse_clearance_hole_bolt_pattern() -> None:
    """#35/#147: a bd_warehouse-driven 4x M6 ClearanceHole bolt pattern builds cleanly
    and the four screws carry standard_part ISO semantics and correct BOM count.

    Regression for the documented-but-broken `ClearanceHole(fastener="M6", ...)`
    pattern: ClearanceHole takes a Fastener OBJECT (not a size string) and a
    `fit` of Close/Normal/Loose. This is the corrected pattern now in SKILL.md."""
    pytest.importorskip("build123d")
    pytest.importorskip("bd_warehouse")
    from app.cad_generation import _execute_build123d_code, _topology_to_feature_graph

    positions = [(30, 20, 8), (-30, 20, 8), (30, -20, 8), (-30, -20, 8)]
    code = (
        "with BuildPart() as bp:\n"
        "    Box(80, 60, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "    screw = fastener.SocketHeadCapScrew(size='M6-1', length=16, simple=True)\n"
        f"    with Locations{tuple(positions)}:\n"
        "        fastener.ClearanceHole(fastener=screw, fit='Normal')\n"
        "    plate = bp.part\n"
        "plate.label = 'base_plate'\n"
        "screws = []\n"
        f"for pos in {positions}:\n"
        "    s = Pos(*pos) * fastener.SocketHeadCapScrew(size='M6-1', length=16, simple=True)\n"
        "    s.label = 'mounting_bolt_M6'\n"
        "    screws.append(s)\n"
        "result = Compound(children=[plate] + screws)\n"
    )
    step_bytes, stl_bytes, _glb_bytes, topo, _mesh_meta = _execute_build123d_code(code)
    # the build succeeds (this is the core #35 regression — no AttributeError)
    assert step_bytes[:13] == b"ISO-10303-21;"
    assert stl_bytes
    assert isinstance(topo, dict) and topo.get("entities")

    # four screws are recognised as standard_part features
    fg = _topology_to_feature_graph(topo, source_code=code)
    standards = [
        f for f in fg["features"]
        if f["type"] == "standard_part" and f.get("canonical_type") == "screw"
    ]
    assert len(standards) == 4, "bd_warehouse bolt pattern should yield four standard_part screws"
    for standard in standards:
        assert standard["source_library"] == "bd_warehouse"
        assert standard["designation"] == "M6-1"

    # BOM metadata reflects the count
    assert "metadata" in fg
    assert "standard_parts" in fg["metadata"]
    sp_summary = fg["metadata"]["standard_parts"]
    assert sp_summary["count"] == 4
    assert sp_summary["by_canonical_type"].get("screw") == 4


def test_execute_build123d_freeform_faces_get_rich_surface_metadata() -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import _execute_build123d_code

    code = """
body = lofted_stack([(0, 20, 10), (15, 26, 14), (30, 12, 8)], label="shell")
result = body
"""
    _step, _stl, _glb, topo, _mesh_meta = _execute_build123d_code(code, timeout=60)
    freeform_faces = [
        entity for entity in topo.get("entities", [])
        if entity.get("type") == "face" and entity.get("freeform") is True
    ]
    assert freeform_faces
    assert any(face.get("surface_type") in {"bspline", "bezier", "freeform"} for face in freeform_faces)
    assert any("uv_bounds" in face or "proxy_normal" in face for face in freeform_faces)


# ── thumbnail rendering ───────────────────────────────────────────────────────

def test_render_mesh_thumbnail_returns_png_base64() -> None:
    """A valid ASCII STL renders to a base64-encoded PNG (headless, no GL)."""
    import base64

    from app.cad_generation import render_mesh_thumbnail

    # Minimal single-triangle ASCII STL.
    stl = (
        b"solid t\n"
        b"facet normal 0 0 1\n outer loop\n"
        b"  vertex 0 0 0\n  vertex 10 0 0\n  vertex 0 10 0\n"
        b" endloop\nendfacet\n"
        b"endsolid t\n"
    )
    out = render_mesh_thumbnail(stl)
    assert out is not None
    raw = base64.b64decode(out)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_mesh_thumbnail_empty_bytes_returns_none() -> None:
    from app.cad_generation import render_mesh_thumbnail

    assert render_mesh_thumbnail(b"") is None


# ── named parts + append mode (real build123d) ────────────────────────────────

def test_execute_build123d_captures_part_labels(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "named")
    code = (
        "from build123d import *\n"
        "body = Box(40, 40, 10); body.label = 'fuselage'\n"
        "fl = Cylinder(3, 30); fl.label = 'motor_pod_FL'\n"
        "result = Compound(children=[body, fl])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    named = [f for f in out["feature_graph"]["features"] if f["type"] == "named_part"]
    assert sorted(f["name"] for f in named) == ["fuselage", "motor_pod_FL"]


def test_execute_build123d_preserves_labels_with_positional_compound_list(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "named-positional-compound")
    code = (
        "from build123d import *\n"
        "body = Box(40, 40, 10); body.label = 'base_plate'\n"
        "dome = Sphere(8); dome.label = 'dome_head'\n"
        "result = Compound([body, dome])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert out["named_parts"] == ["base_plate", "dome_head"]
    named = [f for f in out["feature_graph"]["features"] if f["type"] == "named_part"]
    assert sorted(f["name"] for f in named) == ["base_plate", "dome_head"]


def test_execute_build123d_preserves_parent_assembly_and_child_labels(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "named-parent-compound")
    code = (
        "from build123d import *\n"
        "body = Box(40, 30, 10); body.label = 'drone_body'\n"
        "arm_l = Box(50, 5, 5).moved(Location((-45, 0, 0))); arm_l.label = 'arm_L'\n"
        "arm_r = Box(50, 5, 5).moved(Location((45, 0, 0))); arm_r.label = 'arm_R'\n"
        "result = Compound(children=[body, arm_l, arm_r])\n"
        "result.label = 'drone_assembly'\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert out["named_parts"] == ["drone_assembly", "drone_body", "arm_L", "arm_R"]

    topo = _load_topology_map(settings, pid)
    solids = [e for e in topo["entities"] if e.get("type") == "solid"]
    assert [s.get("name") for s in solids] == ["drone_assembly", "drone_body", "arm_L", "arm_R"]
    assert solids[0].get("assembly") is True
    assert not any(e.get("type") == "face" and e.get("body_id") == solids[0]["id"] for e in topo["entities"])
    named = [f for f in out["feature_graph"]["features"] if f["type"] == "named_part"]
    assert [f["name"] for f in named] == ["drone_assembly", "drone_body", "arm_L", "arm_R"]


def test_execute_build123d_single_labeled_body_regression(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "single-label")
    code = (
        "from build123d import *\n"
        "result = Box(10, 10, 10)\n"
        "result.label = 'single_body'\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert out["named_parts"] == ["single_body"]
    topo = _load_topology_map(settings, pid)
    solids = [e for e in topo["entities"] if e.get("type") == "solid"]
    assert [s.get("name") for s in solids] == ["single_body"]
    assert solids[0].get("assembly") is not True


def test_execute_build123d_append_preserves_prior_parts(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "append")
    base = (
        "from build123d import *\n"
        "body = Box(40, 40, 10); body.label = 'fuselage'\n"
        "result = Compound(children=[body])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": base, "thumbnail": False})["status"] == "ok"

    add = (
        "from build123d import *\n"
        "arm = Cylinder(3, 30); arm.label = 'motor_pod_FL'\n"
        "result = Compound(children=[previous_result, arm])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": add, "mode": "append", "thumbnail": False})
    assert out["status"] == "ok"
    named = {f["name"] for f in out["feature_graph"]["features"] if f["type"] == "named_part"}
    assert named == {"fuselage", "motor_pod_FL"}
    # response summary fields: append consumed the base and added only the new part
    assert out["mode"] == "append"
    assert out["used_base"] is True
    assert out["named_parts"] == ["fuselage", "motor_pod_FL"]
    assert out["parts_added"] == ["motor_pod_FL"]
    # #216 follow-up: an append carries a manufacturability critique_diff vs the
    # prior model (the two parts are close, so no new violation is introduced).
    assert "critique_diff" in out
    assert out["critique_diff"]["verdict"] in ("clean", "improved", "warn", "fail", "skipped")
    assert out["critique_diff"]["delta"]["high"] <= 0


def test_execute_build123d_boolean_preserves_part_labels_and_colors(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "boolean-labels")
    code = (
        "from build123d import *\n"
        "a = Box(10, 10, 10); a.label = 'part_a'; a.color = Color(1, 0, 0)\n"
        "b = Box(10, 10, 10).moved(Location((20, 0, 0))); b.label = 'part_b'; b.color = Color(0, 1, 0)\n"
        "result = a + b\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert sorted(out["named_parts"]) == ["part_a", "part_b"]
    assert out["mesh_meta"] is not None
    body_colors = {b["name"]: b["color"] for b in out["mesh_meta"]["bodies"] if b["name"]}
    assert body_colors.get("part_a") == [1.0, 0.0, 0.0]
    assert body_colors.get("part_b") == [0.0, 1.0, 0.0]


def test_execute_build123d_subtraction_preserves_part_label_and_color(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "subtraction-labels")
    code = (
        "from build123d import *\n"
        "block = Box(20, 20, 20); block.label = 'block'; block.color = Color(1, 0, 0)\n"
        "cutter = Box(5, 5, 30); cutter.label = 'cutter'; cutter.color = Color(0, 1, 0)\n"
        "result = block - cutter\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert out["named_parts"] == ["block"]
    body = next((b for b in out["mesh_meta"]["bodies"] if b["name"] == "block"), None)
    assert body is not None
    assert body["color"] == [1.0, 0.0, 0.0]


def test_execute_build123d_append_three_times_preserves_parts(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "append-three")
    base = (
        "from build123d import *\n"
        "body = Box(40, 40, 10); body.label = 'fuselage'; body.color = Color(1, 0, 0)\n"
        "result = Compound(children=[body])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": base, "thumbnail": False})["status"] == "ok"

    additions = [
        ("motor_pod_FL", "Color(0, 1, 0)", "(-25, 0, 0)"),
        ("motor_pod_FR", "Color(0, 0, 1)", "(25, 0, 0)"),
    ]
    for label, color, pos in additions:
        add = (
            f"from build123d import *\n"
            f"arm = Cylinder(3, 30).moved(Location({pos})); arm.label = '{label}'; arm.color = {color}\n"
            f"result = Compound(children=[previous_result, arm])\n"
        )
        out = execute_build123d_code(settings, pid, {"code": add, "mode": "append", "thumbnail": False})
        assert out["status"] == "ok"

    assert sorted(out["named_parts"]) == ["fuselage", "motor_pod_FL", "motor_pod_FR"]
    assert out["mesh_meta"] is not None
    body_colors = {b["name"]: b["color"] for b in out["mesh_meta"]["bodies"] if b.get("name")}
    assert body_colors.get("fuselage") == [1.0, 0.0, 0.0]
    assert body_colors.get("motor_pod_FL") == [0.0, 1.0, 0.0]
    assert body_colors.get("motor_pod_FR") == [0.0, 0.0, 1.0]


def test_execute_build123d_replace_summary_fields(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "replace-summary")
    code = (
        "from build123d import *\n"
        "b = Box(40, 40, 10); b.label = 'fuselage'\n"
        "result = Compound(children=[b])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["mode"] == "replace"
    assert out["used_base"] is False
    # in a fresh replace, everything is newly added
    assert out["named_parts"] == ["fuselage"]
    assert out["parts_added"] == ["fuselage"]
    # replace has no "before" model to diff against → no critique_diff
    assert "critique_diff" not in out


def test_execute_build123d_append_without_base_errors(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "no-base")
    out = execute_build123d_code(
        settings, pid,
        {"code": "from build123d import *\nresult = Box(1, 1, 1)", "mode": "append", "thumbnail": False},
    )
    assert out["status"] == "error"
    assert out["code"] == "append_without_base"


def test_read_cad_source_no_geometry_yet(tmp_path: Path) -> None:
    from app.cad_generation import read_cad_source

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "empty")
    out = read_cad_source(settings, pid)
    assert out["status"] == "ok"
    assert out["has_base"] is False
    assert out["source"] is None
    assert out["named_parts"] == []
    assert out["mode"] == "build123d"


def test_read_cad_source_after_build(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, read_cad_source

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "with-geo")
    code = (
        "from build123d import *\n"
        "b = Box(40, 40, 10); b.label = 'fuselage'\n"
        "result = Compound(children=[b])\n"
    )
    execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    out = read_cad_source(settings, pid)
    assert out["status"] == "ok"
    assert out["has_base"] is True
    assert "Box(40, 40, 10)" in out["source"]
    assert out["named_parts"] == ["fuselage"]


# ── endpoint integration tests ────────────────────────────────────────────────

def test_generate_cad_endpoint_missing_description(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    project_id = _make_project(settings, "test-proj")

    resp = client.post(f"/api/projects/{project_id}/generate-cad", json={})
    assert resp.status_code == 400
    assert "description" in resp.text


def test_generate_cad_endpoint_no_build123d(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    project_id = _make_project(settings, "no-b3d")

    with patch("app.cad_generation.Build123dBackend.can_generate", return_value=False):
        resp = client.post(
            f"/api/projects/{project_id}/generate-cad",
            json={"description": "simple bracket"},
        )
    assert resp.status_code == 503
    assert "build123d" in resp.text


_FAKE_STEP = b"ISO-10303-21;"
_FAKE_STL = b"solid result\nendsolid result"
_FAKE_GLB = b"glTF\x02\x00\x00\x00"  # minimal GLB magic


def _fake_execute_ok(code, timeout=60):
    return _FAKE_STEP, _FAKE_STL, _FAKE_GLB, _SAMPLE_TOPOLOGY, None


def test_generate_cad_endpoint_dry_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)
    project_id = _make_project(settings, "dry-run")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_code", return_value=_SAMPLE_CODE),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok),
    ):
        resp = client.post(
            f"/api/projects/{project_id}/generate-cad",
            json={
                "description": "100x50x10mm plate with 4 corner bolt holes r=4mm",
                "write_files": False,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "build123d"
    assert "result = bp.part" in data["generated_code"]
    assert data["topology_summary"]["face_count"] == 6
    assert data["written_artifacts"] == []
    assert data["preview_format"] == "glb"


def test_generate_cad_endpoint_writes_artifacts(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)
    project_id = _make_project(settings, "write-test")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_code", return_value=_SAMPLE_CODE),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok),
    ):
        resp = client.post(
            f"/api/projects/{project_id}/generate-cad",
            json={
                "description": "100x50x10mm plate with 4 corner bolt holes r=4mm",
                "write_files": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "geometry/generated.step" in data["written_artifacts"]
    assert "geometry/preview.stl" in data["written_artifacts"]
    assert "geometry/preview.glb" in data["written_artifacts"]
    assert "geometry/topology_map.json" in data["written_artifacts"]
    assert "graph/feature_graph.json" in data["written_artifacts"]
    assert "geometry/source.py" in data["written_artifacts"]

    from app.main import project_dir
    from app.project_io import get_project
    project = get_project(settings, project_id)
    pkg_path = project_dir(settings, project_id) / project["aieng_file"]
    assert pkg_path.exists()

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "geometry/generated.step" in names
        assert "geometry/preview.stl" in names
        assert "geometry/preview.glb" in names
        assert "geometry/topology_map.json" in names
        assert "geometry/source.py" in names

        topo = json.loads(zf.read("geometry/topology_map.json"))
        assert topo["format_version"] == "0.1"

        fg = json.loads(zf.read("graph/feature_graph.json"))
        assert len(fg["features"]) >= 1


# ── cad-preview endpoint ──────────────────────────────────────────────────────

def test_cad_preview_endpoint_returns_glb(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)
    project_id = _make_project(settings, "preview-test")

    # First generate to create the package with both preview.stl and preview.glb
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_code", return_value=_SAMPLE_CODE),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok),
    ):
        client.post(
            f"/api/projects/{project_id}/generate-cad",
            json={"description": "simple plate", "write_files": True},
        )

    resp = client.get(f"/api/projects/{project_id}/cad-preview")
    assert resp.status_code == 200
    # GLB is preferred over STL when available
    assert resp.content == _FAKE_GLB
    assert resp.headers["content-type"] == "model/gltf-binary"


def test_cad_preview_endpoint_404_without_package(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    project_id = _make_project(settings, "no-pkg-preview")

    resp = client.get(f"/api/projects/{project_id}/cad-preview")
    assert resp.status_code == 404


# ── refine-cad endpoint ───────────────────────────────────────────────────────

def test_refine_cad_endpoint_missing_feedback(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    project_id = _make_project(settings, "refine-no-feedback")

    resp = client.post(f"/api/projects/{project_id}/refine-cad", json={})
    assert resp.status_code == 400
    assert "feedback" in resp.text


def test_refine_cad_endpoint_no_source(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    project_id = _make_project(settings, "refine-no-source")

    # Create empty package (no source.py)
    from app.main import project_dir, save_project
    from app.project_io import get_project
    project = get_project(settings, project_id)
    pkg_path = project_dir(settings, project_id) / "test.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", "{}")
    project["aieng_file"] = "test.aieng"
    save_project(settings, project)

    with patch("app.cad_generation.Build123dBackend.can_generate", return_value=True):
        resp = client.post(
            f"/api/projects/{project_id}/refine-cad",
            json={"feedback": "make it taller"},
        )
    assert resp.status_code == 404


def test_refine_cad_endpoint_success(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app, raise_server_exceptions=True)
    project_id = _make_project(settings, "refine-ok")

    _REFINED_CODE = "with BuildPart() as bp:\n    Box(100, 50, 15)\nresult = bp.part\n"

    # First generate
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_code", return_value=_SAMPLE_CODE),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok),
    ):
        client.post(
            f"/api/projects/{project_id}/generate-cad",
            json={"description": "plate", "write_files": True},
        )

    # Then refine
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_refinement", return_value=_REFINED_CODE),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok),
    ):
        resp = client.post(
            f"/api/projects/{project_id}/refine-cad",
            json={"feedback": "make thickness 15mm", "write_files": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mode"] == "refine"
    assert data["refined_code"] == _REFINED_CODE
    assert data["preview_format"] == "glb"
    assert "geometry/preview.stl" in data["written_artifacts"]
    assert "geometry/preview.glb" in data["written_artifacts"]


def test_get_named_part_bbox_found_and_not_found(tmp_path: Path) -> None:
    from app import runtime as _rt
    from app.main import default_project, project_dir, save_project

    settings = _make_settings(tmp_path)
    create_app(settings)
    project = save_project(settings, default_project("bbox"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "bbox.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr(
            "geometry/topology_map.json",
            json.dumps(
                {
                    "entities": [
                        {
                            "id": "body_001",
                            "type": "solid",
                            "name": "thigh_L",
                            "bounding_box": [-50, -18, -130, -10, 18, -40],
                        },
                        {
                            "id": "body_002",
                            "type": "solid",
                            "name": "torso",
                            "bounding_box": [-30, -20, -40, 30, 20, 40],
                        },
                    ]
                }
            ),
        )
    project["aieng_file"] = "bbox.aieng"
    save_project(settings, project)

    found = _rt.invoke_tool("cad.get_named_part_bbox", {"project_id": project_id, "part_name": "thigh_L"})
    assert found["status"] == "ok"
    assert found["bounding_box"] == [-50, -18, -130, -10, 18, -40]
    assert found["center"] == [-30.0, 0.0, -85.0]
    assert found["available_parts"] == ["thigh_L", "torso"]

    missing = _rt.invoke_tool("cad.get_named_part_bbox", {"project_id": project_id, "part_name": "thigh_R"})
    assert missing["status"] == "error"
    assert missing["message"] == "part 'thigh_R' not found"
    assert missing["available_parts"] == ["thigh_L", "torso"]


def test_cad_refine_tool_uses_server_env_key_and_mocked_refinement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import runtime as _rt
    from app.main import default_project, project_dir, save_project
    from app.project_io import get_project, resolve_project_path

    settings = _make_settings(tmp_path)
    create_app(settings)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-from-env")

    project = save_project(settings, default_project("refine-tool"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "tool_refine.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("geometry/source.py", _SAMPLE_CODE)
        zf.writestr(
            "graph/feature_graph.json",
            json.dumps({"features": [{"type": "named_part", "name": "torso"}]}),
        )
    project["aieng_file"] = "tool_refine.aieng"
    save_project(settings, project)

    refined_code = (
        "from build123d import *\n"
        "torso = Box(10, 10, 10); torso.label = 'torso'\n"
        "leg = Box(10, 10, 20); leg.label = 'thigh_L'\n"
        "result = Compound(children=[torso, leg])\n"
    )
    topo = {
        "entities": [
            {"id": "body_001", "type": "solid", "name": "torso", "bounding_box": [0, 0, 0, 10, 10, 10]},
            {"id": "body_002", "type": "solid", "name": "thigh_L", "bounding_box": [0, 0, -20, 10, 10, 0]},
            {"id": "face_001", "type": "face", "body_id": "body_001", "surface_type": "plane", "bounding_box": [0, 0, 0, 10, 10, 0]},
        ]
    }

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation.call_claude_for_build123d_refinement", return_value=refined_code) as refine_mock,
        patch("app.cad_generation._execute_build123d_code", return_value=(_FAKE_STEP, _FAKE_STL, _FAKE_GLB, topo, None)),
    ):
        out = _rt.invoke_tool(
            "cad.refine",
            {"project_id": project_id, "feedback": "move thigh_L down by 20mm", "write_files": True, "timeout": 60},
        )

    assert out["status"] == "ok"
    assert out["mode"] == "refine"
    assert out["named_parts"] == ["torso", "thigh_L"]
    assert out["parts_added"] == ["thigh_L"]
    # #285: topology_summary.bounding_box is the union over ALL solids (whole model),
    # not just the first — here torso (z 0..10) ∪ thigh_L (z -20..0) → z -20..10.
    assert out["topology_summary"]["bounding_box"] == [0, 0, -20, 10, 10, 10]
    refine_mock.assert_called_once()
    assert refine_mock.call_args.kwargs["api_key"] is None

    project = get_project(settings, project_id)
    resolved = resolve_project_path(settings, project_id, project.get("aieng_file"))
    assert resolved is not None and resolved.exists()


def test_cad_refine_tool_requires_server_env_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import runtime as _rt

    settings = _make_settings(tmp_path)
    create_app(settings)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    out = _rt.invoke_tool("cad.refine", {"project_id": "proj_123", "feedback": "make the torso narrower"})
    assert out["status"] == "error"
    assert out["message"] == "ANTHROPIC_API_KEY not configured; cad.refine requires LLM access"


# ── execute_build123d_code (agent-supplied code, no LLM) ──────────────────────

def _fake_stream_ok(code, timeout=60):
    """Mimic _execute_build123d_code_streaming: a heartbeat then a result."""
    yield {"kind": "heartbeat", "elapsed_s": 0}
    yield {
        "kind": "result",
        "step_bytes": _FAKE_STEP,
        "stl_bytes": _FAKE_STL,
        "glb_bytes": _FAKE_GLB,
        "topo": _SAMPLE_TOPOLOGY,
        "mesh_meta": None,
    }


def _fake_stream_error(code, timeout=60):
    yield {"kind": "heartbeat", "elapsed_s": 0}
    yield {"kind": "error", "error": "NameError: name 'Bx' is not defined"}


def test_execute_build123d_code_writes_artifacts(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_ok),
    ):
        result = cad_generation.execute_build123d_code(
            settings,
            project_id,
            {"code": "from build123d import *\nresult = Box(100, 50, 10)"},
        )

    assert result["status"] == "ok"
    assert result["backend"] == "build123d"
    assert result["topology_summary"]["face_count"] == 6
    assert "geometry/source.py" in result["written_artifacts"]
    assert "geometry/preview.glb" in result["written_artifacts"]
    assert result["preview_format"] == "glb"


def test_execute_build123d_code_persists_source(tmp_path: Path) -> None:
    """The exact agent-supplied code must land in geometry/source.py."""
    from app import cad_generation
    from app.main import get_project
    from app.project_io import resolve_project_path

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-src")
    code = "from build123d import *\nresult = Box(120, 60, 8)"

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_ok),
    ):
        cad_generation.execute_build123d_code(settings, project_id, {"code": code})

    project = get_project(settings, project_id)
    pkg_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    with zipfile.ZipFile(pkg_path, "r") as zf:
        source = zf.read("geometry/source.py").decode("utf-8")
    assert "Box(120, 60, 8)" in source


def test_execute_build123d_code_dry_run_no_write(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-dry")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_ok),
    ):
        result = cad_generation.execute_build123d_code(
            settings, project_id, {"code": "from build123d import *\nresult = Box(1,1,1)", "write_files": False},
        )

    assert result["status"] == "ok"
    assert result["written_artifacts"] == []


def test_execute_build123d_code_compact_response_omits_thumbnail(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-compact")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_ok),
        patch("app.cad_generation.render_mesh_thumbnail") as render_thumb,
    ):
        result = cad_generation.execute_build123d_code(
            settings,
            project_id,
            {
                "code": "from build123d import *\nresult = Box(10, 10, 10)",
                "response_detail": "compact",
            },
        )

    assert result["status"] == "ok"
    assert result["response_detail"] == "compact"
    assert result["cache_hit"] is False
    assert isinstance(result["geometry_report"], str)
    assert result["geometry_report"].startswith("geometry:")
    assert "thumbnail_png_base64" not in result
    render_thumb.assert_not_called()


def test_execute_build123d_code_exact_cache_hit_skips_executor(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-cache")
    calls = 0

    def _fake_stream_counted(code: str, timeout: int = 60):
        nonlocal calls
        calls += 1
        yield from _fake_stream_ok(code, timeout=timeout)

    payload = {
        "code": "from build123d import *\nresult = Box(20, 10, 5)",
        "thumbnail": False,
    }
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_counted),
    ):
        first = cad_generation.execute_build123d_code(settings, project_id, payload)
        second = cad_generation.execute_build123d_code(settings, project_id, payload)

    assert first["status"] == "ok"
    assert first["cache_hit"] is False
    assert second["status"] == "ok"
    assert second["cache_hit"] is True
    assert calls == 1
    assert second["topology_summary"] == first["topology_summary"]
    assert second["written_artifacts"] == first["written_artifacts"]


def test_recompile_shape_ir_cache_hit_restores_execution_manifest(tmp_path: Path) -> None:
    from aieng.cache.geometry_cache import CachedGeometry
    from app import cad_generation

    package = tmp_path / "cached-recompile.aieng"
    shape_ir = {
        "representation": "brep_build123d",
        "parts": [{"id": "block", "type": "box", "parameters": {"length": 10}}],
    }
    cached = CachedGeometry(
        shape_ir_hash="cache-key",
        topology_map={"entities": []},
        feature_graph={"features": []},
        metadata={
            "representation": "brep_build123d",
            "runtime": "build123d",
            "geometry_kind": "brep",
            "source": "from build123d import *\nresult = Box(10, 10, 10)",
            "source_path": "geometry/source.py",
            "step_bytes": b"cached-step",
            "stl_bytes": b"cached-stl",
        },
    )
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("geometry/shape_ir.json", json.dumps(shape_ir))

    with patch("aieng.cache.geometry_cache.GeometryCache.get", return_value=cached):
        result = cad_generation.recompile_shape_ir_package(package)

    assert result["cache_hit"] is True
    with zipfile.ZipFile(package) as zf:
        manifest = json.loads(zf.read("provenance/conversion_manifest.json"))
    execution = manifest["geometry_execution"]
    assert execution["executed"] is True
    assert execution["geometry_kind"] == "brep"
    assert "geometry/generated.step" in execution["artifacts"]


def test_recompile_shape_ir_does_not_treat_source_only_cache_as_geometry(
    tmp_path: Path,
) -> None:
    from aieng.cache.geometry_cache import CachedGeometry
    from app import cad_generation

    package = tmp_path / "source-only-cache.aieng"
    shape_ir = {
        "representation": "brep_build123d",
        "parts": [{"id": "block", "type": "box", "parameters": {"length": 10}}],
    }
    source_only = CachedGeometry(
        shape_ir_hash="cache-key",
        metadata={
            "representation": "brep_build123d",
            "runtime": "build123d",
            "source": "from build123d import *\nresult = Box(10, 10, 10)",
            "source_path": "geometry/source.py",
        },
    )
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("geometry/shape_ir.json", json.dumps(shape_ir))

    with (
        patch("aieng.cache.geometry_cache.GeometryCache.get", return_value=source_only),
        patch("aieng.cache.geometry_cache.GeometryCache.set"),
        patch("app.cad_generation._execute_build123d_code", side_effect=_fake_execute_ok) as execute,
    ):
        result = cad_generation.recompile_shape_ir_package(package)

    assert result["executed"] is True
    execute.assert_called_once()
    with zipfile.ZipFile(package) as zf:
        manifest = json.loads(zf.read("provenance/conversion_manifest.json"))
    assert manifest["geometry_execution"]["executed"] is True


def test_execute_build123d_code_missing_code(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-nocode")

    result = cad_generation.execute_build123d_code(settings, project_id, {})
    assert result["status"] == "error"
    assert result["code"] == "missing_code"


def test_execute_build123d_code_execution_error(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-err")

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_error),
    ):
        result = cad_generation.execute_build123d_code(
            settings, project_id, {"code": "result = Bx(1,1,1)"},
        )

    assert result["status"] == "error"
    assert result["code"] == "execution_failed"
    assert "NameError" in result["message"]


def test_execute_build123d_code_build123d_unavailable(tmp_path: Path) -> None:
    from app import cad_generation

    settings = _make_settings(tmp_path)
    create_app(settings)
    project_id = _make_project(settings, "agent-cad-unavail")

    with patch("app.cad_generation.Build123dBackend.can_generate", return_value=False):
        result = cad_generation.execute_build123d_code(
            settings, project_id, {"code": "from build123d import *\nresult = Box(1,1,1)"},
        )

    assert result["status"] == "error"
    assert result["code"] == "build123d_unavailable"


def test_cad_execute_build123d_tool_registered_for_plan_approved_execution() -> None:
    from app import runtime
    from app.app_factory import create_app as _ca

    _ca()
    tools = {t["name"]: t for t in runtime.list_tools_for_mcp()}
    assert "cad.plan_build123d_skill" in tools
    assert tools["cad.plan_build123d_skill"]["requires_approval"] is False
    skill_schema = tools["cad.plan_build123d_skill"]["input_schema"]
    assert set(skill_schema["required"]) == {"project_id", "message"}
    assert tools["cad.confirm_modeling_plan"]["requires_approval"] is True
    confirm_schema = tools["cad.confirm_modeling_plan"]["input_schema"]
    assert set(confirm_schema["required"]) == {"project_id", "summary", "steps"}
    assert "cad.execute_build123d" in tools
    assert tools["cad.execute_build123d"]["requires_approval"] is False
    assert tools["cad.execute_build123d"]["read_only"] is False
    assert tools["cad.execute_build123d"]["destructive"] is False
    schema = tools["cad.execute_build123d"]["input_schema"]
    assert "code" in schema["properties"]
    assert schema["properties"]["response_detail"]["enum"] == ["full", "compact"]
    assert set(schema["required"]) == {"project_id", "code"}
    for tool_name in ("cad.edit_parameter", "cad.remove_part", "cad.replace_part"):
        assert tools[tool_name]["input_schema"]["properties"]["response_detail"]["enum"] == ["full", "compact"]


# ── geometry report ───────────────────────────────────────────────────────────

def test_geometry_report_flags_asymmetry_and_floating() -> None:
    from app.cad_generation import _compute_geometry_report

    topo = {"entities": [
        {"type": "solid", "id": "b1", "name": "torso", "bounding_box": [-30, -15, 100, 30, 15, 300]},
        {"type": "solid", "id": "b2", "name": "arm_L", "bounding_box": [-50, -10, 150, -30, 10, 290]},
        {"type": "solid", "id": "b3", "name": "arm_R", "bounding_box": [30, -10, 150, 50, 10, 250]},
        {"type": "solid", "id": "b4", "name": "foot_FL", "bounding_box": [-200, -10, -20, -180, 10, 0]},
    ]}
    report = _compute_geometry_report(topo)
    assert report["available"] is True
    assert report["part_count"] == 4
    # asymmetric arm pair (different Z extents) flagged
    arm = next(s for s in report["symmetry"] if s.get("pair") == ["arm_L", "arm_R"])
    assert arm["ok"] is False
    assert arm["mirror_axis"] == "x"
    # foot_FL has no partner and is far from everything
    assert "foot_FL" in report.get("floating_parts", [])
    # always-present contact summary spans every part and the buckets add up
    gs = report["gaps_summary"]
    assert gs["total"] == 4
    assert gs["floating"] >= 1
    assert gs["touching"] + gs["near"] + gs["floating"] == gs["total"]


# ── geometry regression diff ──────────────────────────────────────────────────

def test_geometry_report_flags_deep_overlap_and_containment() -> None:
    from app.cad_generation import _compute_geometry_report, _geometry_report_summary

    topo = {"entities": [
        {"type": "solid", "id": "b1", "name": "base", "bounding_box": [0, 0, 0, 100, 80, 20]},
        {"type": "solid", "id": "b2", "name": "motor", "bounding_box": [40, 30, 5, 70, 60, 25]},
        {"type": "solid", "id": "b3", "name": "buried_insert", "bounding_box": [10, 10, 5, 20, 20, 15]},
    ]}

    report = _compute_geometry_report(topo)

    spatial = report["spatial_summary"]
    assert spatial["deep_overlaps"] >= 1
    assert spatial["containments"] >= 1
    summary = _geometry_report_summary(report)
    assert "spatial_issues=" in summary
    assert "spatial_issues=2" in summary


def test_geometry_report_marks_hollow_containment_as_review_not_failure() -> None:
    from app.cad_generation import _compute_geometry_report

    topo = {"entities": [
        {
            "type": "solid",
            "id": "b1",
            "name": "housing",
            "bounding_box": [0, 0, 0, 100, 80, 60],
            "volume": 40000.0,
        },
        {
            "type": "solid",
            "id": "b2",
            "name": "bearing",
            "bounding_box": [40, 30, 20, 60, 50, 40],
            "volume": 6000.0,
        },
    ]}

    report = _compute_geometry_report(topo)

    assert report["spatial_summary"]["contained_in_hollow"] == 1
    assert report["spatial_summary"]["containments"] == 0
    assert report["spatial_relationships"][0]["status"] == "contained_in_hollow"


def _topo_from(parts: list[tuple[str, list[float]]]) -> dict:
    return {"entities": [
        {"type": "solid", "id": f"b{i}", "name": n, "bounding_box": bb}
        for i, (n, bb) in enumerate(parts)
    ]}


def test_diff_topology_clean_edit() -> None:
    from app.cad_generation import _diff_topology

    before = _topo_from([("torso", [-30, -15, 100, 30, 15, 300]), ("arm_L", [-50, -10, 150, -30, 10, 290])])
    after = _topo_from([("torso", [-30, -15, 100, 30, 15, 300]), ("arm_L", [-50, -10, 150, -30, 10, 330])])
    diff = _diff_topology(before, after, expected_parts={"arm_L"})
    assert diff["verdict"] == "clean"
    assert diff["collateral_parts"] == []
    assert diff["changed"][0]["part"] == "arm_L"
    assert diff["changed"][0]["expected"] is True


def test_diff_topology_flags_collateral_change() -> None:
    from app.cad_generation import _diff_topology

    before = _topo_from([("torso", [-30, -15, 100, 30, 15, 300]), ("arm_L", [-50, -10, 150, -30, 10, 290])])
    # both torso AND arm_L changed, but only arm_L was the target
    after = _topo_from([("torso", [-30, -15, 100, 30, 15, 360]), ("arm_L", [-50, -10, 150, -30, 10, 330])])
    diff = _diff_topology(before, after, expected_parts={"arm_L"})
    assert diff["verdict"] == "collateral_change"
    assert diff["collateral_parts"] == ["torso"]


def test_diff_topology_identical_is_noop() -> None:
    from app.cad_generation import _diff_topology

    before = _topo_from([("torso", [-30, -15, 100, 30, 15, 300])])
    diff = _diff_topology(before, before, expected_parts={"torso"})
    assert diff["verdict"] == "identical"


def test_diff_topology_global_param_no_collateral_judgment() -> None:
    from app.cad_generation import _diff_topology

    before = _topo_from([("torso", [-30, -15, 100, 30, 15, 300]), ("arm_L", [-50, -10, 150, -30, 10, 290])])
    after = _topo_from([("torso", [-32, -17, 100, 32, 17, 300]), ("arm_L", [-52, -12, 150, -28, 12, 290])])
    diff = _diff_topology(before, after, expected_parts=None)
    assert diff["verdict"] == "clean"
    assert diff["collateral_parts"] == []
    assert len(diff["changed"]) == 2


def _solid(name: str, bbox: list[float], volume: float, area: float) -> dict:
    return {"type": "solid", "id": name, "name": name,
            "bounding_box": bbox, "volume": volume, "area": area}


def test_diff_topology_detects_internal_feature_change() -> None:
    """An edit that changes volume/area but NOT the bounding box (e.g. widening
    a bore) must be reported as a change, not 'identical'."""
    from app.cad_generation import _diff_topology

    box = [0, 0, 0, 70, 6, 70]  # identical bbox before/after
    before = {"entities": [_solid("motor_face", box, 28000.0, 12000.0)]}
    after = {"entities": [_solid("motor_face", box, 26500.0, 12600.0)]}  # bore widened
    diff = _diff_topology(before, after, expected_parts={"motor_face"})
    assert diff["verdict"] == "clean"
    assert diff["changed"][0]["part"] == "motor_face"
    assert diff["changed"][0]["internal_feature_change"] is True
    assert diff["changed"][0]["volume_delta_pct"] > 0.5
    assert diff["internal_feature_parts"] == ["motor_face"]


def test_diff_topology_volume_jitter_below_threshold_is_identical() -> None:
    """Sub-threshold volume/area jitter with an unchanged bbox stays 'identical'
    so floating-point noise doesn't read as a real edit."""
    from app.cad_generation import _diff_topology

    box = [0, 0, 0, 10, 10, 10]
    before = {"entities": [_solid("p", box, 1000.0, 600.0)]}
    after = {"entities": [_solid("p", box, 1001.0, 600.2)]}  # ~0.1% < 0.5% eps
    diff = _diff_topology(before, after, expected_parts={"p"})
    assert diff["verdict"] == "identical"
    assert diff["internal_feature_parts"] == []


def test_union_solid_bbox_spans_all_solids() -> None:
    from app.cad_generation import _union_solid_bbox

    topo = {"entities": [
        {"type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]},
        {"type": "solid", "bounding_box": [0, 0, -20, 10, 10, 0]},
        {"type": "face", "bounding_box": [0, 0, 0, 5, 5, 5]},  # faces ignored
    ]}
    assert _union_solid_bbox(topo) == [0, 0, -20, 10, 10, 10]
    assert _union_solid_bbox({"entities": []}) is None


def test_param_binding_uses_source_usage_not_name_token() -> None:
    # #288: both gearbox gears used to carry BOTH gear-diameter constants because
    # "gear" name-tokens matched both. Source-usage binding fixes that.
    from app.cad_generation import _enrich_feature_graph_with_source_params

    source = (
        "GEAR_IN_PITCH_DIA = 30.0\n"
        "GEAR_OUT_PITCH_DIA = 50.0\n"
        "GEAR_WIDTH = 8.0\n"
        "gi = Cylinder(GEAR_IN_PITCH_DIA / 2, GEAR_WIDTH)\n"
        "gi.label = 'gear_input'\n"
        "go = Cylinder(GEAR_OUT_PITCH_DIA / 2, GEAR_WIDTH)\n"
        "go.label = 'gear_output'\n"
    )
    fg = {"features": [
        {"id": "f1", "type": "named_part", "name": "gear_input", "geometry_refs": {"body": "b1"}},
        {"id": "f2", "type": "named_part", "name": "gear_output", "geometry_refs": {"body": "b2"}},
    ]}
    out = _enrich_feature_graph_with_source_params(source, fg)
    feats = {f["name"]: f for f in out["features"]}

    def _consts(feat):
        return {p["cad_parameter_name"] for p in (feat.get("parameters") or {}).values()}

    gi, go = _consts(feats["gear_input"]), _consts(feats["gear_output"])
    assert "GEAR_IN_PITCH_DIA" in gi and "GEAR_OUT_PITCH_DIA" not in gi   # no cross-pollution
    assert "GEAR_OUT_PITCH_DIA" in go and "GEAR_IN_PITCH_DIA" not in go
    assert "GEAR_WIDTH" not in gi and "GEAR_WIDTH" not in go              # shared → not per-gear
    gp = next((f for f in out["features"] if f.get("type") == "global_params"), None)
    assert gp is not None and any(p["cad_parameter_name"] == "GEAR_WIDTH" for p in gp["parameters"].values())


# ── cad.validate_subpart (read-only isolated fragment validation) ─────────────

def test_validate_subpart_accepts_valid_solid() -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import validate_subpart

    out = validate_subpart(None, {"code": "from build123d import *\nresult = Box(10, 10, 10)\n"})
    assert out["status"] == "ok"
    assert out["valid"] is True
    assert out["executed"] is True
    assert out["solid_count"] == 1
    assert out["total_volume_mm3"] == pytest.approx(1000.0, rel=0.02)
    assert out["bounding_box"] is not None
    # no project artifact is implied by a read-only validation
    assert any(c["name"] == "non_empty_solid" and c["status"] == "pass" for c in out["checks"])


def test_validate_subpart_reports_build_error_without_raising() -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import validate_subpart

    # references an undefined name -> the subprocess raises -> reported, not thrown
    out = validate_subpart(None, {"code": "from build123d import *\nresult = totally_undefined_symbol\n"})
    assert out["status"] == "invalid"
    assert out["valid"] is False
    assert out["executed"] is False
    assert any(c["name"] == "builds" and c["status"] == "fail" for c in out["checks"])


def test_validate_subpart_surfaces_design_rule_violation() -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import validate_subpart

    code = "from build123d import *\nrequire(False, 'wall too thin')\nresult = Box(1, 1, 1)\n"
    out = validate_subpart(None, {"code": code})
    assert out["status"] == "invalid"
    assert out["code"] == "design_rule_violation"
    assert out["valid"] is False


def test_validate_subpart_requires_code() -> None:
    from app.cad_generation import validate_subpart

    out = validate_subpart(None, {})
    assert out["status"] == "error"
    assert out["code"] == "missing_code"


# ── assembly authoring (cad.define_part / cad.define_mate) end-to-end ─────────

def test_assembly_authoring_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import (
        define_assembly_mate,
        define_assembly_part,
        execute_build123d_code,
    )

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "assembly-authoring")
    code = (
        "from build123d import *\n"
        "a = Box(40, 40, 5); a.label = 'base_plate'\n"
        "b = Cylinder(5, 30).moved(Location((0, 0, 5))); b.label = 'pillar'\n"
        "result = Compound(children=[a, b])\n"
    )
    built = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert built["status"] == "ok"

    p1 = define_assembly_part(settings, pid, {"geometry_ref": "base_plate", "role": "design_part"})
    assert p1["status"] == "ok"
    assert p1["geometry_ref_known"] is True  # links to the real named part
    p2 = define_assembly_part(settings, pid, {"geometry_ref": "pillar", "role": "design_part"})
    assert p2["geometry_ref_known"] is True

    mate = define_assembly_mate(
        settings, pid,
        {"connection_type": "bolted_proxy", "part_a": "base_plate", "part_b": "pillar"},
    )
    assert mate["status"] == "ok"
    assert mate["is_proxy"] is True
    assert mate["connection"]["limitations"]  # proxy never recorded without honest limitations
    assert mate["assembly_summary"]["part_count"] == 2
    assert mate["assembly_summary"]["connection_count"] == 1

    # a mate to an undefined part is refused, not silently created
    bad = define_assembly_mate(
        settings, pid,
        {"connection_type": "rigid_tie", "part_a": "base_plate", "part_b": "ghost"},
    )
    assert bad["status"] == "error"
    assert bad["code"] == "unknown_parts"


def test_assembly_authoring_without_package_errors(tmp_path: Path) -> None:
    from app.cad_generation import define_assembly_part

    settings = _make_settings(tmp_path)
    out = define_assembly_part(settings, "does-not-exist", {"part_id": "x"})
    assert out["status"] == "error"
    assert out["code"] in ("project_not_found", "package_not_found")


def test_assembly_interface_binds_real_face(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import (
        define_assembly_interface,
        define_assembly_part,
        execute_build123d_code,
    )

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "assembly-interface")
    code = (
        "from build123d import *\n"
        "a = Box(40, 40, 5); a.label = 'base_plate'\n"
        "result = Compound(children=[a])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})["status"] == "ok"

    # pull a real B-Rep face id from the built model's topology
    topo = _load_topology_map(settings, pid)
    face_ids = [e["id"] for e in topo["entities"] if e.get("type") == "face"]
    assert face_ids, "expected the built box to have faces"

    define_assembly_part(settings, pid, {"geometry_ref": "base_plate", "role": "design_part"})
    out = define_assembly_interface(settings, pid, {
        "part_id": "base_plate",
        "semantic_role": "mounting_face",
        "face_ids": face_ids[:1],
    })
    assert out["status"] == "ok"
    assert out["face_ids_known"] is True  # a real built face id resolves
    assert out["resolution_status"] in {"resolved", "partially_resolved", "unresolved"}


def test_design_review_surfaces_modeling_fidelity_for_crude_build(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import design_review, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "fidelity-crude")
    # a bare box: 6 faces, no fillets, no shaped bodies → crude
    code = "from build123d import *\nb = Box(80, 60, 10); b.label = 'block'\nresult = Compound(children=[b])\n"
    assert execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})["status"] == "ok"

    rev = design_review(settings, pid, {})
    assert rev["status"] == "ok"
    assert rev["summary"]["modeling_fidelity"]["level"] in {"crude", "basic"}
    assert rev["fidelity"]["findings"]
    assert "fidelity" in rev["recommendation"].lower()
    # fidelity is a separate axis: a bare box still passes the DfM critique
    assert rev["critique_verdict"] in {"passes", "passes_with_notes", "passes_with_warnings"}


def test_housing_helper_builds_and_reads_designed(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import design_review, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "housing-helper")
    # the scaffold: one call replaces a crude Box-Box with a filleted shell
    code = (
        "from build123d import *\n"
        "h = housing(120, 80, 60, wall=5, label='gearbox_housing')\n"
        "result = Compound(children=[h])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    assert "gearbox_housing" in out["named_parts"]

    rev = design_review(settings, pid, {})
    # the helper's filleted edges are credited even though the user source never
    # literally calls fillet() — so it is NOT flagged for crude sharp edges
    assert rev["fidelity"]["signals"]["has_edge_breaking"] is True
    assert rev["summary"]["modeling_fidelity"]["level"] in {"designed", "basic"}
    assert not any(f["rule"] == "no_edge_breaking" for f in rev["fidelity"]["findings"])


def test_nested_paren_fillet_is_credited_as_edge_breaking() -> None:
    # the common build123d form fillet(bp.edges().filter_by(Axis.Z), radius=N) has a
    # nested paren the radius regex misses — credit edge-breaking by presence so a
    # filleted model is not mislabelled 'crude'.
    from app.cad_generation import _detect_advanced_features

    feats: list[dict] = []
    src = (
        "with BuildPart() as bp:\n"
        "    Box(10, 10, 10)\n"
        "    fillet(bp.edges().filter_by(Axis.Z), radius=2)\n"
    )
    _detect_advanced_features(feats, src)
    assert any(f["type"] == "fillet" for f in feats)


def test_source_intent_feature_candidates_are_tagged_honestly() -> None:
    from app.cad_generation import _detect_advanced_features

    feats: list[dict] = []
    src = """
rib_wall = rib(length=40, height=18, thickness=4)
seat_boss = boss(outer_diameter=24, height=14)
slot_profile = SlotOverall(32, 8)
pocket_cut = Box(20, 12, 4, mode=Mode.SUBTRACT)  # pocket relief
threaded_hole = thread.IsoThread(major_diameter=6, pitch=1)
"""

    _detect_advanced_features(feats, src)

    by_type = {f["type"]: f for f in feats}
    for feature_type in ("rib", "boss", "slot", "pocket", "thread"):
        assert feature_type in by_type
        assert by_type[feature_type]["recognition"]["method"] == "source_pattern"
        assert by_type[feature_type]["recognition"]["confidence"] == "medium"
        assert "Source-intent signal only" in by_type[feature_type]["recognition"]["limitations"]

    assert by_type["rib"]["intent"]["role"] == "stiffener"
    assert by_type["pocket"]["intent"]["role"] == "material_removal"
    assert "Mode.SUBTRACT" in by_type["pocket"]["recognition"]["matched_patterns"]
    assert by_type["thread"]["intent"]["role"] == "threaded_interface"


def test_cylinder_face_carries_axis_for_mate_predicates(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import _execute_build123d_code

    # the concentric/tangent mate predicates need cylinder faces to carry an axis
    _s, _st, _g, topo, _mesh_meta = _execute_build123d_code("from build123d import *\nresult = Cylinder(6, 40)\n")
    cyls = [e for e in topo.get("entities", []) if e.get("type") == "face" and e.get("surface_type") == "cylinder"]
    assert cyls, "expected at least one cylindrical face"
    assert any(isinstance(c.get("axis"), list) and len(c["axis"]) == 3 for c in cyls)
    assert any(isinstance(c.get("radius"), (int, float)) for c in cyls)


def test_topology_extracts_edges_resolvable_in_brep_graph(tmp_path: Path) -> None:
    # #287: topology now carries B-Rep edges (deduped, with adjacent faces) so
    # @edge: pointers resolve and explicit face-adjacency relations exist.
    pytest.importorskip("build123d")
    from app.brep_graph import build_brep_graph_from_topology
    from app.cad_generation import _execute_build123d_code

    _s, _st, _g, topo, _mesh_meta = _execute_build123d_code("from build123d import *\nresult = Box(10, 10, 10)\n")
    raw_edges = [e for e in topo["entities"] if e.get("type") == "edge"]
    assert len(raw_edges) == 12, "a box has 12 edges"  # deduped, not 24 (per-face)
    assert all(len(e.get("adjacent_faces", [])) == 2 for e in raw_edges)  # each shared by 2 faces

    out = build_brep_graph_from_topology(topo, feature_graph={})
    edges = out["brep_graph"]["entities"]["edges"]
    assert len(edges) == 12
    assert all(e["pointer"].startswith("@edge:") for e in edges)
    rels = out["brep_graph"]["relations"]
    assert any(r["type"] == "face_has_edge" for r in rels)
    assert any(r["type"] == "face_adjacent_face" and r.get("confidence") == "explicit" for r in rels)


def test_validate_targets_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, validate_targets

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "validate-targets")
    code = (
        "from build123d import *\n"
        "a = Box(80, 60, 10); a.label = 'base'\n"
        "b = Cylinder(5, 30).moved(Location((0, 0, 10))); b.label = 'pin'\n"
        "result = Compound(children=[a, b])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})["status"] == "ok"

    out = validate_targets(settings, pid, {"targets": [
        {"kind": "named_part_present", "part": "base"},
        {"kind": "named_part_present", "part": "ghost"},
        {"kind": "part_count", "count": 2},
        {"kind": "part_size", "part": "base", "size_mm": [80, 60, 10], "tolerance_mm": 1},
    ]})
    assert out["status"] == "ok"
    statuses = [t["status"] for t in out["targets"]]
    assert statuses == ["pass", "fail", "pass", "pass"]
    assert out["verdict"] == "fail"  # the deliberate 'ghost' target fails

    # input + error paths
    assert validate_targets(settings, pid, {})["code"] == "missing_targets"


def test_cad_brief_feeds_validate_targets_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import author_brief, execute_build123d_code, get_brief, validate_targets

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "brief-loop")

    # 1) author the brief BEFORE any geometry exists
    ab = author_brief(settings, pid, {
        "request": "base plate + pin", "model_type": "assembly",
        "parts": [{"name": "base", "size_mm": [80, 60, 10]}, {"name": "pin"}],
    })
    assert ab["status"] == "ok"
    assert any(t["kind"] == "named_part_present" and t.get("part") == "base" for t in ab["validation_targets"])
    assert get_brief(settings, pid)["status"] == "ok"

    # 2) build a model matching the brief
    code = (
        "from build123d import *\n"
        "a = Box(80, 60, 10); a.label = 'base'\n"
        "b = Cylinder(5, 30).moved(Location((0, 0, 10))); b.label = 'pin'\n"
        "result = Compound(children=[a, b])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})["status"] == "ok"

    # 3) validate with NO explicit targets → auto-loads the brief's targets
    out = validate_targets(settings, pid, {})
    assert out["status"] == "ok"
    assert out["targets_source"] == "cad_brief"
    assert out["verdict"] == "pass"
    assert all(t["status"] == "pass" for t in out["targets"])


def test_positioning_helpers_place_parts_relative(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import _execute_build123d_code

    code = (
        "from build123d import *\n"
        "plate = Box(80, 60, 10); plate.label = 'plate'\n"
        "pin = stack_on(Cylinder(5, 20), plate, label='pin')\n"          # bottom on plate top, XY-centered
        "ref = Box(20, 20, 40).moved(Location((10, 20, 0)))\n"
        "shaft = coaxial(Cylinder(3, 60), ref, axis='Z', label='shaft')\n"  # X,Y → ref center (10,20)
        "result = Compound(children=[plate, pin, shaft])\n"
    )
    _s, _st, _g, topo, _mesh_meta = _execute_build123d_code(code)
    bb = {e["name"]: e["bounding_box"] for e in topo["entities"] if e.get("type") == "solid"}

    def cx(b):
        return (b[0] + b[3]) / 2

    def cy(b):
        return (b[1] + b[4]) / 2

    # stack_on: pin's bottom sits at the plate's top, centered in XY
    assert abs(bb["pin"][2] - bb["plate"][5]) < 0.05
    assert abs(cx(bb["pin"]) - cx(bb["plate"])) < 0.05 and abs(cy(bb["pin"]) - cy(bb["plate"])) < 0.05
    # coaxial(Z): shaft's cross-axis center matches the ref's center (10, 20)
    assert abs(cx(bb["shaft"]) - 10) < 0.05 and abs(cy(bb["shaft"]) - 20) < 0.05


def test_diagnose_ready_vs_needs_repair(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import diagnose, execute_build123d_code

    settings = _make_settings(tmp_path)

    # clean single filleted part → no blocking issues → ready
    pid = _make_project(settings, "diag-ready")
    ok_code = (
        "from build123d import *\n"
        "with BuildPart() as bp:\n"
        "    Box(40, 40, 10)\n"
        "    fillet(bp.edges().filter_by(Axis.Z), radius=3)\n"
        "p = bp.part; p.label = 'plate'\n"
        "result = Compound(children=[p])\n"
    )
    assert execute_build123d_code(settings, pid, {"code": ok_code, "thumbnail": False})["status"] == "ok"
    ready = diagnose(settings, pid, {})
    assert ready["status"] == "ok"
    assert ready["verdict"] == "ready"
    assert ready["blocking_issues"] == []

    # a floating part → high risk + blocking → needs_repair
    pid2 = _make_project(settings, "diag-float")
    bad_code = (
        "from build123d import *\n"
        "a = Box(10, 10, 10); a.label = 'a'\n"
        "b = Box(10, 10, 10).moved(Location((500, 0, 0))); b.label = 'b'\n"  # 500mm away → floating
        "result = Compound(children=[a, b])\n"
    )
    assert execute_build123d_code(settings, pid2, {"code": bad_code, "thumbnail": False})["status"] == "ok"
    bad = diagnose(settings, pid2, {})
    assert bad["verdict"] == "needs_repair"
    assert bad["high_risk"] is True
    assert "floating_parts" in bad["triggers"]
    assert bad["blocking_issues"]
    assert bad["repair_actions"] is not None


def test_large_bores_are_bores_not_mounting_holes(tmp_path: Path) -> None:
    # #289: small cylinders are bolt holes (mounting_candidate); large ones are bores.
    pytest.importorskip("build123d")
    from app.brep_graph import build_brep_graph_from_topology
    from app.cad_generation import _execute_build123d_code, _topology_to_feature_graph

    code = (
        "from build123d import *\n"
        "with BuildPart() as bp:\n"
        "    Box(100, 80, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
        "    with Locations((40, 30, 0), (-40, 30, 0), (40, -30, 0), (-40, -30, 0)):\n"
        "        Cylinder(2.5, 30, mode=Mode.SUBTRACT)\n"   # 4 bolt holes (r=2.5)
        "    with Locations((20, 0, 0), (-20, 0, 0)):\n"
        "        Cylinder(11, 30, mode=Mode.SUBTRACT)\n"     # 2 bearing bores (r=11)
        "result = bp.part\n"
    )
    _s, _st, _g, topo, _mesh_meta = _execute_build123d_code(code)
    fg = _topology_to_feature_graph(topo, source_code=code, model_kind="mechanical")
    types = [f["type"] for f in fg["features"]]
    assert "mounting_hole_pattern" in types          # the 4 small holes
    assert "bore" in types                            # the 2 large bores (was mounting_hole)
    bore = next(f for f in fg["features"] if f["type"] == "bore")
    assert bore["intent"]["role"] == "feature_reference"
    assert not any(f["type"] in ("mounting_hole", "mounting_hole_pattern")
                   and f["parameters"].get("hole_diameter_mm", 0) > 16 for f in fg["features"])

    g = build_brep_graph_from_topology(topo, feature_graph=fg)
    cyl = [f for f in g["brep_graph"]["entities"]["faces"] if f.get("surface_type") == "cylinder"]
    big = [f for f in cyl if (f.get("radius_mm") or 0) > 8]
    small = [f for f in cyl if 0 < (f.get("radius_mm") or 0) <= 8]
    assert big and all("bore" in f["roles"] and "mounting_candidate" not in f["roles"] for f in big)
    assert small and all("mounting_candidate" in f["roles"] for f in small)


def test_engineering_detail_helpers_build_and_compose(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import design_review, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "detailed-housing")
    # housing + bearing-seat boss + stiffening rib + mounting foot — the detail a
    # raw Box-Box gearbox was missing
    code = (
        "from build123d import *\n"
        "body = housing(120, 80, 60, wall=5, label='housing')\n"
        "seat = boss(26, 8, hole_dia=12).moved(Location((40, 0, 30))); seat.label = 'bearing_seat'\n"
        "stiff = rib(20, 30, 5).moved(Location((-55, 0, 5))); stiff.label = 'rib_main'\n"
        "foot = mounting_tab(24, 20, 6, 5).moved(Location((70, 0, 0))); foot.label = 'foot_R'\n"
        "result = Compound(children=[body, seat, stiff, foot])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    for part in ("housing", "bearing_seat", "rib_main", "foot_R"):
        assert part in out["named_parts"], out["named_parts"]

    rev = design_review(settings, pid, {})
    assert rev["fidelity"]["signals"]["has_edge_breaking"] is True
    assert rev["summary"]["modeling_fidelity"]["level"] in {"designed", "basic"}


# ── critique (engineering-diagnostics) regression diff ────────────────────────
# Two solids far apart trip the deterministic `floating_component` high finding;
# moving them apart introduces it, moving them together resolves it. This lets us
# unit-test _diff_critique purely (no build123d) on topology dicts alone.

_CLOSE_TWO_PART = _topo_from(
    [("torso", [-15, -15, -15, 15, 15, 15]), ("arm", [25, -15, -15, 55, 15, 15])]
)  # center gap ~40, no floating finding
_FAR_TWO_PART = _topo_from(
    [("torso", [-15, -15, -15, 15, 15, 15]), ("arm", [185, -15, -15, 215, 15, 15])]
)  # center gap ~200, both parts read as floating


def test_diff_critique_fail_on_new_high_violation() -> None:
    from app.cad_generation import _diff_critique

    diff = _diff_critique(_CLOSE_TWO_PART, {}, _FAR_TWO_PART, {})
    assert diff["verdict"] == "fail"
    assert diff["before_counts"]["high"] == 0
    assert diff["after_counts"]["high"] > 0
    assert diff["delta"]["high"] > 0
    rules = {f["rule"] for f in diff["introduced"]}
    assert "floating_component" in rules


def test_diff_critique_improved_when_violation_resolved() -> None:
    from app.cad_generation import _diff_critique

    diff = _diff_critique(_FAR_TWO_PART, {}, _CLOSE_TWO_PART, {})
    assert diff["verdict"] == "improved"
    assert diff["delta"]["high"] < 0
    assert diff["introduced"] == []
    assert diff["resolved_count"] > 0


def test_diff_critique_clean_when_unchanged() -> None:
    from app.cad_generation import _diff_critique

    diff = _diff_critique(_CLOSE_TWO_PART, {}, _CLOSE_TWO_PART, {})
    assert diff["verdict"] == "clean"
    assert diff["delta"] == {"high": 0, "medium": 0, "low": 0}
    assert diff["introduced"] == []


def test_diff_critique_skipped_without_solids() -> None:
    from app.cad_generation import _diff_critique

    empty = {"entities": []}
    diff = _diff_critique(empty, {}, empty, {})
    assert diff["verdict"] == "skipped"


def test_write_last_edit_diff_persists_to_package(tmp_path: Path) -> None:
    import zipfile
    from app.cad_generation import _LAST_EDIT_DIFF_MEMBER, _write_last_edit_diff

    pkg = tmp_path / "p.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")

    _write_last_edit_diff(
        pkg,
        tool="cad.edit_parameter",
        regression_diff={"verdict": "clean"},
        critique_diff={"verdict": "warn"},
        geometry_verification={"topology_preserved": True, "stale_reference_risk": False},
    )
    with zipfile.ZipFile(pkg, "r") as zf:
        assert _LAST_EDIT_DIFF_MEMBER in zf.namelist()
        payload = json.loads(zf.read(_LAST_EDIT_DIFF_MEMBER).decode("utf-8"))
    assert payload["tool"] == "cad.edit_parameter"
    assert payload["regression_diff"]["verdict"] == "clean"
    assert payload["critique_diff"]["verdict"] == "warn"
    assert payload["geometry_verification"]["topology_preserved"] is True

    # A later edit replaces (not appends) the record.
    _write_last_edit_diff(pkg, tool="cad.remove_part", regression_diff={"verdict": "topology_changed"})
    with zipfile.ZipFile(pkg, "r") as zf:
        payload2 = json.loads(zf.read(_LAST_EDIT_DIFF_MEMBER).decode("utf-8"))
    assert payload2["tool"] == "cad.remove_part"
    assert payload2["critique_diff"] is None
    assert payload2["geometry_verification"] is None


def test_write_last_edit_diff_noop_when_both_none(tmp_path: Path) -> None:
    import zipfile
    from app.cad_generation import _LAST_EDIT_DIFF_MEMBER, _write_last_edit_diff

    pkg = tmp_path / "p.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")
    _write_last_edit_diff(pkg, tool="cad.edit_parameter")  # both diffs None → no write
    with zipfile.ZipFile(pkg, "r") as zf:
        assert _LAST_EDIT_DIFF_MEMBER not in zf.namelist()


def test_append_mode_critique_diff_only_for_append_with_prior() -> None:
    from app.cad_generation import _append_mode_critique_diff

    prior = _CLOSE_TWO_PART
    after = _FAR_TWO_PART
    # append with a prior model → a diff is produced (new floating part → fail)
    diff = _append_mode_critique_diff("append", prior, {}, after, {})
    assert diff is not None and diff["verdict"] == "fail"
    # replace mode → no before-model to diff → None
    assert _append_mode_critique_diff("replace", prior, {}, after, {}) is None
    # append with no prior topology (first build) → None
    assert _append_mode_critique_diff("append", {}, {}, after, {}) is None


# ── design-rule assertions (require) — marker parser ──────────────────────────

def test_extract_design_rule_violation_finds_marker() -> None:
    from app.cad_generation import _extract_design_rule_violation

    stderr = (
        "Traceback (most recent call last):\n"
        "  ...\n"
        "__AIENG_DESIGN_RULE_VIOLATION__ wall too thin: 2.0mm < 3mm\n"
    )
    assert _extract_design_rule_violation(stderr) == "wall too thin: 2.0mm < 3mm"


def test_extract_design_rule_violation_absent_returns_none() -> None:
    from app.cad_generation import _extract_design_rule_violation

    assert _extract_design_rule_violation(None) is None
    assert _extract_design_rule_violation("ordinary build error\n") is None


# ── parametric edit end-to-end (real build123d) ───────────────────────────────

def test_edit_build123d_parameter_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "param-edit")
    # Named constants → the feature graph exposes an editable parameter.
    code = (
        "from build123d import *\n"
        "BODY_LENGTH = 120\n"
        "BODY_WIDTH = 80\n"
        "BODY_HEIGHT = 8\n"
        "body = Box(BODY_LENGTH, BODY_WIDTH, BODY_HEIGHT); body.label = 'base_plate'\n"
        "result = Compound(children=[body])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    # find the base_plate feature + its length parameter
    feat = next(
        f for f in out["feature_graph"]["features"]
        if f.get("name") == "base_plate" and (f.get("parameters") or {})
    )
    assert "length_mm" in feat["parameters"]
    assert feat["parameters"]["length_mm"]["cad_parameter_name"] == "BODY_LENGTH"

    edited = edit_build123d_parameter(
        settings, pid,
        feature_id=feat["id"], parameter_name="length_mm", new_value=200,
    )
    assert edited["status"] == "ok"
    assert edited["previous_value"] == 120
    assert edited["new_value"] == 200
    # the box got longer in X
    bbox = edited["topology_summary"]["bounding_box"]
    assert abs((bbox[3] - bbox[0]) - 200) < 1.0
    # regression diff: base_plate changed as expected, nothing collateral
    diff = edited["regression_diff"]
    assert diff["verdict"] in ("clean", "topology_changed")
    assert diff["collateral_parts"] == []
    # critique diff: a benign length change introduces no new manufacturability
    # violations, so the engineering-diagnostics verdict is not a regression.
    cdiff = edited["critique_diff"]
    assert cdiff["verdict"] in ("clean", "improved", "skipped")
    assert cdiff["delta"]["high"] <= 0
    assert cdiff["introduced"] == []
    # #226: the real edit path persists the diff to package state so the viewer
    # (GET /edit-diff) can re-surface it — not just the tool response.
    import zipfile as _zf
    from app.cad_generation import _LAST_EDIT_DIFF_MEMBER
    from app.project_io import get_project, resolve_project_path
    pkg = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with _zf.ZipFile(pkg, "r") as z:
        assert _LAST_EDIT_DIFF_MEMBER in z.namelist()
        persisted = json.loads(z.read(_LAST_EDIT_DIFF_MEMBER).decode("utf-8"))
    assert persisted["tool"] == "cad.edit_parameter"
    assert persisted["regression_diff"]["verdict"] == diff["verdict"]


def test_replace_cad_parameter_assignment_preserves_expression_comments() -> None:
    from app.cad_generation import _replace_cad_parameter_assignment

    source = (
        "WALL = 3 * 2  # editable wall\n"
        "// WALL = 5\n"
        "body = Box(WALL, 10, 10)\n"
    )

    modified, found = _replace_cad_parameter_assignment(source, "WALL", "5 * 2")

    assert found is True
    assert "WALL = 5 * 2  # editable wall" in modified
    assert "// WALL = 5" in modified


def test_edit_build123d_parameter_expression_rhs_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "param-edit-expression")
    code = (
        "from build123d import *\n"
        "INNER_LENGTH = 60\n"
        "BODY_LENGTH = INNER_LENGTH * 2  # expression stays editable\n"
        "BODY_WIDTH = 80\n"
        "BODY_HEIGHT = 8\n"
        "body = Box(BODY_LENGTH, BODY_WIDTH, BODY_HEIGHT); body.label = 'base_plate'\n"
        "result = Compound(children=[body])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    target_feature = None
    target_parameter_name = None
    target_parameter = None
    for feature in out["feature_graph"]["features"]:
        for name, param in (feature.get("parameters") or {}).items():
            if isinstance(param, dict) and param.get("cad_parameter_name") == "BODY_LENGTH":
                target_feature = feature
                target_parameter_name = name
                target_parameter = param
                break
        if target_feature:
            break
    assert target_feature is not None
    assert target_parameter_name is not None
    assert target_parameter["current_value"] == 120

    edited = edit_build123d_parameter(
        settings, pid,
        feature_id=target_feature["id"],
        parameter_name=target_parameter_name,
        new_value="INNER_LENGTH * 3 + 20",
        thumbnail=False,
    )

    assert edited["status"] == "ok", edited
    assert edited["previous_value"] == 120
    assert edited["new_value"] == "INNER_LENGTH * 3 + 20"
    bbox = edited["topology_summary"]["bounding_box"]
    assert abs((bbox[3] - bbox[0]) - 200) < 1.0
    assert edited["topology_changed"] is False

    from app.project_io import get_project, resolve_project_path
    pkg = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with zipfile.ZipFile(pkg, "r") as z:
        source_after = z.read("geometry/source.py").decode("utf-8")
    assert "BODY_LENGTH = INNER_LENGTH * 3 + 20  # expression stays editable" in source_after

    rejected = edit_build123d_parameter(
        settings, pid,
        feature_id=target_feature["id"], parameter_name=target_parameter_name,
        new_value="__import__('os').system('echo unsafe')",
        thumbnail=False,
    )
    assert rejected["status"] == "error"
    assert rejected["code"] == "invalid_parameter_expression"
    with zipfile.ZipFile(pkg, "r") as z:
        source_after_rejected = z.read("geometry/source.py").decode("utf-8")
    assert source_after_rejected == source_after


def test_edit_build123d_integer_count_reports_topology_change(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "param-edit-hole-count")
    code = (
        "from build123d import *\n"
        "import math\n"
        "PLATE_LENGTH = 90\n"
        "PLATE_WIDTH = 70\n"
        "PLATE_THICKNESS = 8\n"
        "HOLE_COUNT = 4\n"
        "HOLE_RADIUS = 3\n"
        "PATTERN_RADIUS = 25\n"
        "with BuildPart() as bp:\n"
        "    Box(PLATE_LENGTH, PLATE_WIDTH, PLATE_THICKNESS)\n"
        "    for i in range(HOLE_COUNT):\n"
        "        angle = 2 * math.pi * i / HOLE_COUNT\n"
        "        with Locations((PATTERN_RADIUS * math.cos(angle), PATTERN_RADIUS * math.sin(angle), 0)):\n"
        "            Cylinder(HOLE_RADIUS, PLATE_THICKNESS + 2, mode=Mode.SUBTRACT)\n"
        "body = bp.part\n"
        "body.label = 'bolt_plate'\n"
        "result = Compound(children=[body])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    feat = next(
        f for f in out["feature_graph"]["features"]
        if f.get("name") == "bolt_plate" and (f.get("parameters") or {})
    )
    assert any(
        p.get("cad_parameter_name") == "HOLE_COUNT"
        for p in feat["parameters"].values()
        if isinstance(p, dict)
    )

    edited = edit_build123d_parameter(
        settings, pid,
        feature_id=feat["id"], parameter_name="HOLE_COUNT", new_value=6,
        thumbnail=False,
    )

    assert edited["status"] == "ok", edited
    assert edited["topology_changed"] is True
    assert edited["topology_change"]["stale_reference_risk"] is True
    face_change = edited["topology_change"]["entity_id_changes"]["face"]
    assert face_change["before_count"] != face_change["after_count"]


def test_edit_build123d_shared_parameter_requires_explicit_scope_confirmation(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code
    from app.project_io import get_project, resolve_project_path

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "param-edit-shared")
    code = (
        "from build123d import *\n"
        "GEAR_IN_DIA = 30\n"
        "GEAR_OUT_DIA = 50\n"
        "GEAR_WIDTH = 8\n"
        "gi = Cylinder(GEAR_IN_DIA / 2, GEAR_WIDTH); gi.label = 'gear_input'\n"
        "go = Cylinder(GEAR_OUT_DIA / 2, GEAR_WIDTH); go.label = 'gear_output'\n"
        "result = Compound(children=[gi, go])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    global_feat = next(f for f in out["feature_graph"]["features"] if f.get("type") == "global_params")
    assert any(
        p.get("cad_parameter_name") == "GEAR_WIDTH"
        for p in global_feat["parameters"].values()
        if isinstance(p, dict)
    )

    pkg = resolve_project_path(settings, pid, get_project(settings, pid).get("aieng_file"))
    with zipfile.ZipFile(pkg, "r") as z:
        source_before = z.read("geometry/source.py").decode("utf-8")

    rejected = edit_build123d_parameter(
        settings, pid,
        feature_id=global_feat["id"], parameter_name="width_mm", new_value=10,
        thumbnail=False,
    )
    assert rejected["status"] == "error"
    assert rejected["code"] == "scope_risk_confirmation_required"
    assert rejected["scope_risk"]["scope"] == "global"
    with zipfile.ZipFile(pkg, "r") as z:
        assert z.read("geometry/source.py").decode("utf-8") == source_before

    edited = edit_build123d_parameter(
        settings, pid,
        feature_id=global_feat["id"], parameter_name="width_mm", new_value=10,
        thumbnail=False,
        confirm_scope_risk=True,
    )
    assert edited["status"] == "ok", edited
    assert edited["scope_risk"]["scope"] == "global"
    assert edited["confirm_scope_risk"] is True
    with zipfile.ZipFile(pkg, "r") as z:
        source_after = z.read("geometry/source.py").decode("utf-8")
    assert "GEAR_WIDTH = 10" in source_after


def test_edit_build123d_parameter_invalid_value_preserves_geometry(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import edit_build123d_parameter, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "param-edit-bad")
    code = (
        "from build123d import *\n"
        "BODY_LENGTH = 120\n"
        "body = Box(BODY_LENGTH, 80, 8); body.label = 'base_plate'\n"
        "result = Compound(children=[body])\n"
    )
    execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    feat = next(
        f for f in execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})["feature_graph"]["features"]
        if f.get("name") == "base_plate" and (f.get("parameters") or {})
    )
    # 0-length box is geometrically invalid → build fails, prior geometry preserved
    edited = edit_build123d_parameter(
        settings, pid,
        feature_id=feat["id"], parameter_name="length_mm", new_value=0,
    )
    assert edited["status"] == "error"
    assert edited["code"] in ("execution_failed", "invalid_contract")


# ── F1: organic models skip mechanical heuristics ─────────────────────────────

def test_feature_graph_organic_skips_mechanical_heuristics() -> None:
    from app.cad_generation import _topology_to_feature_graph

    # Two cylinders of equal radius would normally trip the bolt-pattern heuristic.
    topo = {"entities": [
        {"type": "solid", "id": "b1", "name": "arm_L", "bounding_box": [-50, -10, 0, -30, 10, 200]},
        {"type": "solid", "id": "b2", "name": "arm_R", "bounding_box": [30, -10, 0, 50, 10, 200]},
        {"type": "face", "id": "f1", "body_id": "b1", "surface_type": "cylinder", "radius": 20.0},
        {"type": "face", "id": "f2", "body_id": "b2", "surface_type": "cylinder", "radius": 20.0},
        {"type": "face", "id": "f3", "body_id": "b1", "surface_type": "plane",
         "normal": [0, 0, -1], "center": [-40, 0, 0], "area": 1200, "bounding_box": [-50, -10, 0, -30, 10, 0]},
    ]}
    src = "from build123d import *\narm_L = capsule(20, 160)\nresult = Compound(children=[arm_L])\n"
    fg = _topology_to_feature_graph(topo, source_code=src, model_kind="auto")
    types = {f["type"] for f in fg["features"]}
    assert fg["model_kind"] == "organic"
    assert "mounting_hole" not in types and "mounting_hole_pattern" not in types
    assert "base_plate" not in types


def test_feature_graph_mechanical_keeps_heuristics() -> None:
    from app.cad_generation import _topology_to_feature_graph

    topo = {"entities": [
        {"type": "solid", "id": "b1", "name": "base_plate", "bounding_box": [0, 0, 0, 100, 60, 8]},
        {"type": "face", "id": "f1", "body_id": "b1", "surface_type": "cylinder", "radius": 5.0},
        {"type": "face", "id": "f2", "body_id": "b1", "surface_type": "cylinder", "radius": 5.0},
    ]}
    fg = _topology_to_feature_graph(topo, model_kind="auto")
    assert fg["model_kind"] == "mechanical"
    assert any(f["type"] in ("mounting_hole", "mounting_hole_pattern") for f in fg["features"])


# ── F2: remove / replace a named part (real build123d) ────────────────────────

def _ironman_stub_code() -> str:
    return (
        "from build123d import *\n"
        "torso = lofted_stack([(0,300,200),(400,360,220)], label='torso', color=(0.7,0.1,0.1))\n"
        "head = Sphere(80).moved(Location((0,0,520))); head.label='head'; head.color=Color(0.85,0.66,0.12)\n"
        "arm_L = capsule(40, 200, label='arm_L').moved(Location((-220,0,300)))\n"
        "result = Compound(children=[torso, head, arm_L])\n"
    )


def test_remove_part_drops_named_part(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, remove_build123d_part

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "ironman-remove")
    execute_build123d_code(settings, pid, {"code": _ironman_stub_code(), "thumbnail": False})

    out = remove_build123d_part(settings, pid, label="head")
    assert out["status"] == "ok"
    assert "head" not in out["named_parts"]
    assert sorted(out["named_parts"]) == ["arm_L", "torso"]
    assert "head" in out["regression_diff"]["removed"]


def test_remove_part_unknown_label_errors(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, remove_build123d_part

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "ironman-remove-bad")
    execute_build123d_code(settings, pid, {"code": _ironman_stub_code(), "thumbnail": False})

    out = remove_build123d_part(settings, pid, label="cape")
    assert out["status"] == "error"
    assert out["code"] == "part_not_found"
    assert "head" in out["available_parts"]


def test_replace_part_swaps_named_part(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, replace_build123d_part

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "ironman-replace")
    execute_build123d_code(settings, pid, {"code": _ironman_stub_code(), "thumbnail": False})

    # swap the spherical head for a taller lofted helmet, same label
    new_head = (
        "from build123d import *\n"
        "result = lofted_stack([(440,150,170),(560,90,110)], label='head', color=(0.85,0.66,0.12))\n"
    )
    out = replace_build123d_part(settings, pid, label="head", code=new_head)
    assert out["status"] == "ok"
    assert sorted(out["named_parts"]) == ["arm_L", "head", "torso"]
    # head changed, torso/arm should not be collateral
    assert out["regression_diff"]["collateral_parts"] == []


def test_replace_part_contract_requires_result(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code, replace_build123d_part

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "ironman-replace-bad")
    execute_build123d_code(settings, pid, {"code": _ironman_stub_code(), "thumbnail": False})

    out = replace_build123d_part(settings, pid, label="head", code="x = Sphere(50)")
    assert out["status"] == "error"
    assert out["code"] == "contract_violation"


# ── free-form faces carry a proxy normal (CAE face-binding fix) ───────────────

def test_freeform_faces_get_proxy_normal(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import _execute_build123d_code

    # capsule = sphere caps + cylinder → all free-form / curved faces
    _step, _stl, _glb, topo, _mesh_meta = _execute_build123d_code(
        "arm = capsule(20, 80, label='arm')\nresult = Compound(children=[arm])\n"
    )
    freeform_faces = [
        e for e in topo["entities"]
        if e.get("type") == "face" and e.get("freeform") is True
    ]
    assert freeform_faces, "capsule should produce curved/free-form faces"
    # every free-form face now carries a proxy normal + freeform flag so CAE can bind it
    for f in freeform_faces:
        assert f.get("freeform") is True
        assert f.get("normal") and len(f["normal"]) == 3


# ── agent builds populate the UI viewer asset (web_asset) ─────────────────────

def test_execute_build123d_publishes_web_asset(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code
    from app.main import get_project, project_dir

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "viewer-asset")
    code = "from build123d import *\nresult = Box(40, 40, 10)\n"
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    # the build must point web_asset at viewer/model.* so the UI viewer loads it
    project = get_project(settings, pid)
    assert project.get("web_asset"), "web_asset must be set so the UI viewer can load the model"
    assert project.get("web_asset_format") in ("glb", "stl")
    viewer_file = project_dir(settings, pid) / project["web_asset"]
    assert viewer_file.exists() and viewer_file.stat().st_size > 0


# ── project discoverability: auto-naming + named_parts + part search ──────────

def test_derive_project_name_prefixed_assembly() -> None:
    from app.cad_generation import _derive_project_name
    # parts sharing prefixes (an assembly) -> "Optimus + Bee"
    assert _derive_project_name(["optimus_torso", "optimus_head", "bee_torso", "bee_head"]) == "Optimus + Bee"


def test_derive_project_name_flat_falls_back_to_count() -> None:
    from app.cad_generation import _derive_project_name
    # flat labels with no shared scheme -> count-based fallback (agent should pass `name`)
    flat = ["torso", "windshield", "grille", "waist", "neck", "head"]
    assert _derive_project_name(flat) == "6-part model"
    assert _derive_project_name([]) is None


def test_is_placeholder_project_name() -> None:
    from app.cad_generation import _is_placeholder_project_name
    assert _is_placeholder_project_name("STEP workbench project")
    assert _is_placeholder_project_name("  untitled project ")
    assert _is_placeholder_project_name("")
    assert not _is_placeholder_project_name("Optimus + Bumblebee")
    assert not _is_placeholder_project_name("Iron Man Mark III")


def test_named_parts_from_package_reads_feature_graph(tmp_path: Path) -> None:
    from app.cad_generation import _named_parts_from_package
    pkg = tmp_path / "m.aieng"
    fg = {"features": [
        {"type": "named_part", "name": "base_plate"},
        {"type": "named_part", "name": "rib_main"},
        {"type": "loft", "name": "Loft"},  # non-part feature ignored
    ]}
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("graph/feature_graph.json", json.dumps(fg))
    assert _named_parts_from_package(pkg) == ["base_plate", "rib_main"]


def test_named_parts_from_package_falls_back_to_topology(tmp_path: Path) -> None:
    from app.cad_generation import _named_parts_from_package
    pkg = tmp_path / "m.aieng"
    topo = {"entities": [
        {"type": "solid", "name": "body_a"},
        {"type": "solid", "name": "body_b"},
        {"type": "face"},
    ]}
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("geometry/topology_map.json", json.dumps(topo))
    assert _named_parts_from_package(pkg) == ["body_a", "body_b"]


def test_find_projects_by_part_tool(tmp_path: Path) -> None:
    from app.runtime_tool_schemas import get_schema
    schema = get_schema("aieng.find_projects_by_part")
    assert schema and "query" in schema["properties"]


# ── Shape IR: post-execution provenance reconciliation ───────────────────────

def test_reconcile_shape_ir_provenance_refreshes_registry_and_manifest(tmp_path: Path) -> None:
    """After Shape IR source executes, object_registry is rebuilt against the
    real executed ids and the conversion manifest is stamped — no dangling
    projected slug references remain."""
    from app.cad_generation import reconcile_shape_ir_provenance

    pkg = tmp_path / "m.aieng"
    projected_registry = {"objects": [{"id": "body_helmet_shell", "kind": "topology_entity"}], "relationships": []}
    manifest = {
        "format_version": "0.1",
        "converter": {"converter_id": "shape_ir_reference"},
        "coverage_categories": [{"category": "geometry", "status": "inferred"}],
    }
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("objects/object_registry.json", json.dumps(projected_registry))
        zf.writestr("provenance/conversion_manifest.json", json.dumps(manifest))
        zf.writestr("geometry/source.py", "x = 1\n")
        zf.writestr("geometry/generated.step", "ISO-10303-21;\nEND-ISO-10303-21;\n")

    topo = {
        "format_version": "0.1",
        "entities": [
            {"id": "body_001", "type": "solid", "name": "helmet"},
            {"id": "face_001", "type": "face", "body_id": "body_001"},
        ],
    }
    fg = {"features": [{
        "id": "feat_body_001", "type": "named_part", "name": "helmet",
        "geometry_refs": {"entities": ["body_001"], "faces": ["face_001"]},
    }]}

    reconcile_shape_ir_provenance(pkg, topo, fg, executed_at="2026-01-01T00:00:00Z")

    with zipfile.ZipFile(pkg) as zf:
        reg = json.loads(zf.read("objects/object_registry.json"))
        man = json.loads(zf.read("provenance/conversion_manifest.json"))

    ids = {o["id"] for o in reg["objects"]}
    assert "body_001" in ids and "feat_body_001" in ids
    assert "body_helmet_shell" not in ids  # projected slug id is gone
    assert all(o["status"] == "compiled_and_executed" for o in reg["objects"])
    assert man["geometry_execution"]["executed"] is True
    assert man["geometry_execution"]["real_geometry"] is True
    assert man["geometry_execution"]["executed_at_utc"] == "2026-01-01T00:00:00Z"


# ── implicit SDF runner (Shape IR representation: implicit_sdf) ───────────────

def test_mesh_feature_graph_reads_representation_from_topology() -> None:
    from app.cad_generation import _mesh_feature_graph
    topo = {
        "metadata": {"representation": "manifold_mesh", "extractor": "ManifoldRunner"},
        "entities": [
            {"id": "body_001", "type": "solid", "name": "manifold_body", "face_ids": ["face_001"]},
            {"id": "face_001", "type": "face", "body_id": "body_001"},
        ],
    }
    fg = _mesh_feature_graph(topo)
    assert len(fg["features"]) == 1
    feat = fg["features"][0]
    assert feat["type"] == "named_part" and feat["id"] == "feat_body_001"
    assert "body_001" in feat["geometry_refs"]["entities"]
    assert "face_001" in feat["geometry_refs"]["faces"]
    # representation/recognizer flow through from the topology the runner wrote
    assert fg["metadata"]["representation"] == "manifold_mesh"
    assert fg["metadata"]["recognizer"] == "ManifoldRunner"


def test_execute_sdf_code_meshes_a_field() -> None:
    """End-to-end SDF run: meshes a field to STL + GLB and projects mesh topology.
    Skips where the SDF runtime isn't installed (runs in aieng311/CI)."""
    pytest.importorskip("sdf")
    pytest.importorskip("trimesh")
    from app.cad_generation import _execute_sdf_code
    src = "from sdf import *\nf = sphere(10) | sphere(8).translate((0, 0, 12))\n"
    stl, glb, topo = _execute_sdf_code(src, timeout=120, samples=2 ** 15)
    assert len(stl) > 0 and len(glb) > 0
    assert topo["metadata"]["extractor"] == "SDFRunner"
    assert topo["metadata"]["representation"] == "implicit_sdf"
    faces = [e for e in topo["entities"] if e.get("type") == "face"]
    assert faces and faces[0]["surface_type"] == "freeform"


def test_execute_manifold_code_meshes_a_solid() -> None:
    """End-to-end Manifold run: CSG -> mesh STL + GLB + region topology.
    Skips where manifold3d isn't installed (runs in aieng311/CI)."""
    pytest.importorskip("manifold3d")
    pytest.importorskip("trimesh")
    from app.cad_generation import _execute_manifold_code
    src = (
        "from manifold3d import Manifold\n"
        "result = Manifold.sphere(10.0) - Manifold.cube((6.0, 6.0, 6.0), True).translate((0, 0, 6))\n"
    )
    stl, glb, topo = _execute_manifold_code(src, timeout=120)
    assert len(stl) > 0 and len(glb) > 0
    assert topo["metadata"]["extractor"] == "ManifoldRunner"
    assert topo["metadata"]["representation"] == "manifold_mesh"
    faces = [e for e in topo["entities"] if e.get("type") == "face"]
    assert faces and faces[0]["surface_type"] == "mesh_region"


def test_execute_nurbs_source_builds_brep_face() -> None:
    """nurbs_brep compiles to build123d+OCP source that builds a real B-Rep NURBS
    face (surface_type 'bspline') and exports STEP. Runs in aieng311 (build123d+OCP)."""
    pytest.importorskip("build123d")
    from aieng.converters.shape_ir_nurbs import compile_shape_ir_to_nurbs_source
    from app.cad_generation import _execute_build123d_code

    payload = {"representation": "nurbs_brep", "parts": [
        {"id": "patch", "type": "nurbs_surface", "control_net": [
            [[0, 0, 0], [10, 0, 5], [20, 0, 0]],
            [[0, 10, 5], [10, 10, -4], [20, 10, 5]],
            [[0, 20, 0], [10, 20, 5], [20, 20, 0]],
        ]},
    ]}
    src = compile_shape_ir_to_nurbs_source(payload)
    step, _stl, _glb, topo, _mesh_meta = _execute_build123d_code(src, timeout=120)
    assert len(step) > 0
    faces = [e for e in topo.get("entities", []) if e.get("type") == "face"]
    assert faces, "NURBS surface must yield at least one B-Rep face"
    assert any(f.get("surface_type") == "bspline" for f in faces)


# ── cad.search_reference_image (Wikimedia Commons) ───────────────────────────

def _fake_urlopen_json(payload: dict) -> MagicMock:
    """Build a urlopen() return value usable as a context manager yielding JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def test_search_wikimedia_images_filters_svg_and_ranks_by_index() -> None:
    from app import cad_generation as cg

    api_payload = {
        "query": {
            "pages": {
                "20": {  # higher index -> ranked second
                    "title": "File:Plane B.jpg",
                    "index": 2,
                    "imageinfo": [{
                        "mime": "image/jpeg",
                        "thumburl": "https://upload.wikimedia.org/b_thumb.jpg",
                        "url": "https://upload.wikimedia.org/b_full.jpg",
                        "thumbwidth": 1024, "thumbheight": 600,
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plane_B.jpg",
                    }],
                },
                "10": {  # lower index -> ranked first
                    "title": "File:Plane A.png",
                    "index": 1,
                    "imageinfo": [{
                        "mime": "image/png",
                        "thumburl": "https://upload.wikimedia.org/a_thumb.png",
                        "url": "https://upload.wikimedia.org/a_full.png",
                        "thumbwidth": 1024, "thumbheight": 700,
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plane_A.png",
                    }],
                },
                "30": {  # SVG must be filtered out
                    "title": "File:Plane C.svg",
                    "index": 3,
                    "imageinfo": [{
                        "mime": "image/svg+xml",
                        "url": "https://upload.wikimedia.org/c.svg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Plane_C.svg",
                    }],
                },
            }
        }
    }
    with patch("urllib.request.urlopen", return_value=_fake_urlopen_json(api_payload)):
        candidates = cg._search_wikimedia_images("airplane")

    assert [c["title"] for c in candidates] == ["File:Plane A.png", "File:Plane B.jpg"]
    assert candidates[0]["url"] == "https://upload.wikimedia.org/a_thumb.png"  # thumb preferred
    assert candidates[0]["page_url"].endswith("File:Plane_A.png")


def test_search_reference_image_attaches_best_match() -> None:
    from app import cad_generation as cg

    candidates = [
        {"title": "File:A.jpg", "url": "https://x/a.jpg", "width": 1024, "height": 700,
         "mime": "image/jpeg", "page_url": "https://commons.wikimedia.org/wiki/File:A.jpg"},
    ]
    attach_result = {"status": "ok", "width": 800, "height": 547, "byte_size_kb": 42.0}
    with (
        patch.object(cg, "_search_wikimedia_images", return_value=candidates),
        patch.object(cg, "set_reference_image", return_value=attach_result) as mock_set,
    ):
        out = cg.search_reference_image(None, "proj1", {"query": "Boeing 747 side view"})

    assert out["status"] == "ok"
    assert out["attached"] is True
    assert out["matched_url"] == "https://x/a.jpg"
    assert out["page_url"].endswith("File:A.jpg")
    assert out["byte_size_kb"] == 42.0
    assert out["candidates_considered"] == 1
    # Delegates the fetch/store to set_reference_image with the resolved URL.
    assert mock_set.call_args.args[2]["image_url"] == "https://x/a.jpg"


def test_search_reference_image_falls_back_to_next_candidate() -> None:
    from app import cad_generation as cg

    candidates = [
        {"title": "File:Dead.jpg", "url": "https://x/dead.jpg", "page_url": "p1"},
        {"title": "File:Good.jpg", "url": "https://x/good.jpg", "page_url": "p2"},
    ]
    results = [
        {"status": "error", "code": "fetch_failed", "message": "404"},
        {"status": "ok", "width": 800, "height": 600, "byte_size_kb": 30.0},
    ]
    with (
        patch.object(cg, "_search_wikimedia_images", return_value=candidates),
        patch.object(cg, "set_reference_image", side_effect=results),
    ):
        out = cg.search_reference_image(None, "proj1", {"query": "thing"})

    assert out["status"] == "ok"
    assert out["matched_url"] == "https://x/good.jpg"


def test_search_reference_image_no_results() -> None:
    from app import cad_generation as cg

    with patch.object(cg, "_search_wikimedia_images", return_value=[]):
        out = cg.search_reference_image(None, "proj1", {"query": "asdfqwerty nonexistent"})
    assert out["status"] == "no_results"
    assert "proceed without a reference" in out["message"].lower()


def test_search_reference_image_search_failure_is_graceful() -> None:
    from app import cad_generation as cg

    with patch.object(cg, "_search_wikimedia_images", side_effect=OSError("network down")):
        out = cg.search_reference_image(None, "proj1", {"query": "thing"})
    assert out["status"] == "error"
    assert out["code"] == "search_failed"


def test_search_reference_image_validates_input() -> None:
    from app import cad_generation as cg

    assert cg.search_reference_image(None, "p", {"query": "   "})["code"] == "missing_query"
    bad_source = cg.search_reference_image(None, "p", {"query": "x", "source": "bing"})
    assert bad_source["code"] == "unsupported_source"


# ── cad.design_review (critique + structure + fix targets) ───────────────────

def test_design_review_symmetry_findings_from_report() -> None:
    from app import cad_generation as cg

    report = {
        "symmetry": [
            {"pair": ["arm_L", "arm_R"], "ok": False, "mirror_axis": "x", "align_residual_mm": 12.0},
            {"pair": ["leg_L", "leg_R"], "ok": True, "mirror_axis": "x"},  # ignored
            {"part": "ear_L", "expected_partner": "ear_R", "status": "missing_partner"},
        ]
    }
    findings = cg._symmetry_findings(report)
    rules = [f["rule"] for f in findings]
    assert rules == ["broken_symmetry", "broken_symmetry"]  # ok==True pair skipped
    assert all(f["severity"] == "medium" and f["category"] == "structure" for f in findings)
    assert "arm_L / arm_R" in findings[0]["feature"]
    assert "ear_R" in findings[1]["observation"]


def test_design_review_spatial_findings_from_report() -> None:
    from app import cad_generation as cg

    report = {
        "spatial_relationships": [
            {
                "parts": ["shaft", "housing"],
                "status": "deep_overlap",
                "overlap_ratio_of_smaller": 0.8,
            },
            {
                "parts": ["bearing", "housing"],
                "status": "contained_in_hollow",
                "relationship": "housing contains bearing",
            },
            {
                "parts": ["insert", "block"],
                "status": "contained",
                "relationship": "block contains insert",
            },
        ]
    }

    findings = cg._spatial_findings(report)

    rules = [f["rule"] for f in findings]
    assert rules == ["bbox_interference", "contained_part"]
    assert findings[0]["severity"] == "high"
    assert findings[1]["severity"] == "medium"


def _param_entry(cad_name: str, tokens: set[str], **kw) -> dict:
    base = {
        "feature_id": kw.get("feature_id", "f1"), "feature_name": "global_params",
        "feature_type": "global_params", "scope": "global",
        "parameter_name": kw.get("parameter_name", "thickness_mm"),
        "cad_parameter_name": cad_name, "current_value": kw.get("current_value", 2.0),
        "min_value": 1.0, "max_value": 20.0, "search_tokens": sorted(tokens),
    }
    return base


def test_bind_finding_to_parameter_unique_match() -> None:
    from app import cad_generation as cg

    index = [_param_entry("WALL_THICKNESS", {"wall", "thickness"})]
    finding = {"feature": "wall_front", "rule": "min_wall_thickness"}
    target = cg._bind_finding_to_parameter(finding, index)
    assert target["known"] is True
    assert target["cad_parameter_name"] == "WALL_THICKNESS"
    assert target["min_value"] == 1.0 and target["max_value"] == 20.0


def test_bind_finding_to_parameter_ambiguous_lists_candidates() -> None:
    from app import cad_generation as cg

    index = [
        _param_entry("WALL_THICKNESS", {"wall", "thickness"}, feature_id="a", parameter_name="p1"),
        _param_entry("WALL_THICKNESS_2", {"wall", "thickness"}, feature_id="b", parameter_name="p2"),
    ]
    finding = {"feature": "wall", "rule": "min_wall_thickness"}
    target = cg._bind_finding_to_parameter(finding, index)
    assert target["known"] is False
    assert len(target["candidates"]) == 2


def test_bind_finding_to_parameter_no_index_or_no_overlap_is_none() -> None:
    from app import cad_generation as cg

    assert cg._bind_finding_to_parameter({"feature": "wall", "rule": "x"}, None) is None
    index = [_param_entry("BOLT_CIRCLE_DIA", {"bolt", "circle", "dia"})]
    assert cg._bind_finding_to_parameter({"feature": "rib_main", "rule": "min_wall_thickness"}, index) is None


def test_design_review_merges_critique_and_respects_detail(tmp_path: Path) -> None:
    from app import cad_generation as cg

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "review-merge")  # no geometry → no package
    fake_crit = {
        "status": "ok", "verdict": "passes_with_notes",
        "findings": [{"id": "find_001", "severity": "low", "category": "manufacturing_rule",
                      "rule": "standard_hole_size", "feature": "h1", "feature_id": "b1",
                      "observation": "non-standard hole", "suggested_fix": "round it"}],
    }
    with patch.object(cg, "critique", return_value=fake_crit):
        full = cg.design_review(settings, pid, {})
        compact = cg.design_review(settings, pid, {"response_detail": "compact"})

    assert full["verdict"] == "passes_with_notes"
    assert full["critique_verdict"] == "passes_with_notes"
    assert full["summary"]["findings_count"] == 1
    assert full["summary"]["actionable_count"] == 0  # no feature graph → no bound target
    assert "findings" in full and "findings" not in compact  # compact omits the full list
    assert "actions" in compact and "recommendation" in compact


def test_design_review_propagates_critique_error(tmp_path: Path) -> None:
    from app import cad_generation as cg

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "review-err")
    with patch.object(cg, "critique", return_value={"status": "error", "code": "no_package"}):
        out = cg.design_review(settings, pid, {})
    assert out["status"] == "error" and out["code"] == "no_package"


def test_design_review_end_to_end_binds_target_and_flags_symmetry(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import design_review, execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "review-e2e")
    # Thin wall (2mm < 3mm CNC min) with an editable WALL_THICKNESS constant,
    # plus an asymmetric arm pair (different Z extents).
    code = (
        "from build123d import *\n"
        "WALL_THICKNESS = 2\n"
        "wall = Box(40, 40, WALL_THICKNESS); wall.label = 'wall_front'\n"
        "arm_l = Box(5, 5, 40).moved(Location((-30, 0, 0))); arm_l.label = 'arm_L'\n"
        "arm_r = Box(5, 5, 20).moved(Location((30, 0, 0))); arm_r.label = 'arm_R'\n"
        "result = Compound(children=[wall, arm_l, arm_r])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"

    review = design_review(settings, pid, {})
    assert review["status"] == "ok"
    # thin wall is a high-severity manufacturing finding → fails the audit
    assert review["verdict"] == "fails_audit"
    # symmetry check (which plain critique lacks) flags the mismatched arm pair
    assert any("arm_L" in s for s in review["summary"]["broken_symmetry"])
    # the thin-wall finding is bound to the concrete editable constant
    wall_actions = [
        a for a in review["actions"]
        if a.get("parameter_target", {}).get("cad_parameter_name") == "WALL_THICKNESS"
    ]
    assert wall_actions, "thin-wall finding should bind to WALL_THICKNESS"
    assert wall_actions[0]["parameter_target"]["known"] is True


# ── domain primitives (naca_airfoil / fuselage_profile / wheel / ribbed_plate) ─

@pytest.mark.parametrize("snippet", [
    "result = naca_airfoil(120, 14, span=200, label='wing')",
    "result = fuselage_profile(400, 60, label='fuselage')",
    "result = wheel(30, 8, 20, label='wheel')",
    "result = ribbed_plate(120, 80, 6, rib_count=3, label='panel')",
])
def test_domain_primitive_builds_valid_solid(tmp_path: Path, snippet: str) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "primitive")
    code = "from build123d import *\n" + snippet + "\n"
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    # a real solid with faces was produced (the helper geometry is valid)
    assert out["topology_summary"]["bounding_box"] is not None
    assert out["topology_summary"]["face_count"] > 0


@pytest.mark.parametrize("snippet,part_label", [
    ("result = tube(12, 8, 40, label='bushing')", "bushing"),
    ("result = tube(10, 6, 30, axis='X', label='sleeve')", "sleeve"),
    ("result = hex_prism(13, 8, label='nut_blank')", "nut_blank"),
    ("result = chamfered_box(60, 40, 20, 4, label='housing')", "housing"),
    ("result = chamfered_box(60, 40, 20, 3, edges='vertical', label='housing')", "housing"),
    ("result = l_bracket(80, 40, 60, 6, label='bracket')", "bracket"),
    ("result = l_bracket(80, 40, 60, 6, fillet_radius=4, label='bracket')", "bracket"),
])
def test_mechanical_helper_builds_named_part(tmp_path: Path, snippet: str, part_label: str) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "mech-helper")
    code = "from build123d import *\n" + snippet + "\n"
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    assert out["topology_summary"]["bounding_box"] is not None
    assert out["topology_summary"]["face_count"] > 0
    assert part_label in out["named_parts"], out["named_parts"]


def test_tube_is_hollow(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "tube-hollow")
    # A hollow tube has an inner + outer cylindrical wall → more faces than a solid rod.
    solid = execute_build123d_code(
        settings, _make_project(settings, "rod"),
        {"code": "from build123d import *\nresult = Cylinder(12, 40); result.label='rod'\n", "thumbnail": False},
    )
    hollow = execute_build123d_code(
        settings, pid,
        {"code": "from build123d import *\nresult = tube(12, 8, 40, label='bushing')\n", "thumbnail": False},
    )
    assert hollow["status"] == "ok" and solid["status"] == "ok"
    assert hollow["topology_summary"]["face_count"] > solid["topology_summary"]["face_count"]


def test_invalid_tube_radii_is_a_clean_error(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "tube-bad")
    out = execute_build123d_code(
        settings, pid,
        {"code": "from build123d import *\nresult = tube(8, 12, 40, label='bad')\n", "thumbnail": False},
    )
    # inner >= outer raises ValueError inside the runner → surfaced as an error, not a crash.
    assert out["status"] == "error"


def test_fuselage_primitive_marks_model_organic(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "organic-primitive")
    code = "from build123d import *\nresult = fuselage_profile(400, 60, label='fuselage')\n"
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok", out
    # organic helper hint → mechanical heuristics are skipped
    assert out["feature_graph"]["model_kind"] == "organic"


# ── category starter templates produce code that actually builds ─────────────

@pytest.mark.parametrize("message,expect_parts", [
    ("model an airplane, fuselage length 500mm", {"fuselage", "wing", "tail_fin"}),
    ("design a car body 420x180x140mm", {"body", "wheel_1", "wheel_4"}),
    ("make a 200mm pulley wheel", {"wheel"}),
])
def test_category_template_code_builds(tmp_path: Path, message: str, expect_parts: set) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code
    from app.cad_skill_planner import plan_build123d_skill

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "starter")
    plan = plan_build123d_skill({"project_id": pid, "message": message})
    assert plan["status"] == "ready", plan
    exec_input = plan["proposed_input"]
    out = execute_build123d_code(settings, pid, {"code": exec_input["code"], "thumbnail": False})
    assert out["status"] == "ok", out
    assert expect_parts.issubset(set(out["named_parts"])), (expect_parts, out["named_parts"])
    # organic family → mechanical heuristics skipped
    assert out["feature_graph"]["model_kind"] == "organic"


def test_execute_response_always_includes_geometry_report_summary(tmp_path: Path) -> None:
    """geometry_report_summary is a stable one-liner in BOTH detail modes."""
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "summary-contract")
    code = "from build123d import *\nbody = Box(40, 40, 10); body.label='base'\nresult = Compound(children=[body])\n"

    full = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False, "response_detail": "full"})
    compact = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False, "response_detail": "compact"})
    for out in (full, compact):
        assert out["status"] == "ok", out
        assert isinstance(out["geometry_report_summary"], str)
        assert out["geometry_report_summary"].startswith("geometry:")
    # full mode keeps the rich dict under geometry_report; compact collapses it to the summary
    assert isinstance(full["geometry_report"], dict)
    assert isinstance(compact["geometry_report"], str)


# ── cache hardening tests (#25) ──────────────────────────────────────────────


def test_concurrent_cache_writes_produce_one_valid_entry(tmp_path: Path) -> None:
    """Two threads writing the same cache key must produce exactly one valid entry."""
    from concurrent.futures import ThreadPoolExecutor
    from app import cad_generation

    settings = _make_settings(tmp_path)
    code = "from build123d import *\nresult = Box(1, 1, 1)"
    cache_key, _ = cad_generation._build123d_cache_key(
        code=code, mode="replace", model_kind="auto",
    )
    calls = 0

    def _counting_executor(c, timeout=60):
        nonlocal calls
        calls += 1
        return _FAKE_STEP, _FAKE_STL, _FAKE_GLB, dict(_SAMPLE_TOPOLOGY), None

    def _write_once():
        with patch("app.cad_generation._execute_build123d_code", side_effect=_counting_executor):
            return cad_generation._execute_build123d_cached(
                settings, code, mode="replace", model_kind="auto",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_write_once)
        f2 = pool.submit(_write_once)
        r1 = f1.result(timeout=30)
        r2 = f2.result(timeout=30)

    assert "step_bytes" in r1
    assert "step_bytes" in r2
    # The underlying executor should have been invoked exactly once.
    assert calls == 1
    # Both threads must receive the same cached result (cache coherence).
    assert r1["step_bytes"] == r2["step_bytes"]
    assert r1["topo"] == r2["topo"]
    # The cache entry must be complete and readable.
    cached = cad_generation._read_build123d_cache(settings, cache_key)
    assert cached is not None
    assert cached["step_bytes"] == _FAKE_STEP


def test_corrupt_cache_entry_removed_on_read(tmp_path: Path) -> None:
    """A cache entry missing required files is treated as a miss and removed."""
    from app import cad_generation

    settings = _make_settings(tmp_path)
    code = "from build123d import *\nresult = Box(2, 2, 2)"
    cache_key, _ = cad_generation._build123d_cache_key(
        code=code, mode="replace", model_kind="auto",
    )
    root = cad_generation._build123d_cache_root(settings)
    entry = root / cache_key
    entry.mkdir(parents=True, exist_ok=True)
    # Write only metadata and .complete — missing generated.step, topology_map, etc.
    (entry / "metadata.json").write_text(
        json.dumps({"cache_key": cache_key, "cache_format_version": "1"}), encoding="utf-8",
    )
    (entry / ".complete").write_text("ok", encoding="utf-8")

    assert entry.exists()
    cached = cad_generation._read_build123d_cache(settings, cache_key)
    assert cached is None
    assert not entry.exists()


def test_cache_integrity_wrong_key_removed(tmp_path: Path) -> None:
    """A cache entry whose metadata cache_key does not match the directory name is removed."""
    from app import cad_generation

    settings = _make_settings(tmp_path)
    code = "from build123d import *\nresult = Box(3, 3, 3)"
    cache_key, _ = cad_generation._build123d_cache_key(
        code=code, mode="replace", model_kind="auto",
    )
    root = cad_generation._build123d_cache_root(settings)
    entry = root / cache_key
    entry.mkdir(parents=True, exist_ok=True)
    for name in cad_generation._CACHE_REQUIRED_FILES:
        (entry / name).write_text("{}", encoding="utf-8")
    (entry / ".complete").write_text("ok", encoding="utf-8")
    # Tamper with metadata cache_key
    (entry / "metadata.json").write_text(
        json.dumps({"cache_key": "wrong-key", "cache_format_version": "1"}), encoding="utf-8",
    )

    assert entry.exists()
    cached = cad_generation._read_build123d_cache(settings, cache_key)
    assert cached is None
    assert not entry.exists()


def test_cache_env_limits_override_defaults(tmp_path: Path) -> None:
    """AIENG_CACHE_MAX_ENTRIES and AIENG_CACHE_MAX_BYTES are read from env at import time."""
    import subprocess
    import sys

    script = '''
import os, sys
sys.path.insert(0, r"''' + str(_WORKSPACE_ROOT / "aieng-ui" / "backend") + '''")
os.environ["AIENG_CACHE_MAX_ENTRIES"] = "8"
os.environ["AIENG_CACHE_MAX_BYTES"] = "1048576"
from app.cad_generation import _BUILD123D_CACHE_MAX_ENTRIES, _BUILD123D_CACHE_MAX_BYTES
print(_BUILD123D_CACHE_MAX_ENTRIES, _BUILD123D_CACHE_MAX_BYTES)
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    entries, bytes_val = result.stdout.strip().split()
    assert int(entries) == 8
    assert int(bytes_val) == 1048576


def test_mutation_path_uses_shared_cache(tmp_path: Path) -> None:
    """_execute_build123d_cached is used by edit/remove/replace and hits on repeats."""
    from app import cad_generation

    settings = _make_settings(tmp_path)
    code = "from build123d import *\nresult = Box(1,1,1)"
    calls = 0

    def _counting_executor(c, timeout=60):
        nonlocal calls
        calls += 1
        return _FAKE_STEP, _FAKE_STL, _FAKE_GLB, dict(_SAMPLE_TOPOLOGY), None

    with patch("app.cad_generation._execute_build123d_code", side_effect=_counting_executor):
        r1 = cad_generation._execute_build123d_cached(settings, code)
        r2 = cad_generation._execute_build123d_cached(settings, code)

    assert r1["cache_hit"] is False
    assert r2["cache_hit"] is True
    assert calls == 1


def test_remove_part_cache_hit_on_repeated_identical_remove(tmp_path: Path) -> None:
    """Repeated identical remove_build123d_part hits the internal cache."""
    from app import cad_generation

    settings = _make_settings(tmp_path)
    code = "from build123d import *\nbody = Box(10,10,10); body.label='body'\nresult = Compound(children=[body])"
    modified = code + cad_generation._REMOVE_PART_SNIPPET.format(label="body")
    calls = 0

    def _counting_executor(c, timeout=60):
        nonlocal calls
        calls += 1
        return _FAKE_STEP, _FAKE_STL, _FAKE_GLB, dict(_SAMPLE_TOPOLOGY), None

    with patch("app.cad_generation._execute_build123d_code", side_effect=_counting_executor):
        r1 = cad_generation._execute_build123d_cached(settings, modified)
        r2 = cad_generation._execute_build123d_cached(settings, modified)

    assert r1["cache_hit"] is False
    assert r2["cache_hit"] is True
    assert calls == 1


# ── append-step-cache spike (#91) ─────────────────────────────────────────────

def test_step_roundtrip_preserves_labels() -> None:
    """Sanity check for the STEP roundtrip probe; skips when build123d absent."""
    pytest.importorskip("build123d")
    from app.cad_generation import _step_roundtrip_preserves_labels

    result = _step_roundtrip_preserves_labels()
    assert isinstance(result, bool)


def test_build_face_colors_from_mesh_meta_fallback() -> None:
    """Fallback mesh_meta tints the whole mesh with the first available body color."""
    import numpy as np
    from app.cad_generation import _build_face_colors_from_mesh_meta

    mesh_meta = {
        "fallback": True,
        "total_triangles": 10,
        "bodies": [
            {"body_id": "body_001", "name": "block", "color": [1.0, 0.0, 0.0], "triangle_count": 0},
            {"body_id": "body_002", "name": "pin", "color": None, "triangle_count": 0},
        ],
    }
    colors = _build_face_colors_from_mesh_meta(mesh_meta)
    assert colors is not None
    assert colors.shape == (10, 3)
    np.testing.assert_allclose(colors, np.tile([1.0, 0.0, 0.0], (10, 1)))

    # No available color → no fallback array.
    mesh_meta_no_color = {
        "fallback": True,
        "total_triangles": 10,
        "bodies": [{"body_id": "body_001", "name": "block", "color": None, "triangle_count": 0}],
    }
    assert _build_face_colors_from_mesh_meta(mesh_meta_no_color) is None


def test_execute_build123d_append_step_cache_mocked(tmp_path: Path) -> None:
    """When append_step_cache=True and labels survive, execution uses STEP prefix."""
    from unittest.mock import patch

    from app.cad_generation import execute_build123d_code, read_cad_source

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "append-cache")

    # Build a base package so append has something to read.
    base = "result = Box(40, 40, 10)\n"
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream_ok),
    ):
        execute_build123d_code(settings, pid, {"code": base, "thumbnail": False})

    captured_code: str | None = None

    def _capture_stream(code: str, timeout: int = 60):
        nonlocal captured_code
        captured_code = code
        yield from _fake_stream_ok(code, timeout)

    add = "result = previous_result + Cylinder(3, 30)\n"
    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._step_roundtrip_preserves_labels", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_capture_stream),
    ):
        out = execute_build123d_code(
            settings,
            pid,
            {"code": add, "mode": "append", "thumbnail": False, "append_step_cache": True},
        )

    assert out["status"] == "ok"
    assert captured_code is not None
    # The execution code must import the prior STEP rather than replay the full script.
    assert "_aieng_build123d.import_step" in captured_code
    # The full prior source should NOT be re-executed.
    assert "Box(40, 40, 10)" not in captured_code
    # geometry/source.py still stores the full accumulated script for reproducibility.
    source = read_cad_source(settings, pid)
    assert source["has_base"] is True
    assert "Box(40, 40, 10)" in source["source"]


def test_execute_build123d_append_step_cache_regression(tmp_path: Path) -> None:
    """append_step_cache produces identical topology/feature graph to full re-exec."""
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)

    base = (
        "from build123d import *\n"
        "body = Box(40, 40, 10); body.label = 'fuselage'\n"
        "result = Compound(children=[body])\n"
    )
    add = (
        "from build123d import *\n"
        "arm = Cylinder(3, 30); arm.label = 'motor_pod_FL'\n"
        "result = Compound(children=[previous_result, arm])\n"
    )

    # Path A: default append (full re-execution)
    pid_a = _make_project(settings, "append-default")
    execute_build123d_code(settings, pid_a, {"code": base, "thumbnail": False})
    out_a = execute_build123d_code(settings, pid_a, {"code": add, "mode": "append", "thumbnail": False})

    # Path B: append with prefix reuse
    pid_b = _make_project(settings, "append-cache")
    execute_build123d_code(settings, pid_b, {"code": base, "thumbnail": False})
    out_b = execute_build123d_code(
        settings,
        pid_b,
        {"code": add, "mode": "append", "thumbnail": False, "append_step_cache": True},
    )

    assert out_a["status"] == "ok"
    assert out_b["status"] == "ok"
    assert out_a["named_parts"] == out_b["named_parts"]
    assert out_a["parts_added"] == out_b["parts_added"]
    assert out_a["topology_summary"]["face_count"] == out_b["topology_summary"]["face_count"]
    # Feature graphs should be structurally equivalent.
    assert len(out_a["feature_graph"]["features"]) == len(out_b["feature_graph"]["features"])

def _write_baseline_package(path: Path, shape_ir: dict, topology_map: dict) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("geometry/shape_ir.json", json.dumps(shape_ir).encode())
        zf.writestr("geometry/topology_map.json", json.dumps(topology_map).encode())


def _replace_package_member(pkg_path: Path, member_name: str, data: bytes) -> None:
    """Replace an existing package member or append it if absent."""
    tmp = pkg_path.with_suffix(".replace.tmp.aieng")
    replaced = False
    with zipfile.ZipFile(pkg_path, "r") as src, zipfile.ZipFile(tmp, "w") as dst:
        for item in src.infolist():
            if item.filename == member_name:
                dst.writestr(item, data)
                replaced = True
            else:
                dst.writestr(item, src.read(item.filename))
        if not replaced:
            dst.writestr(member_name, data)
    tmp.replace(pkg_path)


def _write_conversion_manifest(pkg_path: Path) -> None:
    manifest = {
        "format": "aieng.geometry_execution_manifest",
        "geometry_execution": {
            "executed": True,
            "geometry_kind": "brep",
            "representation_kind": "brep",
        },
    }
    _replace_package_member(pkg_path, "provenance/conversion_manifest.json",
                            json.dumps(manifest).encode())


def test_make_candidate_recompiler_flags_collateral_change(tmp_path: Path) -> None:
    """A candidate whose regression_diff verdict is collateral_change is rejected."""
    from app import cad_generation

    baseline = tmp_path / "base.aieng"
    shape_ir = {"representation": "brep_build123d", "parts": [{"id": "base", "type": "box"}]}
    before_topo = {
        "entities": [
            {"id": "base", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 10.0]},
            {"id": "rib", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 10.0, 50.0, 10.0]},
        ]
    }
    after_topo = {
        "entities": [
            {"id": "base", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 10.0]},
            {"id": "rib", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 20.0, 50.0, 10.0]},
        ]
    }
    _write_baseline_package(baseline, shape_ir, before_topo)

    def fake_recompile(pkg_path: Path, **kwargs: Any) -> dict[str, Any]:
        _replace_package_member(pkg_path, "geometry/topology_map.json",
                                json.dumps(after_topo).encode())
        _write_conversion_manifest(pkg_path)
        return {"executed": True, "geometry_kind": "brep"}

    with patch("app.cad_generation.recompile_shape_ir_package", side_effect=fake_recompile):
        recompiler = cad_generation.make_candidate_recompiler(baseline)
        result = recompiler(shape_ir, {"candidate_id": "c1", "selected_part_id": "base"})

    assert result["compile_status"] == "compile_failed"
    diff = result["regression_diff"]
    assert diff["verdict"] == "collateral_change"
    assert diff["collateral_parts"] == ["rib"]
    assert any("collateral_change" in e for e in result["errors"])


def test_make_candidate_recompiler_clean_diff_passes(tmp_path: Path) -> None:
    """A candidate whose regression_diff verdict is clean keeps compile_succeeded."""
    from app import cad_generation

    baseline = tmp_path / "base.aieng"
    shape_ir = {"representation": "brep_build123d", "parts": [{"id": "base", "type": "box"}]}
    before_topo = {
        "entities": [
            {"id": "base", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 100.0, 50.0, 10.0]},
            {"id": "rib", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 10.0, 50.0, 10.0]},
        ]
    }
    after_topo = {
        "entities": [
            {"id": "base", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 120.0, 50.0, 10.0]},
            {"id": "rib", "type": "solid", "bounding_box": [0.0, 0.0, 0.0, 10.0, 50.0, 10.0]},
        ]
    }
    _write_baseline_package(baseline, shape_ir, before_topo)

    def fake_recompile(pkg_path: Path, **kwargs: Any) -> dict[str, Any]:
        _replace_package_member(pkg_path, "geometry/topology_map.json",
                                json.dumps(after_topo).encode())
        _write_conversion_manifest(pkg_path)
        return {"executed": True, "geometry_kind": "brep"}

    with patch("app.cad_generation.recompile_shape_ir_package", side_effect=fake_recompile):
        recompiler = cad_generation.make_candidate_recompiler(baseline)
        result = recompiler(shape_ir, {"candidate_id": "c1", "selected_part_id": "base"})

    assert result["compile_status"] == "compile_succeeded"
    diff = result["regression_diff"]
    assert diff["verdict"] == "clean"
    assert diff["collateral_parts"] == []


def test_mark_cae_mapping_stale_records_topology_hash(tmp_path: Path) -> None:
    from app.cad_generation import _mark_cae_mapping_stale
    from app.project_io import compute_topology_hash

    pkg = tmp_path / "stale_test.aieng"
    topology = {
        "format_version": "0.1",
        "entities": [
            {"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]},
            {"id": "face_001", "type": "face", "surface_type": "plane", "bounding_box": [0, 0, 0, 10, 10, 0]},
        ],
    }
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "0.1"}))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/cae_mapping.json", json.dumps({"mappings": []}))

    _mark_cae_mapping_stale(pkg)

    with zipfile.ZipFile(pkg, "r") as zf:
        mapping = json.loads(zf.read("simulation/cae_mapping.json"))

    assert mapping.get("stale") is True
    assert "stale_at" in mapping
    assert mapping.get("topology_hash_at_stale") == compute_topology_hash(topology)


# ── design-rule assertions as build failures (#217) — end-to-end ──────────────

def test_require_failure_is_structured_design_rule_violation(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "require-fail")
    code = (
        "from build123d import *\n"
        "WALL = 2.0\n"
        "require(WALL >= 3.0, f'wall too thin: {WALL}mm < 3mm')\n"
        "result = Box(10, 10, WALL)\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "error"
    assert out["code"] == "design_rule_violation"
    assert "wall too thin" in out["message"]
    assert out["design_rule"]["message"] == "wall too thin: 2.0mm < 3mm"


def test_require_pass_is_a_noop(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "require-pass")
    code = (
        "from build123d import *\n"
        "WALL = 5.0\n"
        "require(WALL >= 3.0, 'wall too thin')\n"
        "body = Box(10, 10, WALL); body.label = 'plate'\n"
        "result = Compound(children=[body])\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "ok"
    assert "plate" in _named_parts_in(out)


def test_bare_assert_is_first_class_design_rule(tmp_path: Path) -> None:
    pytest.importorskip("build123d")
    from app.cad_generation import execute_build123d_code

    settings = _make_settings(tmp_path)
    pid = _make_project(settings, "assert-fail")
    code = (
        "from build123d import *\n"
        "HOLE_SPACING = 4.0\n"
        "assert HOLE_SPACING >= 10.0, 'hole spacing below 2x radius'\n"
        "result = Box(10, 10, 5)\n"
    )
    out = execute_build123d_code(settings, pid, {"code": code, "thumbnail": False})
    assert out["status"] == "error"
    assert out["code"] == "design_rule_violation"
    assert "hole spacing" in out["message"]


def _named_parts_in(out: dict) -> list:
    fg = out.get("feature_graph") or {}
    names = []
    for feat in fg.get("features", []):
        nm = feat.get("name")
        if nm:
            names.append(nm)
    return names
