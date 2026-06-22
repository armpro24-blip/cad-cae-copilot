"""
aieng local runtime — tool registry, run model, and synchronous run executor.

No imports from app.main; business logic is injected via tool handler closures
registered at app startup. This keeps the dependency graph one-directional:
  main.py → runtime.py   (never the reverse)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── data models ───────────────────────────────────────────────────────────────

@dataclass
class ToolError:
    """Structured error payload for a failed or rejected tool invocation."""
    code: str
    message: str
    tool_name: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    requires_approval: bool = False


@dataclass
class ToolResult:
    id: str
    status: Literal["success", "error", "needs_approval", "rejected"]
    output: Any = None
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RuntimeEvent:
    id: str
    run_id: str
    type: Literal[
        "run_started",
        "plan_created",
        "tool_started",
        "tool_succeeded",
        "tool_failed",
        "approval_required",
        "approval_granted",
        "approval_rejected",
        "run_completed",
        "run_failed",
        "run_rejected",
        "run_cancelled",
    ]
    timestamp: str
    payload: Any = None


@dataclass
class RunRecord:
    run_id: str
    message: str
    created_at: str
    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "awaiting_approval",
        "rejected",
        "cancelled",
    ]
    plan: list[dict[str, Any]] = field(default_factory=list)
    events: list[RuntimeEvent] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    tool_errors: list[ToolError] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    project_id: str | None = None
    package_path: str | None = None
    summary: str = ""
    pending_step_index: int | None = None


# ── tool registry ─────────────────────────────────────────────────────────────

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Any]

_REGISTRY: dict[str, dict[str, Any]] = {}


def register_tool(
    name: str,
    handler: ToolHandler,
    *,
    requires_approval: bool = False,
    read_only: bool | None = None,
    destructive: bool | None = None,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
) -> None:
    """Register a runtime tool.

    Args:
        input_schema: JSON Schema (draft-7+) describing the tool's input dict.
            Optional but strongly recommended for tools exposed via MCP — the
            agent uses the schema to construct valid tool calls. When omitted,
            consumers fall back to a permissive ``{"type": "object"}`` schema.
    """
    _REGISTRY[name] = {
        "handler": handler,
        "requires_approval": requires_approval,
        "read_only": (not requires_approval) if read_only is None else read_only,
        "destructive": requires_approval if destructive is None else destructive,
        "description": description,
        "input_schema": input_schema,
    }


def registered_tool_names() -> list[str]:
    return list(_REGISTRY.keys())


def registered_tools_info() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "requires_approval": meta["requires_approval"],
            "read_only": meta["read_only"],
            "destructive": meta["destructive"],
            "description": meta.get("description", ""),
        }
        for name, meta in _REGISTRY.items()
    ]


def registered_tool_metadata(name: str) -> dict[str, Any] | None:
    """Return metadata for a single registered tool, or None if unknown."""
    meta = _REGISTRY.get(name)
    if meta is None:
        return None
    return {
        "requires_approval": bool(meta.get("requires_approval", False)),
        "read_only": bool(meta.get("read_only", True)),
        "destructive": bool(meta.get("destructive", False)),
        "description": str(meta.get("description", "")),
    }


def list_tools_for_mcp() -> list[dict[str, Any]]:
    """Return tool metadata suitable for exposing via an MCP server.

    Each entry carries ``name``, ``description``, ``requires_approval`` and an
    explicit ``input_schema`` (defaulting to a permissive object schema when
    the tool was registered without one).
    """
    out: list[dict[str, Any]] = []
    for name, meta in _REGISTRY.items():
        schema = meta.get("input_schema") or {
            "type": "object",
            "additionalProperties": True,
        }
        out.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "requires_approval": bool(meta.get("requires_approval", False)),
                "read_only": bool(meta.get("read_only", False)),
                "destructive": bool(meta.get("destructive", False)),
                "input_schema": schema,
            }
        )
    return out


def registry_identity() -> dict[str, Any]:
    """A deterministic identity for the currently-registered tool set.

    The MCP server builds its tool registry once at process start (via
    ``create_app``), so a long-lived session can silently serve a stale set —
    new tools invisible, changed descriptions outdated — with no other signal
    (#29). This returns a content hash that changes whenever a tool is added or
    removed, or its description / approval flags / input schema change, plus the
    tool count. Surfaced in ``GET /api/health`` and ``aieng.agent_readme`` so an
    agent or operator can tell whether it is talking to a current registry.
    """
    import hashlib

    entries = [
        {
            "name": name,
            "description": meta.get("description", ""),
            "requires_approval": bool(meta.get("requires_approval", False)),
            "read_only": bool(meta.get("read_only", False)),
            "destructive": bool(meta.get("destructive", False)),
            "input_schema": meta.get("input_schema") or {"type": "object", "additionalProperties": True},
        }
        for name, meta in sorted(_REGISTRY.items())
    ]
    blob = json.dumps(entries, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return {
        "tool_count": len(entries),
        "registry_hash": f"sha256:{digest[:16]}",
    }


def invoke_tool(
    name: str,
    inp: dict[str, Any],
    ctx: dict[str, Any] | None = None,
) -> Any:
    """Synchronously invoke a registered tool by name.

    Bypasses the run model — intended for direct adapters (e.g. an MCP server)
    that wrap each tool call in their own protocol-level lifecycle.

    Raises:
        KeyError: if the tool is not registered.
    """
    meta = _REGISTRY.get(name)
    if meta is None:
        raise KeyError(f"tool not registered: {name}")
    handler: ToolHandler = meta["handler"]
    return handler(inp or {}, ctx or {})


# ── file-backed run store ─────────────────────────────────────────────────────

_STORE: dict[str, RunRecord] = {}
_STATE_DIR: Path | None = None


def configure(state_dir: Path) -> None:
    """Set the directory used for file-backed run persistence."""
    global _STATE_DIR
    _STATE_DIR = state_dir
    state_dir.mkdir(parents=True, exist_ok=True)


def _run_file(run_id: str) -> Path | None:
    if _STATE_DIR is None:
        return None
    return _STATE_DIR / f"{run_id}.json"


def _run_from_dict(data: dict[str, Any]) -> RunRecord:
    events = [
        RuntimeEvent(
            id=ev["id"],
            run_id=ev["run_id"],
            type=ev["type"],  # type: ignore[arg-type]
            timestamp=ev["timestamp"],
            payload=ev.get("payload"),
        )
        for ev in (data.get("events") or [])
    ]
    tool_calls = [
        ToolCall(
            id=tc["id"],
            name=tc["name"],
            input=tc.get("input") or {},
            requires_approval=tc.get("requires_approval", False),
        )
        for tc in (data.get("tool_calls") or [])
    ]
    tool_results = [
        ToolResult(
            id=tr["id"],
            status=tr["status"],  # type: ignore[arg-type]
            output=tr.get("output"),
            error=tr.get("error"),
            artifacts=tr.get("artifacts") or [],
        )
        for tr in (data.get("tool_results") or [])
    ]
    tool_errors = [
        ToolError(
            code=te["code"],
            message=te["message"],
            tool_name=te.get("tool_name"),
            details=te.get("details"),
        )
        for te in (data.get("tool_errors") or [])
    ]
    return RunRecord(
        run_id=data["run_id"],
        message=data.get("message", ""),
        created_at=data.get("created_at", ""),
        status=data.get("status", "failed"),  # type: ignore[arg-type]
        plan=data.get("plan") or [],
        events=events,
        tool_calls=tool_calls,
        tool_results=tool_results,
        tool_errors=tool_errors,
        errors=data.get("errors") or [],
        project_id=data.get("project_id"),
        package_path=data.get("package_path"),
        summary=data.get("summary", ""),
        pending_step_index=data.get("pending_step_index"),
    )


def store_run(run: RunRecord) -> None:
    _STORE[run.run_id] = run
    path = _run_file(run.run_id)
    if path is not None:
        try:
            path.write_text(
                json.dumps(run_to_dict(run), ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass  # persistence is best-effort; in-memory store is authoritative


def get_run(run_id: str) -> RunRecord | None:
    run = _STORE.get(run_id)
    if run is not None:
        return run
    path = _run_file(run_id)
    if path is not None and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            run = _run_from_dict(data)
            _STORE[run_id] = run
            return run
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return None


def get_all_runs(limit: int = 50) -> list[RunRecord]:
    """Return recent runs sorted newest-first. Merges in-memory and persisted runs."""
    result: dict[str, RunRecord] = dict(_STORE)
    if _STATE_DIR is not None:
        try:
            paths = sorted(
                _STATE_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in paths[:limit]:
                run_id = path.stem
                if run_id not in result:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        run = _run_from_dict(data)
                        result[run_id] = run
                    except (OSError, json.JSONDecodeError, KeyError):
                        pass
        except OSError:
            pass
    return sorted(result.values(), key=lambda r: r.created_at, reverse=True)[:limit]


# ── plan builder ──────────────────────────────────────────────────────────────

# (keywords, tool_name, description)
_INTENT_MAP: list[tuple[list[str], str, str]] = [
    (
        [
            "agent context", "cad cae context", "cad/cae context",
            "engineering context", "agent-facing context",
        ],
        "aieng.agent_context",
        "Build compact agent-facing CAD/CAE context",
    ),
    (
        ["inspect", "package", "summary"],
        "aieng.inspect_package",
        "Inspect .aieng package and return project summary",
    ),
    (
        [
            "convert step to aieng", "convert fcstd file", "import step to aieng",
            "convert step", "step to aieng", "import step",
        ],
        "aieng.convert",
        "Convert a STEP or FreeCAD file to an .aieng package",
    ),
    (
        ["refresh semantic", "refresh semantics", "validate"],
        "aieng.refresh_semantics",
        "Re-validate package and refresh semantic state",
    ),
    (
        ["preview", "glb", "stl", "viewer"],
        "aieng.generate_preview",
        "Generate 3-D web preview asset",
    ),
    (
        ["audit", "log"],
        "aieng.read_audit_log",
        "Return the most recent audit log entries for this project",
    ),
    (
        [
            "generate computed metrics", "import computed metrics", "normalize metrics",
        ],
        "postprocess.generate_computed_metrics",
        "Import computed metrics from a CSV or JSON file into a .aieng package",
    ),
    (
        [
            "refresh cae summary", "regenerate cae summary", "update postprocessing summary",
            "refresh result summary", "update cae summary", "summarize cae results",
        ],
        "postprocess.refresh_cae_summary",
        "Regenerate CAE result summary, evidence index, and markdown inside the .aieng package",
    ),
    (
        [
            "apply cae setup patch", "cae setup patch", "patch cae setup",
            "apply setup patch", "patch setup", "cae patch",
        ],
        "cae.apply_setup_patch",
        "Apply a controlled patch to CAE setup artifacts inside a .aieng package",
    ),
    (
        [
            "extract solver results", "parse frd", "extract frd", "import frd results",
            "extract cae results", "parse solver output", "extract computed metrics from frd",
        ],
        "cae.extract_solver_results",
        "Parse a CalculiX FRD result file and write computed_metrics.json into a .aieng package",
    ),
    (
        [
            "extract field regions", "field region extraction", "extract stress clusters",
            "extract high stress regions", "identify stress concentrations", "find stress hotspots",
            "field region clusters", "frd region extraction",
        ],
        "cae.extract_field_regions",
        "Extract high-magnitude spatial clusters from a CalculiX FRD file and write results/field_regions.json",
    ),
    (
        [
            "prepare solver run", "solver preflight", "run preflight", "check solver readiness",
            "prepare cae run", "cae run preflight", "check simulation readiness",
            "prepare simulation run", "simulation preflight",
        ],
        "cae.prepare_solver_run",
        "Inspect .aieng package and return solver run preflight plan (no solver execution)",
    ),
    (
        [
            "generate solver input", "generate calculix deck", "generate solver deck",
            "create solver input", "build solver deck", "generate inp",
        ],
        "cae.generate_solver_input",
        "Generate a runnable CalculiX solver input deck from existing .aieng setup artifacts",
    ),
    (
        [
            "execute solver run", "start solver run", "run calculix",
            "execute calculix", "start calculix", "run simulation",
            "execute simulation", "start simulation",
        ],
        "cae.run_solver",
        "Execute an external CalculiX solver run on an existing input deck (requires approval)",
    ),
    (
        [
            "write mesh handoff", "mesh handoff", "generate mesh handoff",
            "create mesh handoff", "mesh handoff contract",
        ],
        "cae.write_mesh_handoff",
        "Write a mesh handoff contract into a .aieng package for external Gmsh execution",
    ),
    (
        [
            "import solver evidence", "import solver result", "import solver output",
            "import result evidence", "solver evidence import",
        ],
        "cae.import_solver_evidence",
        "Import an external solver result file as evidence into a .aieng package",
    ),
    (
        [
            "write evidence scaffold", "evidence scaffold", "create evidence scaffold",
            "init evidence", "setup evidence", "evidence scaffold",
        ],
        "aieng.write_evidence_scaffold",
        "Write results/evidence_index.json scaffold into a .aieng package without creating or advancing claims",
    ),
    (
        [
            "validate package", "validation", "check package",
        ],
        "aieng.validate",
        "Validate a .aieng package against AIENG schemas and rules",
    ),
    (
        [
            "write completeness report", "completeness report", "generate completeness report",
            "package completeness", "missingness report", "what is missing",
        ],
        "aieng.write_completeness_report",
        "Write a completeness/missingness report into a .aieng package",
    ),
    (
        [
            "update validation status", "write validation status", "validation status",
            "package validation status",
        ],
        "aieng.update_validation_status",
        "Update validation status (status.yaml) inside a .aieng package",
    ),
    (
        [
            "named part bbox", "part bbox", "bounding box of part", "part center",
            "named part center", "center of part",
        ],
        "cad.get_named_part_bbox",
        "Read bbox and center of a named CAD part from topology_map",
    ),
    (
        [
            "refine cad", "refine geometry", "feedback driven cad edit",
            "edit build123d via feedback", "update model from feedback",
        ],
        "cad.refine",
        "Refine existing build123d CAD using natural-language feedback",
    ),
    (
        [
            "edit cad parameter", "change cad parameter", "update cad parameter",
            "modify cad parameter", "set cad parameter", "apply cad edit",
        ],
        "cad.edit_parameter",
        "Apply a parametric edit to a CAD model feature (requires approval)",
    ),
    (
        [
            "editable parameters", "list parameters", "what can i edit",
            "which parameters", "what parameters", "editable dimensions",
            "what can i change", "tunable parameters",
        ],
        "cad.list_editable_parameters",
        "List the CAD parameters that can be edited fast via cad.edit_parameter (read-only)",
    ),
]


def build_plan(message: str, project_id: str | None) -> list[dict[str, Any]]:
    lower = message.lower()
    seen: set[str] = set()
    steps: list[dict[str, Any]] = []
    for keywords, tool_name, description in _INTENT_MAP:
        if tool_name in seen:
            continue
        if any(kw in lower for kw in keywords):
            steps.append(
                {
                    "name": tool_name,
                    "description": description,
                    "input": {"project_id": project_id} if project_id else {},
                }
            )
            seen.add(tool_name)
    if not steps:
        steps.append(
            {
                "name": "aieng.inspect_package",
                "description": "Default: inspect package and return project summary",
                "input": {"project_id": project_id} if project_id else {},
            }
        )
    return steps


# ── event helpers ─────────────────────────────────────────────────────────────

def _emit(run: RunRecord, event_type: str, payload: Any = None) -> RuntimeEvent:
    ev = RuntimeEvent(
        id=uuid.uuid4().hex[:10],
        run_id=run.run_id,
        type=event_type,  # type: ignore[arg-type]
        timestamp=_now_iso(),
        payload=payload,
    )
    run.events.append(ev)
    return ev


# ── shared step executor ───────────────────────────────────────────────────────

def _execute_steps(
    run: RunRecord,
    steps: list[dict[str, Any]],
    start_index: int,
    skip_approval_for_first: bool = False,
) -> None:
    """
    Execute plan steps from start_index.
    If skip_approval_for_first is True, the first step's approval check is skipped
    (used by resume_run to execute the previously-blocked tool).
    Mutates run in place; does NOT call store_run.
    """
    for i, step in enumerate(steps):
        actual_idx = start_index + i
        kind = step.get("kind") or "tool"
        tool_name = step.get("name") or step.get("tool_name") or step.get("id") or kind
        step_input = step.get("input") if isinstance(step.get("input"), dict) else {}

        if kind in {"llm", "benchmark", "artifact"}:
            tc = ToolCall(
                id=uuid.uuid4().hex[:8],
                name=str(tool_name),
                input=step_input,
                requires_approval=False,
            )
            run.tool_calls.append(tc)
            _emit(run, "tool_started", {"tool": tool_name, "kind": kind})
            output = {
                "status": "ok",
                "kind": kind,
                "message": step.get("description") or f"{kind} step recorded by workflow runtime.",
            }
            run.tool_results.append(ToolResult(id=tc.id, status="success", output=output))
            _emit(run, "tool_succeeded", {"tool": tool_name, "kind": kind, "artifact_count": 0})
            continue

        if kind == "approval":
            if i == 0 and skip_approval_for_first:
                tc = (
                    run.tool_calls[actual_idx]
                    if actual_idx < len(run.tool_calls)
                    else ToolCall(id=uuid.uuid4().hex[:8], name=str(tool_name), input=step_input, requires_approval=True)
                )
                if actual_idx >= len(run.tool_calls):
                    run.tool_calls.append(tc)
                run.tool_results.append(
                    ToolResult(id=tc.id, status="success", output={"status": "approved", "kind": "approval"})
                )
                _emit(run, "approval_granted", {"tool": tool_name, "kind": "approval"})
                continue

            tc = ToolCall(
                id=uuid.uuid4().hex[:8],
                name=str(tool_name),
                input=step_input,
                requires_approval=True,
            )
            run.tool_calls.append(tc)
            run.tool_results.append(ToolResult(id=tc.id, status="needs_approval"))
            _emit(run, "approval_required", {"tool": tool_name, "kind": "approval"})
            run.status = "awaiting_approval"
            run.pending_step_index = actual_idx
            run.errors.append(f"{tool_name} requires explicit approval before execution")
            return

        tool_def = _REGISTRY.get(tool_name)

        if i == 0 and actual_idx < len(run.tool_calls):
            # Reuse the ToolCall already recorded when the approval gate fired
            tc = run.tool_calls[actual_idx]
        else:
            # An explicit per-step ``requires_approval`` (set by a caller that
            # gates a tool independently of the global registry flag — e.g. the
            # copilot loop's human-in-the-loop apply step) takes precedence;
            # otherwise fall back to the tool's registry default.
            step_requires_approval = step.get("requires_approval")
            requires_approval = (
                bool(step_requires_approval)
                if step_requires_approval is not None
                else (tool_def.get("requires_approval", False) if tool_def else False)
            )
            tc = ToolCall(
                id=uuid.uuid4().hex[:8],
                name=tool_name,
                input=step_input,
                requires_approval=requires_approval,
            )
            run.tool_calls.append(tc)

        is_first_after_approval = i == 0 and skip_approval_for_first
        if tc.requires_approval and not is_first_after_approval:
            result = ToolResult(id=tc.id, status="needs_approval")
            run.tool_results.append(result)
            _emit(run, "approval_required", {"tool": tool_name})
            run.status = "awaiting_approval"
            run.pending_step_index = actual_idx
            run.errors.append(f"{tool_name} requires explicit approval before execution")
            return

        if tool_def is None:
            err = f"tool not registered: {tool_name}"
            run.tool_results.append(ToolResult(id=tc.id, status="error", error=err))
            run.tool_errors.append(
                ToolError(code="tool_not_registered", message=err, tool_name=tool_name)
            )
            run.errors.append(err)
            _emit(run, "tool_failed", {"tool": tool_name, "error": err})
            run.status = "failed"
            _emit(run, "run_failed", {"errors": run.errors})
            return

        _emit(run, "tool_started", {"tool": tool_name})
        try:
            output = tool_def["handler"](step_input, {"project_id": run.project_id})
            # Hoist artifacts out of the output dict into ToolResult.artifacts
            artifacts: list[dict[str, Any]] = []
            if isinstance(output, dict):
                raw = output.get("artifacts")
                if isinstance(raw, list):
                    artifacts = [a for a in raw if isinstance(a, dict)]
            run.tool_results.append(
                ToolResult(id=tc.id, status="success", output=output, artifacts=artifacts)
            )
            _emit(run, "tool_succeeded", {"tool": tool_name, "artifact_count": len(artifacts)})
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            run.tool_results.append(ToolResult(id=tc.id, status="error", error=err))
            run.tool_errors.append(
                ToolError(
                    code="tool_execution_error",
                    message=err,
                    tool_name=tool_name,
                )
            )
            run.errors.append(err)
            _emit(run, "tool_failed", {"tool": tool_name, "error": err})
            run.status = "failed"
            _emit(run, "run_failed", {"errors": run.errors})
            return


# ── run executor ──────────────────────────────────────────────────────────────

def execute_run(run: RunRecord, ctx: dict[str, Any]) -> RunRecord:
    """
    Synchronously execute all planned steps, emitting RuntimeEvents along the way.
    Stops at the first failure or approval gate.

    ``ctx`` may contain ``tool_input`` — a dict of structured parameters that is
    merged into each plan step's input. This lets callers pass explicit
    ``inputPath``, ``outputPath``, etc. without parsing them from the message.
    """
    run.status = "running"
    _emit(run, "run_started", {"message": run.message})

    plan = build_plan(run.message, run.project_id)
    # Merge structured tool_input into each step so handlers receive explicit params
    tool_input = ctx.get("tool_input")
    if isinstance(tool_input, dict):
        for step in plan:
            step_input = step.get("input") or {}
            if isinstance(step_input, dict):
                merged = dict(step_input)
                merged.update(tool_input)
                step["input"] = merged
    # Let a caller force an approval gate for this run independently of the
    # global registry flag (the registry treats CAD edits as plan-confirmed, but
    # the copilot loop keeps its own per-step approval checkpoint).
    if ctx.get("require_approval"):
        for step in plan:
            if (step.get("kind") or "tool") in {"tool", "mcp_tool"}:
                step["requires_approval"] = True
    run.plan = plan
    _emit(run, "plan_created", {"steps": [s["name"] for s in plan]})

    _execute_steps(run, plan, start_index=0, skip_approval_for_first=False)

    if run.status == "running":
        success_count = sum(1 for r in run.tool_results if r.status == "success")
        run.summary = (
            f"Completed {success_count}/{len(run.tool_calls)} tool call(s) successfully."
        )
        run.status = "completed"
        _emit(run, "run_completed", {"summary": run.summary})

    store_run(run)
    return run


def execute_run_with_plan(run: RunRecord, plan: list[dict[str, Any]], ctx: dict[str, Any] | None = None) -> RunRecord:
    """Execute an explicit workflow plan instead of using the intent mapper.

    The step shape is intentionally loose so the UI can submit workflow steps
    with ``kind`` values such as ``tool``, ``llm``, ``benchmark``, ``approval``,
    and ``artifact``. Only registered runtime tools execute real handlers in
    this V1 runtime; other step kinds are recorded as auditable workflow events.
    """
    ctx = ctx or {}
    run.status = "running"
    _emit(run, "run_started", {"message": run.message, "workflow_id": ctx.get("workflow_id")})

    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        tool_name = step.get("name") or step.get("tool_name") or step.get("id") or f"step_{index + 1}"
        step_input = step.get("input") if isinstance(step.get("input"), dict) else {}
        normalized.append(
            {
                **step,
                "name": tool_name,
                "kind": step.get("kind") or "tool",
                "description": step.get("description") or str(tool_name),
                "input": step_input,
            }
        )

    tool_input = ctx.get("tool_input")
    if isinstance(tool_input, dict):
        for step in normalized:
            if step.get("kind") in {"tool", "mcp_tool"}:
                merged = dict(step.get("input") or {})
                merged.update(tool_input)
                step["input"] = merged

    run.plan = normalized
    _emit(run, "plan_created", {"steps": [s["name"] for s in normalized], "workflow_id": ctx.get("workflow_id")})

    _execute_steps(run, normalized, start_index=0, skip_approval_for_first=False)

    if run.status == "running":
        success_count = sum(1 for r in run.tool_results if r.status == "success")
        run.summary = f"Completed {success_count}/{len(run.tool_calls)} workflow step(s) successfully."
        run.status = "completed"
        _emit(run, "run_completed", {"summary": run.summary})

    store_run(run)
    return run


# ── approval resumption ───────────────────────────────────────────────────────

def resume_run(run_id: str) -> RunRecord | None:
    """
    Resume a run that is awaiting_approval.
    Executes the pending blocked tool (without re-checking the approval flag)
    and continues with any remaining plan steps.
    """
    run = get_run(run_id)
    if run is None:
        return None
    if run.status != "awaiting_approval":
        return run

    pending_idx = run.pending_step_index
    if pending_idx is None or pending_idx >= len(run.plan):
        run.status = "failed"
        run.errors.append("approval resumption failed: pending step index invalid")
        _emit(run, "run_failed", {"errors": run.errors})
        store_run(run)
        return run

    pending_tool = run.plan[pending_idx]["name"]
    _emit(run, "approval_granted", {"tool": pending_tool})
    run.status = "running"
    run.pending_step_index = None

    # Remove the needs_approval placeholder result so _execute_steps can write the real one
    if pending_idx < len(run.tool_calls):
        pending_tc_id = run.tool_calls[pending_idx].id
        run.tool_results = [
            tr for tr in run.tool_results if tr.id != pending_tc_id
        ]

    remaining_steps = run.plan[pending_idx:]
    _execute_steps(
        run,
        remaining_steps,
        start_index=pending_idx,
        skip_approval_for_first=True,
    )

    if run.status == "running":
        success_count = sum(1 for r in run.tool_results if r.status == "success")
        run.summary = (
            f"Completed {success_count}/{len(run.tool_calls)} tool call(s) successfully."
        )
        run.status = "completed"
        _emit(run, "run_completed", {"summary": run.summary})

    store_run(run)
    return run


def reject_run(run_id: str) -> RunRecord | None:
    """
    Reject a run that is awaiting_approval.
    The pending tool is not executed; run is marked rejected.
    """
    run = get_run(run_id)
    if run is None:
        return None
    if run.status != "awaiting_approval":
        return run

    pending_idx = run.pending_step_index
    tool_name = (
        run.plan[pending_idx]["name"]
        if pending_idx is not None and pending_idx < len(run.plan)
        else "unknown"
    )

    # Mark the needs_approval result as rejected
    if pending_idx is not None and pending_idx < len(run.tool_calls):
        pending_tc_id = run.tool_calls[pending_idx].id
        for tr in run.tool_results:
            if tr.id == pending_tc_id and tr.status == "needs_approval":
                tr.status = "rejected"  # type: ignore[assignment]
                break

    _emit(run, "approval_rejected", {"tool": tool_name, "note": "user rejected"})
    run.status = "rejected"
    run.pending_step_index = None
    run.errors.append(f"Run rejected — {tool_name} was not executed")
    run.tool_errors.append(
        ToolError(
            code="approval_rejected",
            message=f"User rejected approval for {tool_name}",
            tool_name=tool_name,
        )
    )
    _emit(run, "run_rejected", {"tool": tool_name, "errors": run.errors})
    store_run(run)
    return run


# ── serialisation ─────────────────────────────────────────────────────────────

def run_to_dict(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "message": run.message,
        "created_at": run.created_at,
        "status": run.status,
        "project_id": run.project_id,
        "package_path": run.package_path,
        "plan": run.plan,
        "pending_step_index": run.pending_step_index,
        "events": [
            {
                "id": e.id,
                "run_id": e.run_id,
                "type": e.type,
                "timestamp": e.timestamp,
                "payload": e.payload,
            }
            for e in run.events
        ],
        "tool_calls": [
            {
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
                "requires_approval": tc.requires_approval,
            }
            for tc in run.tool_calls
        ],
        "tool_results": [
            {
                "id": tr.id,
                "status": tr.status,
                "output": tr.output,
                "error": tr.error,
                "artifacts": tr.artifacts,
            }
            for tr in run.tool_results
        ],
        "tool_errors": [
            {
                "code": te.code,
                "message": te.message,
                "tool_name": te.tool_name,
                "details": te.details,
            }
            for te in run.tool_errors
        ],
        "errors": run.errors,
        "summary": run.summary,
    }


def run_to_summary_dict(run: RunRecord) -> dict[str, Any]:
    """Slim serialisation for the run listing endpoint."""
    last_event = run.events[-1] if run.events else None
    return {
        "run_id": run.run_id,
        "created_at": run.created_at,
        "status": run.status,
        "message": run.message,
        "project_id": run.project_id,
        "event_count": len(run.events),
        "last_event_type": last_event.type if last_event else None,
        "error_summary": run.errors[0] if run.errors else None,
    }
