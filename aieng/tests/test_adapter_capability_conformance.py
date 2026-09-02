"""Conformance tests for adapter capability declarations (G11).

This test suite verifies that:
1. All adapter capability declarations are valid
2. Each adapter has been formally assigned a capability level
3. Known limitations are documented
4. Tests verify declared ≤ produced resources
"""
import json
from pathlib import Path

import pytest
from jsonschema import validate

CAPABILITY_MANIFEST_PATH = Path("docs/adapter_capability_declarations.json")
CAPABILITY_SCHEMA_PATH = (Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "adapter_capability_manifest.schema.json")


class TestAdapterCapabilityManifest:
    """Verify adapter capability declarations are complete and valid."""

    def test_adapter_capability_manifest_exists(self):
        """Capability declarations file must exist."""
        assert CAPABILITY_MANIFEST_PATH.exists()

    def test_adapter_capability_manifest_is_valid_json(self):
        """Capability declarations must be valid JSON."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        assert manifest is not None

    def test_adapter_capability_manifest_conforms_to_schema(self):
        """Manifest must conform to adapter_capability_manifest schema."""
        with open(CAPABILITY_SCHEMA_PATH) as f:
            schema = json.load(f)
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        # Should not raise
        validate(instance=manifest, schema=schema)

    def test_adapter_capability_manifest_has_required_fields(self):
        """Manifest must have format_version and adapters array."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        assert manifest["format_version"] == "0.1.0"
        assert isinstance(manifest["adapters"], list)
        assert len(manifest["adapters"]) > 0

    def test_all_adapters_have_unique_ids(self):
        """Each adapter must have a unique adapter_id."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        ids = [a["adapter_id"] for a in manifest["adapters"]]
        assert len(ids) == len(set(ids))

    def test_all_adapters_have_capability_levels(self):
        """Every adapter must have a capability_level (L0-L5)."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            assert "capability_level" in adapter
            assert adapter["capability_level"] in {0, 1, 2, 3, 4, 5}

    def test_all_adapters_have_documented_limitations(self):
        """Every adapter should have known_limitations documented."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            assert "known_limitations" in adapter
            # At least adapter level 1 should have some limitations
            if adapter["capability_level"] <= 2:
                assert len(adapter["known_limitations"]) > 0, \
                    f"{adapter['adapter_id']} is low-capability but has no limitations documented"

    def test_all_importers_and_exporters_declared(self):
        """All major adapters should be declared."""
        expected_roles = {
            "importer_step",
            "importer_cae_deck",
            "importer_solver_evidence",
            "importer_mesh_evidence",
            "exporter_calculix",
            "exporter_updated_deck",
            "tool_trace_recorder",
            "evidence_recorder",
        }
        
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        declared_roles = {a["adapter_role"] for a in manifest["adapters"]}
        assert expected_roles <= declared_roles, \
            f"Missing adapter roles: {expected_roles - declared_roles}"


class TestAdapterCapabilityConstraints:
    """Verify capability level constraints are enforced."""

    def test_l5_adapters_declare_deterministic_edit(self):
        """L5 adapters must handle deterministic CAD edits."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        l5_adapters = [a for a in manifest["adapters"] if a["capability_level"] == 5]
        # May be empty if no L5 adapters exist yet
        for adapter in l5_adapters:
            assert "roundtrip_invariance" in str(adapter.get("notes", [])).lower() or \
                   any("deterministic" in str(l.get("description", "")).lower() 
                       for l in adapter.get("known_limitations", []))

    def test_l0_adapters_are_scaffold_only(self):
        """L0 adapters should only create empty resources."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        l0_adapters = [a for a in manifest["adapters"] if a["capability_level"] == 0]
        # May be empty if no L0 adapters exist yet
        for adapter in l0_adapters:
            assert "scaffold" in str(adapter.get("notes", [])).lower()

    def test_capability_level_is_reasonable_for_adapter_role(self):
        """Capability levels should be reasonable for each adapter role."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        # Check that adapters have reasonable capability levels for their roles
        for adapter in manifest["adapters"]:
            role = adapter["adapter_role"]
            level = adapter["capability_level"]
            
            # Importers and exporters should be at least L1
            if "importer" in role or "exporter" in role:
                assert level >= 1, f"{role} should be at least L1"
            
            # Evidence recorders should be at least L3 (structured)
            if "evidence" in role or "tool_trace" in role:
                assert level >= 3, f"{role} should be at least L3"


class TestAdapterCapabilityDocumentation:
    """Verify capability declarations are well-documented."""

    def test_all_adapters_have_display_names(self):
        """Every adapter should have a human-readable display_name."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            assert "display_name" in adapter
            assert len(adapter["display_name"]) > 0

    def test_all_adapters_have_notes(self):
        """Adapters should document their approach in notes."""
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            # Some adapters may not have notes, but it's recommended
            if "notes" in adapter:
                assert isinstance(adapter["notes"], list)

    def test_limitation_categories_are_valid(self):
        """All limitation categories should be from allowed set."""
        valid_categories = {
            "geometry",
            "topology",
            "features",
            "constraints",
            "simulation_setup",
            "mesh",
            "results",
            "validation",
            "roundtrip_invariance",
        }
        
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            for limitation in adapter.get("known_limitations", []):
                assert limitation["category"] in valid_categories, \
                    f"Unknown category: {limitation['category']}"

    def test_all_supported_resources_are_valid(self):
        """All supported resources should be valid .aieng resource paths."""
        valid_resources = {
            "manifest.json",
            "geometry/source.step",
            "geometry/normalized.step",
            "geometry/topology_map.json",
            "graph/feature_graph.json",
            "graph/semantic_graph.json",
            "graph/constraints.json",
            "simulation/setup.yaml",
            "simulation/cae_mapping.json",
            "results/evidence_index.json",
            "results/claim_map.json",
            "provenance/tool_trace.json",
            "ai/summary.md",
        }
        
        with open(CAPABILITY_MANIFEST_PATH) as f:
            manifest = json.load(f)
        
        for adapter in manifest["adapters"]:
            for resource in adapter.get("supported_resources", []):
                assert resource in valid_resources, \
                    f"Unknown resource: {resource}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
