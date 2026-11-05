from __future__ import annotations

from pathlib import Path

import pytest


def test_the_cae_guide_states_the_rules_for_the_tools_it_gates() -> None:
    """The guide gate is per CATEGORY, so a tool's contract must be in ITS category.

    Measured before the fix: `TOPIC_SECTIONS["cae"]` served the CAE tool list,
    workflows C and D — but not workflow E, the only place the re-solve contract
    lives. `cae.generate_solver_input` and `cae.run_solver` are gated on `cae`;
    the rules governing them were served only under `cad`. So an agent doing
    exactly what the gate asks re-solved into `run_001` and hit guards whose
    explanation it had never been shown.

    Asserted as an invariant — the rules must be reachable — not as a section
    list, which could be satisfied by editing the expectation.
    """
    from app import agent_guides

    content = agent_guides.guide_result("cae")["content"]

    for rule in ("run_id", "stale_deck", "run_id_conflict", "cae.compare_runs"):
        assert rule in content, (
            f"{rule!r} governs a cae.* tool but is not in the guide that gates it"
        )


def test_the_quickstart_describes_the_loop_it_guards() -> None:
    """The one document every session reads must contain the promised task.

    It listed CAD, dimension edit and simulate as three DISCONNECTED workflows:
    "Modify dimension" ended at `regression_diff` with no re-solve, and
    "Simulate" named no `run_id` discipline. The guards that make the wrong path
    fail loudly were built without the right path appearing in the only file
    everyone reads.
    """
    from app import agent_guides

    content = agent_guides.quickstart_result()["content"]

    assert "cad.edit_parameter" in content
    assert "cae.compare_runs" in content
    assert "run_002" in content, "the quickstart must show that a re-solve needs a NEW run id"
    assert "stale_deck" in content


def test_quickstart_is_compact_and_preserves_first_three_calls() -> None:
    from app import agent_guides

    result = agent_guides.quickstart_result()

    assert result["mode"] == "quickstart"
    assert len(result["content"]) < 8_000
    assert "aieng.agent_readme" in result["content"]
    assert "aieng.list_projects" in result["content"]
    assert "aieng.agent_context" in result["content"]
    assert "aieng.guide" in result["content"]
    assert "Before the first CAD modeling or geometry-edit action" in result["content"]
    assert "cad.confirm_modeling_plan" in result["content"]
    assert "AskUserQuestion" in result["content"]
    assert "request_user_input" in result["content"]
    assert "Re-read a guide only if" in result["content"]
    assert 'code: "guide_required"' in result["content"]


def test_full_guide_is_served_whole_in_parts_that_fit_a_client_budget() -> None:
    """Served in one piece, AGENTS.md (~46k tokens) exceeded Claude Code's 25k
    MCP output limit, so the documented "read full" path was cut off."""
    from app import agent_guides

    canonical, _ = agent_guides.read_full_guide()
    first = agent_guides.full_result()
    parts = [agent_guides.full_result(n)["content"] for n in range(1, first["parts_total"] + 1)]

    assert first["mode"] == "full"
    assert first["path"].endswith("AGENTS.md")
    assert "".join(parts) == canonical, "paging must not drop or repeat anything"
    assert all(len(p) <= agent_guides.FULL_PART_MAX_CHARS for p in parts)
    assert first["next_call"] == {"tool": "aieng.guide", "input": {"topic": "full", "part": 2}}
    # The gate lets part 1 unlock every category, so part 1 must hold the contracts.
    assert "## CAD contract" in first["content"]
    assert "## CAE contract" in first["content"]
    assert "next_call" not in agent_guides.full_result(first["parts_total"])
    assert agent_guides.full_result(first["parts_total"] + 1)["code"] == "invalid_input"


@pytest.mark.parametrize(("value", "expected"), [
    (None, 1), (1, 1), (3, 3), (2.0, 2), ("2", 2),
    (0, None), (-1, None), (1.5, None), (True, None), ("x", None), ("", None),
])
def test_part_parsing_keeps_the_default_but_refuses_explicit_invalid_values(value, expected) -> None:
    """`int(part or 1)` read an explicit 0 as part 1 — the part that unlocks the gate."""
    from app import agent_guides

    assert agent_guides.parse_part(value) == expected


def test_full_guide_split_never_cuts_a_code_fence() -> None:
    from app import agent_guides

    text = "intro\n\n```python\nx = 1\n\ny = 2\n```\n\nouter\n"
    parts = agent_guides.split_full_guide(text, max_chars=12)

    assert "".join(parts) == text
    assert any("x = 1\n\ny = 2" in part for part in parts)


def test_topic_guide_extracts_only_requested_sections() -> None:
    from app import agent_guides

    result = agent_guides.guide_result("pointers")

    assert result["mode"] == "topic"
    assert result["topic"] == "pointers"
    assert "## Pointer syntax" in result["content"]
    assert "## Fallback mode" not in result["content"]


def test_core_topics_point_at_what_they_left_out() -> None:
    """A lean core is only an improvement if the dropped material stays findable.

    `cad` and `cae` are the MANDATORY pre-action reads, so they carry the
    contract (code rules, approval boundary, honesty rules) and hand off the
    reference catalogues. Measured before the split: cad 8505 tokens, cae 10200
    — paid once per session by every connected agent.
    """
    from app import agent_guides

    cad = agent_guides.guide_result("cad")
    cae = agent_guides.guide_result("cae")

    assert {s["topic"] for s in cad["see_also"]} == {"cad-reference", "cad-helpers", "cad-modes"}
    assert {s["topic"] for s in cae["see_also"]} == {"cae-reference", "cae-assembly"}
    for entry in cad["see_also"] + cae["see_also"]:
        assert entry["when"], "a pointer without a 'when' is not actionable"
        assert entry["topic"] in agent_guides.TOPIC_SECTIONS

    # Sub-topics point back at the contract they do not repeat.
    assert {s["topic"] for s in agent_guides.guide_result("cad-helpers")["see_also"]} == {"cad"}
    assert {s["topic"] for s in agent_guides.guide_result("cae-assembly")["see_also"]} == {"cae"}
    assert {s["topic"] for s in agent_guides.guide_result("cad-reference")["see_also"]} == {"cad"}
    assert {s["topic"] for s in agent_guides.guide_result("cae-reference")["see_also"]} == {"cae"}


def test_required_reading_stays_small() -> None:
    """The reads every CAD->CAE session must do before working.

    Measured 2026-11-05 before the contract split: quickstart + cad + cae were
    ~26k tokens, most of it the history behind guards that already explain
    themselves when they refuse. Context spent there is context the agent does
    not have for the task. ~6k tokens is the budget; rationale belongs in the
    *-reference topics.
    """
    from app import agent_guides

    required = (
        agent_guides.quickstart_result()["content"]
        + agent_guides.guide_result("cad")["content"]
        + agent_guides.guide_result("cae")["content"]
    )
    assert len(required) < 24_000


def test_split_kept_the_contract_in_the_mandatory_read() -> None:
    """The gate exists for the rules — those must not have moved to a sub-topic."""
    from app import agent_guides

    cad = agent_guides.guide_result("cad")["content"]
    for rule in (
        "Bind the final model to a variable named **`result`**",
        "UPPER_SNAKE_CASE",
        "cad.confirm_modeling_plan",
        "AskUserQuestion",
        "previous_result",
        "regression_diff",
        "editable_parameters.total == 0",
        "cad.restore_snapshot",
    ):
        assert rule in cad, f"the cad contract lost {rule!r}"

    cae = agent_guides.guide_result("cae")["content"]
    for rule in (
        "accuracy.band",
        "credibility",
        "stale_mesh",
        "new** `run_id`",
        "needs_user_input",
        "cae.mesh_convergence",
        "No fatigue",
    ):
        assert rule.lower() in cae.lower(), f"the cae contract lost {rule!r}"


def test_cad_topic_keeps_feedback_loop_after_python_comments() -> None:
    from app import agent_guides

    content = agent_guides.guide_result("cad-reference")["content"]

    assert "# step 2, mode=append" in content
    assert "**Visual feedback (multi-view contact sheet).**" in content
    assert "**Iterate using fail-first review.**" in content
    assert "**Reference image calibration.**" in content
    assert "**Quantitative geometry report (`geometry_report`).**" in content
    assert "**Regression diff on every edit (`regression_diff`).**" in content
    assert "### B2 — Incremental modeling (the sustainable loop)" in content
    assert "## Approval-gated tools" in content
    assert "## Stale-artifact warnings" in content


@pytest.mark.parametrize(
    ("topic", "required_fragments"),
    [
        (
            "cae-reference",
            (
                "## Pointer syntax — `@kind:id`",
                "**Free-form faces and CAE.**",
                "### C — CAD → CAE simulation pipeline",
                "### D — Inspect results and explain findings",
                "## Approval-gated tools",
                "## Stale-artifact warnings",
            ),
        ),
        (
            # Multi-part material moved OUT of the mandatory `cae` read (#482):
            # it was 24% of that topic and irrelevant to every single-part
            # session. It must still be complete where it now lives.
            "cae-assembly",
            (
                "### Assembly IR v0 (optional, multi-part)",
                "contact_physics_modeled:false",
            ),
        ),
        (
            "cad-helpers",
            (
                "### High-level helpers",
                "`lofted_stack(sections)`",
                "### Curve patterns",
            ),
        ),
        (
            "cad-modes",
            (
                "### Industrial Design Mode",
                "### Engineering Mode",
                "base_plate",
            ),
        ),
        (
            "frontend",
            (
                "## Workspace layout",
                "## Frontend maintainability rules",
                "### Agent run display state",
                # The composer/intent-routing layer was deleted in the MCP-first
                # cutover; the guide must still SAY so, because an agent that
                # goes looking for engine.py / intent_resolution.py wastes a
                # detour on modules that no longer exist.
                "### Composer slash commands and routing — REMOVED",
                "no longer exist",
                # …and it must still carry the panels that DO exist.
                "**Editable Parameter Explorer",
            ),
        ),
        (
            "workflows",
            (
                "## Pointer syntax — `@kind:id`",
                "## Recommended workflows",
                "## Approval-gated tools",
                "## Stale-artifact warnings",
            ),
        ),
        (
            "tools",
            (
                "## STOP — read this first",
                "## First three calls every session",
                "## Tool taxonomy",
                "## Approval-gated tools",
            ),
        ),
        (
            "package",
            (
                "### Package lifecycle",
                "## Approval-gated tools",
                "## Stale-artifact warnings",
                "## .aieng package structure (reference)",
            ),
        ),
        (
            "approvals",
            (
                "## Tool taxonomy",
                "## Approval-gated tools",
                "## If the backend (port 8000) is unreachable",
                "`AIENG_MCP_MANAGED_APPROVAL`",
                "`AIENG_MCP_BLOCK_APPROVAL_TOOLS`",
            ),
        ),
        (
            "operators",
            (
                "## Workspace layout",
                "## Approval-gated tools",
                "## If the backend (port 8000) is unreachable",
                "## Environment variables (for MCP server operators)",
                "`AIENG_MCP_MANAGED_APPROVAL`",
                "`AIENG_MCP_REQUIRE_GUIDES`",
                "`AIENG_AGENTIC_PERMISSION_TOOL`",
            ),
        ),
    ],
)
def test_topic_guides_include_task_complete_operational_context(
    topic: str,
    required_fragments: tuple[str, ...],
) -> None:
    from app import agent_guides

    content = agent_guides.guide_result(topic)["content"]

    for fragment in required_fragments:
        assert fragment in content, f"{topic} guide is missing {fragment!r}"


def test_all_configured_topic_sections_exist_in_canonical_guide() -> None:
    from app import agent_guides

    markdown, _ = agent_guides.read_full_guide()

    for topic, sections in agent_guides.TOPIC_SECTIONS.items():
        content = agent_guides._extract_sections(markdown, sections)
        for section in sections:
            assert agent_guides._extract_sections(markdown, (section,)) != (
                "Requested guide sections were not found in AGENTS.md."
            ), f"{topic} references missing canonical section {section!r}"
        assert content != "Requested guide sections were not found in AGENTS.md."


def test_section_extraction_ignores_markdown_headings_inside_fences() -> None:
    from app import agent_guides

    markdown = """\
## Keep
```python
# This is Python, not a Markdown heading
value = 1
```
still kept
## Drop
not kept
"""

    extracted = agent_guides._extract_sections(markdown, ("Keep",))

    assert "still kept" in extracted
    assert "not kept" not in extracted


def test_unknown_topic_reports_available_topics() -> None:
    from app import agent_guides

    result = agent_guides.guide_result("mystery")

    assert result["status"] == "error"
    assert result["code"] == "unknown_guide_topic"
    assert "cad" in result["available_topics"]
def test_guide_result_caches_topic_extractions() -> None:
    from app import agent_guides

    agent_guides.clear_guide_cache()
    result1 = agent_guides.guide_result("pointers")
    assert "pointers" in agent_guides._topic_extract_cache
    result2 = agent_guides.guide_result("pointers")
    assert result1["content"] == result2["content"]
    assert result1["path"] == result2["path"]


def test_read_full_guide_caches_content() -> None:
    from app import agent_guides

    agent_guides.clear_guide_cache()
    content1, path1 = agent_guides.read_full_guide()
    assert agent_guides._guide_content_cache is not None
    content2, path2 = agent_guides.read_full_guide()
    assert content1 is content2
    assert path1 is path2


def test_clear_guide_cache_resets_state() -> None:
    from app import agent_guides

    agent_guides.read_full_guide()
    agent_guides.guide_result("cad")
    assert agent_guides._guide_content_cache is not None
    assert "cad" in agent_guides._topic_extract_cache

    agent_guides.clear_guide_cache()

    assert agent_guides._guide_content_cache is None
    assert agent_guides._guide_content_mtime == 0.0
    assert not agent_guides._topic_extract_cache


def test_guide_cache_invalidates_when_file_mtime_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import time
    from app import agent_guides

    guide_path = tmp_path / "AGENTS.md"
    guide_path.write_text("## Hello\nworld\n", encoding="utf-8")
    monkeypatch.setattr(agent_guides, "canonical_agents_path", lambda: guide_path)
    agent_guides.clear_guide_cache()

    content1, _ = agent_guides.read_full_guide()
    assert content1 == "## Hello\nworld\n"

    # Ensure mtime changes even on coarse-resolution filesystems (e.g., FAT with 2s granularity).
    time.sleep(2.0)
    guide_path.write_text("## Hello\nupdated\n", encoding="utf-8")
    content2, _ = agent_guides.read_full_guide()
    assert content2 == "## Hello\nupdated\n"

    result1 = agent_guides.full_result()
    assert "updated" in result1["content"]
