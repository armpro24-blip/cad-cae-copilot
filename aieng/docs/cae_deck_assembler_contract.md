# CalculiX Deck Assembler Contract (#352)

Authoritative contract for how a CAE setup is turned into a CalculiX `.inp`
solver deck. It pins the setup-schema keys and pointer forms each assembler
reads, the unit conventions, and the physics semantics that must stay identical
across entry points — so the two historical code paths cannot drift apart in a
way that changes solver results.

## Why this document exists

There are two deck assemblers in the codebase, reached by different entry points:

1. **Legacy REST path** — `_build_calculix_deck` in
   [`aieng-ui/backend/app/simulation_runner.py`](../../aieng-ui/backend/app/simulation_runner.py).
   The proven path behind the live REST simulation. It reads `simulation/setup.yaml`
   directly, builds node sets geometrically from `geometry/topology_map.json` +
   `simulation/cae_mapping.json`, and emits the full deck in one pass.
2. **Canonical core path** — `_assemble_deck` in
   [`aieng/src/aieng/simulation/deck_generator.py`](../src/aieng/simulation/deck_generator.py),
   consumed by the MCP tool `cae.generate_solver_input`. It consumes a
   pre-synthesized *source deck* (mesh + already-bound NSETs, written by
   `ensure_source_deck_from_mesh`) and resolves materials/BCs/loads from
   `setup.yaml` + `cae_mapping.json` through dedicated `_resolve_*` helpers.

Two paths that must produce physically equivalent decks are a standing risk of
silent divergence (unit conversions, DOF mapping, force-per-node distribution).
This contract + the parity regression test
`test_legacy_and_core_static_deck_semantics_match`
([`aieng-ui/backend/tests/test_simulation_runner.py`](../../aieng-ui/backend/tests/test_simulation_runner.py))
are the guard.

**Canonical direction of travel:** the core `deck_generator` is the intended
single source of truth (it is the MCP path and owns the source-deck/NSET
synthesis from #350/#354). The remaining convergence work is to route the legacy
REST endpoint through it. Until then, this contract is what keeps them equivalent.

## Authoritative setup-schema keys

`simulation/setup.yaml` is authoritative for simulation intent (see
[`cad_cae_emitter_contract.md`](cad_cae_emitter_contract.md) §Source-of-truth).
The deck assemblers read the following keys.

### Materials

```yaml
material_name: Al6061-T6          # selects the entry in `materials`
materials:
  Al6061-T6:
    youngs_modulus_mpa: 69000     # MPa  -> *ELASTIC E
    poisson_ratio: 0.33           #       -> *ELASTIC nu
    density_kg_m3: 2700           # kg/m^3 -> *DENSITY (converted, see Units)
```

### Boundary conditions and loads — target pointer forms

| Key | Authoritative form | Notes |
|-----|--------------------|-------|
| `boundary_conditions[].target_feature` | **feature id** (e.g. `feat_hole_001`) | Resolved to an NSET via `cae_mapping`. |
| `boundary_conditions[].type` | `fixed` (translational) | Maps to `*BOUNDARY <nset>, 1, 3, 0.0` for solid elements. |
| `loads[].target_feature` | **feature id** | Resolved to an NSET via `cae_mapping`. |
| `loads[].type` | `force` | Maps to `*CLOAD`. |
| `loads[].value_n` | **total** force in N | Distributed over the NSET nodes (see Semantics). |
| `loads[].direction` | unit vector `[x, y, z]` | Decomposed into one `*CLOAD` line per non-zero DOF. |

`cae_mapping` (`simulation/cae_mapping.json`) is the feature->NSET bridge:

```json
{"mappings": [
  {"cae_entity": "FEAT_HOLE_001",
   "maps_to": {"feature_id": "feat_hole_001", "role": "fixed_support"},
   "face_ids": ["face_003"]}
]}
```

Both assemblers resolve a `target_feature` to its `cae_entity` (the NSET name) by
matching `maps_to.feature_id`. `face_ids` reference `geometry/topology_map.json`
entities and drive the geometric node binding.

### Pointer-form divergence (normalization note)

- The **deck path** is keyed on `target_feature` (feature id) -> NSET via
  `cae_mapping`.
- The normalized `agent_context` CAE view exposes `target` + `@face:` pointers.

These are two views of the same intent. The authoritative form *for deck
assembly* is `target_feature` + `cae_mapping`. When authoring or patching setup
for the solver, write `target_feature`; `@face:` pointers are the
inspection/UI-facing form and are reconciled through `cae_mapping.face_ids`.

## Unit conventions

CalculiX is unitless; this stack uses the **mm - tonne - s** consistent system
(stress in MPa, force in N):

- **Young's modulus**: `youngs_modulus_mpa` -> MPa, emitted verbatim.
- **Density**: `density_kg_m3` (kg/m^3) -> **tonne/mm^3** by `x 1e-12`
  (e.g. `2700 kg/m^3 -> 2.7e-9 t/mm^3`). Required for correct mass in modal/dynamic.
- **Force**: `value_n` in N, emitted verbatim (consistent with MPa.mm^2).
- **Length**: mesh node coordinates in mm.

## Physics semantics (must match across both paths)

These are the result-bearing semantics the parity test asserts as a *set*, so any
drift fails CI:

1. **DOF mapping.** Solid (C3D4/C3D10) elements expose **translational DOFs only**.
   A `fixed` constraint emits `*BOUNDARY <nset>, 1, 3, 0.0` — never the old
   `1, 6` rotational range (which corrupts solid analyses).
2. **Force-per-node distribution.** `value_n` is the **total** force on the NSET.
   The emitted `*CLOAD` value is `value_n / n_nodes(nset)`, applied per node, so
   the resultant equals the requested total regardless of mesh density.
3. **Direction decomposition.** Each non-zero component of `direction` yields one
   `*CLOAD` line on the matching DOF (1=x, 2=y, 3=z), preserving sign.
4. **Density conversion.** As above — kg/m^3 -> tonne/mm^3.
5. **Elastic constants.** `(E_MPa, nu)` emitted on the `*ELASTIC` line.

The two assemblers differ only in *when* these are computed (the legacy path
decomposes/distributes during emission; the core path pre-resolves via
`_resolve_loads` / `_resolve_boundary_conditions`), not in the resulting cards.

## Element-type scope

Both paths are **solid-only** (the legacy path filters the Gmsh mesh to solid
element blocks, dropping 2D surface elements that otherwise corrupt the deck).
Shell/beam sections are out of scope for this contract.

## Parity guard

`test_legacy_and_core_static_deck_semantics_match` runs **both** assemblers on one
deterministic fixture (a meshed bracket: Al6061-T6, one `fixed` BC, one 500 N
downward load) and asserts the legacy and core decks emit the **same set** of
elastic / density / `*BOUNDARY` / `*CLOAD` cards, plus pins the expected physics.
Run it with:

```bash
cd aieng-ui/backend
python -m pytest tests/test_simulation_runner.py -k semantics_match -q
```

## Remaining convergence work

This contract + the parity test lock the *semantics*. The structural
consolidation — routing the legacy REST endpoint through the core
`deck_generator` so there is literally one assembler — remains open follow-up
work tracked in issue #352. It is deferred because it changes the proven live-sim
physics path and warrants a real-solver (`ccx`) equivalence check, not just a
deck-text comparison.
