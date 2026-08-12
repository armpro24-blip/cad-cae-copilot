"""The review lens must stay reachable, and stay in sync with what enforces it.

This cycle found 22 real defects by dogfooding, and most fall into a handful of
recurring patterns. Writing them down is worth something only if two things
hold: an agent can ask for them, and the automated reviewer is actually told to
look for them. Both are easy to break silently — which is `undocumented-path`,
the first pattern of the lens itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from app.agent_guides import available_topics, guide_result  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CODERABBIT = _REPO_ROOT / ".coderabbit.yaml"
_PATTERN_ID = re.compile(r"`([a-z][a-z-]+)`")

# The detection QUESTION, not the pattern's name: a lens whose questions get
# edited away is no longer a lens. Whitespace is normalized before matching, so
# re-wrapping the Markdown cannot fail a test that is about meaning.
_QUESTIONS = (
    "does anything but the docs mention it",
    "what does this rule say about a CORRECT input",
    "does it say so, or does it do something else",
    "is every documented input actually read",
    "if a default stood in for missing input, can the caller tell",
    "is this gate holding because the rule fired",
    "was this file written by today's code",
)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _lens_text() -> str:
    return guide_result("review")["content"]


def _lens_pattern_ids() -> set[str]:
    """The ids on the lens's numbered headings in AGENTS.md."""
    return {
        _PATTERN_ID.search(line).group(1)
        for line in _lens_text().splitlines()
        if re.match(r"### \d+\. `", line)
    }


def _reviewer_pattern_ids() -> set[str]:
    """The ids the automated reviewer is told to weight."""
    config = yaml.safe_load(_CODERABBIT.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for entry in config["reviews"]["path_instructions"]:
        for line in entry["instructions"].splitlines():
            if re.match(r"\s*\d+\. `", line):
                ids.add(_PATTERN_ID.search(line).group(1))
    return ids


def test_the_review_topic_is_askable() -> None:
    assert "review" in available_topics()
    result = guide_result("review")
    assert result["topic"] == "review"
    assert "Review lens" in result["content"]


def test_every_pattern_keeps_its_detection_question() -> None:
    """The abstract pattern is easy to nod at; the question is what gets used."""
    text = _squash(_lens_text())
    missing = [q for q in _QUESTIONS if q not in text]
    assert missing == [], f"detection question(s) lost from the lens: {missing}"
    assert len(_lens_pattern_ids()) == len(_QUESTIONS), \
        "every pattern needs a question and every question a pattern"


def test_the_lens_stays_grounded_in_measured_instances() -> None:
    """A pattern without its instance degrades into a platitude."""
    text = _lens_text()
    for evidence in (
        "NameError",                 # the fallback path, dead on command one
        "4 of 4 correct interfaces",
        "cantilever",                # the substituted textbook problem
        "design_space_node",         # the selector read too late to matter
        "69000 MPa",                 # the invented aluminium
        "30-face bracket",           # safety by accident
        'scope: "local"',            # the stale artifact
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

def test_the_automated_reviewer_is_told_about_exactly_the_same_patterns() -> None:
    """Writing the lens down changes nothing unless the reviewer reads it.

    Asserting the two SETS are equal, not that each is non-empty. The first
    version of this test checked only that the config mentioned six markers, so
    it passed while the halves had genuinely drifted — AGENTS.md carried
    `undocumented-path` and the config did not, the config carried
    `invented-data` and AGENTS.md did not. That is `asked-a-got-b` (a check
    claiming to verify synchronisation while verifying one side) sitting inside
    the lens that names it; CodeRabbit caught it using these very rules.
    """
    assert _CODERABBIT.is_file(), "no .coderabbit.yaml — the lens has no enforcer"
    lens, reviewer = _lens_pattern_ids(), _reviewer_pattern_ids()

    assert lens, "no numbered patterns found in the lens"
    assert lens == reviewer, (
        f"the two halves disagree — only in AGENTS.md: {sorted(lens - reviewer)}; "
        f"only in .coderabbit.yaml: {sorted(reviewer - lens)}"
    )


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
