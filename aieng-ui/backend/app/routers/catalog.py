"""Materials catalog REST routes for the frontend Material Library panel (#393).

The Material Library panel calls ``GET /api/materials`` and
``GET /api/materials/{name}``, but those routes were never registered — only the
MCP tools (``list_materials`` / ``get_material_details``) and the
``materials_bridge`` existed. The panel therefore always 404'd. These endpoints
bridge ``materials_bridge`` to the frontend ``Material`` / ``MaterialProperties``
contracts (properties nested under ``properties``).

Read-only: no project state, no solver, no mutation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

LOGGER = logging.getLogger("app.app_factory")


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


def register_catalog_routes(app: FastAPI, *, active_settings: Any) -> None:
    """Register the read-only materials catalog endpoints."""

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
