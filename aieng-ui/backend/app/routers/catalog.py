"""Catalog REST routes for the frontend library panels (#393, #410).

The Material Library and Standard Parts panels call REST endpoints that were
never registered — only the MCP tools + bridges existed — so the panels 404'd.
These endpoints bridge ``materials_bridge`` / ``standards_bridge`` to the
frontend ``Material`` / ``MaterialProperties`` / ``MaterialComparison`` /
``StandardPartCategory`` / ``StandardPartSpec`` / ``InsertResult`` contracts.

All read-only except the standard-part insert, which goes through the same
Shape-IR-patch path as the MCP tool.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Body, FastAPI, HTTPException

LOGGER = logging.getLogger("app.app_factory")

# Display units for the material properties surfaced in the compare table.
_MATERIAL_PROP_UNITS: dict[str, str] = {
    "youngs_modulus_mpa": "MPa",
    "poisson_ratio": "",
    "density_kg_m3": "kg/m³",
    "yield_strength_mpa": "MPa",
    "ultimate_strength_mpa": "MPa",
}


def _material_view(row: dict[str, Any]) -> dict[str, Any]:
    """Reshape a ``materials_bridge.list_materials`` row to the frontend
    ``Material`` contract (mechanical properties nested under ``properties``)."""
    return {
        "name": row.get("name"),
        "category": row.get("category") or "Other",
        "description": row.get("description"),
        "properties": {
            "youngs_modulus_mpa": row.get("youngs_modulus_mpa"),
            "poisson_ratio": row.get("poisson_ratio"),
            "density_kg_m3": row.get("density_kg_m3"),
            "yield_strength_mpa": row.get("yield_strength_mpa"),
            "ultimate_strength_mpa": row.get("ultimate_strength_mpa"),
        },
    }


def _material_detail_view(name: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Reshape ``get_material_details`` to the frontend ``Material`` contract."""
    return {
        "name": name,
        "category": detail.get("category") or "Other",
        "description": detail.get("description"),
        "properties": detail.get("properties", {}),
    }


def _standard_part_type_view(part_type: str, category: str, specs: dict[str, Any]) -> dict[str, Any]:
    """Frontend ``StandardPartType`` view (camelCase)."""
    return {
        "name": part_type,
        "displayName": specs.get("standard_name") or part_type,
        "category": category,
        "description": specs.get("standard_name"),
        "standardReference": specs.get("standard_reference"),
        "editableParameters": specs.get("editable_parameters", []),
    }


def _standard_spec_view(specs: dict[str, Any]) -> dict[str, Any]:
    """Frontend ``StandardPartSpec`` view: presets as an array, numeric defaults
    from the first preset (drive_type and other non-numeric params are dropped
    so ``defaultParameters`` stays ``Record<string, number>``)."""
    presets_dict: dict[str, dict[str, Any]] = specs.get("presets", {}) or {}
    presets = [
        {"name": name, "displayName": name, "parameters": params}
        for name, params in presets_dict.items()
    ]
    default_parameters: dict[str, float] = {}
    if presets:
        default_parameters = {
            k: v for k, v in presets[0]["parameters"].items() if isinstance(v, (int, float))
        }
    return {
        "partType": specs.get("part_type"),
        "category": specs.get("category"),
        "description": specs.get("standard_name"),
        "standardReference": specs.get("standard_reference"),
        "presets": presets,
        "defaultParameters": default_parameters,
        "parameterUnits": {},
        "parameterDescriptions": None,
    }


def register_catalog_routes(app: FastAPI, *, active_settings: Any) -> None:
    """Register the materials + standard-parts catalog endpoints."""

    @app.get("/api/materials")
    def list_materials_endpoint(category: str | None = None, query: str | None = None) -> Any:
        """List engineering materials in the frontend ``Material[]`` shape.

        Optional ``category`` / ``query`` filters mirror the ``list_materials``
        MCP tool. Returns a bare array (the panel maps over it directly).
        """
        from .. import materials_bridge

        result = materials_bridge.list_materials(category=category, query=query)
        return [_material_view(m) for m in result.get("materials", [])]

    @app.get("/api/materials/{material_name}")
    def material_details_endpoint(material_name: str) -> Any:
        """Return one material's full ``MaterialProperties``; 404 if unknown."""
        from .. import materials_bridge

        result = materials_bridge.get_material_details(material_name)
        if result.get("status") != "ok":
            raise HTTPException(status_code=404, detail=result.get("message", "Material not found"))
        return result.get("properties", {})

    @app.post("/api/materials/compare")
    def compare_materials_endpoint(payload: Any = Body(...)) -> Any:
        """Compare 2+ materials in the frontend ``MaterialComparison`` shape.

        Built from ``get_material_details`` (not the ``compare_materials`` bridge,
        which has a flat-vs-nested key bug) so it stays aligned with the
        ``Material`` contract.
        """
        from .. import materials_bridge

        names = list((payload or {}).get("names") or [])
        materials: list[dict[str, Any]] = []
        for name in names:
            detail = materials_bridge.get_material_details(name)
            if detail.get("status") != "ok":
                raise HTTPException(status_code=404, detail=detail.get("message", f"Unknown material: {name}"))
            materials.append(_material_detail_view(name, detail))
        if len(materials) < 2:
            raise HTTPException(status_code=400, detail="At least 2 materials are required for comparison.")

        differences: list[dict[str, Any]] = []
        for prop, unit in _MATERIAL_PROP_UNITS.items():
            values = {m["name"]: m["properties"].get(prop) for m in materials}
            if all(v is None for v in values.values()):
                continue
            differences.append({"property": prop, "values": values, "unit": unit or None})
        return {"materials": materials, "differences": differences}

    @app.get("/api/standards/parts")
    def list_standard_parts_endpoint(category: str | None = None) -> Any:
        """List standard parts grouped into the frontend ``StandardPartCategory[]``
        shape. Each part is enriched from its spec for displayName / reference /
        editable parameters."""
        from .. import standards_bridge

        listing = standards_bridge.list_standard_parts(category=category)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for part in listing.get("parts", []):
            part_type = part.get("part_type")
            cat = part.get("category") or "other"
            specs = standards_bridge.get_standard_part_specs(part_type)
            grouped.setdefault(cat, []).append(_standard_part_type_view(part_type, cat, specs))
        return [
            {
                "id": cat,
                "displayName": cat.replace("_", " ").title(),
                "description": None,
                "partTypes": part_types,
            }
            for cat, part_types in sorted(grouped.items())
        ]

    @app.get("/api/standards/parts/{part_type}/specs")
    def standard_part_specs_endpoint(part_type: str, preset: str | None = None) -> Any:
        """Return one part type's ``StandardPartSpec``; 404 if unknown."""
        from .. import standards_bridge

        specs = standards_bridge.get_standard_part_specs(part_type, preset_name=preset)
        if specs.get("status") != "ok":
            raise HTTPException(status_code=404, detail=specs.get("message", "Part type not found"))
        return _standard_spec_view(specs)

    @app.post("/api/projects/{project_id}/standards/insert")
    def insert_standard_part_endpoint(project_id: str, payload: Any = Body(...)) -> Any:
        """Insert a standard part into the project as Shape IR; returns the
        frontend ``InsertResult`` shape. Goes through the same audited
        Shape-IR-patch path as the MCP tool."""
        from .. import standards_bridge

        part_type = (payload or {}).get("part_type")
        parameters = (payload or {}).get("parameters") or {}
        if not part_type:
            raise HTTPException(status_code=400, detail="part_type is required")

        result = standards_bridge.insert_standard_part(
            active_settings, project_id, None, part_type, parameters
        )
        if result.get("status") != "ok":
            return {
                "ok": False,
                "part_id": None,
                "message": result.get("message", "Insert failed"),
                "warnings": [],
            }
        return {
            "ok": True,
            "part_id": result.get("node_id"),
            "message": f"Inserted {result.get('part_name', part_type)}",
            "warnings": [],
        }
