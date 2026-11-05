"""Compact, topic-addressable agent onboarding derived from root AGENTS.md."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _version_surface() -> dict[str, Any]:
    """Return the AIENG version surface from the installed aieng schemas."""
    try:
        from importlib.resources import files

        resource = files("aieng.schemas").joinpath("version_surface.json")
        if resource.is_file():
            return json.loads(resource.read_text(encoding="utf-8"))
    except Exception:
        pass
    # Fallback for source-tree runs.
    fallback = Path(__file__).resolve().parents[3] / "aieng" / "src" / "aieng" / "schemas" / "version_surface.json"
    if fallback.exists():
        return json.loads(fallback.read_text(encoding="utf-8"))
    return {"error": "version_surface.json not found"}


QUICKSTART = """\
# aieng Workbench — MCP Quickstart

The `aieng-workbench` MCP tools are the interface. Do not browse `aieng/src/`
to discover capabilities; it is not the runtime.

## First three calls

1. `aieng.agent_readme` — this page.
2. `aieng.list_projects` — project ids (or `aieng.create_project` for a new one).
3. `aieng.agent_context {project_id}` — geometry, `@face:` pointers, stale
   warnings, CAE state, suggested next steps.

## Guides are required per category, once per session

Before the first CAD modeling or geometry-edit action, read
`aieng.guide {topic:"cad"}`. Before the first simulation, solver,
post-processing or optimization action, read `aieng.guide {topic:"cae"}`. Each
is a short contract. Re-read a guide only if the session lost that context. The
server enforces this: a skipped read returns `code: "guide_required"` with the
exact `next_call`, and the tool does not run.

Reference topics, only when the task needs them: `cad-reference` and
`cae-reference` (why each rule exists, with the measured failures),
`cad-helpers`, `cad-modes`, `cae-assembly`, `pointers`, `tools`, `workflows`,
`package` (required before import/validation tools), `approvals`, `operators`,
`fallback`, `frontend`, `review`, and `full` (the whole AGENTS.md, in parts).

## Rules that apply everywhere

- Before the first CAD mutation of a new request, show a concise plan through
  your native question tool (Claude Code `AskUserQuestion`, Codex
  `request_user_input`) with Approve / Revise / Cancel; without one, call
  `cad.confirm_modeling_plan`. After approval, iterate without asking per call.
- `cae.run_solver`, `cad.restore_snapshot`, `aieng.apply_shape_ir_patch` and
  `aieng.delete_project` are approved individually.
- Use `@face:` / `@feature:` / `@artifact:` pointers exactly as returned.
- Never claim a solver result unless `cae.run_solver` completed and its results
  were extracted. Report the result's `credibility` tier with it.
- Follow a refusal's `code`, message and `recommended_next_calls`; do not work
  around it.
- Long tools (CAD builds, solves) keep running; do not repeat a call that has
  not returned.

## The core loop: build, analyse, resize, re-analyse

`cad.execute_build123d` → `cae.setup_static` (once) →
`cae.run_simulation_pipeline {project_id, mesh_size_mm}` → `cad.edit_parameter`
→ `cae.run_simulation_pipeline {project_id}` again. Each pipeline call meshes
when the mesh is missing or older than the geometry, solves into a NEW run
(`run_001`, then `run_002`, …), and returns the metrics, the mesh accuracy band
and `change_since_previous_run` — the before/after, the same numbers
`cae.compare_runs` gives. Driving the steps yourself also works; reusing an old
mesh or the pre-edit deck is then refused (`stale_mesh`, `stale_deck`) rather
than silently reporting the old numbers.
"""


# The mandatory pre-action reads (`cad`, `cae`) carry only the CONTRACT: the
# rules an agent must apply on its own and how to read the responses. Rules the
# code enforces explain themselves when they refuse, so their history — the
# measured failures behind each guard — lives in `cad-reference` /
# `cae-reference`. Measured before this split (2026-11-05): the gated reads were
# cad ~7.7k and cae ~16.7k tokens, ~26k of required reading with the quickstart
# before a CAD->CAE task could start; that history is maintainer rationale, not
# operating instructions. Only the core topics satisfy the guide gate.
TOPIC_SECTIONS: dict[str, tuple[str, ...]] = {
    "cad": ("CAD contract",),
    "cad-reference": (
        "Real 3D CAD modeling (no API key needed)",
        "Pointer syntax — `@kind:id`",
        "B — CAD generation from scratch",
        "B2 — Incremental modeling (the sustainable loop)",
        "E — Parametric modification (design iteration)",
        "Approval-gated tools",
        "Stale-artifact warnings",
        "Common mistakes to avoid",
    ),
    "cad-helpers": (
        "High-level helpers — prefer these over hand-rolled boilerplate",
        "Curve patterns — copy + adapt (when a helper doesn't fit)",
    ),
    "cad-modes": (
        "Industrial Design Mode — escape primitive stacking",
        "Engineering Mode — well-formed mechanical parts",
    ),
    "cae": ("CAE contract",),
    "cae-reference": (
        "Structural FEA (CalculiX)",
        "Pointer syntax — `@kind:id`",
        "Credibility tiering (read the `credibility` stamp)",
        "CAE setup (no approval)",
        "Simulation execution (requires approval — runs external CalculiX)",
        "Post-processing (no approval)",
        "C — CAD → CAE simulation pipeline",
        "D — Inspect results and explain findings",
        "E — Parametric modification (design iteration)",
        "Approval-gated tools",
        "Stale-artifact warnings",
    ),
    "cae-assembly": (
        "Assembly IR v0 (optional, multi-part)",
    ),
    # (see_also below tells an agent these sub-topics exist — a lean core is only
    # an improvement if the material it left out is still findable.)
    "pointers": ("Pointer syntax — `@kind:id`",),
    "tools": (
        "STOP — read this first",
        "First three calls every session",
        "Tool taxonomy",
        "Approval-gated tools",
    ),
    # Its own topic on purpose: a reviewer wants it, a modeller does not, and
    # folding it into `cad`/`cae` would undo the weight cuts of #494.
    "review": (
        "Review lens — the defect patterns this codebase actually produces",
        "Common mistakes to avoid",
    ),
    "workflows": (
        "Pointer syntax — `@kind:id`",
        "Recommended workflows",
        "Approval-gated tools",
        "Stale-artifact warnings",
    ),
    "package": (
        "Package lifecycle",
        "Approval-gated tools",
        "Stale-artifact warnings",
        ".aieng package structure (reference)",
    ),
    "fallback": (
        "If the backend (port 8000) is unreachable",
        "Fallback mode — when you do not have MCP tools",
        "Common mistakes to avoid",
    ),
    "frontend": (
        "Workspace layout",
        "Development path rules",
        "Frontend maintainability rules",
    ),
    "approvals": (
        "Tool taxonomy",
        "Approval-gated tools",
        "If the backend (port 8000) is unreachable",
        "Environment variables (for MCP server operators)",
    ),
    "operators": (
        "Workspace layout",
        "Approval-gated tools",
        "If the backend (port 8000) is unreachable",
        "Environment variables (for MCP server operators)",
    ),
}

# What a core topic deliberately leaves out, and when to go get it. A lean core
# is only an improvement if the material it dropped stays findable.
TOPIC_SEE_ALSO: dict[str, tuple[tuple[str, str], ...]] = {
    "cad": (
        ("cad-reference",
         "when a rule or response field needs its rationale — the measured "
         "failures behind each CAD guard, the full response-field semantics, "
         "and worked examples"),
        ("cad-helpers",
         "before writing lofts/sweeps/fillets by hand — the pre-injected helper "
         "catalogue (housing/rib/boss/capsule/…) and the curve patterns"),
        ("cad-modes",
         "when the target is a named product, character or vehicle (industrial "
         "design mode) or a bracket/housing destined for CNC or FEA "
         "(engineering mode, canonical feature labels + DfM rules)"),
    ),
    "cad-reference": (("cad", "the code contract, approval boundary and response fields"),),
    "cad-helpers": (("cad", "the code contract, approval boundary and response fields"),),
    "cad-modes": (("cad", "the code contract, approval boundary and response fields"),),
    "cae": (
        ("cae-reference",
         "when a refusal or result field needs its rationale — mesh accuracy, "
         "binding and rebind mechanics, post-processing definitions, solver "
         "installation, and the measured failures behind each guard"),
        ("cae-assembly",
         "only for MULTI-PART packages: Assembly IR, interfaces, proxy "
         "connections, bolt-preload honesty and assembly topology optimization"),
    ),
    "cae-reference": (("cae", "the analysis contract, solver gate and result honesty rules"),),
    "cae-assembly": (("cae", "the analysis contract, solver gate and result honesty rules"),),
}

# In-memory cache for guide content and topic extractions to avoid repeated
# disk reads and markdown parsing.
_guide_content_cache: tuple[str, Path] | None = None
_guide_content_mtime: float = 0.0
_topic_extract_cache: dict[str, str] = {}


def canonical_agents_path() -> Path:
    parents = Path(__file__).resolve().parents
    backend_root = parents[1]
    workspace_root = parents[3]
    candidates = (workspace_root / "AGENTS.md", backend_root / "AGENTS.md")
    return next((path for path in candidates if path.exists()), candidates[0])


def clear_guide_cache() -> None:
    """Invalidate in-memory guide caches.

    Call after AGENTS.md is edited on disk or in tests that mock the canonical
    guide path so subsequent reads reflect the latest content.
    """
    global _guide_content_cache, _guide_content_mtime
    _guide_content_cache = None
    _guide_content_mtime = 0.0
    _topic_extract_cache.clear()


def read_full_guide() -> tuple[str, Path]:
    global _guide_content_cache, _guide_content_mtime
    path = canonical_agents_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _guide_content_cache is None or mtime != _guide_content_mtime:
        try:
            content = path.read_text(encoding="utf-8")
            _guide_content_cache = content, path
        except (FileNotFoundError, OSError):
            _guide_content_cache = "AGENTS.md not found.", path
        _guide_content_mtime = mtime
        _topic_extract_cache.clear()
    return _guide_content_cache


def available_topics() -> list[str]:
    return [*TOPIC_SECTIONS, "full"]


def quickstart_result() -> dict[str, Any]:
    _, path = read_full_guide()
    return {
        "content": QUICKSTART,
        "path": str(path),
        "mode": "quickstart",
        "available_topics": available_topics(),
        "version_surface": _version_surface(),
        "detail_hint": "MANDATORY: Call aieng.guide {topic} once per session before the first action of that category. Topic map — cad: geometry creation/edit, cae: simulation/solver, workflows: multi-step pipelines, approvals: gated tools, operators: admin actions, pointers: entity selection, package: import/export/lifecycle, fallback: non-MCP mode, frontend: UI development, tools: tool taxonomy, full: complete rules. Re-read only if session context was lost or the guide changed. Full reference: aieng.agent_readme {detail:'full'}."
    }


# One part of the full guide must fit an MCP client's tool-output budget.
# Claude Code's default is 25k tokens; the whole AGENTS.md is ~46k, so served in
# one piece it was cut off and the "read full" path delivered half a guide.
# 60k characters is ~15k tokens of this markdown.
FULL_PART_MAX_CHARS = 60_000


def split_full_guide(markdown: str, max_chars: int = FULL_PART_MAX_CHARS) -> list[str]:
    """Split the guide into parts of at most ``max_chars``.

    Breaks only at blank lines outside code fences, so no paragraph, table or
    code block is cut in two. A single paragraph longer than ``max_chars``
    becomes its own (oversized) part rather than being cut.
    """
    # Blocks end at a blank line outside a fence; parts are packed from whole blocks.
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and not line.strip():
            blocks.append("".join(current))
            current = []
    if current:
        blocks.append("".join(current))

    parts: list[str] = []
    part = ""
    for block in blocks:
        if part and len(part) + len(block) > max_chars:
            parts.append(part)
            part = ""
        part += block
    if part:
        parts.append(part)
    return parts or [""]


def parse_part(value: Any) -> int | None:
    """Return the requested full-guide part, or None when the value is invalid.

    A missing part means part 1. An explicit 0, negative, fractional or
    non-numeric value is invalid rather than silently read as 1 — part 1 is
    the one that unlocks every guide category.
    """
    if value is None:
        return 1
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        try:
            value = int(value.strip())  # "²".isdigit() is True but int("²") raises
        except ValueError:
            return None
    if isinstance(value, int) and value >= 1:
        return value
    return None


def full_result(part: int = 1) -> dict[str, Any]:
    """Return one part of the complete AGENTS.md (part 1 holds both contracts)."""
    content, path = read_full_guide()
    parts = split_full_guide(content)
    if not 1 <= part <= len(parts):
        return {
            "status": "error",
            "code": "invalid_input",
            "message": f"part must be between 1 and {len(parts)}",
            "parts_total": len(parts),
        }
    result = {
        "content": parts[part - 1],
        "path": str(path),
        "mode": "full",
        "part": part,
        "parts_total": len(parts),
        "available_topics": available_topics(),
        "version_surface": _version_surface(),
    }
    if part < len(parts):
        result["next_call"] = {"tool": "aieng.guide", "input": {"topic": "full", "part": part + 1}}
    return result


def guide_result(topic: str, part: int = 1) -> dict[str, Any]:
    """One guide topic from AGENTS.md; `part` applies only to `full`, other topics ignore it."""
    normalized = topic.strip().lower()
    if normalized == "full":
        return full_result(part)
    sections = TOPIC_SECTIONS.get(normalized)
    if not sections:
        return {
            "status": "error",
            "code": "unknown_guide_topic",
            "message": f"Unknown guide topic: {topic}",
            "available_topics": available_topics(),
        }
    content, path = read_full_guide()
    extracted = _topic_extract_cache.get(normalized)
    if extracted is None:
        extracted = _extract_sections(content, sections)
        _topic_extract_cache[normalized] = extracted
    result = {
        "content": extracted,
        "path": str(path),
        "mode": "topic",
        "topic": normalized,
        "sections": list(sections),
        "available_topics": available_topics(),
    }
    related = TOPIC_SEE_ALSO.get(normalized)
    if related:
        result["see_also"] = [
            {"topic": name, "when": reason} for name, reason in related
        ]
    return result


def _extract_sections(markdown: str, requested: tuple[str, ...]) -> str:
    lines = markdown.splitlines()
    selected: list[str] = []
    requested_set = set(requested)
    active_level: int | None = None
    fence_marker: str | None = None
    for line in lines:
        stripped = line.lstrip()
        if fence_marker is not None:
            heading = None
            if stripped.startswith(fence_marker):
                fence_marker = None
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence_marker = stripped[:3]
            heading = None
        else:
            heading = _heading(line)
        if heading:
            level, title = heading
            if active_level is not None and level <= active_level:
                active_level = None
            if title in requested_set:
                if selected and selected[-1] != "":
                    selected.append("")
                active_level = level
        if active_level is not None:
            selected.append(line)
    return "\n".join(selected).strip() or "Requested guide sections were not found in AGENTS.md."


def _heading(line: str) -> tuple[int, str] | None:
    stripped = line.lstrip()
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if hashes < 1 or hashes > 6 or len(stripped) <= hashes or stripped[hashes] != " ":
        return None
    return hashes, stripped[hashes + 1 :].strip()
