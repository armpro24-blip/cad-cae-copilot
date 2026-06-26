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


def test_compare_materials_returns_comparison_shape(tmp_path: Path) -> None:
    client = _client(tmp_path)
    names = [m["name"] for m in client.get("/api/materials").json()[:2]]
    resp = client.post("/api/materials/compare", json={"names": names})
    assert resp.status_code == 200
    data = resp.json()
    assert [m["name"] for m in data["materials"]] == names
    assert isinstance(data["differences"], list) and len(data["differences"]) > 0
    diff = data["differences"][0]
    assert "property" in diff and "values" in diff
    assert set(diff["values"].keys()) == set(names)

    # fewer than 2 → 400; unknown material → 404
    assert client.post("/api/materials/compare", json={"names": names[:1]}).status_code == 400
    assert client.post("/api/materials/compare", json={"names": ["X", "NoSuchY"]}).status_code == 404


def test_list_standard_parts_returns_category_tree(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/standards/parts")
    assert resp.status_code == 200
    cats = resp.json()
    assert isinstance(cats, list) and len(cats) > 0
    cat = cats[0]
    assert {"id", "displayName", "partTypes"} <= set(cat)
    assert isinstance(cat["partTypes"], list) and len(cat["partTypes"]) > 0
    pt = cat["partTypes"][0]
    assert {"name", "displayName", "category", "editableParameters"} <= set(pt)
    assert isinstance(pt["editableParameters"], list)


def test_standard_part_specs_known_and_unknown(tmp_path: Path) -> None:
    client = _client(tmp_path)
    ok = client.get("/api/standards/parts/hex_bolt/specs")
    assert ok.status_code == 200
    spec = ok.json()
    assert spec["partType"] == "hex_bolt"
    assert isinstance(spec["presets"], list) and len(spec["presets"]) > 0
    assert all({"name", "parameters"} <= set(p) for p in spec["presets"])
    # numeric defaults only
    assert all(isinstance(v, (int, float)) for v in spec["defaultParameters"].values())

    assert client.get("/api/standards/parts/no_such_part/specs").status_code == 404


def test_insert_standard_part_endpoint_is_wired(tmp_path: Path) -> None:
    """The insert route is registered and validates its payload. (The full
    Shape-IR insert path is covered via the standards_bridge tests.)"""
    client = _client(tmp_path)
    # Missing part_type → 400 (route exists and validates, not a 404).
    resp = client.post("/api/projects/anyproj/standards/insert", json={"parameters": {}})
    assert resp.status_code == 400

    # Unknown project with a valid part_type → handled (404 from project lookup),
    # not a 500 crash. The happy path is covered by the standards_bridge tests.
    resp2 = client.post(
        "/api/projects/nonexistent123/standards/insert",
        json={"part_type": "hex_bolt", "parameters": {}},
    )
    assert resp2.status_code in (200, 404)
