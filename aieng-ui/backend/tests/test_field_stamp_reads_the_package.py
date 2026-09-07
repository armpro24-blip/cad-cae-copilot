"""A field served off a bad mesh must not outrank the summary of the same package.

`GET /api/projects/{id}/fields/{name}` stamps an FRD-backed field
`executed_solver_result` (rank 4). It passed no mesh and no geometry evidence to
the classifier, so the invariants that guard `results/cae_result_summary.json`
did not reach this surface: for one package, the summary said `unreliable_mesh`
while the field descriptor said "Executed-solver result". AGENTS.md states the
rule this violated — "every artifact carrying a `credibility` stamp derives its
flags from `cae_result_summary.read_solver_evidence(zf)`".

`solver_executed=True` stays as it is here: this branch is only reached with an
FRD parsed out of the package, which is solver output read rather than asserted.
What it cannot answer — whether that mesh could resolve the result, and whether
the geometry has moved on since — now comes from the package.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from app.config import Settings
from app.routers.evidence import _field_credibility

_AIENG_ROOT = Settings.from_env().aieng_root


def _package(path: Path, *, accuracy: dict[str, Any], revision: int | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        zf.writestr(
            "simulation/mesh/mesh_metadata.json",
            json.dumps({"schema_version": "0.1", "accuracy": accuracy}),
        )
        zf.writestr(
            "simulation/runs/run_001/solver_run.json",
            json.dumps({"run_id": "run_001", "status": "completed", "solved": True}),
        )
        zf.writestr(
            "simulation/runs/run_001/deck_provenance.json",
            json.dumps({"run_id": "run_001", "geometry_revision": 0}),
        )
        if revision is not None:
            zf.writestr(
                "state/revalidation_status.json",
                json.dumps({"current_geometry_revision": revision}),
            )
    return path


def test_a_reliable_mesh_still_earns_the_solver_tier(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "good.aieng", accuracy={"band": "reliable"})

    stamp = _field_credibility("frd", _AIENG_ROOT, package_path=pkg)

    assert stamp["tier"] == "executed_solver_result"
    assert stamp["signals"]["mesh_accuracy_band"] == "reliable"


def test_an_unreliable_mesh_downgrades_the_field_stamp(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "coarse.aieng", accuracy={"band": "unreliable"})

    stamp = _field_credibility("frd", _AIENG_ROOT, package_path=pkg)

    assert stamp["tier"] == "unverified"
    assert "unreliable" in stamp["downgrade_reason"]


def test_a_field_from_a_superseded_run_is_downgraded(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "stale.aieng", accuracy={"band": "reliable"}, revision=1)

    stamp = _field_credibility("frd", _AIENG_ROOT, package_path=pkg)

    assert stamp["tier"] == "unverified"
    assert "geometry revision" in stamp["downgrade_reason"]


def test_an_unjudged_mesh_qualifies_the_field_without_downgrading_it(
    tmp_path: Path,
) -> None:
    pkg = _package(
        tmp_path / "hollow.aieng",
        accuracy={"band": None, "measured_on": "not_determined"},
    )

    stamp = _field_credibility("frd", _AIENG_ROOT, package_path=pkg)

    assert stamp["tier"] == "executed_solver_result"
    assert stamp["qualifications"], stamp


def test_no_package_leaves_every_signal_unrecorded(tmp_path: Path) -> None:
    """The previous behaviour, and the honest one when there is nothing to read."""
    stamp = _field_credibility("frd", _AIENG_ROOT, package_path=None)

    assert stamp["tier"] == "executed_solver_result"
    assert "mesh_accuracy_band" not in stamp["signals"]
    assert "geometry_stale" not in stamp["signals"]

    missing = _field_credibility(
        "frd", _AIENG_ROOT, package_path=tmp_path / "does_not_exist.aieng"
    )
    assert missing["tier"] == "executed_solver_result"


def test_the_other_two_sources_are_unchanged() -> None:
    assert _field_credibility("vtu", _AIENG_ROOT)["tier"] == "unverified"
    assert _field_credibility("synthetic", _AIENG_ROOT)["tier"] == "unverified"
