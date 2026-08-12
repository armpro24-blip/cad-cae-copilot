> **Experimental pre-release.** Second published release of the AIENG Workbench
> line, following `v0.1.0-alpha.3` (2026-08-10). The Python packages carry
> version `0.1.0a3`.

## What changed

This release is almost entirely **defects found by using the product**, not by
reading it. Four rounds of dogfooding drove the documented agent paths end to end —
assembly authoring, the no-MCP fallback scripts, topology optimization, and
parametric editing — and every one of them was broken in a way the test suite
could not see. Thirteen real defects, plus the CAE pre-processing rewrite that
started the round.

The pattern worth stating up front, because it shaped the whole release: **a
path the docs advertise but no test exercises is probably rotten.** In each case
CI was green throughout.

## Say the physics instead of hand-translating it

Setting up an analysis used to mean reading a digest of every face's normal and
area, picking ids by eye, and hand-writing four JSON patches with NSET names,
DOF ranges and direction vectors. Now it is one call in engineering language:

```text
cae.setup_static {
  material: "Al6061-T6",
  fix:  "bottom",                                    # or "bolt holes" / "base_plate bottom"
  load: { at: "rib_main top", force_n: 500, direction: "-Z" }
}
```

Chinese wording works (`底面`, `螺栓孔`, `向下`). What makes it safe rather than
merely convenient: it **echoes what it actually bound** — face pointer, surface
type, area, normal, owning part — so a mis-pick is visible immediately; ambiguous
wording is **refused with the real candidates listed**, never guessed; a sloped
face resolves but is reported as `inclined 32° from top`; and `force_n: 0` is
refused outright, because it would converge and report zero stress as a result.

**The load case can also be recorded as a requirement** (`cae.author_load_case`)
with acceptance criteria. It is resolved against the geometry *at authoring
time*, so unpinnable wording is caught while rewording is still cheap, and it is
executable (`cae.apply_load_case`) so the recorded requirement and what was
actually solved cannot drift apart. Criteria land in the package's existing
`task/design_targets.yaml` and come back as pass / fail / **unknown** — a
criterion the run could not measure never silently passes.

## Thirteen defects found by dogfooding

**Assembly authoring**
- A `bonded` tie between faces **20 mm apart** stayed solver-enabled and
  load-transferring — stiffer than reality, in the non-conservative direction,
  with `needs_user_input: []`. A joint across a gap cannot exist at any scale;
  it is now `invalid` and the existing disable gate fires.
- `aieng.agent_context` — the tool an agent reads every session — reported
  **nothing** about assemblies. It now carries a compact block, and a refused
  joint reaches the top-level warnings.
- **4 of 4 correctly-authored interfaces warned** (`ok: 0`). Both rules were
  satisfied by construction rather than by defect. Now judged by area coverage.

**The no-MCP fallback path**
- **Dead on command one** — the runner re-implemented the backend's placeholder
  substitution and had been failing with a `NameError` since a second
  placeholder was added. Nothing exercised these scripts; only the doc that
  advertises them to agents with no other way in.
- `--data-root`, a documented flag, raised `TypeError`.
- A failed `require()` leaked an internal marker and a temp-file traceback
  instead of the promised structured `design_rule_violation`.

**Topology optimization**
- The chain **could not see** a setup authored by the key-free path, and did not
  fail — it substituted a **textbook cantilever preset** under `status: "ok"`,
  so a real 500 N bracket was posed as someone else's beam and would have been
  written back as its geometry. Both dimensions now refuse honestly.
- The 2D idealization cannot represent plate bending at all (the projection
  plane is spanned by the two largest dimensions, so the load is always
  out-of-plane). It now says exactly that and points at 3D.

**Parametric editing**
- A constant that dimensions the plate *and* positions the rib was reported as
  `scope: "local"` — "the safe single-part edit" — because the stored feature
  graph predated the binder that would have caught it. The scope-risk gate reads
  the same graph, so the edit skipped confirmation and resized the plate. Scope
  is now re-checked against the live source and **only ever widened**.

**Windows / MCP transport** (from the same practice, earlier in the cycle)
- Real CAD/CAE through the stdio server was fully broken: a lazy heavy
  C-extension import deadlocked the DLL loader, and any child inheriting the
  protocol pipe as stdin blocked at startup.
- A 500 N load silently became **0 N** (`0.0 mm / 0.0 MPa` reported as a result);
  a second ccx spawn site hung until the client's 1800 s timeout.
- The server now stays answerable during long calls — a liveness ping 2 s into a
  16 s build was answered at **+14.12 s**, now **+0.01 s**.

## Accuracy and honesty

- Mesh accuracy is measured through the **thinnest solid** — the wall a bending
  gradient must resolve through — not the model bounding box. On the canonical
  plate-plus-rib bracket the old reading was **6× optimistic**, in exactly the
  non-conservative direction quadratic elements exist to prevent.
- The solver preflight stopped blocking correct setups, and reads the bound
  physics back in engineering language so you can answer "what is set up here?"
  without running anything.

## Install

Unchanged from alpha.3 — Docker image, or `uvx` from a tag pin; see
[`MCP_SETUP.md`](../../../aieng-ui/backend/MCP_SETUP.md). PyPI publication is
still pending owner setup (#273); the git-pin and container paths are the
supported ones.

## Honesty boundaries (unchanged)

Linear static / modal / buckling / steady-state thermal only. Assembly
connections are simplified proxies — no nonlinear contact, no bolt preload, no
friction. Topology optimization 2D is the solid path and 3D is experimental,
producing a mesh proxy rather than production CAD. Heuristic manufacturability
rules, not certification. Every output is review material for a qualified
engineer, and the workbench says so in its own responses.
