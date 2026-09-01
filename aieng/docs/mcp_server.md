# MCP Server for .aieng Packages

The `aieng serve` command starts a Model Context Protocol (MCP) server that exposes
a `.aieng` package as a set of structured, agent-callable tools. An AI agent (Claude,
or any MCP-compatible client) can query the package's feature graph, topology, interfaces,
and validation state without needing the entire file in its context window.

MCP is an optional access interface over `.aieng` package resources. It is not the core product and should not redefine the package as an agent runtime; the core remains the CAD/CAE-side semantic export and evidence package.

## Installation

The MCP transport layer requires the `mcp` Python package:

```bash
pip install "mcp>=1.0,<3"
```

`aieng-format` itself is not on PyPI (see the release gate doc) — install it
from a checkout or a release tag, with the `mcp` extra:

```bash
pip install -e "./aieng[mcp]"
# or, without cloning:
pip install "aieng-format[mcp] @ git+https://github.com/armpro24-blip/cad-cae-copilot.git@v0.1.0-alpha.4#subdirectory=aieng"
```

## Starting the server

### stdio transport (Claude Desktop / Claude Code)

```bash
aieng serve bracket.aieng
```

The server communicates over stdin/stdout. This is the standard transport for
Claude Desktop and Claude Code MCP integrations.

### SSE / HTTP transport (testing, custom clients)

```bash
aieng serve bracket.aieng --port 8080
```

The server starts an HTTP SSE endpoint on `0.0.0.0:8080`.

## Connecting from Claude Code

Add the server to your MCP configuration (`~/.claude/claude_code_config.json` or
project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "aieng-bracket": {
      "command": "aieng",
      "args": ["serve", "/path/to/bracket.aieng"]
    }
  }
}
```

Once connected, the tools listed below are available as `mcp__aieng-bracket__<tool_name>`.

## Tool reference

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `get_manifest` | — | `dict` | Parsed `manifest.json` |
| `get_feature` | `feature_id: str` | `dict` | Single feature from `graph/feature_graph.json` |
| `get_topology` | `entity_type?: str` | `dict` | All topology entities, or filtered by `face`/`edge`/`vertex` |
| `get_interfaces` | `role?: str` | `dict` | Interface graph, optionally filtered by role string |
| `get_validation_status` | — | `dict` | Parsed `validation/status.yaml` |
| `get_aag_neighbors` | `face_id: str` | `dict` | AAG arcs and neighbor nodes for a topology face ID |
| `get_task_spec` | — | `dict` | `task/task_spec.yaml` if present; otherwise `{"status": "not_found"}` |
| `get_external_tool_requirements` | — | `dict` | `task/external_tool_requirements.json` if present; otherwise `{"status": "not_found"}` |
| `get_evidence_index` | — | `dict` | `results/evidence_index.json` if present; otherwise `{"status": "not_found"}` |
| `get_tool_trace` | — | `dict` | `provenance/tool_trace.json` if present; otherwise `{"status": "not_found"}` |
| `propose_patch` | `intent: str` | `dict` | Runs the rule-based patch proposer; returns the proposal JSON |
| `get_summary` | — | `str` | Contents of `ai/summary.md` |

## claim_policy enforcement

If `validation/status.yaml` contains a `claim_policy.forbidden_operations` list,
any tool whose name appears in that list returns an explicit error referencing the
status file rather than executing:

```yaml
# validation/status.yaml
claim_policy:
  forbidden_operations:
    - propose_patch
  rationale: "package is read-only pending geometry re-validation"
```

Tools not in `forbidden_operations` are unaffected. If `validation/status.yaml` is
absent or contains no `forbidden_operations` key, all tools are available.

## Notes on propose_patch

`propose_patch` is the only tool that writes to the package (it appends a patch
proposal file under `ai/patches/`). All other tools are read-only. Patch execution
(`apply-patch`) is a separate CLI command and is intentionally not exposed through
the MCP server in this phase.

## Errors

| Situation | Exception / exit code |
|---|---|
| Package file missing | `FileNotFoundError` / CLI exit 2 |
| Package has wrong extension | `ValueError` / CLI exit 2 |
| `mcp` package not installed | `ImportError` / CLI exit 2 |
| Requested member not in package | `PackageNotReadable` (tool returns error message) |
| Operation blocked by `claim_policy` | `OperationForbidden` (tool returns error message) |
