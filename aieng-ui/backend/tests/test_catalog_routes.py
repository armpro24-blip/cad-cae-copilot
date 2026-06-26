"""Tests for the materials catalog REST routes (#393).

These endpoints back the Material Library panel, which previously 404'd because
only the MCP tools were wired (no REST routes).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import Settings, create_app


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    return TestClient(create_app(settings))


def test_list_materials_returns_frontend_material_array(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/materials")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0, "material catalog should not be empty"

    first = data[0]
    # Frontend `Material` contract: name + category + nested properties.
    assert isinstance(first["name"], str) and first["name"]
    assert "category" in first
    props = first["properties"]
    for key in ("youngs_modulus_mpa", "poisson_ratio", "density_kg_m3", "yield_strength_mpa"):
        assert key in props


def test_list_materials_query_filter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    full = client.get("/api/materials").json()
    # Filter by a substring of a known material name; result is a subset.
    name_fragment = full[0]["name"][:3].lower()
    filtered = client.get(f"/api/materials?query={name_fragment}").json()
    assert isinstance(filtered, list)
    assert len(filtered) <= len(full)


def test_material_details_known_and_unknown(tmp_path: Path) -> None:
    client = _client(tmp_path)
    known_name = client.get("/api/materials").json()[0]["name"]

    ok = client.get(f"/api/materials/{known_name}")
    assert ok.status_code == 200
    props = ok.json()
    assert "youngs_modulus_mpa" in props
    assert "poisson_ratio" in props

    missing = client.get("/api/materials/NoSuchMaterialXYZ")
    assert missing.status_code == 404
