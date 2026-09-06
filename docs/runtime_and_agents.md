# Runtime and Agent Integration

The local orchestration runtime is the primary integration point for the UI,
external CLI scripts, and MCP/agent clients. (MCP was "future" when this
was written; it is now the PRIMARY interface — see [`../AGENTS.md`](../AGENTS.md).
What follows describes the REST/runtime layer underneath it, which is still
accurate.) This document explains how
it works and how external agents connect to it.

---

## Core Concepts

### Run

A run represents one orchestration request from any caller (UI, CLI, agent).

```python
@dataclass
class RunRecord:
    run_id: str                  # hex12, unique per run
    message: str                 # original user request (natural language)
    created_at: str              # ISO 8601 UTC
    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
    ]
    plan: list[dict]             # declared tool steps (name, description, input)
    events: list[RuntimeEvent]   # ordered event timeline
    tool_calls: list[ToolCall]   # one per planned step
    tool_results: list[ToolResult]
    errors: list[str]
    project_id: str | None       # optional: scopes tools to a project
    package_path: str | None     # optional: override package path
    summary: str                 # human-readable completion summary
```

Current implementation: file-backed persistence in `data/runtime/runs/` (configurable via `AIENG_RUNTIME_STATE_DIR`). Runs survive server restart.

---

### Plan Step

Each plan step maps to one registered tool.

```python
{
    "name": "aieng.inspect_package",
    "description": "Inspect .aieng package and return project summary",
    "input": {"project_id": "abc123def456"},
}
```

The plan builder uses keyword matching against the user message:

| Keywords (sample) | Tool |
|-------------------|------|
| inspect, package, summary, 摘要 | `aieng.inspect_package` |
| refresh, semantic, validate, 校验 | `aieng.refresh_semantics` |
| preview, glb, stl, viewer, 预览 | `aieng.generate_preview` |
| audit, log, 审计, 日志 | `aieng.read_audit_log` |
| geometry, freecad, 几何, bounding box | `freecad.inspect_geometry` |
| export, step export, export step, 导出 | `freecad.export_step` |
| macro, 宏, 脚本 | `freecad.run_macro` |
| computed metrics, generate computed metrics, 计算指标, 归一化指标 | `postprocess.generate_computed_metrics` |

If no keyword matches, `aieng.inspect_package` is used as the default step.

---

### Tool Call and Tool Result

```python
@dataclass
class ToolCall:
    id: str                       # hex8
    name: str                     # e.g. "aieng.inspect_package"
    input: dict[str, Any]         # forwarded to handler
    requires_approval: bool       # if True, run pauses before executing

@dataclass
class ToolResult:
    id: str                       # matches ToolCall.id
    status: Literal["success", "error", "needs_approval"]
    output: Any                   # handler return value on success
    error: str | None             # exception string on failure
    artifacts: list[dict]         # {path, kind, role} — populated for export tools
```

---

### Runtime Events

Events form an ordered timeline of what happened during a run.

```python
@dataclass
class RuntimeEvent:
    id: str            # hex10
    run_id: str
    type: Literal[
        "run_started",
        "plan_created",
        "tool_started",
        "tool_succeeded",
        "tool_failed",
        "approval_required",
        "run_completed",
        "run_failed",
    ]
    timestamp: str     # ISO 8601 UTC
    payload: Any       # dict with context (tool name, error string, summary, etc.)
```

A successful single-tool run produces this event sequence:
```
run_started → plan_created → tool_started → tool_succeeded → run_completed
```

A failed run:
```
run_started → plan_created → tool_started → tool_failed → run_failed
```

A run blocked at an approval gate:
```
run_started → plan_created → tool_started (not emitted) → approval_required
```
(run status becomes `awaiting_approval`; further steps are not executed)

---

### Approval Gate

Any tool registered with `requires_approval=True` will pause the run before
execution and emit an `approval_required` event.

Currently gated tools:
- `freecad.run_macro` — arbitrary macro execution requires explicit approval

The run stops at the first approval gate.  Resumption and rejection endpoints
are both implemented:

- `POST /api/runtime/runs/{id}/approve` — resumes the run; executes the tool
- `POST /api/runtime/runs/{id}/reject` — discards the pending tool; marks run `rejected`

This gate exists because `freecad.run_macro` can execute arbitrary Python
inside FreeCAD. No unsafe tool path bypasses the gate.

---

### Audit Log

After every run that has a `project_id`, the runtime writes a structured audit
record to the project's log directory:

```json
{
  "kind": "runtime_run",
  "run_id": "abc123",
  "message": "inspect the package",
  "project_id": "...",
  "tools": ["aieng.inspect_package"],
  "status": "completed",
  "errors": [],
  "created_at": "2026-05-15T..."
}
```

Audit records are accessible via `GET /assets/projects/{id}/logs/...` and
listed in the project summary under `recent_logs`.

---

## REST API

```
POST /api/runtime/runs
  Body: {
    "message": str,
    "project_id": str|null,
    "package_path": str|null,
    "tool_input": dict|null   # optional structured params merged into each step
  }
  Returns: RunRecord as JSON (blocking; run completes before response)

GET /api/runtime/runs
  Returns: list of slim run summaries (run_id, status, message, created_at,
           event_count, last_event_type, error_summary)

GET /api/runtime/runs/{run_id}
  Returns: RunRecord as JSON

GET /api/runtime/runs/{run_id}/events
  Returns: list of RuntimeEvent dicts

POST /api/runtime/runs/{run_id}/approve
  Resumes a run in awaiting_approval; executes the pending tool and continues.
  Returns: updated RunRecord as JSON

POST /api/runtime/runs/{run_id}/reject
  Rejects a run in awaiting_approval; pending tool is not executed.
  Returns: updated RunRecord as JSON (status: "rejected")

GET /api/runtime/tools
  Returns: list of registered tools with name, requiresApproval, description
```

Streaming (SSE/WebSocket) is not yet implemented. Callers poll
`GET /api/runtime/runs/{id}/events` for live updates.

---

## Registered Tool Adapters

Integration-specific tool registrations live in `backend/app/runtime_tools.py`
(`register_engineering_template_tools` and `register_freecad_wrapper_tools`).
The app factory calls them at startup; handler closures capture the active
`Settings` instance. General-purpose tools are still registered directly in
`app_factory.py`.

| Tool name | Handler wraps | Notes |
|-----------|---------------|-------|
| `aieng.inspect_package` | `package_summary()` | Returns full project semantic state |
| `aieng.refresh_semantics` | `validate_aieng_file()` | Re-validates and refreshes status |
| `aieng.generate_preview` | `convert_asset()` | Produces GLB or STL web asset |
| `aieng.read_audit_log` | `recent_logs()` | Returns last 8 audit entries |
| `freecad.inspect_geometry` | `freecad_bridge.inspect_geometry()` | Spawns `FreeCADCmd` via `aieng_freecad_mcp`; returns face/edge/vertex counts, bounding box, volume |
| `freecad.export_step` | `freecad_bridge.export_step()` | Spawns `FreeCADCmd`; returns STEP artifact refs in `ToolResult.artifacts` |
| `freecad.run_macro` | — | Skeleton; `requires_approval=True` |
| `postprocess.generate_computed_metrics` | `freecad_bridge.export_computed_metrics()` | Normalizes flat JSON/CSV into `results/computed_metrics.json`; no solver execution; returns artifact refs |

The runtime module (`backend/app/runtime.py`) has no imports from `main.py`.
Tool handlers are injected via `_rt.register_tool(name, handler)`.

`ToolResult.artifacts` is hoisted automatically by `_execute_steps()` from
any `{"artifacts": [...]}` key in the handler's return dict.

---

## Agent Integration

### Claude Code / Codex via MCP (Phase 3 — implemented)

The `aieng_freecad_mcp` MCP server now exposes runtime bridge tools that
delegate to the aieng-ui REST API.  No new business logic lives in the MCP
layer.

```
Claude Code / Codex
    │  MCP protocol (stdio or HTTP)
    ▼
freecad-mcp  (aieng_freecad_mcp server)
    └── runtime bridge tools (thin HTTP wrappers)
              │  REST
              ▼
        aieng-ui runtime (port 8000)
              │
              ├── freecad.inspect_geometry → FreeCADCmd
              ├── freecad.export_step      → FreeCADCmd
              ├── freecad.run_macro        → (approval-gated)
              └── aieng.*                  → package tools
```

**Quick start:**
```bash
# 1. Start the aieng-ui backend
cd aieng-ui && uvicorn app.main:app --port 8000

# 2. Start the MCP server
AIENG_RUNTIME_BASE_URL=http://localhost:8000 freecad-mcp
```

**Claude Code configuration** (`.claude/mcp.json`):
```json
{
  "mcpServers": {
    "aieng": {
      "command": "freecad-mcp",
      "env": { "AIENG_RUNTIME_BASE_URL": "http://localhost:8000" }
    }
  }
}
```

MCP tools exposed: `aieng_list_runtime_tools`, `aieng_start_runtime_run`,
`aieng_get_runtime_run`, `aieng_inspect_geometry`, `aieng_export_step`,
`aieng_approve_runtime_run`, `aieng_reject_runtime_run`.

See `docs/runtime_and_agents.md` or `aieng_freecad_mcp/docs/mcp_runtime_tools.md`
for full tool documentation and limitations.

### CLI scripts (works today)

Any script can call `POST /api/runtime/runs` over HTTP while the service is
running. The response is the full `RunRecord` as JSON.

```bash
curl -X POST http://localhost:8000/api/runtime/runs \
  -H 'Content-Type: application/json' \
  -d '{"message": "inspect the package", "project_id": "abc123def456"}'
```

### Direct Python (works today)

Test code and automation scripts import the runtime module directly and call
`execute_run()` without an HTTP layer:

```python
from app import runtime as rt
from app.main import create_app, Settings, package_summary

settings = Settings(...)
app = create_app(settings)   # registers tools

run = rt.RunRecord(
    run_id="test01",
    message="inspect the package",
    created_at="...",
    status="pending",
    project_id="abc123",
)
result = rt.execute_run(run, {"project_id": "abc123"})
print(result.status, result.summary)
```

### Future: aieng MCP server (`aieng serve`)

The `aieng` repo already ships an MCP server that exposes package-level tools
(import, validate, summarize, propose-patch, etc.). This is a separate MCP
surface from the `aieng-ui` runtime; it operates at the package level without
the project/file management layer.

Both MCP surfaces can coexist. An agent could:
1. Call `aieng serve` tools to work with a `.aieng` package directly.
2. Call `aieng-ui` runtime tools to trigger the full workbench workflow
   (upload → import → preview → semantic refresh).
