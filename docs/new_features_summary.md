# New Features Summary (snapshot: 2026-06)

This document is a **dated snapshot** of capabilities added in the June-2026
development cycle (materials library, standard parts, BOM). It predates the
quadratic-mesh accuracy work, GCI mesh convergence, real-solver sizing sweeps,
face-id stability, and the v0.1.0-alpha.3 release — see `AGENTS.md` and the
release notes for the current state.

---

## 1. Extended Material Database

### What changed
- Expanded from a handful of materials to **51 engineering materials** across 10 categories.
- Added full mechanical properties (Young's modulus, Poisson ratio, density, yield strength).
- Added extended properties (ultimate strength, thermal expansion) for every material.
- Added human-readable descriptions and category tags for all materials.

### Categories
| Category | Count | Example materials |
|----------|-------|-------------------|
| Aluminum Alloy | 6 | Al6061-T6, Al7075-T6, Al2024-T3 |
| Carbon / Alloy Steel | 7 | Steel-1045, Steel-4140, Tool-Steel-H13 |
| Stainless Steel | 5 | Steel-316L, Steel-17-4PH, Steel-440C |
| Titanium Alloy | 3 | Ti-6Al-4V, Ti-Grade2 |
| Copper Alloy | 4 | Cu-C11000, Cu-C36000, Brass-C360 |
| Magnesium Alloy | 2 | Mg-AZ31B, Mg-AZ91D |
| Nickel Alloy | 4 | Inconel-718, Inconel-625, Hastelloy-C276 |
| Engineering Plastic | 11 | PEEK, Nylon-PA66, ABS, PC, PTFE |
| Composite | 4 | CFRP-T300, CFRP-T700, GFRP-E-Glass |
| Other Metal | 5 | Zinc-ZA-8, Cobalt-Chrome-MP1, Cast-Iron-Grey |

### API
```python
from aieng.context.materials import (
    MATERIALS,                    # dict[str, dict[str, float]] — basic 4 props
    MATERIAL_DESCRIPTIONS,        # dict[str, str]
    MATERIAL_CATEGORIES,          # dict[str, str]
    MATERIAL_PROPERTIES,          # dict[str, dict[str, float | None]] — full 6 props
    get_material,                 # (name: str) -> dict[str, float]
    get_material_properties,      # (name: str) -> dict[str, float | None]
    list_materials_by_category,   # () -> dict[str, list[str]]
    search_materials,             # (query: str) -> list[str]
)
```

### MCP tools
```
aieng.list_materials { category: "Aluminum Alloy", query: "aerospace" }
aieng.get_material_details { material_name: "Al6061-T6" }
aieng.compare_materials { material_names: ["Al6061-T6", "Steel-316L"] }
```

---

## 2. Standard Parts Library

### What changed
- Added **17 standard part generators** producing valid Shape IR nodes.
- Organized into 5 categories: fasteners, bearings, shafts, structural profiles, holes.
- Each part carries ISO/DIN standard references, editable parameters, and preset sizes.

### Part types
| Category | Part types | Presets |
|----------|------------|---------|
| Fastener | hex_bolt, hex_nut, washer, socket_head_cap_screw, set_screw | M6–M12 metric |
| Bearing | deep_groove_ball_bearing, thrust_ball_bearing | 6200–6205, 51100–51105 |
| Shaft | stepped_shaft, splined_shaft | — |
| Structural profile | angle_profile, channel_profile, i_beam_profile, rectangular_tube, round_tube | — |
| Hole | through_hole, blind_hole, countersunk_hole, counterbored_hole, tapped_hole | — |

### API
```python
from aieng.standards import hex_bolt, hex_nut, deep_groove_ball_bearing
from aieng.standards.fasteners import METRIC_BOLT_PRESETS

bolt = hex_bolt(**METRIC_BOLT_PRESETS["M8"])
# bolt is a Shape IR node dict ready for compilation
```

### MCP tools
```
aieng.list_standard_parts { category: "fastener" }
aieng.get_standard_part_specs { part_type: "hex_bolt", preset_name: "M8" }
aieng.insert_standard_part {
  part_type: "hex_bolt",
  preset_name: "M8",
  position: [0, 0, 0],
  orientation: [0, 0, 0]
}
```

---

## 3. MCP Tool Enhancements

### New tools (8 total)
| Tool | Backend function | File |
|------|------------------|------|
| `aieng.list_materials` | `list_materials()` | `materials_bridge.py` |
| `aieng.get_material_details` | `get_material_details()` | `materials_bridge.py` |
| `aieng.compare_materials` | `compare_materials()` | `materials_bridge.py` |
| `aieng.list_standard_parts` | `list_standard_parts()` | `standards_bridge.py` |
| `aieng.get_standard_part_specs` | `get_standard_part_specs()` | `standards_bridge.py` |
| `aieng.insert_standard_part` | `insert_standard_part()` | `standards_bridge.py` |
| `aieng.batch_insert_standard_parts` | `batch_insert_standard_parts()` | `standards_bridge.py` |
| `aieng.generate_bom` | `generate_bom()` | `standards_bridge.py` |

### Additional bridge utilities
- `set_part_material()` — assign a material to a named part in the feature graph.
- `batch_insert_standard_parts()` — parallel insertion with incremental recompilation.

---

## 4. Frontend UI Components

### New React components
| Component | Purpose |
|-----------|---------|
| `MaterialLibraryPanel.tsx` | Browse, search, and compare materials |
| `MaterialCard.tsx` | Display material properties in a card |
| `StandardPartsPanel.tsx` | Browse and select standard parts |
| `StandardPartCard.tsx` | Display part specs and presets |
| `BOMPanel.tsx` | Display generated Bill of Materials |

### Type definitions
- `aieng-ui/frontend/src/types/materials.ts` — `Material`, `MaterialProperties`, `MaterialComparison`, `MaterialAssignment`
- `aieng-ui/frontend/src/types/standards.ts` — `StandardPartType`, `StandardPartPreset`, `StandardPartSpec`, `InsertResult`
- `aieng-ui/frontend/src/types/bom.ts` — `BOMItem`, `BOMData`

---

## 5. Performance Optimizations

### Cache system (`aieng/cache/`)
- **GeometryCache** — SHA256-keyed, two-tier (memory LRU + disk) cache for compiled geometry.
- **MaterialCache** — pre-loaded in-memory index for O(1) material lookups by name, category, or property range.
- **CacheMetrics** — records hits, misses, compile times, and hit rates.

### Async parallel processing (`aieng/async_utils.py`)
- `async_recompile_parts()` — recompile multiple Shape IR parts in parallel with thread-pool workers.
- `async_insert_standard_parts()` — batch-insert standard parts into a project concurrently.
- `async_generate_previews()` — generate thumbnail previews for multiple projects in parallel.

### Incremental compilation (`aieng/incremental.py`)
- **IncrementalCompiler** — compares old vs new Shape IR, identifies changed nodes, and recompiles only the modified parts.
- Unchanged nodes are retrieved from cache; changed nodes are compiled fresh.
- Produces merged topology maps and feature graphs.

---

## Quick start for developers

### Run the new integration tests
```bash
pytest aieng/tests/test_integration_materials_standards.py -v
pytest aieng/tests/test_integration_mcp_tools.py -v
pytest aieng/tests/test_integration_cache_standards.py -v
```

### Use the material cache in a script
```python
from aieng.cache.material_cache import MaterialCache

cache = MaterialCache()
print(cache.count())          # 51
print(cache.get("Al6061-T6"))
print(cache.search_by_property("density_kg_m3", max_value=3000))
```

### Generate a standard part and inspect its Shape IR
```python
from aieng.standards import hex_bolt
from aieng.standards.fasteners import METRIC_BOLT_PRESETS

bolt = hex_bolt(**METRIC_BOLT_PRESETS["M8"])
print(bolt["id"])             # hex_bolt
print(bolt["parameters"])     # {diameter: 8.0, length: 25.0, ...}
print(bolt["metadata"]["standard_reference"])  # ISO 4014 / DIN 933
```

---

## Backward compatibility

All existing APIs remain unchanged:
- `MATERIALS` and `MATERIAL_DESCRIPTIONS` dicts are still available.
- `get_material(name)` still returns `dict[str, float]` with the same 4 keys.
- `aieng.convert`, `cad.execute_build123d`, and all existing MCP tools are unaffected.
- Existing tests (`test_materials_extended.py`, `test_standards.py`, `test_geometry_cache.py`) continue to pass.
