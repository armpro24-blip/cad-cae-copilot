"""MCP server exposing the workbench runtime tool registry.

Lets external agents (Claude Code, Cursor, Cline, etc.) drive the workbench
via their own tool-calling harness — no need to reimplement the LLM loop.

Architecture:
    runtime._REGISTRY (single source of truth, registered by create_app)
        │
        ▼
    list_tools_for_mcp() → MCP tool definitions with JSON schemas
        │
        ▼
    FastMCP server → stdio (default) or HTTP (--http)

Usage:
    # stdio (Claude Code-style):
    aieng-workbench-mcp
    python -m aieng_workbench_mcp

    # HTTP transport for debugging or multi-client:
    aieng-workbench-mcp --http --port 8765

The server boots a FastAPI app instance just to trigger the existing
``create_app()`` tool registration; the FastAPI request handlers themselves
are never used. All tool invocations are dispatched through
``runtime.invoke_tool``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import ToolAnnotations
from pydantic import ConfigDict

from . import mcp_approval
from . import mcp_tool_surface

logger = logging.getLogger(__name__)


class _PassthroughArgModel(ArgModelBase):
    """Arg model that accepts any fields and forwards them all as kwargs.

    Our tool handlers use a generic ``def _handler(**kwargs)`` signature. FastMCP
    derives each tool's argument model from that signature, which yields a model
    requiring a single field literally named ``kwargs`` — so a client sending the
    real fields (project_id, code, mode, …) fails validation and the call never
    reaches the handler. We replace the derived model with this passthrough one:
    no required fields, extras allowed, and ``model_dump_one_level`` returns every
    provided field so the handler receives them as kwargs. The advertised JSON
    schema (curated, set on ``tool.parameters``) is unaffected.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def model_dump_one_level(self) -> dict[str, Any]:
        return dict(self.__pydantic_extra__ or {})

# When set, tool calls are forwarded to the running FastAPI backend's
# /api/agent/invoke-tool endpoint instead of executing in-process. This is what
# lets the React UI render live agent activity (CAD build animation) — the
# single backend process owns state AND emits SSE events. Configure it in the
# Claude Code mcp.json env block: AIENG_BACKEND_URL=http://127.0.0.1:8000
_BACKEND_URL = (os.environ.get("AIENG_BACKEND_URL") or "").rstrip("/")

_GUIDE_REQUIRED_PREFIXES = {
    "cad.": "cad",
    "cae.": "cae",
    "postprocess.": "cae",
    "opt.": "cae",
}
_PACKAGE_GUIDE_TOOLS = {
    "aieng.apply_shape_ir_patch",
    "aieng.convert",
    "aieng.delete_project",
    "aieng.generate_preview",
    "aieng.refresh_semantics",
    "aieng.update_validation_status",
    "aieng.validate",
    "aieng.write_completeness_report",
    "aieng.write_evidence_scaffold",
}


def _guide_guard_enabled() -> bool:
    """Require task-specific guide reads before category tool calls by default."""
    return os.environ.get("AIENG_MCP_REQUIRE_GUIDES", "1") != "0"


def _required_guide_topic(tool_name: str) -> str | None:
    if tool_name in _PACKAGE_GUIDE_TOOLS:
        return "package"
    for prefix, topic in _GUIDE_REQUIRED_PREFIXES.items():
        if tool_name.startswith(prefix):
            return topic
    return None


def _guide_required_result(tool_name: str, topic: str, read_topics: set[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "code": "guide_required",
        "tool": tool_name,
        "required_topic": topic,
        "read_topics": sorted(read_topics),
        "message": (
            f"Read aieng.guide with topic={topic!r} once in this MCP session "
            f"before calling {tool_name}. The tool was not executed."
        ),
        "next_call": {"tool": "aieng.guide", "input": {"topic": topic}},
    }


def _forward_to_backend(tool_name: str, args: dict[str, Any]) -> Any:
    """POST the tool call to the running backend; return its JSON result.

    Raises urllib.error.URLError if the backend is unreachable so the caller
    can decide whether to fall back to in-process execution.
    """
    url = f"{_BACKEND_URL}/api/agent/invoke-tool"
    body = json.dumps({"tool": tool_name, "input": args}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Generous timeout: a real solver run or build can take a while; the backend
    # streams progress to the UI meanwhile.
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _coerce_result(value: Any) -> str:
    """Serialise tool return value to a JSON string for MCP text content.

    MCP tools return text content blocks. Runtime handlers return arbitrary
    Python objects (mostly dicts); JSON is the lossless representation that
    survives the agent's parser.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str, indent=2)
    except Exception:
        return repr(value)


def _finalize_result(value: Any) -> Any:
    """Convert a tool result into MCP content.

    If the result dict carries a ``thumbnail_png_base64`` field (rendered by
    ``cad.execute_build123d`` so the agent can *see* the geometry), strip it from
    the JSON text and return ``[text, Image]`` so the MCP client renders the
    thumbnail as an actual image rather than an opaque base64 blob. Otherwise
    return the plain JSON string.
    """
    if isinstance(value, dict) and value.get("thumbnail_png_base64"):
        import base64

        b64 = value.get("thumbnail_png_base64")
        rest = {k: v for k, v in value.items() if k != "thumbnail_png_base64"}
        text = _coerce_result(rest)
        try:
            image = Image(data=base64.b64decode(b64), format="png")
            return [text, image]
        except Exception:
            return text
    return _coerce_result(value)


_SERVER_DESCRIPTION = """\
aieng Workbench — CAD/CAE co-pilot for mechanical engineering.

MANDATORY SESSION ONBOARDING — aieng.agent_readme MUST be called before any
other tool in this session. It returns the current operational contract and
available task-specific guide topics. Skipping it risks stale or incomplete
tool behavior.

After onboarding, call aieng.list_projects to discover available project IDs.

This server exposes real 3D CAD modeling (build123d / OpenCASCADE) and
structural FEA (CalculiX) tools. cad.execute_build123d runs your Python code
against build123d and produces actual STEP/STL/GLB geometry — no API key needed.

Quick start (MANDATORY ORDER — do not skip step 1):
  1. aieng.agent_readme      → REQUIRED operational onboarding
  2. aieng.list_projects     → discover project IDs
  3. aieng.agent_context { project_id }  → geometry state + suggested next steps

Before the first CAD modeling/edit action, call aieng.guide { topic: "cad" }.
Before the first simulation/solver action, call aieng.guide { topic: "cae" }.
Use other guide topics when their task category applies.
The server enforces these reads before CAD, CAE, and package-lifecycle tools.

Sustainable modeling loop: cad.get_source (read state) → cad.execute_build123d
with mode=append (build onto `previous_result`; set `.label` and
`.color = Color(r,g,b)` on parts so the topology gets named parts and the
returned thumbnail shows them in distinct colors) → read the returned 2×2
contact-sheet thumbnail (front/side/top/iso) + named_parts/parts_added to
verify silhouette and alignment, then repeat.

Token-aware loop: use response_detail="compact" for routine CAD iterations.
Compact responses keep named_parts/parts_added and a one-line geometry summary,
omit the thumbnail unless thumbnail=true, and may return cache_hit=true when the
exact same build123d source was already executed.

Reference-driven modelling: when the target is a real product/character/
vehicle, call cad.set_reference_image first with image_url or image_path —
every subsequent thumbnail will tile the reference next to the four views
so proportions can be calibrated against the truth.

Engineering parts (brackets / housings / fixtures): label parts with the
canonical types from aieng/schemas/feature_graph.schema.json (base_plate,
mounting_hole, rib, boss, flange, ...) and call cad.critique afterwards
for a deterministic manufacturability audit (min wall thickness, standard
hole sizes, floating components).
"""


_MCP_DISCIPLINE_TEXT = """\
AIENG MCP-first workbench discipline

Use the workbench as a live CAD/CAE capability layer. Your agent is the brain;
the UI is the 3D viewer, spatial input surface, and audit/approval mirror.

MANDATORY FIRST CALLS — these are REQUIRED, not optional. They establish the
operational contract and current project state:
1. aieng.agent_readme  (MUST be called before any other tool)
2. aieng.list_projects
3. aieng.agent_context { project_id }

Guide-read enforcement:
- CAD tools require aieng.guide {topic:"cad"} once per MCP server instance.
- CAE, post-processing, and topology-optimization tools require topic "cae".
- Package-lifecycle tools require topic "package".
- Skipping a required read returns code=guide_required before approval/execution.
- Reading topic "full" (or agent_readme detail="full") unlocks all categories.

Approval boundary:
- Tools advertised with [APPROVAL REQUIRED] mutate CAD/packages or execute
  external processes.
- In workbench-managed approval mode (AIENG_MCP_MANAGED_APPROVAL=1), this server
  routes gated tools through the backend approval broker so the live viewer's
  approval card is authoritative; unavailable approval fails safe.
- In client-managed approval mode, the MCP client must ask the human before
  invoking those tools.
- If AIENG_MCP_BLOCK_APPROVAL_TOOLS=1, this server rejects gated tools with
  code=approval_blocked before backend forwarding or in-process execution. This
  hard-block mode takes precedence over managed approval.

Pointer ergonomics:
- Treat @face, @edge, @feature, @part, and @artifact pointers as opaque IDs from
  the current workbench context. Copy them verbatim; do not invent IDs.
- If geometry changed after a pointer was copied, refresh agent_context or the
  topology map before applying loads, constraints, or localized edits.
- If a pointer cannot be resolved, ask for a new selection or report the target
  as unavailable instead of guessing a nearby face.

CAD discipline:
- Prefer cad.get_source before edits.
- Use cad.execute_build123d for create/add/remove geometry; declare dimensions
  as UPPER_SNAKE_CASE constants, set .label and .color on parts, bind final
  geometry to result, and omit exports.
- Use cad.edit_parameter for pure dimension changes when editable parameters
  exist; read regression_diff after the edit.
- Inspect the returned 4-view thumbnail (front/side/top/iso) and geometry_report;
  do fail-first review before adding more details.
- For engineering parts, use canonical labels (base_plate, mounting_hole, rib,
  boss, flange, interface_face) and call cad.critique.
- For standard parts (fasteners, nuts, washers, bearings, gears, threads, pipes,
  flanges) prefer bd_warehouse over primitive approximations — it is pre-bound in
  the cad.execute_build123d namespace as the modules fastener / bearing / gear /
  thread / pipe / flange / sprocket (e.g. fastener.SocketHeadCapScrew("M6-1",
  length=12, simple=True)); use simple=True unless real thread geometry is needed.

Industrial-design / manufacturability review:
- For brackets, fixtures, housings, and load-bearing interfaces, check minimum
  wall thickness, hole standards, ribs/bosses, floating parts, and edge/corner
  radii before claiming a design is ready.
- Use cad.critique for deterministic manufacturing findings. Treat it as a
  design-review assistant, not as production certification.
- For visual/product form work, explicitly compare the 4-view result against the
  stated reference or user intent and call out the biggest mismatches first.

CAE discipline:
- Never claim the solver ran unless cae.run_solver returned successful solver
  evidence.
- Inspect context/readiness first, patch setup only when needed, then call
  cae.prepare_solver_run, cae.generate_solver_input when the deck is missing,
  and only then cae.run_solver through the approval boundary.
- Report only evidence-backed stress/displacement values and state limitations.
"""


def _register_mcp_first_prompts_and_resources(mcp: FastMCP) -> None:
    """Expose portable MCP-first guidance for clients without local skills."""

    @mcp.prompt(
        name="aieng_mcp_first_onboarding",
        description="Start here: operate AIENG as a BYO-agent MCP-first CAD/CAE workbench.",
    )
    def aieng_mcp_first_onboarding() -> str:
        return (
            "You are driving the AIENG Workbench through MCP. Treat the UI as "
            "a live 3D viewer + spatial input surface. MANDATORY: call "
            "aieng.agent_readme FIRST for the current operational contract. Then "
            "call aieng.list_projects, then aieng.agent_context. Before the first "
            "CAD modeling/edit action call aieng.guide with topic='cad'; before "
            "simulation/solver work call it with topic='cae'. Use "
            "@face/@edge/@feature/@artifact pointers verbatim and refresh context "
            "after topology-changing edits before reusing old face IDs. "
            "Respect [APPROVAL REQUIRED] tools; AIENG_MCP_MANAGED_APPROVAL=1 routes "
            "them through the workbench viewer approval card, while "
            "AIENG_MCP_BLOCK_APPROVAL_TOOLS=1 refuses them outright. Report only "
            "evidence from tool returns and package artifacts; never claim solver "
            "results unless solver execution evidence exists."
        )

    @mcp.prompt(
        name="aieng_cad_build_workflow",
        description="CAD build/edit workflow using the active cad.* MCP tools.",
    )
    def aieng_cad_build_workflow() -> str:
        return (
            "CAD workflow: call aieng.agent_context, then cad.get_source. For "
            "new or additive geometry call cad.execute_build123d with build123d "
            "code that binds result, sets .label/.color, uses UPPER_SNAKE_CASE "
            "dimension constants, and omits exports. Use mode='append' only "
            "when cad.get_source reports has_base. Inspect the 4-view thumbnail, "
            "geometry_report, named_parts, parts_added, symmetry, and gaps. For "
            "pure dimension changes prefer cad.edit_parameter and check "
            "regression_diff. For engineering parts, run cad.critique before "
            "claiming manufacturability."
        )

    @mcp.prompt(
        name="aieng_cae_simulation_workflow",
        description="CAE simulation workflow with preflight, approval, and evidence honesty.",
    )
    def aieng_cae_simulation_workflow() -> str:
        return (
            "CAE workflow: inspect aieng.agent_context/readiness first. Ensure "
            "material, loads, and constraints are explicit before solver work. "
            "Use cae.apply_setup_patch only for setup artifacts, then "
            "cae.prepare_solver_run. If the input deck is missing, call "
            "cae.generate_solver_input. Run cae.run_solver only after a "
            "successful preflight and only through the approval boundary. After "
            "solver execution, call cae.extract_solver_results, optionally "
            "cae.extract_field_regions, and postprocess.refresh_cae_summary. "
            "Never claim solver results unless solver evidence exists."
        )

    @mcp.resource(
        "aieng://guides/mcp-first-discipline",
        name="aieng_mcp_first_discipline",
        title="AIENG MCP-first discipline",
        description="Condensed CAD/CAE operating rules for BYO MCP agents.",
        mime_type="text/plain",
    )
    def aieng_mcp_first_discipline() -> str:
        return _MCP_DISCIPLINE_TEXT


_PERMISSION_TOOL_NAME = "request_approval"


def _permission_decision_deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message}, ensure_ascii=False)


def _mcp_hard_blocks_approval_tools() -> bool:
    """True when raw MCP calls to approval-gated tools must be rejected."""
    return os.environ.get("AIENG_MCP_BLOCK_APPROVAL_TOOLS") == "1"


def _approval_blocked_result(tool_name: str) -> dict[str, Any]:
    return {
        "status": "error",
        "code": "approval_blocked",
        "tool": tool_name,
        "message": (
            f"{tool_name} mutates project state. AIENG_MCP_BLOCK_APPROVAL_TOOLS=1 "
            "(inspection-only mode) is enabled, so this MCP server refuses "
            "CAD/CAE/package mutations — approval-gated or plan-boundary — "
            "instead of executing them."
        ),
    }


def _agentic_mode() -> bool:
    """True when this MCP server was spawned for an agentic session (Approach A).

    Set via the per-run MCP config env injected by ``claude_agent_session``. When
    off (e.g. the normal VSCode MCP client), gated tools are NOT intercepted here —
    that client uses its own approval UX.
    """
    return os.environ.get("AIENG_AGENTIC_PERMISSION_TOOL") == "1"


def _managed_approval_mode() -> bool:
    """True when a plain external MCP agent should route gated mutations through
    the **workbench** approval surface (server-enforced, shown in the live viewer)
    instead of relying on the connecting client's own permission UX.

    Opt-in per connection via ``AIENG_MCP_MANAGED_APPROVAL=1`` (set in the repo
    ``.mcp.json`` env, where a backend + viewer are expected). This is what makes
    the workbench the approval authority for any MCP agent — a user allow-listing
    the workbench tools in their client cannot bypass it. Requires the backend
    (broker + viewer) to be reachable; if not, the gated tool is denied (fail-safe).
    """
    return os.environ.get("AIENG_MCP_MANAGED_APPROVAL") == "1"


def _broker_approval_mode() -> bool:
    """Either approval mode that routes a gated tool through the backend broker."""
    return _agentic_mode() or _managed_approval_mode()


def _elicit_approval_mode() -> bool:
    """True when gated mutations are approved via MCP client elicitation (#228).

    Opt-in per connection via ``AIENG_MCP_APPROVAL_MODE=elicit`` (CLI
    ``--approval-mode elicit``). This makes BYO / headless agents usable WITHOUT
    the workbench web viewer: the server asks the connecting client to prompt the
    human. If the client cannot show a prompt (no elicitation capability), the
    gated tool fails safe (deny). Broker modes take precedence when both are set.
    """
    return (
        os.environ.get(mcp_approval.ELICIT_APPROVAL_MODE_ENV) == mcp_approval.ELICIT_APPROVAL_MODE_VALUE
        and not _broker_approval_mode()
    )


def _apply_cli_runtime_options(
    *,
    backend_url: str | None = None,
    approval_mode: str = "inherit",
    data_dir: str | None = None,
    require_guides: bool | None = None,
    compact_surface: bool = False,
) -> None:
    """Apply console-script options before ``create_app()`` reads env vars."""
    global _BACKEND_URL

    if backend_url is not None:
        normalized = backend_url.rstrip("/")
        if normalized:
            os.environ["AIENG_BACKEND_URL"] = normalized
        else:
            os.environ.pop("AIENG_BACKEND_URL", None)
        _BACKEND_URL = normalized

    if data_dir:
        os.environ["AIENG_PLATFORM_DATA"] = str(Path(data_dir).expanduser().resolve())

    if require_guides is not None:
        os.environ["AIENG_MCP_REQUIRE_GUIDES"] = "1" if require_guides else "0"

    if compact_surface:
        os.environ["AIENG_MCP_COMPACT_SURFACE"] = "1"

    if approval_mode == "inherit":
        return
    os.environ.pop("AIENG_MCP_MANAGED_APPROVAL", None)
    os.environ.pop("AIENG_MCP_BLOCK_APPROVAL_TOOLS", None)
    os.environ.pop(mcp_approval.ELICIT_APPROVAL_MODE_ENV, None)
    if approval_mode == "managed":
        os.environ["AIENG_MCP_MANAGED_APPROVAL"] = "1"
    elif approval_mode == "block":
        os.environ["AIENG_MCP_BLOCK_APPROVAL_TOOLS"] = "1"
    elif approval_mode == "elicit":
        os.environ[mcp_approval.ELICIT_APPROVAL_MODE_ENV] = mcp_approval.ELICIT_APPROVAL_MODE_VALUE
    elif approval_mode != "client":
        raise ValueError(f"unsupported approval mode: {approval_mode}")


def _agentic_permission_decision(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Block on the backend approval broker for a gated tool; return the decision.

    Returns the Claude permission contract dict:
        {"behavior": "allow", "updatedInput": {...}} | {"behavior": "deny", "message": "..."}
    Fail-safe: any backend/transport error returns DENY — a gated mutation is never
    auto-allowed when the approval UI is unreachable.
    """
    import time as _time

    deny = {"behavior": "deny", "message": "approval unavailable; refusing to auto-allow."}
    if not _BACKEND_URL:
        return deny
    # Managed-approval mode (plain external MCP agent): the agent has no autopilot
    # run/session, so scope the approval to the project named in the tool input —
    # that is what the workbench viewer filters on to render the prompt.
    project_id = tool_input.get("project_id") if isinstance(tool_input, dict) else None
    body = {
        "tool_name": tool_name,
        "input": tool_input,
        "run_id": os.environ.get("AIENG_AUTOPILOT_RUN_ID"),
        "project_id": project_id or os.environ.get("AIENG_AUTOPILOT_PROJECT_ID"),
        "session_id": os.environ.get("AIENG_AUTOPILOT_SESSION_ID"),
    }

    def _post(path: str, data: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{_BACKEND_URL}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(path: str, timeout: int = 30) -> dict[str, Any]:
        with urllib.request.urlopen(f"{_BACKEND_URL}{path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # Pre-flight: an approval request can only be resolved by a connected
    # approval surface (a workbench viewer). If none is listening, creating the
    # request would block to AIENG_AGENTIC_APPROVAL_TIMEOUT_SECONDS (~15 min)
    # with nobody able to answer — a silent stall on the very first gated call
    # in a headless / BYO-agent session. Detect that up front and fail fast with
    # a clear, structured signal. (The gate is NOT bypassed — the tool still does
    # not run.) A failed pre-flight is non-fatal: fall through to _post, which
    # fail-safe denies if the backend itself is unreachable.
    try:
        surface = _get("/api/agent/agentic/approval-surface", timeout=10)
    except Exception as exc:
        logger.warning("approval surface pre-flight failed for %s: %s", tool_name, exc)
        surface = None
    if isinstance(surface, dict) and surface.get("available") is False:
        return {
            "behavior": "deny",
            "code": "approval_surface_unavailable",
            "message": (
                "Approval is required for this tool, but no workbench approval "
                "surface is connected — no viewer is listening to approve, so the "
                "request cannot be answered. Open the workbench UI to approve, or "
                "run the MCP server with --approval-mode client (let your own agent "
                "prompt for approval) or --approval-mode block. The tool was NOT "
                "executed; re-run it once an approval surface is available."
            ),
            "recoverable": True,
        }

    try:
        created = _post("/api/agent/agentic/permission", body)
    except Exception as exc:  # backend unreachable / error → fail safe
        logger.warning("approval bridge create failed for %s: %s", tool_name, exc)
        return deny
    if created.get("status") == "resolved":
        return created.get("decision") or deny
    permission_id = created.get("permission_id")
    if not permission_id:
        return deny
    try:
        total_timeout = int(os.environ.get("AIENG_AGENTIC_APPROVAL_TIMEOUT_SECONDS", "900"))
    except ValueError:
        total_timeout = 900
    # Long-poll: each GET blocks up to ~20s server-side, so we wait quietly for
    # the user instead of spamming the endpoint every 1.5s.
    deadline = _time.monotonic() + max(30, total_timeout)
    while _time.monotonic() < deadline:
        try:
            status = _get(f"/api/agent/agentic/permission/{permission_id}?wait=20", timeout=25)
        except Exception as exc:
            logger.warning("approval bridge poll failed for %s: %s", tool_name, exc)
            _time.sleep(2.0)
            continue
        if status.get("status") == "resolved":
            return status.get("decision") or deny
    return {
        "behavior": "deny",
        "message": (
            "Approval timed out (the user did not respond within the timeout window). "
            "The tool was NOT executed. To try again, simply re-run the same tool call — "
            "a new approval request will be surfaced in the workbench UI."
        ),
        "recoverable": True,
    }


def _client_supports_elicitation(ctx: Any) -> bool:
    """Best-effort check that the connected client can show an approval prompt.

    Returns False (→ fail-safe deny) on any uncertainty: no context, no session,
    or the client did not advertise the MCP ``elicitation`` capability.
    """
    try:
        from mcp.types import ClientCapabilities, ElicitationCapability

        session = ctx.request_context.session
        return bool(
            session.check_client_capability(
                ClientCapabilities(elicitation=ElicitationCapability())
            )
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("elicitation capability check failed: %s", exc)
        return False


async def _elicit_permission_decision(
    tool_name: str, tool_input: dict[str, Any], *, ctx: Any | None = None
) -> dict[str, Any]:
    """Approve a gated tool by asking the connecting MCP client to prompt the user.

    Headless-friendly: needs no workbench viewer. Returns the permission contract
    ``{"behavior": "allow"|"deny", ...}``. Fail-safe: if there is no elicitation
    surface, or the prompt errors, the tool is DENIED — never auto-allowed.
    """
    if ctx is None or not _client_supports_elicitation(ctx):
        return mcp_approval.no_surface_deny(tool_name)
    try:
        result = await ctx.elicit(
            message=mcp_approval.elicit_approval_message(tool_name, tool_input),
            schema=mcp_approval.ApprovalConfirmation,
        )
    except Exception as exc:
        logger.warning("elicitation approval prompt failed for %s: %s", tool_name, exc)
        return {
            "behavior": "deny",
            "message": f"Approval prompt failed ({type(exc).__name__}); the tool was NOT executed.",
            "recoverable": True,
        }
    return mcp_approval.decision_from_elicitation(
        getattr(result, "action", "cancel"), getattr(result, "data", None)
    )


def _request_approval_handler(**kwargs: Any) -> Any:
    """Optional ``--permission-prompt-tool`` bridge (kept for completeness).

    The PRIMARY enforcement is in the per-tool handler (see ``_make_handler``),
    which is independent of Claude's permission settings. This tool is no longer
    registered by default.
    """
    tool_name = str(kwargs.get("tool_name") or "")
    tool_input = kwargs.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not tool_name:
        return _permission_decision_deny("permission bridge: missing tool_name")
    return json.dumps(_agentic_permission_decision(tool_name, tool_input), ensure_ascii=False)


def _agent_skill_dirs() -> list[Path]:
    """Modeling/CAE *agent* skill dirs (aieng-agent-skills/skills/*/SKILL.md).

    Deliberately NOT the repo `.claude/skills/` (dev skills such as superpowers) —
    dev skills must not leak to connecting modeling/CAE agents. mcp_server.py lives
    at aieng-ui/backend/app/, so the repo root is parents[3].
    """
    root = Path(__file__).resolve().parents[3]
    skills_root = root / "aieng-agent-skills" / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(d for d in skills_root.iterdir() if (d / "SKILL.md").is_file())


def _parse_skill_frontmatter(text: str) -> tuple[str | None, str | None, str]:
    """Best-effort parse of a SKILL.md: return (name, description, body).

    Avoids a YAML dependency — reads the leading ``---`` frontmatter block for
    ``name:`` / ``description:`` and returns the remaining markdown as the body.
    """
    name: str | None = None
    description: str | None = None
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter, body = parts[1], parts[2]
            for line in frontmatter.splitlines():
                stripped = line.strip()
                lowered = stripped.lower()
                if name is None and lowered.startswith("name:"):
                    name = stripped.split(":", 1)[1].strip().strip("\"'")
                elif description is None and lowered.startswith("description:"):
                    description = stripped.split(":", 1)[1].strip().strip("\"'")
    return name, description, body.strip()


def _register_agent_skill_prompts(mcp: FastMCP) -> None:
    """Expose the modeling/CAE agent skills as MCP **prompts**.

    This is how the workbench's CAD/CAE discipline reaches ANY connecting MCP
    client (portable, client-agnostic) — the MCP-first answer to "skill discovery"
    that keeps *agent* skills separate from *dev* skills (the latter stay in
    `.claude/skills/` and are never registered here). A connecting agent can list
    and pull e.g. ``aieng-cad-authoring`` to load the authoring playbook on demand.
    Read-only; always registered.
    """
    try:
        from mcp.server.fastmcp.prompts import Prompt
    except Exception:  # pragma: no cover - prompts API unavailable
        try:
            from mcp.server.fastmcp.prompts.base import Prompt  # type: ignore
        except Exception:
            return
    for skill_dir in _agent_skill_dirs():
        try:
            text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        except Exception:  # pragma: no cover - unreadable skill file
            continue
        fm_name, fm_desc, body = _parse_skill_frontmatter(text)
        prompt_name = fm_name or skill_dir.name
        description = fm_desc or f"aieng workbench skill: {prompt_name}"
        content = body or text

        def _make_skill_fn(payload: str):
            def _skill_prompt() -> str:
                return payload
            return _skill_prompt

        try:
            mcp.add_prompt(Prompt.from_function(_make_skill_fn(content), name=prompt_name, description=description))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("failed to register agent skill prompt %s: %s", prompt_name, exc)


def _register_permission_tool(mcp: FastMCP) -> None:
    """Register the agentic-session permission bridge tool.

    Gated behind ``AIENG_AGENTIC_PERMISSION_TOOL=1`` (set by the session driver)
    so normal MCP usage (e.g. the VSCode client) never sees this internal tool.
    """
    if os.environ.get("AIENG_AGENTIC_PERMISSION_TOOL") != "1":
        return
    schema = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Name of the tool the agent wants to run."},
            "input": {"type": "object", "description": "Proposed tool input.", "additionalProperties": True},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
        "additionalProperties": True,
    }
    description = (
        "Approval bridge: decides whether a gated workbench tool may run. Returns "
        "{\"behavior\":\"allow\"|\"deny\"}. Used as Claude Code's permission-prompt-tool."
    )
    handler = _request_approval_handler
    handler.__name__ = _PERMISSION_TOOL_NAME
    handler.__doc__ = description
    mcp.add_tool(handler, name=_PERMISSION_TOOL_NAME, description=description, structured_output=False, annotations=None)
    tool_obj = mcp._tool_manager._tools.get(_PERMISSION_TOOL_NAME)  # type: ignore[attr-defined]
    if tool_obj is not None:
        tool_obj.parameters = schema
        tool_obj.fn_metadata = FuncMetadata(arg_model=_PassthroughArgModel)


def _build_mcp_server(name: str = "aieng-workbench", *, compact_surface: bool | None = None) -> FastMCP:
    """Instantiate FastMCP and register runtime tools from the workbench.

    Side effect: triggers ``create_app()`` so the runtime tool registry is
    populated before we read it. The FastAPI app is otherwise discarded.

    Args:
        compact_surface: If True, expose only the curated subset of tools for
            clients with a small active-tool budget (e.g. Cursor). If None,
            the value is read from ``AIENG_MCP_COMPACT_SURFACE``. The full
            surface is exposed when both are false.
    """
    if compact_surface is None:
        compact_surface = mcp_tool_surface.compact_surface_enabled()
    # Import lazily so module-load doesn't pay for FastAPI when only the
    # registry shape is needed (e.g. in tests that don't need a server).
    from .app_factory import create_app
    from . import runtime as _rt

    create_app()  # populates runtime._REGISTRY
    tool_defs = _rt.list_tools_for_mcp()
    if compact_surface:
        tool_defs = [t for t in tool_defs if mcp_tool_surface.is_essential_mcp_tool(t["name"])]

    mcp = FastMCP(name, instructions=_SERVER_DESCRIPTION)
    _register_mcp_first_prompts_and_resources(mcp)
    read_guide_topics: set[str] = set()

    # Onboarding tools first — agents see these at the top of the tool list
    # and are more likely to call them before attempting other operations.
    _ONBOARDING_FIRST = ("aieng.agent_readme", "aieng.guide", "aieng.list_projects", "aieng.agent_context")
    tool_defs = sorted(
        tool_defs,
        key=lambda t: (0 if t["name"] in _ONBOARDING_FIRST else 1, t["name"]),
    )

    for tool_def in tool_defs:
        tool_name = tool_def["name"]
        # MCP protocol name: dots are not valid in function names for many API
        # providers (e.g. Kimi, OpenAI). Replace with underscores for the
        # external-facing name; internal routing still uses the dotted name.
        mcp_name = tool_name.replace(".", "_")
        description = tool_def.get("description") or tool_name
        if tool_def.get("requires_approval"):
            description = (
                f"[APPROVAL REQUIRED] {description}\n\n"
                "This tool performs an action with side effects (e.g. solver "
                "execution or CAD modification). The MCP client should prompt "
                "the human before invoking it."
            )
        input_schema = tool_def.get("input_schema") or {
            "type": "object",
            "additionalProperties": True,
        }

        def _make_handler(name_: str, requires_approval: bool = False, *, is_mutation: bool = False):
            def _preflight(args: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
                """Guide-guard + readme tracking + inspection-only block.

                Returns ``(early_result_or_None, args)``; a non-None early result
                must be returned to the client immediately (no execution).
                """
                if name_ == "aieng.guide":
                    topic = str(args.get("topic") or "").strip().lower()
                    if topic == "full":
                        from . import agent_guides

                        read_guide_topics.update(agent_guides.TOPIC_SECTIONS)
                    elif topic:
                        read_guide_topics.add(topic)
                elif name_ == "aieng.agent_readme" and str(args.get("detail") or "").lower() == "full":
                    from . import agent_guides

                    read_guide_topics.update(agent_guides.TOPIC_SECTIONS)

                required_topic = _required_guide_topic(name_)
                if (
                    _guide_guard_enabled()
                    and required_topic is not None
                    and required_topic not in read_guide_topics
                ):
                    return _coerce_result(_guide_required_result(name_, required_topic, read_guide_topics)), args

                # Inspection-only mode blocks ANY mutating tool, not only the
                # approval-gated ones. CAD authoring/edit tools are
                # requires_approval=False (approval lives at the modeling-plan
                # boundary) but still mutate the package, so gate on is_mutation
                # too — otherwise block mode would silently let CAD edits run.
                if (requires_approval or is_mutation) and _mcp_hard_blocks_approval_tools():
                    return _coerce_result(_approval_blocked_result(name_)), args
                return None, args

            def _apply_decision(decision: dict[str, Any], args: dict[str, Any]) -> tuple[Any | None, dict[str, Any]]:
                """Turn an approval decision into ``(error_result_or_None, args)``."""
                if decision.get("behavior") != "allow":
                    error: dict[str, Any] = {
                        "status": "error",
                        "code": decision.get("code") or "approval_denied",
                        "message": decision.get("message") or "Approval was denied.",
                    }
                    if decision.get("recoverable"):
                        error["recoverable"] = True
                    return _coerce_result(error), args
                updated = decision.get("updatedInput")
                if isinstance(updated, dict) and updated:
                    args = updated
                return None, args

            def _execute(args: dict[str, Any]) -> Any:
                # Prefer forwarding to the running backend so the UI sees live
                # activity; fall back to in-process execution if it's down.
                if _BACKEND_URL:
                    try:
                        return _finalize_result(_forward_to_backend(name_, args))
                    except urllib.error.URLError as exc:
                        logger.warning(
                            "backend forward failed for %s (%s); running in-process",
                            name_, exc,
                        )
                    except Exception as exc:  # pragma: no cover
                        logger.warning("backend forward error for %s: %s; running in-process", name_, exc)
                try:
                    result = _rt.invoke_tool(name_, args)
                except KeyError as exc:
                    return _coerce_result({"status": "error", "code": "tool_not_found", "message": str(exc)})
                except Exception as exc:  # pragma: no cover - propagated to client
                    logger.exception("tool %s raised", name_)
                    return _coerce_result({"status": "error", "code": "tool_exception", "message": f"{type(exc).__name__}: {exc}"})
                return _finalize_result(result)

            # Approach A approval gate: a gated tool must pause for approval BEFORE
            # executing. Enforced here (server-side), independent of the client's
            # own permission settings — a user allow-listing the workbench tools
            # cannot bypass the workbench's own approval policy.
            def _handler(**kwargs: Any) -> Any:
                early, args = _preflight(dict(kwargs))
                if early is not None:
                    return early
                if requires_approval and _broker_approval_mode():
                    err, args = _apply_decision(_agentic_permission_decision(name_, args), args)
                    if err is not None:
                        return err
                return _execute(args)

            async def _handler_elicit(**kwargs: Any) -> Any:
                # Headless approval (#228): ask the connecting client to prompt the
                # user via MCP elicitation; no workbench viewer required.
                early, args = _preflight(dict(kwargs))
                if early is not None:
                    return early
                if requires_approval:
                    try:
                        ctx = mcp.get_context()
                    except Exception:  # pragma: no cover - defensive
                        ctx = None
                    decision = await _elicit_permission_decision(name_, args, ctx=ctx)
                    err, args = _apply_decision(decision, args)
                    if err is not None:
                        return err
                return _execute(args)

            chosen = _handler_elicit if _elicit_approval_mode() else _handler
            chosen.__name__ = name_.replace(".", "_")
            chosen.__doc__ = description
            return chosen

        # Publish standard MCP safety hints in addition to the human-readable
        # approval marker. Client-managed mode relies on the connecting agent's
        # permission UX, and capable clients use these annotations to distinguish
        # read-only inspection from package/geometry/solver mutations.
        annotations = ToolAnnotations(
            readOnlyHint=bool(tool_def.get("read_only")),
            destructiveHint=bool(tool_def.get("destructive")),
            idempotentHint=False,
            openWorldHint=False,
        )
        mcp.add_tool(
            _make_handler(
                tool_name,
                bool(tool_def.get("requires_approval")),
                is_mutation=not bool(tool_def.get("read_only", False)),
            ),
            name=mcp_name,
            description=description,
            structured_output=False,
            annotations=annotations,
        )
        # Override two things on the registered Tool:
        #  - parameters: advertise the curated JSON schema (what clients construct
        #    calls from) instead of the inferred ``**kwargs`` blob.
        #  - fn_metadata: validate/dispatch through a passthrough arg model so the
        #    real fields actually reach the handler. Without this, FastMCP validates
        #    against the ``**kwargs``-derived model and rejects every real call.
        tool_obj = mcp._tool_manager._tools.get(mcp_name)  # type: ignore[attr-defined]
        if tool_obj is not None:
            tool_obj.parameters = input_schema
            tool_obj.fn_metadata = FuncMetadata(arg_model=_PassthroughArgModel)

    # Expose the modeling/CAE agent skills as MCP prompts so any connecting client
    # can pull the workbench discipline (dev skills in `.claude/skills/` are NOT
    # exposed). See `_register_agent_skill_prompts`.
    _register_agent_skill_prompts(mcp)
    # NOTE: the standalone `request_approval` permission-prompt tool is no longer
    # registered — approval is enforced per-tool in `_make_handler`, which cannot
    # be bypassed by a user's Claude permission allow-list. `_register_permission_tool`
    # / `_request_approval_handler` are retained for an optional future
    # `--permission-prompt-tool` path.
    return mcp


def _run_doctor_cli(mcp: FastMCP) -> int:
    """Print setup diagnostics to stdout and return an exit code (#229).

    Safe to print to stdout here — the stdio JSON-RPC protocol is not running in
    doctor mode (we exit before any transport starts).
    """
    from . import mcp_doctor

    def _http_get(url: str) -> Any:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    tool_count = len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    report = mcp_doctor.run_doctor(
        root=Path.cwd(),
        backend_url=_BACKEND_URL or os.environ.get("AIENG_BACKEND_URL"),
        tool_count=tool_count,
        http_get=_http_get,
    )
    print(mcp_doctor.format_report(report))
    return 1 if report["overall"] == "fail" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aieng-workbench-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--http", action="store_true", help="Run over HTTP (SSE) instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (only with --http).")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port (only with --http).")
    parser.add_argument(
        "--backend-url",
        default=None,
        help=(
            "Optional FastAPI backend URL for live viewer mode, e.g. "
            "http://127.0.0.1:8000. Omit for self-contained headless mode."
        ),
    )
    parser.add_argument(
        "--approval-mode",
        choices=["inherit", "client", "managed", "block", "elicit"],
        default="inherit",
        help=(
            "Approval policy for [APPROVAL REQUIRED] tools. inherit reads env; "
            "client advertises approval and trusts the MCP client; managed routes "
            "through the backend approval broker (needs the web viewer); elicit "
            "prompts the human via MCP client elicitation (headless-friendly, no "
            "viewer; fail-safe deny if the client can't prompt); block rejects "
            "gated tools."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory for headless project/package/runtime data.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help=(
            "Run setup diagnostics ('is everything wired?') and exit: check MCP "
            "config references aieng-workbench, the backend is reachable, and tools "
            "are exposed — with fix hints. Exit code is non-zero on a failing check."
        ),
    )
    guide_group = parser.add_mutually_exclusive_group()
    guide_group.add_argument(
        "--require-guides",
        action="store_true",
        default=None,
        help="Require aieng.guide reads before CAD/CAE/package tools.",
    )
    guide_group.add_argument(
        "--no-require-guides",
        action="store_false",
        dest="require_guides",
        help="Disable guide-read enforcement for scripted smoke checks.",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Server log level. stdio transport requires WARNING+ so logs don't corrupt the framed protocol.",
    )
    parser.add_argument(
        "--compact-tool-surface",
        action="store_true",
        help=(
            "Expose only the curated subset of high-frequency tools (≈30) for MCP "
            "clients with a small active-tool budget such as Cursor. The full tool "
            "surface remains the default."
        ),
    )
    args = parser.parse_args(argv)
    _apply_cli_runtime_options(
        backend_url=args.backend_url,
        approval_mode=args.approval_mode,
        data_dir=args.data_dir,
        require_guides=args.require_guides,
        compact_surface=args.compact_tool_surface,
    )

    # IMPORTANT: stdio is the wire — any stray print to stdout corrupts the
    # JSON-RPC frames. Route logging to stderr, which Claude Code surfaces
    # under the MCP server's status panel.
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="[mcp-server] %(levelname)s %(name)s: %(message)s",
    )

    mcp = _build_mcp_server(compact_surface=args.compact_tool_surface)
    logger.info("registered %d tools", len(mcp._tool_manager._tools))  # type: ignore[attr-defined]

    if args.doctor:
        return _run_doctor_cli(mcp)

    if args.http:
        # FastMCP's SSE app provides /sse + /messages.
        import uvicorn

        logger.warning("MCP server listening on http://%s:%d (SSE)", args.host, args.port)
        uvicorn.run(mcp.sse_app(), host=args.host, port=args.port, log_level=args.log_level.lower())
    else:
        # Default: stdio transport. FastMCP's blocking entry point.
        mcp.run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
