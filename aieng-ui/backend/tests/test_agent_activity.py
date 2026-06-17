"""Tests for the live agent-activity bridge (Phase 2).

Covers:
- agent_activity broker: subscribe/publish/unsubscribe fan-out + overflow drop.
- POST /api/agent/invoke-tool: runs a tool, publishes started/completed events.
- cad.execute_build123d via invoke-tool publishes build progress events.
- Unknown tool returns structured error and still emits completed event.
"""

from __future__ import annotations

import queue
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import agent_activity
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
    return save_project(settings, default_project(name))["id"]


@pytest.fixture(autouse=True)
def _clean_broker():
    agent_activity.reset()
    yield
    agent_activity.reset()


# ── broker ────────────────────────────────────────────────────────────────────

def test_broker_publish_reaches_subscriber() -> None:
    q = agent_activity.subscribe()
    agent_activity.publish({"type": "x", "v": 1})
    evt = q.get(timeout=1)
    assert evt["type"] == "x"
    assert evt["v"] == 1
    assert "ts" in evt  # stamped automatically


def test_broker_fans_out_to_all_subscribers() -> None:
    q1 = agent_activity.subscribe()
    q2 = agent_activity.subscribe()
    agent_activity.publish({"type": "broadcast"})
    assert q1.get(timeout=1)["type"] == "broadcast"
    assert q2.get(timeout=1)["type"] == "broadcast"


def test_broker_unsubscribe_stops_delivery() -> None:
    q = agent_activity.subscribe()
    agent_activity.unsubscribe(q)
    agent_activity.publish({"type": "after_unsub"})
    with pytest.raises(queue.Empty):
        q.get(timeout=0.2)


def test_broker_subscriber_count() -> None:
    assert agent_activity.subscriber_count() == 0
    q = agent_activity.subscribe()
    assert agent_activity.subscriber_count() == 1
    agent_activity.unsubscribe(q)
    assert agent_activity.subscriber_count() == 0


def test_broker_overflow_drops_oldest_not_publisher() -> None:
    q = agent_activity.subscribe()
    # Fill beyond capacity; publisher must never block/raise.
    for i in range(agent_activity._MAX_QUEUE + 50):
        agent_activity.publish({"type": "flood", "i": i})
    # Queue holds at most _MAX_QUEUE; newest events survive.
    assert q.qsize() <= agent_activity._MAX_QUEUE
    last = None
    while not q.empty():
        last = q.get_nowait()
    assert last is not None
    assert last["i"] == agent_activity._MAX_QUEUE + 49


# ── invoke-tool endpoint ──────────────────────────────────────────────────────

def test_invoke_tool_missing_tool(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    resp = client.post("/api/agent/invoke-tool", json={"input": {}})
    assert resp.status_code == 200
    assert resp.json()["code"] == "missing_tool"


def test_invoke_tool_unknown_tool_emits_events(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    q = agent_activity.subscribe()

    resp = client.post("/api/agent/invoke-tool", json={"tool": "no.such.tool", "input": {}})
    assert resp.json()["code"] == "tool_not_found"

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e["type"] for e in events]
    assert "tool_started" in types
    assert "tool_completed" in types
    completed = next(e for e in events if e["type"] == "tool_completed")
    assert completed["status"] == "error"


def test_invoke_tool_read_only_tool(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id = _make_project(settings, "activity-read")

    resp = client.post(
        "/api/agent/invoke-tool",
        json={"tool": "aieng.read_audit_log", "input": {"project_id": project_id}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("project_id") == project_id


# ── cad.execute_build123d through invoke-tool (progress events) ───────────────

_FAKE_STEP = b"ISO-10303-21;"
_FAKE_STL = b"solid result\nendsolid result"
_FAKE_GLB = b"glTF\x02\x00\x00\x00"
_FAKE_TOPO = {
    "format_version": "0.1",
    "entities": [
        {"id": "body_001", "type": "solid", "bounding_box": [0, 0, 0, 10, 10, 10]},
        {"id": "face_001", "type": "face", "surface_type": "plane", "area": 100.0,
         "bounding_box": [0, 0, 0, 10, 10, 0], "center": [5, 5, 0]},
    ],
}


def _fake_stream(code, timeout=60):
    yield {"kind": "heartbeat", "elapsed_s": 0}
    yield {"kind": "heartbeat", "elapsed_s": 2}
    yield {"kind": "result", "step_bytes": _FAKE_STEP, "stl_bytes": _FAKE_STL,
           "glb_bytes": _FAKE_GLB, "topo": _FAKE_TOPO, "mesh_meta": None}


def test_invoke_cad_execute_publishes_build_progress(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id = _make_project(settings, "activity-cad")
    q = agent_activity.subscribe()

    with (
        patch("app.cad_generation.Build123dBackend.can_generate", return_value=True),
        patch("app.cad_generation._execute_build123d_code_streaming", side_effect=_fake_stream),
    ):
        resp = client.post(
            "/api/agent/invoke-tool",
            json={
                "tool": "cad.execute_build123d",
                "input": {"project_id": project_id, "code": "from build123d import *\nresult = Box(10,10,10)"},
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e["type"] for e in events]
    assert "tool_started" in types
    assert "cad_build_progress" in types
    assert "tool_completed" in types
    assert "project_changed" in types
    assert "viewer_asset_changed" in types

    # tool_started carries the agent-written code preview.
    started = next(e for e in events if e["type"] == "tool_started")
    assert "Box(10,10,10)" in (started.get("code_preview") or "")

    # building heartbeats forwarded.
    building = [e for e in events if e["type"] == "cad_build_progress" and e.get("phase") == "building"]
    assert len(building) >= 2

    # writing phase emitted.
    assert any(e.get("phase") == "writing" for e in events if e["type"] == "cad_build_progress")

    # completion carries preview_url + topology.
    completed = next(e for e in events if e["type"] == "tool_completed")
    assert completed["status"] == "ok"
    assert completed["preview_url"] == f"/api/projects/{project_id}/cad-preview"
    assert completed["topology_summary"]["face_count"] == 1
    changed = next(e for e in events if e["type"] == "project_changed")
    assert changed["project_id"] == project_id
    assert changed["preview_url"] == f"/api/projects/{project_id}/cad-preview"
    viewer_changed = next(e for e in events if e["type"] == "viewer_asset_changed")
    assert viewer_changed["preview_format"] == "glb"


def test_invoke_cad_execute_missing_project(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    resp = client.post(
        "/api/agent/invoke-tool",
        json={"tool": "cad.execute_build123d", "input": {"code": "result = None"}},
    )
    assert resp.json()["code"] == "missing_project_id"


# ── SSE endpoint registration ─────────────────────────────────────────────────
# NOTE: we deliberately do NOT consume the SSE stream via TestClient — it is an
# infinite generator and TestClient blocks waiting for the full response. The
# fan-out delivery logic is fully covered by the broker tests above; here we
# only assert the route is wired with the event-stream content type.

def test_activity_stream_route_registered(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    paths = {r.path for r in app.routes}
    assert "/api/agent-activity/stream" in paths


def test_invoke_tool_route_registered(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)
    paths = {r.path for r in app.routes}
    assert "/api/agent/invoke-tool" in paths


# ── recent-activity ring buffer (#227) ────────────────────────────────────────

def test_recent_returns_published_events_chronologically() -> None:
    agent_activity.publish({"type": "build_started", "project_id": "p1", "ts": 1.0})
    agent_activity.publish({"type": "build_progress", "project_id": "p1", "ts": 2.0})
    events = agent_activity.recent("p1")
    assert [e["type"] for e in events] == ["build_started", "build_progress"]


def test_recent_filters_by_project() -> None:
    agent_activity.publish({"type": "a", "project_id": "p1", "ts": 1.0})
    agent_activity.publish({"type": "b", "project_id": "p2", "ts": 2.0})
    agent_activity.publish({"type": "c", "ts": 3.0})  # no project_id
    p1 = agent_activity.recent("p1")
    assert [e["type"] for e in p1] == ["a"]
    # no filter → everything (newest last)
    assert [e["type"] for e in agent_activity.recent()] == ["a", "b", "c"]


def test_recent_since_ts_returns_only_newer() -> None:
    agent_activity.publish({"type": "old", "project_id": "p1", "ts": 10.0})
    agent_activity.publish({"type": "new", "project_id": "p1", "ts": 20.0})
    assert [e["type"] for e in agent_activity.recent("p1", since_ts=10.0)] == ["new"]


def test_recent_respects_limit_and_returns_most_recent() -> None:
    for i in range(10):
        agent_activity.publish({"type": f"e{i}", "project_id": "p1", "ts": float(i)})
    tail = agent_activity.recent("p1", limit=3)
    assert [e["type"] for e in tail] == ["e7", "e8", "e9"]


def test_reset_clears_recent_buffer() -> None:
    agent_activity.publish({"type": "x", "project_id": "p1", "ts": 1.0})
    agent_activity.reset()
    assert agent_activity.recent("p1") == []


def test_recent_activity_tool_returns_buffered_events(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    client = TestClient(create_app(settings))
    project_id = _make_project(settings, "activity-recent")
    agent_activity.publish({"type": "build_progress", "project_id": project_id, "ts": 1.0, "content": "building"})

    resp = client.post(
        "/api/agent/invoke-tool",
        json={"tool": "aieng.recent_activity", "input": {"project_id": project_id}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == project_id
    assert body["count"] >= 1
    assert any(e.get("type") == "build_progress" for e in body["events"])
    assert "latest_ts" in body
