"""There is one schema tree, and everything must read it (#517).

The repository carried two: `aieng/schemas/` at the root and
`aieng/src/aieng/schemas/` inside the package. Only the second was ever loaded —
`validate.py` reads it through `importlib.resources.files("aieng.schemas")`, and
it is what the wheel ships. The first had been dead since #297 and had diverged
in **14 files**.

It cost real time before it was found: an edit to `cae_mapping.schema.json` at
the root changed nothing, because the validator was reading the other copy. And
three tests validated against the dead one, so they were green about files the
product does not use. AGENTS.md pointed readers at it too — including at
`assembly_ir.schema.json`, which only ever existed in the packaged tree, so that
citation was already broken.

This is the `stale-artifact` pattern with an extra edge: not merely an old file,
but an old file that LOOKS canonical (repo root, short path) while the real one
is buried under `src/`.
"""

from __future__ import annotations

import re
from pathlib import Path

_AIENG = Path(__file__).resolve().parents[1]
_REPO = _AIENG.parent
_PACKAGED = _AIENG / "src" / "aieng" / "schemas"

#: `… / "schemas"` where the segment before it is NOT `"aieng"` — i.e. a schemas
#: directory that is not the packaged one. Matched against source with all
#: whitespace collapsed, so a path split across lines reads the same as one on a
#: single line: the first version of this check was line-based and flagged the
#: correctly-repointed multi-line paths, its own source, and a `tmp_path /
#: "schemas"` fixture. A rule that fires on correct input is noise.
_FOREIGN_SCHEMA_DIR = re.compile(r'(?<!"aieng" )/ "schemas"')
#: Variables that name a throwaway directory; a `schemas` folder under one of
#: those is a fixture, not a second tree.
_TEMP_PREFIX = re.compile(r'(tmp_path|tmp_dir|tmpdir|temp_dir)\s*/\s*"schemas"')

_SEARCHED_TREES = (_AIENG / "src", _AIENG / "tests", _REPO / "aieng-ui" / "backend" / "app",
                   _REPO / "scripts")


def test_the_duplicate_tree_is_gone() -> None:
    assert not (_AIENG / "schemas").exists(), (
        "the repo-root schema tree is back. It is not the one the library loads "
        "or the wheel ships, so anything edited there silently does nothing."
    )
    assert _PACKAGED.is_dir()
    assert list(_PACKAGED.glob("*.schema.json")), "the packaged tree must hold the schemas"


#: `Path(__file__)…parent[.parent] / "schemas"` — correct FROM INSIDE the package.
_IN_PACKAGE_RESOLUTION = re.compile(r'Path\(__file__\)[^/]*?\.parent(?:\.parent)? / "schemas"')


def _inside_package(path: Path) -> bool:
    return _PACKAGED.parent in path.resolve().parents or path.resolve().parent == _PACKAGED.parent


def _collapsed(path: Path) -> str:
    """Source with runs of whitespace collapsed, so a wrapped path reads as one."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_no_module_resolves_a_schema_directory_outside_the_package() -> None:
    """A path landing anywhere but `aieng/src/aieng/schemas` is a second tree."""
    offenders: list[str] = []
    for tree in _SEARCHED_TREES:
        if not tree.is_dir():
            continue
        for path in tree.glob("**/*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue  # this file quotes the patterns it forbids
            source = _TEMP_PREFIX.sub("<fixture>", _collapsed(path))
            if _inside_package(path):
                # A module in aieng/src/aieng resolving `__file__`-relative to
                # its OWN `schemas` directory lands on the packaged tree — that
                # is how `validate.py` finds its fallback. Correct, and the
                # regex cannot see it from the text alone.
                source = _IN_PACKAGE_RESOLUTION.sub("<packaged>", source)
            if _FOREIGN_SCHEMA_DIR.search(source):
                offenders.append(str(path.relative_to(_REPO)))

    assert offenders == [], (
        "these resolve a schema directory that is not the packaged one: "
        f"{offenders}. Read schemas through `aieng.schemas` "
        "(importlib.resources) or `aieng/src/aieng/schemas`; a second tree "
        "diverges and nothing notices."
    )


def test_no_document_points_readers_at_the_removed_tree() -> None:
    """Docs here are a contract an agent acts on."""
    stale = re.compile(r"(?<!src/)aieng/schemas/")
    offenders: list[str] = []
    for doc in list(_REPO.glob("*.md")) + list((_REPO / "docs").glob("**/*.md")) + \
            list((_AIENG / "docs").glob("**/*.md")):
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if stale.search(line):
                offenders.append(f"{doc.relative_to(_REPO)}:{number}")
    assert offenders == [], (
        f"documents still cite the removed repo-root tree: {offenders}"
    )
