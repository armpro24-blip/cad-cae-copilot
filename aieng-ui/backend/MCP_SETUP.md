# Connecting Claude Code (or any MCP client) to the aieng workbench

This backend exposes its runtime tool registry as an **MCP server** so external
agents can drive the workbench using their own harness — no need to reimplement
the LLM tool-calling loop or context management.

> **New to the workbench?** After connecting, call `aieng.agent_readme` to receive
> the full [AGENTS.md](AGENTS.md) guide in-band, or read it directly in this directory.
> It covers tool taxonomy, workflow patterns, pointer syntax (`@face:`, `@feature:`, …),
> and approval-gated operations.

## What gets exposed

Every tool registered via `runtime.register_tool()` is surfaced. That includes:

- `aieng.inspect_package` / `aieng.agent_context` — read-only project inspection
- `aieng.convert`, `aieng.validate`, `aieng.refresh_semantics`
- `cae.apply_setup_patch`, `cae.prepare_solver_run`, `cae.generate_solver_input`
- `cae.run_solver` ⚠️ approval-gated
- `cae.extract_solver_results`, `cae.extract_field_regions`
- `postprocess.generate_computed_metrics`, `postprocess.refresh_cae_summary`
- `cad.edit_parameter` — executes inside an explicitly approved modeling plan
- …and the remaining runtime tools listed by `python -c "from app import runtime; from app.app_factory import create_app; create_app(); print('\n'.join(runtime.registered_tool_names()))"`.

Tools listed in `app/runtime_tool_schemas.py:TOOL_SCHEMAS` carry curated JSON
schemas so the agent constructs valid calls on the first try. Other tools fall
back to a permissive `{"type": "object"}` schema.

## Connect your agent in 5 minutes

### Supported tiers

Choose one tier:

1. **Headless local-install MCP tools** - no React viewer. The MCP server runs
   in-process and stores projects under a local data directory. This is the
   lowest-friction BYO-agent path.
2. **Full backend + live viewer** - run the FastAPI backend/viewer and point the
   MCP server at it with `AIENG_BACKEND_URL` or `--backend-url`. This preserves
   live project mirroring and workbench-managed approval cards.
3. **Docker all-in-one** - recommended when native CAD dependencies are the
   blocker. The container starts backend, viewer, and MCP HTTP/SSE together.

Headless install/run after publication:

```bash
uvx "aieng-workbench-mcp[full]" \
  --approval-mode client \
  --data-dir ~/.aieng-workbench
```

Headless run directly from this Git repository without cloning it first:

```bash
uvx \
  --from "aieng-workbench-mcp[full] @ git+https://github.com/armpro24-blip/cad-cae-copilot.git#subdirectory=aieng-ui/backend" \
  --with "aieng-format @ git+https://github.com/armpro24-blip/cad-cae-copilot.git#subdirectory=aieng" \
  aieng-workbench-mcp \
  --approval-mode client \
  --data-dir ~/.aieng-workbench
```

Use `--approval-mode block` for planning/inspection-only sessions where no
approval-gated CAD/package/solver mutation may execute. Use `--approval-mode
managed --backend-url http://127.0.0.1:8000` only when the backend/viewer is
running and should own approval.

Use `--approval-mode elicit` for **headless** sessions with no workbench viewer:
gated mutations are approved via **MCP client elicitation**, so the connecting
CLI/IDE agent prompts the human directly. If the client does not support
elicitation there is no approval surface and the gated tool **fails safe**
(`behavior: deny`, `code: approval_surface_unavailable`) — it is never executed
silently. This is the BYO-agent path that does not depend on the web UI.

## "Is everything wired?" — the doctor command

First-time setup juggles agent + MCP config + conda + backend. Run a one-shot
diagnostic from your project root:

```bash
aieng-workbench-mcp --doctor                                   # headless
aieng-workbench-mcp --doctor --backend-url http://127.0.0.1:8000  # with live viewer
```

It checks, with actionable fix hints, that (1) an MCP config in the current
directory (`.mcp.json`, `.vscode/mcp.json`, `.cursor/mcp.json`,
`.codex/config.toml`) references `aieng-workbench`; (2) the backend `/api/health`
is reachable when a URL is given (skipped in headless mode); and (3) the server
exposes a non-empty tool set. It prints an `OK` / `WARN` / `FAIL` report and exits
non-zero on a failing check, so it doubles as a CI/setup smoke check.

The install extras are intentionally explicit:

| Install form | Capability |
|---|---|
| `aieng-workbench-mcp` | MCP server, prompts/resources, project inspection/setup paths when `aieng-format` is installed. |
| `aieng-workbench-mcp[cad]` | Adds build123d, bd_warehouse, and CadQuery/OCP for real CAD execution. |
| `aieng-workbench-mcp[llm]` | Adds Anthropic/OpenAI SDKs for compatibility endpoints. BYO MCP agents do not need this. |
| `aieng-workbench-mcp[full]` | CAD + LLM extras; closest to the local backend environment. |

Until both distributions are published, the Git `uvx --from ... --with ...`
form above is the no-clone install path. If dependency resolution for native CAD
wheels fails on your platform, use the Docker tier.

### Claude Code snippets

Headless local install:

```json
{
  "mcpServers": {
    "aieng-workbench": {
      "command": "uvx",
      "args": [
        "aieng-workbench-mcp[full]",
        "--approval-mode", "client",
        "--data-dir", "~/.aieng-workbench"
      ]
    }
  }
}
```

Full backend + viewer from a repository checkout:

```json
{
  "mcpServers": {
    "aieng-workbench": {
      "command": "conda",
      "args": ["run", "-n", "aieng311", "--no-capture-output", "python", "-m", "aieng_workbench_mcp"],
      "cwd": "aieng-ui/backend",
      "env": {
        "AIENG_BACKEND_URL": "http://127.0.0.1:8000",
        "AIENG_MCP_MANAGED_APPROVAL": "1"
      }
    }
  }
}
```

### OpenAI Codex snippet

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.aieng-workbench]
command = "uvx"
args = [
  "aieng-workbench-mcp[full]",
  "--approval-mode", "client",
  "--compact-tool-surface",
  "--data-dir", "~/.aieng-workbench",
]
```

For a local checkout with the live viewer:

```toml
[mcp_servers.aieng-workbench]
command = "conda"
args = [
  "run", "-n", "aieng311", "--no-capture-output", "python", "-m", "aieng_workbench_mcp",
  "--approval-mode", "client",
]
cwd = "<absolute-path-to-clone>/aieng-ui/backend"

[mcp_servers.aieng-workbench.env]
AIENG_BACKEND_URL = "http://127.0.0.1:8000"
AIENG_MCP_MANAGED_APPROVAL = "1"
```

### Cursor / VS Code MCP-compatible snippet

Use `.vscode/mcp.json` style configuration:

```json
{
  "servers": {
    "aieng-workbench": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "aieng-workbench-mcp[full]",
        "--approval-mode", "client",
        "--compact-tool-surface",
        "--data-dir", "~/.aieng-workbench"
      ]
    }
  }
}
```

### Compact tool surface

The workbench registers ~90 tools. Some MCP clients (notably Cursor and Codex)
advertise a limited active-tool budget and produce cleaner results with a
smaller, curated surface. Pass `--compact-tool-surface` (or set
`AIENG_MCP_COMPACT_SURFACE=1`) to expose only the ~30 high-frequency tools for
onboarding, CAD authoring, CAE setup/solver, and materials lookup. The full
surface remains the default for clients that can handle it (e.g. Claude Code).

For Docker/full viewer mode, prefer MCP-over-HTTP if your client supports it:

```json
{
  "servers": {
    "aieng-workbench": {
      "type": "sse",
      "url": "http://localhost:8765/sse"
    }
  }
}
```

### Pre-publish local wheel install (no PyPI)

Until both packages are published, you can install the built wheels into a clean
venv from a local checkout:

```bash
cd aieng && python -m build
cd ../aieng-ui/backend && python -m build

python -m venv /tmp/mcp-smoke-venv
/tmp/mcp-smoke-venv/bin/pip install \
  ../../aieng/dist/aieng_format-0.1.0a2-py3-none-any.whl \
  ./dist/aieng_workbench_mcp-0.1.0a2-py3-none-any.whl
/tmp/mcp-smoke-venv/bin/aieng-workbench-mcp-smoke
```

On Windows use `\tmp\mcp-smoke-venv\Scripts\python.exe` and `\Scripts\aieng-workbench-mcp-smoke.exe`.

### Smoke checks

CI-safe packaged smoke (stubbed CAD, no model/API/cloud dependency):

```bash
cd aieng-ui/backend
python -m aieng_workbench_mcp.smoke
```

Installed form:

```bash
aieng-workbench-mcp-smoke
```

Manual real-CAD verification on a machine with `build123d`/OCP available:

```bash
python -m aieng_workbench_mcp.smoke --real-cad --data-dir ~/.aieng-workbench-smoke
```

Expected: the smoke reads an onboarding prompt/resource, creates a project,
executes a simple bracket build path (stubbed by default, real with
`--real-cad`), and proves hard-block approval mode returns
`code="approval_blocked"` before dispatch.

### Honesty boundaries

- AIENG Workbench is not production-certified CAD/CAE software.
- Solver execution must be evidenced by `cae.run_solver` output and result
  artifacts; prompts/resources cannot make solver claims true.
- Approval applies only to tools registered with `requires_approval=true`.
- `AIENG_MCP_BLOCK_APPROVAL_TOOLS=1` (a.k.a. `--approval-mode block`) is the
  server-enforced inspection-only mode: it hard-blocks **every mutating tool**,
  not only approval-gated ones. That includes the plan-boundary CAD
  authoring/edit tools (`cad.execute_build123d`, `cad.edit_parameter`,
  `cad.replace_part`, `cad.remove_part`, `cad.refine`, `cad.set_reference_image`),
  which are `requires_approval=false` because approval moved to the modeling-plan
  boundary. Read-only inspection tools still run; blocked calls return
  `code: approval_blocked`.
- `AIENG_MCP_MANAGED_APPROVAL=1` requires a reachable backend/viewer; if the
  approval broker is unavailable, gated calls fail safe. The bridge pre-flights
  the approval surface, so when no viewer is connected to approve, the gated
  call **fails fast** with `code: approval_surface_unavailable` (and a hint to
  open the workbench UI or use `--approval-mode client|block`) instead of
  blocking to `AIENG_AGENTIC_APPROVAL_TIMEOUT_SECONDS`.
- Soft discipline such as industrial-design review, face-pointer hygiene, and
  4-view visual inspection is advisory. External agent prose is not fully
  controlled by the workbench.

### Keeping the tool registry fresh

The MCP server builds its tool registry **once at process start**. After you
pull backend changes that add or modify tools, **restart the MCP server** —
a long-lived session keeps serving the old set (new tools invisible, changed
descriptions stale) with no other signal.

To check what a running server actually exposes, read its **registry identity**:
`aieng.agent_readme` returns a `registry` block (`tool_count` + `registry_hash`),
and `GET /api/health` returns the same `registry_hash`. The hash changes whenever
a tool is added/removed or its description / approval flag / input schema changes,
so a mismatch against a freshly-started server means the session is stale —
restart it.

### Version surface and compatibility contract

`aieng.agent_readme` also returns a `version_surface` block describing the three
external-agent surfaces:

- `mcp_tool_surface` — tool names + input schemas.
- `artifact_schemas` — `.aieng` package / runtime artifact JSON schemas.
- `skill_prompts` — agent skill instructions.

Each surface carries a `version`, `policy`, and `sha256` hash. During alpha the
policy is `unstable`: breaking changes are allowed, but the hash changes whenever
the surface changes, so a connecting agent can detect drift instead of silently
breaking. See [`aieng/docs/version_contract.md`](../../aieng/docs/version_contract.md)
for the full contract and how to regenerate `aieng/src/aieng/schemas/version_surface.json`
after changes.

### Docker all-in-one (recommended packaged viewer mode)

**Quick start — pull the published image (no local build):**

```bash
docker pull ghcr.io/armpro24-blip/aieng-workbench:latest
docker run --rm -it \
  -p 8000:8000 \
  -p 8765:8765 \
  -v aieng-data:/data \
  ghcr.io/armpro24-blip/aieng-workbench:latest
```

The alpha image is published to GHCR from `main` after the Docker smoke passes,
tagged `latest` plus an immutable `sha-<commit>` tag (pin the sha tag for
reproducibility). It is **alpha-scoped and not production-certified**.

**Contributor path — build locally from source** (use this when developing the
image or running an unmerged branch):

```bash
docker build -t aieng/workbench:local .
docker run --rm -it \
  -p 8000:8000 \
  -p 8765:8765 \
  -v aieng-data:/data \
  aieng/workbench:local
```

Either way, the container starts:

- the FastAPI backend at `http://localhost:8000`,
- the built React workbench at `http://localhost:8000/app/`,
- the MCP HTTP/SSE server at `http://localhost:8765/sse`.

The default container environment uses:

```text
AIENG_PLATFORM_DATA=/data
AIENG_BACKEND_URL=http://127.0.0.1:8000
AIENG_MCP_MANAGED_APPROVAL=1
```

That means generated projects persist in the Docker volume and approval-gated
tools route through the workbench approval broker.

**Data persistence across restarts / upgrades.** All projects and generated
`.aieng` packages live under `/data` (`AIENG_PLATFORM_DATA=/data`). As long as
you mount `/data` to a named volume or host directory, your work survives a
container restart, `docker stop`/`start`, or an image upgrade:

```bash
# restart in place — projects persist (same volume)
docker restart <container>

# upgrade the image — projects persist as long as the SAME -v is reused
docker pull <image>:<newtag>
docker run --rm -it -p 8000:8000 -p 8765:8765 -v aieng-data:/data <image>:<newtag>
```

Use a **host path** (`-v /abs/host/path:/data`) if you want the packages on the
host filesystem instead of a Docker-managed volume. Without any `-v`, project
data lives only in the container's writable layer and is lost when the container
is removed. The image itself ships **no** runtime project data — `aieng-ui/data/`
is excluded via `.dockerignore`, and the Docker smoke
(`scripts/docker_smoke.py persist-create` / `persist-verify`) asserts a project
created before a `docker restart` is still readable afterward.

**Running the CalculiX solver from a separate conda env (Windows).**
Installing `calculix` directly into `aieng311` downgrades OpenSSL and breaks SSL.
Create a dedicated env and point the MCP server / backend at it:

```text
AIENG_CCX_CMD="conda run -n calculix-env ccx"
```

The command is parsed with `shlex.split` and invoked with `shell=False`.
On Linux/macOS, leave `AIENG_CCX_CMD` unset if `ccx` is already on PATH.
For container deployments, `ccx` is bundled and no extra variable is needed.
Disable managed approval only when using a trusted client-managed approval flow; set
`AIENG_MCP_BLOCK_APPROVAL_TOOLS=1` for planning/inspection-only containers.

MCP-over-HTTP client snippet:

```json
{
  "mcpServers": {
    "aieng-workbench": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

If your MCP client does not support HTTP/SSE yet, keep using the stdio config
below from a local checkout, or run a client-specific stdio bridge that launches
`python -m app.mcp_server` inside the container.

### stdio (default; what Claude Code expects)

```powershell
# from aieng-ui/backend/
python -m aieng_workbench_mcp
```

The process talks JSON-RPC over stdin/stdout. **Do not** run it in an
interactive shell expecting to type into it — Claude Code spawns it as a
subprocess and pipes the protocol frames itself.

Logs go to stderr only (stdout is the protocol wire).

### HTTP / SSE (for debugging or multi-client setups)

```powershell
python -m aieng_workbench_mcp --http --port 8765
```

Then point an MCP HTTP client at `http://127.0.0.1:8765/sse`.

## Wiring into Claude Code

A project-scoped config is already committed to this repo (`.mcp.json` for Claude
Code, `.vscode/mcp.json` for VS Code/Copilot), so a fresh clone needs no manual
wiring **if you have a conda env named `aieng311`** with build123d installed. It uses
`conda run` so it stays portable — no hard-coded username or install path:

```json
{
  "mcpServers": {
    "aieng-workbench": {
      "command": "conda",
      "args": ["run", "-n", "aieng311", "--no-capture-output", "python", "-m", "app.mcp_server"],
      "cwd": "aieng-ui/backend",
      "env": {
        "AIENG_BACKEND_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

> **Why `conda run -n aieng311`:** MCP clients launch the server as a subprocess
> without activating any environment, so a bare `python` would pick up whatever is
> on PATH (often a system Python *without* build123d → `cad.execute_build123d`
> returns `build123d_unavailable`). `conda run -n aieng311` always resolves the right
> interpreter regardless of machine. `--no-capture-output` is required so conda does
> not buffer the stdio JSON-RPC stream.
>
> **If your env has a different name** (or you don't use conda), either rename your
> env to `aieng311`, edit the `-n aieng311` argument, or point `command` directly at
> that interpreter, e.g. `"command": "C:\\path\\to\\envs\\myenv\\python.exe", "args": ["-m", "app.mcp_server"]`.

> **`AIENG_BACKEND_URL` (live UI):** when set, the MCP server forwards every tool
> call to the *running* FastAPI backend's `/api/agent/invoke-tool` endpoint
> instead of executing in-process. This lets the React workbench show **live
> agent activity**. CAD builds stream progress, `project_changed` refreshes
> project metadata, and `viewer_asset_changed` refreshes the GLB/STL viewer URL
> with a cache-busting version token. The UI live-activity status may show
> `Live`, `Reconnecting`, or `Polling`; during stream failure the frontend polls
> the selected project until SSE reconnects. If the backend is down, the MCP server
> falls back to in-process execution (no live UI, but the call still works).
> Omit this var only for headless use where the UI does not need to track agent
> actions.

### macOS / Linux

Same `conda run` form (paths use forward slashes; the committed config already works
from a clone):

```json
{
  "mcpServers": {
    "aieng-workbench": {
      "command": "conda",
      "args": ["run", "-n", "aieng311", "--no-capture-output", "python", "-m", "app.mcp_server"],
      "cwd": "aieng-ui/backend",
      "env": {
        "AIENG_BACKEND_URL": "http://127.0.0.1:8000"
      }
    }
  }
}
```

If you don't use conda, point `command` at the interpreter that has build123d
directly, e.g. `"command": "/path/to/venv/bin/python", "args": ["-m", "app.mcp_server"]`.

## Per-agent config quick reference

Different agents discover MCP servers from different files. All of them are
checked into this repo so a fresh session needs no manual wiring:

| Agent | Config it reads | Status in this repo |
|-------|-----------------|---------------------|
| Claude Code | `.mcp.json` (project root) | ✅ committed |
| VS Code / GitHub Copilot | `.vscode/mcp.json` | ✅ committed |
| OpenAI Codex | `~/.codex/config.toml` `[mcp_servers.*]` (global) | `.codex/config.toml` template committed; copy to the global path |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | ✅ committed (`.cursor/mcp.json`) |
| Cline (VS Code ext.) | its own `cline_mcp_settings.json` (VS Code globalStorage) | copy the server block below into Cline's MCP settings |

Every agent also reads the workspace-root `AGENTS.md` (Codex/Cursor natively;
Claude Code via `CLAUDE.md` `@import`; Copilot via `.github/copilot-instructions.md`).

For per-client transport / prompt-resource visibility / approval-behavior
support — including which paths are tested vs documented — see the
[MCP client compatibility matrix](docs/mcp_client_compatibility.md).

### Codex (`~/.codex/config.toml`)

Codex uses TOML, not JSON, and its MCP config is global (not per-project). Add:

Codex's MCP config is global (not project-scoped), so `cwd` must be the absolute
path to *your* clone — replace it. The `command` stays portable via `conda run`:

```toml
[mcp_servers.aieng-workbench]
command = "conda"
args = ["run", "-n", "aieng311", "--no-capture-output", "python", "-m", "app.mcp_server"]
cwd = "<absolute-path-to-clone>/aieng-ui/backend"

[mcp_servers.aieng-workbench.env]
AIENG_BACKEND_URL = "http://127.0.0.1:8000"
```

Codex reads the project-root `AGENTS.md` automatically when the workspace is
trusted, so it gets the full guide on its own.

### Verifying the connection

After editing the config, restart Claude Code. Then ask:

> List the tools available from the aieng-workbench MCP server.

Claude Code should enumerate the registered tools. A second smoke test:

> Use aieng-workbench to inspect the project with id `<your-project-id>`.

This should fire `aieng.inspect_package` and return the semantic summary.

### Local agent health/preflight diagnostics

The FastAPI backend exposes local-agent readiness separately from MCP tool
listing:

```powershell
curl http://127.0.0.1:8000/api/local-agents/capabilities
curl http://127.0.0.1:8000/api/local-agents/preflight
curl "http://127.0.0.1:8000/api/local-agents/preflight?adapter=claude-code"
```

`/api/local-agents/capabilities` is the backward-compatible embedded-chat selector
summary. New UI flows should use an external MCP agent connected to this server;
these local-agent/autopilot endpoints are retained for compatibility and
maintainer diagnostics. `/api/local-agents/preflight` is the maintainer/debug
contract. Each adapter
entry includes `available`, normalized `status` (`ready`, `missing_binary`,
`auth_error`, `timeout`, `session_not_found`, `unsupported_flag`,
`unknown_error`), feature flags, an `actionable_fix`, and safe diagnostics
(resolved executable path, cwd, platform, PATH summary, and environment variable
names). Diagnostics may list `ANTHROPIC_*`, `CLAUDE_*`, `OPENAI_*`, or
`CODEX_*` variable **names**, but must not expose token/key values.

Claude Code-specific notes:

- The adapter does **not** pass `--bare` by default. `--bare` can make an
  otherwise-authenticated Windows Claude Code CLI report `Not logged in`; only
  opt in with `AIENG_CLAUDE_CODE_BARE=1` for explicit diagnostics.
- Plain CLI preflight mirrors the manual command
  `claude -p "Say hello" --output-format json`. Its timeout defaults to 20s and
  can be overridden with `AIENG_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS`.
- Runs created without a chat `session_id` derive a stable run-based Claude
  session id, so step 0 and approval/continue/resume steps address the same CLI
  conversation.
- `session_not_found` means the CLI could not resume the requested conversation;
  inspect adapter diagnostics or start a fresh run.

### Autopilot stale-run recovery semantics

`GET /api/agent/autopilot/runs/{run_id}` returns the persisted run plus:

- `stale`
- `recovery_state`: `active`, `needs_resume`, `waiting`, or `terminal`
- `stale_reason` when applicable

A persisted `status="running"` run becomes `stale=true` /
`recovery_state="needs_resume"` only when the backend has no live worker for it
and `updated_at` is older than the conservative stale threshold. The backend
does not rewrite it to `failed`. `awaiting_approval`, `blocked`, and `chatting`
are waiting states; `completed`, `failed`, and `cancelled` are terminal.

Stale running runs can be cleaned up safely with:

```powershell
curl -X POST http://127.0.0.1:8000/api/agent/autopilot/runs/<run_id>/cancel
```

The cancel path persists `status="cancelled"`, emits one public
`run_cancelled` terminal event, and repeated cancel calls on terminal runs are
no-ops.

### Autopilot event contract metadata

Agent transcript events remain backward compatible: consumers may continue to
use `type`, `status`, `content`, and `payload`. The backend also annotates live
and persisted events with:

- `category`: `status`, `progress`, `terminal`, `tool`, `approval`,
  `user_input`, `artifact`, or `diagnostic`
- `visibility`: `public` or `diagnostic`
- `user_visible`: boolean

The same fields are mirrored into `payload` because the current SQLite event
table stores payload JSON rather than dedicated metadata columns. Frontends
should use these fields to keep user-facing timelines compact:

- show `user_visible=true` rows by default;
- keep `visibility="diagnostic"` rows available in details/debug views;
- treat `agent_phase_changed` as diagnostic progress when it duplicates a
  public progress/status row for the same phase;
- treat `awaiting_approval`, `blocked`, and `chatting` as waiting states, not
  terminal failures;
- treat public terminal rows (`category="terminal"`) as mutually exclusive run
  endings. A normal completion emits one public `run_status_changed/completed`;
  a stale or user cancel emits one public `run_cancelled/cancelled`.

`tool_failed` is classified as a public `tool` event so the UI can show the
tool-level error without confusing adapter/tool failures with user cancellation.

## CAD modelling without an API key (agent writes the code)

The backend's built-in text-to-CAD flow (`/generate-cad-stream`) calls Claude
with `ANTHROPIC_API_KEY` to write build123d code. **You don't need that when
Claude Code is the agent** — Claude Code writes the build123d code itself using
its own subscription, and the framework just executes it.

The enabling tool is **`cad.execute_build123d`** (requires an approved modeling plan):

- Input: `{ project_id, code, mode?, write_files?, timeout?, thumbnail? }`
- `code` is a full build123d script that binds the model to a variable named
  `result` and **omits export calls** (the runner adds `export_step` /
  `export_stl` / `export_gltf`).
- **Name parts**: set `.label` on shapes and combine with `Compound` — labels become
  named parts in `topology_map.json` / `feature_graph.json` you can reference later.
- **Color parts**: set `.color = Color(r, g, b)` (RGB in 0..1) — colors render in
  the agent thumbnail and travel through to the GLB the UI viewer displays.
- **`mode`**: `replace` (default) or `append`. In append the previous model is exposed
  as `previous_result`; your code adds to it and still reassigns `result`.
- The tool runs the code in a sandboxed subprocess, writes `geometry/source.py`,
  `generated.step`, `preview.stl/.glb`, `topology_map.json`, `feature_graph.json`,
  and sets the project to `viewer_ready_glb`.
- **Returns**: a **2×2 multi-view PNG contact sheet** (front / side / top / iso) so
  the agent can verify silhouette and alignment from four angles at once, plus
  `named_parts`, `parts_added`, `mode`, `used_base`, topology summary, a
  preview URL, and a deterministic **`geometry_report`** (see below). When a
  reference image is attached, the layout expands to 2×3 with the reference
  filling the right column.

Companion read-only tool **`cad.get_source`** returns the accumulated source and
`{named_parts, has_base}` — call it before an incremental edit to decide replace vs
append and see which named parts already exist.

## Shape IR conversion path

`aieng.convert` accepts `.shape.json` / `.shape_ir.json` files in addition to
STEP/STP and FCStd. The core `shape_ir_reference` converter records the source
IR, projects semantic topology/features, and writes generated build123d
`geometry/source.py` without executing a CAD kernel. In the workbench runtime,
`aieng.convert` then executes that generated source by default and writes:

- `geometry/generated.step`
- `geometry/preview.stl`
- `geometry/preview.glb` when GLB export succeeds
- refreshed `geometry/topology_map.json`
- refreshed `graph/feature_graph.json`

Set `executeShapeIr: false` to keep the conversion evidence-only. Shape IR node
`type` / `kind` / `operation` values compile to the existing helper targets:
`lofted_stack`, `rounded_box`, `capsule`, `swept_tube`, `revolved_profile`,
`organic_blend`, plus primitive fallbacks (`box`, `cylinder`, `sphere`,
`tapered_cylinder`/`cone`). Example:

```json
{
  "parts": [
    {"id": "torso", "type": "lofted_stack", "sections": [[0,120,80],[200,150,90],[392,60]]},
    {"id": "head", "type": "sphere", "radius": 45, "location": [0,0,440]},
    {"id": "body", "type": "organic_blend", "children": ["torso", "head"], "radius": 12}
  ]
}
```

### Quantitative geometry report (numeric self-review)

Every `cad.execute_build123d` and `cad.edit_parameter` response carries a
`geometry_report` so the agent judges form from numbers, not just the low-res
thumbnail (LLMs read ratios far more reliably than 3D renders):

- `overall_proportions` — normalized H:W:D of the whole model (largest dim = 1.0).
- `parts[].ratio_to_largest` — each named part's size vs the biggest part.
- `symmetry[]` — for left/right name pairs (`arm_L`/`arm_R`, `motor_pod_FL`/`FR`):
  `ok:false` flags an asymmetric pair, `align_residual_mm` is how far off the
  mirror is, `status:missing_partner` means only one side was named.
- `gaps[]` / `floating_parts` — `status:floating` flags a detached part (usually a
  coordinate typo); `touching` means parts connect as intended.

### Fast parametric edits — `cad.edit_parameter` (approved modeling plan)

For pure dimensional tweaks of an existing model, prefer `cad.edit_parameter`
over regenerating: it replaces a named **UPPER_SNAKE_CASE** constant in
`geometry/source.py` and re-executes build123d — no LLM, sub-second to a few
seconds, fully reproducible.

- Input: `{ project_id, featureId, parameterName, newValue, timeout? }`
- `featureId` / `parameterName` come from the feature's `parameters` block in
  `feature_graph.json` (each carries a `cad_parameter_name` → the source constant).
- The value is validated against the parameter's declared `min_value`/`max_value`.
  If the edit breaks the build, the package is left untouched and an error is
  returned — the prior geometry is preserved.
- Works only when the source declares dimensions as named constants. Generated
  code does this automatically (system-prompt rule); hand-written code should too:
  `MOTOR_POD_RADIUS = 3` rather than a bare `3` inline.
- **`regression_diff`** in the response compares before/after topology by named
  part so a bad edit can't silently warp unrelated geometry. `verdict` is one of
  `clean` (only the target moved), `collateral_change` (WARNING — `collateral_parts`
  also moved; the constant is likely shared), `identical` (no-op / wrong constant),
  or `topology_changed` (a part appeared/disappeared). Collateral is not judged for
  `Global Parameters` edits, since shared dims are meant to move many parts.

### Part-level edits — `cad.replace_part` / `cad.remove_part` (approved modeling plan)

`append` only adds geometry. To fix or drop ONE part of a model without
resubmitting the whole script:
- `cad.remove_part { project_id, label }` — drops the part with that build123d
  `.label`.
- `cad.replace_part { project_id, label, code }` — swaps it for new build123d
  `code` (must reassign `result` to the new part and set `result.label`). The
  high-level helpers (lofted_stack, capsule, …) are available in `code`.

Both append a transform step to `geometry/source.py` (keeping the stored script
self-consistent), re-execute build123d (no LLM), and return a `regression_diff`.
**This is what makes step-by-step modelling visible**: build the model
incrementally with `append` + part-level edits and the viewer updates after each
call, so the user watches it assemble rather than seeing one monolithic build.

### Organic vs mechanical models — `model_kind`

`cad.execute_build123d` accepts `model_kind` (default `auto`). The feature-graph
heuristics (bolt-pattern detection, base-plate detection) are meant for
mechanical parts; on a character/vehicle/product they mislabel limb cylinders as
`mounting_hole_pattern` and the bottom face as a `base_plate`. Pass
`model_kind="organic"` to skip them (or `"mechanical"` to force them). `auto`
infers from part labels + whether the organic helpers are used. The resolved kind
is echoed in `feature_graph.model_kind`.

### Reference image calibration (recommended for named real-world targets)

When modelling a real product / character / vehicle, attach a reference image once
with **`cad.set_reference_image`** (pass `image_url` for an HTTP fetch or
`image_path` for a local file) BEFORE the first `cad.execute_build123d`. The image
is decoded, downscaled to fit 800×800, and stored as `geometry/reference.png` in
the project's `.aieng` package. Every subsequent `cad.execute_build123d` thumbnail
then tiles the reference next to the four views so the agent can compare
proportions against the truth instead of relying on memory.

### Autopilot CAD quality gate

The Local Agent Autopilot prompt is intentionally token-light: it asks the agent
to form a compact CAD brief before requesting `cad.execute_build123d`, then put
only a short approval summary in `user_message`. After an approved CAD mutation,
the backend runs read-only `cad.critique` automatically when registered and
compacts findings to the top blockers before feeding them back to the agent. This
keeps modeling iterations grounded without carrying full topology/audit payloads
through every prompt.

### Engineering audit (manufacturability check)

For mechanical parts (brackets, housings, fixtures, manifolds), label parts with
the canonical types from `aieng/schemas/feature_graph.schema.json` —
`base_plate` / `mounting_hole` / `mounting_hole_pattern` / `rib` / `boss` /
`flange` / `interface_face` / `wall` / `cover`. Then call **`cad.critique`** to
get a deterministic engineering audit: minimum wall thickness (3mm CNC default),
standard hole sizes, and floating-component detection. Returns a verdict
(`passes` / `passes_with_notes` / `passes_with_warnings` / `fails_audit`) plus
structured findings (severity, rule, affected feature, observation, suggested
fix) and a `fail_first_objections` list of the top blocking issues.

### Example session in Claude Code (incremental, named parts)

> Using aieng-workbench, build a quadcopter for project `6bdf0813f7c6`: start with the
> central body, then add the four motor pods.

Step 1 — `cad.get_source` shows `has_base: false`, so build the base with `mode: "replace"`:

```python
from build123d import *
body = Box(80, 80, 15); body.label = "fuselage"
result = Compound(children=[body])
```

Step 2 — `mode: "append"` adds onto `previous_result`, naming each new part:

```python
from build123d import *
pods = []
for i, (x, y) in enumerate([(40, 40), (-40, 40), (40, -40), (-40, -40)]):
    p = Cylinder(6, 20); p.label = f"motor_pod_{i+1}"
    pods.append(p.translate((x, y, 0)))
result = Compound(children=[previous_result, *pods])
```

Approve each call when prompted. The response's `parts_added` confirms exactly what each
step introduced, and the thumbnail lets you verify the shape before continuing. The model
is written to the package and the React workbench viewer updates automatically.

> **Note on the live build animation:** the in-UI step-by-step build animation
> (`CadProgressPanel`) only fires for generations that go through the backend
> SSE endpoint. When Claude Code drives generation via this MCP tool, you see
> the result after execution, not a live in-UI build. Bridging MCP-driven
> execution events back to the live UI is a planned follow-up (Phase 2 + B2).

## Approval-gated tools

Ordinary CAD build/edit tools use a plan-first confirmation contract: the agent
should prefer its native interactive question tool: Claude Code
`AskUserQuestion`, Codex `request_user_input`, or the client equivalent, with
Approve / Revise / Cancel options. This suspends and resumes the same task rather
than ending the conversation. If that capability is unavailable, the agent calls
the approval-gated, no-write `cad.confirm_modeling_plan` fallback so the MCP
client displays its permission dialog. After approval it may run
`cad.execute_build123d`, `cad.edit_parameter`, `cad.replace_part`,
`cad.remove_part`, and `cad.refine` repeatedly within that approved plan. These
execution tools advertise `readOnlyHint=false` but do not request per-call
approval.

High-risk operations such as `cad.restore_snapshot`, `cae.run_solver`,
`aieng.delete_project`, and `aieng.apply_shape_ir_patch` remain
`requires_approval=true`; the MCP server prepends `[APPROVAL REQUIRED]` so the
client can surface the side effect before invocation.

There are three MCP-first operating modes:

1. **Workbench-managed approval mode** (recommended local viewer mode): set
   `AIENG_MCP_MANAGED_APPROVAL=1` and `AIENG_BACKEND_URL` in the MCP server
   environment. The MCP server routes every approval-gated tool through the
   backend approval broker before execution, so the viewer's approval card is
   authoritative even if the connecting client has allow-listed the tool. If the
   broker/viewer is unavailable, the gated call is denied fail-safe.
2. **Client-managed approval mode**: leave both approval env flags unset.
   Approval-gated tools are advertised as `[APPROVAL REQUIRED]` and with the
   standard MCP `readOnlyHint=false` / `destructiveHint=true` annotations. The
   connecting MCP client is responsible for prompting the human before
   invocation. Use this only with a client permission UX you trust. Do not
   allow-list gated tools in Claude Code, and run Codex with
   `approval_policy="on-request"` (or `--ask-for-approval on-request`).
   An agent sentence such as "please confirm" is not approval. The agent should
   invoke `cad.confirm_modeling_plan`, let the client display the confirmation,
   and continue in the same task after approval.
3. **Hard-block planning/inspection mode**: set
   `AIENG_MCP_BLOCK_APPROVAL_TOOLS=1` in the MCP server environment. In this
   mode the MCP server rejects every registry tool with `requires_approval=true`
   before forwarding to the backend or invoking the runtime in-process. The
   structured response uses `status="error"` and `code="approval_blocked"`.

Hard-block mode is useful for safe inspection, planning, and prompt/resource
discovery and takes precedence over managed approval. It cannot execute CAD
mutations, package mutations, or solver runs. Disable the flag and use
workbench-managed approval when the human wants to perform those side effects
with the live viewer in the loop.

For the full constraint migration audit, see
[`../docs/mcp_constraint_migration_checklist.md`](../docs/mcp_constraint_migration_checklist.md).

## How tool calls reach the workbench

```text
Claude Code
   │ (stdio JSON-RPC: tools/call)
   ▼
mcp_server.py:_make_handler(name)
   │
   ▼
runtime.invoke_tool(name, args)
   │
   ▼
the same closure registered in app_factory.create_app()
   │
   ▼
package on disk / runtime state / etc.
```

This means the FastAPI HTTP backend does **not** need to be running for Claude
Code to use the workbench — the MCP server boots its own in-process copy of
the runtime registry. Run both concurrently if you also want the React UI to
stay live.

## Adding a JSON schema to a new tool

1. Add an entry to `app/runtime_tool_schemas.py:TOOL_SCHEMAS`.
2. At the registration call in `app_factory.py`, pass
   `input_schema=_schema("<tool.name>")`.
3. Run `pytest tests/test_mcp_server.py::test_high_frequency_tools_carry_curated_schema`
   to confirm it's surfaced.

The curated-schema approach is intentional: auto-deriving schemas from handler
signatures gives low-quality output because tool handlers all have the same
`(inp: dict, ctx: dict)` signature.

### Provider-compatible schema design

Some MCP providers (e.g. OpenAI Codex, Kimi Code CLI) require tool input schemas
to be a plain object at the top level and reject ``oneOf`` / ``anyOf`` / ``allOf`` /
``enum`` / ``not`` at the schema root.  Keep every schema in ``TOOL_SCHEMAS`` as:

```json
{
  "type": "object",
  "properties": { ... },
  "required": [ ... ]
}
```

If a parameter has multiple valid shapes (e.g. a legacy alias), prefer explicit
optional fields and runtime validation over a top-level union.  The regression
guard is ``pytest tests/test_mcp_server.py::test_all_mcp_tool_schemas_are_provider_compatible``.
A standalone validator also lives at ``scripts/validate_mcp_schemas.py``.
