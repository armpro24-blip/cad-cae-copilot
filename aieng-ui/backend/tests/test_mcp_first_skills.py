from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "aieng-agent-skills" / "skills"


def _skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def test_cad_authoring_skill_points_to_active_mcp_cad_tools() -> None:
    text = _skill_text("aieng-cad-authoring")
    assert "cad.execute_build123d" in text
    assert "cad.get_source" in text
    assert "cad.edit_parameter" in text
    assert "cad.critique" in text
    assert "aieng plan" not in text
    assert "init-from-plan" not in text


def test_cae_skill_uses_active_mcp_cae_tools_not_legacy_wrappers() -> None:
    text = _skill_text("aieng-cad-cae-copilot")
    for tool_name in (
        "cae.prepare_solver_run",
        "cae.generate_solver_input",
        "cae.run_solver",
        "cae.extract_solver_results",
    ):
        assert tool_name in text
    for legacy_name in (
        "aieng_run_solver",
        "aieng_prepare_solver_run",
        "aieng_get_cae_result_summary",
    ):
        assert legacy_name not in text


def test_closed_loop_skill_keeps_approval_and_evidence_boundaries() -> None:
    text = _skill_text("aieng-closed-loop-copilot")
    assert "cad.list_editable_parameters" in text
    assert "cad.edit_parameter" in text
    assert "cae.run_solver" in text
    assert "AIENG_MCP_BLOCK_APPROVAL_TOOLS=1" in text
    assert "Do not claim improvement until post-change solver/result evidence exists" in text


def test_cad_authoring_skill_teaches_trust_layer_tools() -> None:
    # #231: agent skills must teach the trust-layer tools (#216 critique-diff,
    # #217 require() assertions) or external agents won't use them.
    text = _skill_text("aieng-cad-authoring")
    assert "require(" in text
    assert "design_rule_violation" in text
    assert "critique_diff" in text


def test_cae_skill_teaches_credibility_tiering() -> None:
    # #231: teach the V&V-40 credibility tier (#218).
    text = _skill_text("aieng-cad-cae-copilot")
    assert "credibility" in text
    assert "executed_solver_result" in text
    assert "surrogate_prediction" in text


def test_closed_loop_skill_teaches_credibility_and_surrogate_bands() -> None:
    # #231: teach credibility tiering (#218) + surrogate error-band discipline (#219).
    text = _skill_text("aieng-closed-loop-copilot")
    assert "critique_diff" in text
    assert "credibility" in text
    assert "uncertainty_std" in text
    assert "predicted_score_band" in text


def test_agent_skills_exposed_as_mcp_prompts_dev_skills_excluded() -> None:
    """The modeling/CAE agent skills are registered as MCP prompts (portable skill
    discovery for any client); dev skills (.claude/skills, e.g. superpowers) are NOT."""
    from app.mcp_sdk_compat import FastMCP

    import app.mcp_server as ms

    mcp = FastMCP("test-skill-prompts")
    ms._register_agent_skill_prompts(mcp)
    names = {p.name for p in mcp._prompt_manager.list_prompts()}  # type: ignore[attr-defined]

    assert "aieng-cad-authoring" in names
    assert "aieng-cad-cae-copilot" in names
    assert "aieng-closed-loop-copilot" in names
    # Dev skills must never leak to connecting modeling/CAE agents.
    assert not any("superpower" in name.lower() for name in names), names


def test_agent_skill_prompt_renders_skill_body() -> None:
    import asyncio

    from app.mcp_sdk_compat import FastMCP

    import app.mcp_server as ms

    mcp = FastMCP("test-skill-prompts")
    ms._register_agent_skill_prompts(mcp)
    prompt = next(p for p in mcp._prompt_manager.list_prompts() if p.name == "aieng-cad-authoring")  # type: ignore[attr-defined]
    # Pass both explicitly: 1.x defaults them to None, 2.x made them required.
    messages = asyncio.run(prompt.render(None, None))
    assert messages, "prompt should render at least one message"
    text = messages[0].content.text  # type: ignore[union-attr]
    assert "cad.execute_build123d" in text  # the skill body, not just frontmatter
