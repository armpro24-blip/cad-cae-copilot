# AIENG Version / Compatibility Contract

This document defines the version surface and compatibility policy for the
three surfaces an external agent binds to when connecting to the AIENG
Workbench:

1. **MCP tool surface** — tool names and input/output shapes.
2. **`.aieng` / artifact schemas** — the JSON schemas used by packages and
   runtime artifacts.
3. **Skill prompts** — the agent skill instructions shipped in
   `aieng-agent-skills/`.

## Version surface

The canonical version surface is stored in
`aieng/src/aieng/schemas/version_surface.json`. It is exposed read-only to
connected agents via the `aieng.agent_readme` tool (`version_surface` field).

Example:

```json
{
  "format": "aieng.version_surface.v1",
  "version": "0.1.0-alpha.1",
  "surfaces": {
    "mcp_tool_surface": {
      "version": "0.1.0-alpha.1",
      "policy": "unstable",
      "sha256": "..."
    },
    "artifact_schemas": {
      "version": "0.1.0-alpha.1",
      "policy": "unstable",
      "sha256": "..."
    },
    "skill_prompts": {
      "version": "0.1.0-alpha.1",
      "policy": "unstable",
      "sha256": "..."
    }
  },
  "compat_policy": { ... }
}
```

Agents should record the surface version/hash at session start and compare it
to a known-good value. A mismatch means the contract may have drifted and the
agent should re-inspect the tools/schemas/prompts before relying on them.

## Compatibility policy (alpha)

During the `0.1.0-alpha` period the contract declares itself **unstable**.
Breaking changes are allowed, but they must be deliberate and detectable:

- **Breaking change** examples:
  - MCP tool surface: renaming/removing a tool, removing or retyping a required
    parameter, tightening `additionalProperties`.
  - Artifact schemas: removing a schema, removing a required field, or changing
    a field type in a way that invalidates existing packages.
  - Skill prompts: removing a skill, changing a skill's MCP tool contract, or
    changing prompt instructions that agents depend on.
- **Additive change** examples:
  - MCP tool surface: adding a new tool or adding optional parameters to an
    existing tool.
  - Artifact schemas: adding a new schema or adding optional fields to an
    existing schema.
  - Skill prompts: adding a new skill or adding clarifying guidance that does
    not change tool usage.

There is **no deprecation window during alpha**. When a breaking change lands,
the relevant surface `version` must be bumped and `version_surface.json` must
be regenerated.

## Regenerating the version surface

After changing any tool schema, artifact schema, or skill prompt, run:

```bash
python scripts/update_version_surface.py
```

Text artifacts are normalized to LF line endings before hashing so the stored
surface hashes are deterministic across Windows and Unix checkouts.

If the change is breaking, manually bump the affected surface `version` in
`aieng/src/aieng/schemas/version_surface.json` before committing. The
anti-drift gate (`pytest aieng/tests/test_backend_stability_gate.py`) will fail
if the stored SHA256 hashes do not match the current artifacts.

## Using the version surface in an external agent

A typical external agent flow:

1. Call `aieng.agent_readme` at session start.
2. Read `version_surface` from the response.
3. Compare `version_surface.surfaces.*.sha256` against a pinned or cached
   value.
4. If any hash differs, re-list tools (`mcp.list_tools`) and re-read the
   relevant schemas/prompts before constructing tool calls.

Do not rely on the surface being frozen during alpha; rely on it being
*detectable*.
