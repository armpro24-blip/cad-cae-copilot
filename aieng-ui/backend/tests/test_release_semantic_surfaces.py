"""Release semantic-surface guard (issue #181).

The alpha honesty posture is: `.aieng` *records* evidence and context; it does
**not** certify engineering correctness and does **not** silently advance
engineering "claims". A prior one-off audit
(``aieng/docs/release/release_blocker_audit.md``) cleaned the static surfaces;
these tests pin that cleaned state so it cannot silently regress.

Scope: the *static, alpha-facing* surfaces an external agent / user actually
reads — top-level + package READMEs, the agent guides, and the canonical MCP
tool-schema descriptions. Generated/runtime artifacts are guarded separately by
``app.project_health`` and the export smoke checks; this module covers the text
that ships with the repository.

Note on matching: we look for *affirmative* prohibited phrases (e.g. "design is
certified"). Negated honesty wording ("does not certify the design", "not
production-certified") is the desired posture and is intentionally NOT matched —
the affirmative phrasings below do not occur as substrings of those negations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.runtime_tool_schemas import TOOL_SCHEMAS

# tests/ -> backend/ -> aieng-ui/ -> workspace root
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Affirmative phrases that must never appear on an alpha-facing surface.
# Union of the lists already enforced on generated artifacts
# (app/project_health.py, tests/test_review_support_packet.py) plus the
# claim-advancement phrasings the release audit flagged.
PROHIBITED_PHRASES: tuple[str, ...] = (
    # Certification / physical-validation guarantees
    "design is certified",
    "design is validated",
    "certified safe",
    "guaranteed safe",
    "approved design",
    "engineering claim approved",
    "engineering claim accepted",
    "claim accepted",
    # Automatic claim advancement presented as a normal workflow
    "automatically advances claims",
    "claims are advanced automatically",
    "auto-advance claims",
    "automatically advance the claim",
)

# Static surfaces an external agent / user reads. Missing files are skipped so
# the guard stays robust to repo reorganisation.
_ALPHA_FACING_FILES: tuple[str, ...] = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "aieng/README.md",
    "aieng-ui/README.md",
    "aieng-ui/backend/MCP_SETUP.md",
)


def _scan(text: str) -> list[str]:
    """Return the prohibited phrases present in ``text`` (case-insensitive)."""
    haystack = text.lower()
    return [phrase for phrase in PROHIBITED_PHRASES if phrase in haystack]


def _existing_alpha_files() -> list[Path]:
    return [
        path
        for rel in _ALPHA_FACING_FILES
        if (path := _REPO_ROOT / rel).is_file()
    ]


def test_alpha_facing_files_are_present() -> None:
    """At least the core READMEs/guides must resolve, else the guard is hollow."""
    found = {p.relative_to(_REPO_ROOT).as_posix() for p in _existing_alpha_files()}
    # These three are load-bearing for the alpha story and must exist.
    for required in ("README.md", "aieng/README.md", "aieng-ui/README.md"):
        assert required in found, f"alpha-facing file missing: {required}"


@pytest.mark.parametrize("doc_path", _existing_alpha_files(), ids=lambda p: p.name)
def test_static_surface_has_no_prohibited_certification_language(doc_path: Path) -> None:
    """Shipped docs must not affirmatively certify designs or advance claims."""
    hits = _scan(doc_path.read_text(encoding="utf-8"))
    rel = doc_path.relative_to(_REPO_ROOT).as_posix()
    assert not hits, (
        f"{rel} contains prohibited certification/claim-advancement wording: {hits}. "
        "Use evidence/readiness/review-required language instead (see "
        "aieng/docs/release/release_blocker_audit.md)."
    )


def test_mcp_tool_descriptions_have_no_prohibited_language() -> None:
    """The canonical MCP tool-schema descriptions external agents list must stay
    evidence-only — no certification or auto-claim-advancement wording."""
    offenders: dict[str, list[str]] = {}
    for tool_name, schema in TOOL_SCHEMAS.items():
        description = schema.get("description")
        if not isinstance(description, str):
            continue
        hits = _scan(description)
        if hits:
            offenders[tool_name] = hits
    assert not offenders, (
        f"MCP tool descriptions contain prohibited wording: {offenders}. "
        "Tool descriptions must not present certification or automatic claim "
        "advancement as a capability."
    )


def test_readmes_explain_proof_not_just_screenshots() -> None:
    english = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (_REPO_ROOT / "README.zh.md").read_text(encoding="utf-8")

    assert "## Proof, not just screenshots" in english
    assert "generated build123d source" in english
    assert "STEP/STL/GLB exports" in english
    assert "topology maps and stable `@face:*` pointers" in english
    assert "instead of trusting a static render" in english

    assert "## 不是只看截图" in chinese
    assert "生成的 build123d 源码" in chinese
    assert "STEP/STL/GLB 导出" in chinese
    assert "稳定的 `@face:*` 指针" in chinese
    assert "静态渲染图" in chinese


# ── install snippets must name a channel that exists ─────────────────────────
#
# Distributions this project does NOT publish. `aieng/README.md` carried
# `pip install aieng-format` as its first Quick Start line for the whole alpha:
# the name is unregistered on PyPI, so anyone following it would have installed
# whatever a stranger uploads under that name — a documented path nothing
# exercises, with a supply-chain edge on it.
#
# Delete a name from this tuple on the day it is actually published, and the
# guard stops objecting to docs that advertise it. Keeping the list explicit is
# what lets the rule say something about a CORRECT input instead of banning the
# phrase forever.
_UNPUBLISHED_DISTRIBUTIONS: tuple[str, ...] = (
    "aieng-format",
    "aieng-workbench-mcp",
)

# An installer invocation, however it is spelled. The first version matched only
# a line STARTING with `pip install`, which is one of the several forms this
# repo's own docs use — `python -m pip install`, a venv-scoped
# `.venv\Scripts\python -m pip install`, `uv pip install`. A guard that knows
# one spelling of the thing it forbids is the same defect it exists to catch.
_INSTALLER = re.compile(
    r"""(?:^|\s)(?:
          (?:[\w./\\:-]*python[\w.]*\s+-m\s+)?pip\s+install
        | uv\s+pip\s+install
        | pipx\s+install
        | uvx(?:\s+--from|\s+--with)?
        )\s""",
    re.IGNORECASE | re.VERBOSE,
)
_FENCE = re.compile(r"^\s*```")
# A pip/uvx argument that is a requirement rather than a flag or a flag's value.
_FLAGS_TAKING_A_VALUE = {"--index-url", "-i", "--extra-index-url", "--find-links",
                         "-f", "--constraint", "-c", "--requirement", "-r",
                         "--python", "-p", "--data-dir", "--approval-mode",
                         "--backend-url", "--from", "--with", "-e"}


def _install_commands(text: str) -> list[str]:
    """Installer invocations inside fenced code blocks, continuations joined.

    Only code blocks: prose that MENTIONS a command in order to warn against it
    ("do not run `pip install aieng-format`") is not an instruction, and a rule
    that cannot tell the difference fires on every correct document — which
    buries the one real finding with it.
    """
    commands: list[str] = []
    in_fence = False
    pending: str | None = None
    for raw in text.splitlines():
        if _FENCE.match(raw):
            if pending is not None:
                commands.append(pending)
            in_fence, pending = not in_fence, None
            continue
        if not in_fence:
            continue
        line = raw.strip()
        fragment = line.rstrip("\\").strip()
        if pending is not None:
            pending = pending + " " + fragment
        elif _INSTALLER.search(line):
            pending = fragment
        else:
            continue
        if not line.endswith("\\"):
            commands.append(pending)
            pending = None
    if pending is not None:
        commands.append(pending)
    return commands


def _requirements(command: str) -> list[str]:
    """The requirement arguments of an installer command, quotes preserved.

    Each is judged on its own: `pip install "a @ git+https://…" b` pins the
    first and resolves the second from an index, so one `git+` anywhere must not
    excuse the whole line.

    Installer grammar matters here. `uvx --from SPEC --with SPEC COMMAND [args]`
    takes its requirements from the flags — the positional is the executable to
    run, and reading it as a requirement made the project's own recommended
    install command an offender. Only the bare `uvx NAME` form (no `--from`)
    names a distribution positionally.
    """
    # Split on quotes so a quoted requirement (which may contain spaces, as the
    # `name @ git+URL` form does) survives as one token.
    tokens = [t for t in re.findall(r'"[^"]*"|\'[^\']*\'|\S+', command) if t]
    is_uvx = False
    for index, token in enumerate(tokens):
        bare = token.strip("\"'").lower()
        if bare == "uvx":
            is_uvx, tokens = True, tokens[index + 1:]
            break
        if bare == "install":
            tokens = tokens[index + 1:]
            break

    requirements: list[str] = []
    positionals: list[str] = []
    from_flag_seen = False
    expect_value_for: str | None = None
    for token in tokens:
        bare = token.strip("\"'")
        if expect_value_for is not None:
            if expect_value_for in {"--from", "--with"}:
                requirements.append(bare)
                from_flag_seen = True
            expect_value_for = None
            continue
        if bare.startswith("-"):
            flag, _, inline = bare.partition("=")
            if inline and flag in {"--from", "--with"}:
                requirements.append(inline)
                from_flag_seen = True
            elif not inline and flag in _FLAGS_TAKING_A_VALUE:
                expect_value_for = flag
            continue
        positionals.append(bare)

    if is_uvx:
        # Without --from, `uvx NAME` resolves NAME from the index and runs it.
        if not from_flag_seen and positionals:
            requirements.append(positionals[0])
    else:
        requirements.extend(positionals)
    return requirements


def _distribution_name(requirement: str) -> str:
    """`aieng-format[mcp]==1.0` / `aieng-format @ git+…` -> `aieng-format`."""
    head = re.split(r"[@\s]", requirement, maxsplit=1)[0]
    return re.split(r"[\[=<>!~;]", head, maxsplit=1)[0].strip().lower()


@pytest.mark.parametrize("doc_path", _existing_alpha_files(), ids=lambda p: p.name)
def test_no_surface_advertises_an_unpublished_distribution(doc_path: Path) -> None:
    """An install command must point at a channel that actually has the package."""
    offenders: list[str] = []
    for command in _install_commands(doc_path.read_text(encoding="utf-8")):
        for requirement in _requirements(command):
            if _distribution_name(requirement) not in _UNPUBLISHED_DISTRIBUTIONS:
                continue
            # A source-pinned requirement names the distribution but resolves
            # from Git, which is this project's actual channel. Judged per
            # requirement, not per command.
            if "git+" in requirement:
                continue
            offenders.append(f"{requirement}  (in: {command})")

    rel = doc_path.relative_to(_REPO_ROOT).as_posix()
    assert not offenders, (
        f"{rel} tells the reader to install an unpublished distribution from a "
        f"public index: {offenders}. These names are unregistered, so the command "
        "resolves to a stranger's upload. Use the git+ / tag form, or remove a "
        "name from _UNPUBLISHED_DISTRIBUTIONS once it is genuinely published."
    )


def test_the_guard_recognises_every_spelling_of_an_index_install() -> None:
    """This guard's own coverage boundary, checked.

    Its first version knew one installer spelling and applied the `git+`
    exemption to the whole command. Both gaps are cheap to state as fixtures,
    and stating them is the difference between a guard and the appearance of
    one.
    """
    caught = (
        "pip install aieng-format",
        "python -m pip install --pre aieng-format==0.1.0a3",
        "python3.11 -m pip install aieng-format[mcp]",
        r".venv\Scripts\python -m pip install aieng-workbench-mcp",
        'uv pip install "aieng-format"',
        "uvx aieng-workbench-mcp --approval-mode client",
        # One requirement pinned, one not: the unpinned one is still an offence.
        'pip install "aieng-format @ git+https://example.invalid/x" aieng-workbench-mcp',
    )
    for command in caught:
        doc = f"```bash\n{command}\n```\n"
        found = [
            r for c in _install_commands(doc) for r in _requirements(c)
            if _distribution_name(r) in _UNPUBLISHED_DISTRIBUTIONS and "git+" not in r
        ]
        assert found, f"guard missed an index install: {command}"

    allowed = (
        'pip install "aieng-format @ git+https://example.invalid/x#subdirectory=aieng"',
        "pip install -e ./aieng",
        "pip install build pytest",
        "uvx --from \"aieng-workbench-mcp[full] @ git+https://example.invalid/x\" \\\n"
        "  aieng-workbench-mcp --data-dir ~/.aieng",
    )
    for command in allowed:
        doc = f"```bash\n{command}\n```\n"
        found = [
            r for c in _install_commands(doc) for r in _requirements(c)
            if _distribution_name(r) in _UNPUBLISHED_DISTRIBUTIONS and "git+" not in r
        ]
        assert not found, f"guard fired on a correct command: {command} -> {found}"


def test_the_release_gate_records_the_distribution_decision() -> None:
    """The docs above are only honest while the gate doc says why (#273/#152).

    Asserting the channel ROWS, not loose phrases: a stray "not planned"
    elsewhere in the document would otherwise keep this green while the table
    that carries the actual claim had been edited away.
    """
    gate = (
        _REPO_ROOT / "aieng" / "docs" / "release" / "current_alpha_release_gate.md"
    ).read_text(encoding="utf-8")

    def _key(cell: str) -> str:
        return re.sub(r"[`*]", "", cell).strip().lower()

    rows = {
        _key(line.split("|")[1]): line.split("|")[2].strip().lower()
        for line in gate.splitlines()
        if line.startswith("|") and line.count("|") >= 3
    }
    for channel in ("pypi aieng-format", "pypi aieng-workbench-mcp"):
        state = rows.get(channel, "")
        assert "not planned" in state, (
            f"the gate doc's channel table must record {channel} as not planned; "
            f"found: {state!r}"
        )
    # Named exactly, not matched loosely: the same document has a metrics table
    # whose "GHCR pulls" row is legitimately `unknown`, and a substring match
    # folded that in as a channel claim.
    for channel in ("ghcr ghcr.io/armpro24-blip/aieng-workbench",
                    "git tag / github release"):
        state = rows.get(channel, "")
        assert "published" in state, (
            f"the table must record {channel} as published, or 'not planned' on "
            f"the PyPI rows reads as an outstanding gap rather than a decision; "
            f"found: {state!r}"
        )
