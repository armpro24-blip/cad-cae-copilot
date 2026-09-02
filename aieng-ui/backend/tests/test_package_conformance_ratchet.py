"""A package the workbench builds must conform to the format it claims (#513).

Measured: every one of eight real agent-built packages failed `aieng.validate`,
including the canonical value-demo package — 13 to 76 failures each. Nothing
noticed because the validator's tests use hand-built fixtures that match the
schemas, so nobody ever validated a package the CAD path actually produced.

The decisive measurement: a package built through the format library's **own**
documented path (`aieng.cli.import_step_package`) validates cleanly — `ok=True`,
0 failures of 57 checks. The schemas are not stale. The workbench was writing
packages without using the library's writers, starting with a manifest that was
literally `{"schema_version": "0.1"}` where the format requires `model_id`,
`format_version`, `units`, `resources` and `created_by` — so the library's own
AI summary writer reported every agent-built model as `unknown_model`.

This is a RATCHET, not a pass/fail conformance test. The remaining drift is real
and is being paid down per writer; recording it per member means:

* a NEW disagreement fails immediately, and
* fixing a writer is a one-line edit here (lower the number), so progress is
  visible instead of hidden behind one red assertion nobody can act on.

Lower a number when you fix a writer. Never raise one to make the suite green —
raising it is the regression this file exists to catch.

Where this runs: the ordinary CI lanes install no CAD/mesh stack, so the
module-level `importorskip` skips it there. It is wired into the
`Real CCX Verification` workflow instead — the one lane that installs
`[dev,cad]` + gmsh — triggered by changes to `cad_generation.py`, the schemas,
or this file. A guard that never runs in CI is the defect it exists to catch.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("build123d", reason="package conformance needs the real CAD stack")

from aieng.validate import validate_package  # noqa: E402

# Per-member ceiling of schema/rule failures for a freshly built package.
#
# `simulation/cae_mapping.json` and the `parsed_*` artifacts dominate for one
# reason worth writing down: those schemas were designed for the CAE *import*
# direction and require its provenance (`parser`, `source_file`,
# `mapping_status`, `mapping_method`, `confidence`). The workbench now AUTHORS
# setups natively (`cae.setup_static`), and the authoring writer omits all of it
# rather than filling it in honestly. Conforming there is not box-ticking: an
# authored mapping that records that it was authored, by which tool, and how
# confident the binding is, is strictly more informative than one that does not.
_BASELINE: dict[str, int] = {
    "simulation/cae_mapping.json": 18,
    "simulation/cae_imports/parsed_loads.json": 7,
    "simulation/cae_imports/parsed_materials.json": 5,
    "simulation/cae_imports/parsed_boundary_conditions.json": 5,
    "manifest.json": 1,
    "geometry/topology_map.json": 1,
    "graph/feature_graph.json": 1,
    "CAE": 2,
}

_MEMBER = re.compile(r"[\w./-]+\.(?:json|yaml)")

_CODE = (
    "from build123d import *\n"
    "PLATE_LENGTH = 60.0\n"
    "base = Box(PLATE_LENGTH, 40, 8)\n"
    "base.label = 'base_plate'\n"
    "result = Compound(children=[base])\n"
)


def _failures_by_member(package: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for message in validate_package(package).messages or []:
        if "FAIL" not in str(message.level):
            continue
        text = str(message.text)
        found = _MEMBER.search(text)
        counts[found.group(0) if found else text.split(" ", 1)[0]] += 1
    return counts


_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _settings(tmp_path: Path):
    """Explicit settings, then `create_app` — the pattern the other backend tests use.

    Not `AIENG_PLATFORM_DATA`: the tool handlers close over the settings they
    were REGISTERED with, so setting the env var only works when this module
    happens to trigger the import that builds the app. Run alongside any test
    that already built one and every `invoke_tool` here resolves against that
    test's data root instead — which is how this file passed alone and errored
    in the full suite.
    """
    from app.config import Settings

    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=_WORKSPACE_ROOT / "aieng",
        sample_step=workspace / "sample.step",
    )


@pytest.fixture(scope="module")
def built_package(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A package produced by the real CAD + CAE authoring path."""
    from app import cad_generation, runtime
    from app.app_factory import create_app
    from app.main import default_project, save_project

    settings = _settings(tmp_path_factory.mktemp("conformance"))
    create_app(settings)  # rebinds the runtime tool registry to these settings
    project_id = save_project(settings, default_project("conformance"))["id"]

    built = cad_generation.execute_build123d_code(
        settings, project_id, {"code": _CODE, "timeout": 180}
    )
    assert built.get("status") == "ok", built
    runtime.invoke_tool("cae.setup_static", {
        "project_id": project_id,
        "material": "Al6061-T6",
        "fix": "bottom",
        "load": {"at": "top", "force_n": 500, "direction": "-Z"},
    })
    # Best effort: the mesh + deck stages need the optional mesh stack, and they
    # are what write `simulation/cae_mapping.json`. Where they run, the ratchet
    # covers that member too; where they cannot, the presence check below leaves
    # it out instead of reading "fixed".
    for tool, payload in (
        ("cae.generate_mesh", {"project_id": project_id, "mesh_size_mm": 8}),
        ("cae.generate_solver_input", {"project_id": project_id}),
    ):
        try:
            runtime.invoke_tool(tool, payload)
        except Exception:  # noqa: BLE001 - optional stage, absence is not failure
            break

    from app.project_io import project_dir

    package = project_dir(settings, project_id) / f"{project_id}.aieng"
    assert package.exists()
    return package


def test_no_member_drifts_further_from_its_schema(built_package: Path) -> None:
    counts = _failures_by_member(built_package)
    with zipfile.ZipFile(built_package) as zf:
        present = set(zf.namelist())

    regressions = {
        member: (count, _BASELINE.get(member, 0))
        for member, count in counts.items()
        if count > _BASELINE.get(member, 0)
    }
    assert not regressions, (
        "these members now fail their schema MORE than the recorded baseline "
        f"(member: now vs baseline): {regressions}. A new required field or an "
        "undeclared property was added to a writer without updating its schema "
        "— fix the writer, or declare the field in the schema (an additive, "
        "non-breaking change per the version-surface compat policy)."
    )

    # Only members this package actually contains: an artifact that a stage did
    # not write is absent, not fixed, and reading 0 for it would report a
    # phantom improvement — the by-construction trap of judging a thing by a
    # quantity that is degenerate when the thing is missing.
    improved = {
        member: (counts.get(member, 0), ceiling)
        for member, ceiling in _BASELINE.items()
        if member in present and counts.get(member, 0) < ceiling
    }
    assert not improved, (
        "a writer got closer to its schema — lower the baseline in this file to "
        f"lock the improvement in (member: now vs baseline): {improved}"
    )


def test_the_manifest_is_built_by_the_format_library(built_package: Path) -> None:
    """The single-line root cause: a hand-rolled two-key manifest.

    `{"schema_version": "0.1"}` failed the schema twice over — four missing
    required fields, plus `schema_version` not being a declared property at all
    — and left `model_id` absent, which is what the library's AI summary writer
    reads. This asserts the identity fields are really there, not merely that
    the failure count went down.
    """
    with zipfile.ZipFile(built_package) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["model_id"], "the AI summary writer reports 'unknown_model' without it"
    assert manifest["format_version"], manifest
    assert manifest["units"], manifest
    assert manifest["created_by"]["tool"].startswith("aieng "), manifest["created_by"]
    assert "schema_version" not in manifest, (
        "not a declared property; it was itself an additionalProperties violation"
    )


def test_the_library_path_still_conforms_completely(tmp_path: Path) -> None:
    """The reference point that proves the schemas are not the problem.

    If this ever fails, the premise of the ratchet is gone: it would mean the
    schemas have genuinely drifted from the format's own writers, and the
    baseline above would be measuring the wrong side of the disagreement.
    """
    from aieng.cli import import_step_package

    source = Path(__file__).resolve().parents[3] / "aieng" / "examples" / "bracket.step"
    if not source.exists():
        pytest.skip("example STEP not present")

    package = tmp_path / "bracket.aieng"
    import_step_package(source, package)

    failures = _failures_by_member(package)
    assert not failures, (
        f"the format library's own package no longer validates: {dict(failures)}"
    )


def test_the_ratchet_actually_covers_the_members_it_claims(built_package: Path) -> None:
    """A baseline for a member the fixture never writes governs nothing.

    The mesh and deck stages are best-effort, so an environment without the mesh
    stack legitimately covers less. What must not happen is coverage quietly
    shrinking where the stack IS present — the dominant member,
    `simulation/cae_mapping.json`, is written by the deck stage, and a fixture
    that stopped reaching it would leave 18 recorded failures unwatched while
    the suite stayed green.
    """
    with zipfile.ZipFile(built_package) as zf:
        present = set(zf.namelist())

    assert "manifest.json" in present
    assert "graph/feature_graph.json" in present, "the CAD stage must have run"

    meshed = "simulation/mesh/mesh_metadata.json" in present
    if not meshed:
        pytest.skip("optional mesh stack absent; CAE deck members not covered here")

    for member in (
        "simulation/cae_mapping.json",
        "simulation/cae_imports/parsed_loads.json",
        "simulation/cae_imports/parsed_materials.json",
        "simulation/cae_imports/parsed_boundary_conditions.json",
    ):
        assert member in present, (
            f"{member} has a recorded baseline but the fixture no longer writes "
            "it — the ratchet would be watching nothing"
        )
