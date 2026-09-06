"""A workflow path filter that matches nothing is silently inert.

Found while checking why the `Real CCX Verification` lane did not run on a PR
that changed `cae_setup_view.py`. It also carried `aieng/schemas/**` — a
directory deleted in #523 — as the trigger for the package-conformance ratchet,
which only runs in that lane because it is the only one with the CAD + mesh
stack installed. So after #523, editing a schema no longer ran the ratchet.

Nothing failed. GitHub does not warn about a glob that matches no file; the lane
just quietly stops firing for that reason. And it looked fine in review: the
conformance PR (#524) *did* run the lane — because it happened to also touch
`aieng/src/aieng/simulation/**`. A gate that fires for an unrelated reason is
the `safety-by-accident` pattern from the review lens.

This is a spelling check, not a completeness check: it cannot know a path that
SHOULD be listed and is not. It only catches an entry that has gone dead, which
is the failure that actually happened.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / ".github" / "workflows"

#: A quoted list entry in a workflow, which is how every path filter here is
#: written. Unquoted entries (job names, `branches:` values) are not matched.
_LIST_ENTRY = re.compile(r'^\s*-\s+"([^"]+)"\s*$', re.M)

#: Quoted list entries that are not paths. Kept explicit rather than guessed at
#: by shape, so a genuinely dead path cannot hide behind a heuristic.
_NOT_PATHS = frozenset({"main"})


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return result.stdout.split()


def _as_regex(glob: str) -> re.Pattern[str]:
    """A GitHub path filter as a regex.

    Not `fnmatch`: there, `*` crosses `/`, so `aieng/*.py` would "match"
    `aieng/nested/file.py` and a filter that GitHub considers dead would be
    judged live — a guard reading too leniently is the one failure mode a guard
    cannot afford. Here `**` crosses separators and `*` does not.
    """
    out: list[str] = []
    i = 0
    while i < len(glob):
        char = glob[i]
        if char == "*":
            if glob[i:i + 3] == "**/":
                out.append("(?:.*/)?")   # zero or more leading segments
                i += 3
                continue
            if glob[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches(glob: str, paths: list[str]) -> bool:
    """Whether a GitHub path filter matches any tracked file."""
    if glob.endswith("/**"):
        prefix = glob[:-3] + "/"
        return any(p.startswith(prefix) for p in paths)
    pattern = _as_regex(glob)
    return any(pattern.match(p) for p in paths)


def test_every_workflow_path_filter_still_matches_something() -> None:
    tracked = _tracked_files()
    dead: list[str] = []
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for glob in sorted(set(_LIST_ENTRY.findall(workflow.read_text(encoding="utf-8")))):
            if glob in _NOT_PATHS or "/" not in glob and "." not in glob:
                continue
            if not _matches(glob, tracked):
                dead.append(f"{workflow.name}: {glob}")

    assert dead == [], (
        "these workflow path filters match no tracked file, so the jobs they "
        f"gate no longer run for that reason: {dead}. A moved or deleted path "
        "must be updated here too — GitHub does not warn, the trigger just "
        "stops firing."
    )


def test_the_check_can_actually_fail() -> None:
    """A guard whose matcher says yes to everything would pass forever."""
    tracked = _tracked_files()
    assert not _matches("aieng/schemas/**", tracked), (
        "the tree deleted in #523 is back, or the matcher is broken"
    )
    assert _matches("aieng/src/aieng/schemas/**", tracked), "the live tree must match"
    assert _matches("**/pyproject.toml", tracked)
    assert not _matches("aieng/does_not_exist/**", tracked)
    # `**/x` must also match `x` at the root — asserted against a synthetic list,
    # because this repo happens to have no root-level pyproject.toml and the
    # first version of this line therefore checked something else entirely.
    assert _matches("**/pyproject.toml", ["pyproject.toml"])
    # A single `*` must not cross a separator, or a filter GitHub considers
    # dead would read as live here.
    assert _matches("aieng/*.toml", ["aieng/pyproject.toml"])
    assert not _matches("aieng/*.py", ["aieng/nested/file.py"])
    assert _matches("aieng/**/*.py", ["aieng/nested/file.py"])


def test_the_ccx_gate_runs_every_target_it_registers() -> None:
    """"all" must mean all, or a registered suite silently never runs.

    `selected_targets("all")` named two targets literally while `TARGETS` held
    three, and CI invokes exactly that default — so the acceptance suite was
    added, the lane reported green, and the acceptance run had not executed.
    The lane passing is not evidence that what it gates ran.
    """
    import importlib.util
    import sys

    script = _REPO / "scripts" / "run_real_ccx_verification_gate.py"
    spec = importlib.util.spec_from_file_location("_ccx_gate", script)
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` resolves the defining module through
    # `sys.modules`, and fails on a module that is not there yet.
    sys.modules["_ccx_gate"] = gate
    try:
        spec.loader.exec_module(gate)
    finally:
        sys.modules.pop("_ccx_gate", None)

    selected = {target.label for target in gate.selected_targets("all")}
    registered = {target.label for target in gate.TARGETS.values()}
    assert selected == registered, (
        f"targets registered but not run by --suite all: {registered - selected}"
    )

    for name in gate.TARGETS:
        assert gate.selected_targets(name), f"--suite {name} selects nothing"
