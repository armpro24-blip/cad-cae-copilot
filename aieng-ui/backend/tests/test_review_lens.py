"""The review lens must stay reachable, and stay in sync with what enforces it.

This session found 22 real defects by dogfooding, and most of them fall into a
handful of recurring patterns. Writing the patterns down is only worth something
if two things hold: an agent can ask for them, and the automated reviewer is
actually told to look for them. Both are easy to break silently — which is
pattern 1 of the lens itself (a documented thing nothing exercises).
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from app.agent_guides import available_topics, guide_result  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CODERABBIT = _REPO_ROOT / ".coderabbit.yaml"

# One anchor phrase per pattern. Deliberately the DETECTION QUESTION rather than
# the pattern's name: a lens whose questions get edited away is no longer a lens.
_PATTERNS = (
    "does anything but the docs mention it",
    "what does this rule say about a CORRECT input",
    "does it say so, or does it do\nsomething else",
    "is every documented input actually read",
    "is this gate holding because the rule fired",
    "was this file written by today's code",
)


def _lens_text() -> str:
    return guide_result("review")["content"]


def test_the_review_topic_is_askable() -> None:
    assert "review" in available_topics()
    result = guide_result("review")
    assert result["topic"] == "review"
    assert "Review lens" in result["content"]


def test_every_pattern_keeps_its_detection_question() -> None:
    """The abstract pattern is easy to nod at; the question is what gets used."""
    text = _lens_text()
    missing = [q for q in _PATTERNS if q not in text]
    assert missing == [], f"detection question(s) lost from the lens: {missing}"


def test_the_lens_stays_grounded_in_measured_instances() -> None:
    """A pattern without its instance degrades into a platitude."""
    text = _lens_text()
    for evidence in (
        "NameError",              # the fallback path dead on command one
        "4 of 4 correct interfaces",
        "cantilever",             # the substituted textbook problem
        "design_space_node",      # the selector read too late to matter
        "30-face bracket",        # safety by accident
        'scope: "local"',         # the stale artifact
    ):
        assert evidence in text, f"lens lost the evidence for a pattern: {evidence}"


def test_the_lens_is_not_folded_into_the_modelling_topics() -> None:
    """#494 cut cad/cae weight; a reviewer's checklist must not put it back."""
    for topic in ("cad", "cae"):
        assert "Review lens" not in guide_result(topic)["content"], topic


def test_the_lens_stays_cheap_enough_to_read() -> None:
    content = _lens_text()
    assert len(content) < 12000, f"review topic grew to {len(content)} chars"


# ── the half that actually enforces it ───────────────────────────────────────

def test_the_automated_reviewer_is_told_about_the_same_patterns() -> None:
    """Writing the lens down changes nothing unless the reviewer reads it."""
    assert _CODERABBIT.is_file(), "no .coderabbit.yaml — the lens has no enforcer"
    config = yaml.safe_load(_CODERABBIT.read_text(encoding="utf-8"))
    instructions = "\n".join(
        entry["instructions"] for entry in config["reviews"]["path_instructions"]
    ).lower()

    for pattern_marker in (
        "silent substitute",
        "silently got b",
        "invented data",
        "by construction",
        "safety by accident",
        "older than the logic",
    ):
        assert pattern_marker in instructions, f"reviewer not told about: {pattern_marker}"


def test_the_reviewer_config_covers_the_surfaces_that_produced_the_defects() -> None:
    config = yaml.safe_load(_CODERABBIT.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in config["reviews"]["path_instructions"]}
    assert "**/*.py" in paths
    assert "**/test_*.py" in paths, "tests encode the honesty contract — review them as such"
    assert "**/*.md" in paths, "docs here are a contract an agent acts on"
    assert any("runtime_registry" in p for p in paths), "the MCP tool surface needs its own lens"


def test_the_reviewer_config_points_at_the_long_form() -> None:
    """So a reader of either half can find the evidence behind it."""
    text = _CODERABBIT.read_text(encoding="utf-8")
    assert "AGENTS.md" in text and "review" in text
