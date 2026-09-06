#!/usr/bin/env python3
"""Anti-drift check for agent onboarding docs (#15).

Ensures thin wrappers still point to the canonical doc and that
AGENTS.md contains the sections every agent is expected to see.

Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"FAIL: cannot read {path}: {exc}")
        sys.exit(1)


def check_agents_md() -> list[str]:
    errors: list[str] = []
    text = _read(REPO_ROOT / "AGENTS.md")

    required_sections = [
        "## STOP — read this first",
        "## First three calls every session",
        "## Workflow priority matrix",
        "## Workspace layout",
        "## Recommended workflows",
        "## Approval-gated tools",
        "## Fallback mode",
    ]
    for section in required_sections:
        if section not in text:
            errors.append(f"AGENTS.md missing section: {section}")

    # Sanity-check that the workflow matrix mentions both skills.
    if "aieng-cad-authoring" not in text:
        errors.append("AGENTS.md workflow matrix missing aieng-cad-authoring")
    if "aieng-cad-cae-copilot" not in text:
        errors.append("AGENTS.md workflow matrix missing aieng-cad-cae-copilot")

    return errors


def check_thin_wrappers() -> list[str]:
    errors: list[str] = []
    agents_path = REPO_ROOT / "AGENTS.md"

    for wrapper_name in ("CLAUDE.md", ".github/copilot-instructions.md"):
        path = REPO_ROOT / wrapper_name
        if not path.exists():
            errors.append(f"{wrapper_name} does not exist")
            continue
        text = _read(path)
        if "AGENTS.md" not in text:
            errors.append(f"{wrapper_name} must reference AGENTS.md")
        # Should be short — if it grows past 50 lines it is no longer a thin wrapper.
        lines = text.splitlines()
        if len(lines) > 50:
            errors.append(f"{wrapper_name} is {len(lines)} lines; keep it under 50")

    return errors


def check_skill_alignment() -> list[str]:
    errors: list[str] = []
    skills_dir = REPO_ROOT / "aieng-agent-skills" / "skills"
    if not skills_dir.exists():
        errors.append("aieng-agent-skills/skills/ missing")
        return errors

    for skill_name in ("aieng-cad-authoring", "aieng-cad-cae-copilot"):
        skill_md = skills_dir / skill_name / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"Skill file missing: {skill_md}")
            continue
        text = _read(skill_md)
        if "## Hard rules" not in text and "## Required workflow" not in text:
            errors.append(f"{skill_name}/SKILL.md missing Hard rules or Required workflow")

    return errors


def check_docs_index() -> list[str]:
    """Every file in docs/ must be named in docs/README.md.

    31 files with no index made the directory unreadable to anyone new: a plan
    abandoned in May sat beside the contract the runtime enforces today, and two
    of them still called FreeCAD the CAD backend. An index fixes that exactly
    once; this check is what stops it drifting back, because an index nobody
    updates is just one more stale artifact.

    Checked in ONE direction on purpose — a doc on disk must be indexed. The
    reverse (an indexed doc must exist) would fail on any working copy holding
    an untracked note, and would also forbid the "Removed" section from naming
    what was deleted and why, which is the part a reader coming from an old link
    actually needs.
    """
    errors: list[str] = []
    docs_dir = REPO_ROOT / "docs"
    index = docs_dir / "README.md"
    if not index.exists():
        return ["docs/README.md is missing — docs/ needs an index"]

    text = _read(index)
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        if path.name not in text:
            errors.append(
                f"docs/{path.name} is not listed in docs/README.md — add it under "
                "Current or Historical so a reader can tell which it is"
            )
    return errors


def main() -> int:
    all_errors: list[str] = []
    all_errors.extend(check_agents_md())
    all_errors.extend(check_thin_wrappers())
    all_errors.extend(check_skill_alignment())
    all_errors.extend(check_docs_index())

    if all_errors:
        print("Agent doc anti-drift check FAILED:")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print("Agent doc anti-drift check PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
