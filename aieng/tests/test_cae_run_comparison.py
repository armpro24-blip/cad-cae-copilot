"""The before/after comparison the promised task ends with.

Its whole reason to exist is that `results/computed_metrics.json` is one fixed
path: the re-solve's extraction replaces the baseline's numbers, so "the
comparison" was recoverable only by re-extracting an earlier run's FRD by hand.
These tests pin the two things that make the comparison worth trusting — each
side's numbers come from **that side's own FRD**, and the geometry revision the
two decks were built for is reported beside them.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from aieng.cae_run_comparison import compare_runs, list_runs


def _frd_value(v: float) -> str:
    return f"{v:12.5E}"


def _node_line(node_id: int, values: list[float]) -> str:
    return "    -1" + f"{node_id:12d}" + "".join(_frd_value(v) for v in values)


def _make_frd(disp: dict[int, list[float]], stress: dict[int, list[float]]) -> str:
    lines = [
        "    1C                                                                         1",
        "    1UCUT.......................                                                2",
        "    -4  DISP        4    1",
        "    -5  D1          1    2    1    0",
        "    -5  D2          1    2    2    0",
        "    -5  D3          1    2    3    0",
        "    -5  ALL         1    2    0    1",
    ]
    lines += [_node_line(nid, vals) for nid, vals in disp.items()]
    lines.append("    -3")
    lines += [
        "    -4  S           6    1",
        "    -5  SXX         1    4    1    1",
        "    -5  SYY         1    4    2    1",
        "    -5  SZZ         1    4    3    1",
        "    -5  SXY         1    4    4    1",
        "    -5  SXZ         1    4    5    1",
        "    -5  SYZ         1    4    6    1",
    ]
    lines += [_node_line(nid, vals) for nid, vals in stress.items()]
    lines += ["    -3", " 9999"]
    return "\n".join(lines) + "\n"


def _run_members(
    zf: zipfile.ZipFile,
    run_id: str,
    *,
    displacement: float | None,
    stress: float,
    geometry_revision: int | None,
    completed: bool = True,
    bindings: dict[str, list[str]] | None = None,
) -> None:
    if displacement is not None:
        frd = _make_frd(
            {1: [displacement, 0.0, 0.0, displacement]},
            {1: [stress, 0.0, 0.0, 0.0, 0.0, 0.0]},
        )
        zf.writestr(f"simulation/runs/{run_id}/outputs/result.frd", frd)
    zf.writestr(
        f"simulation/runs/{run_id}/solver_run.json",
        json.dumps(
            {
                "run_id": run_id,
                "solver": "CalculiX",
                "state": "completed" if completed else "failed",
                "solved": completed,
                "analysis_type": "static",
                "finished_at": f"2026-09-06T10:0{run_id[-1]}:00Z",
            }
        ),
    )
    if geometry_revision is not None:
        provenance = {"run_id": run_id, "geometry_revision": geometry_revision}
        if bindings is not None:
            provenance["setup_bindings"] = bindings
        zf.writestr(
            f"simulation/runs/{run_id}/deck_provenance.json", json.dumps(provenance)
        )


def _bindings_package(
    path: Path, *, before: dict | None, after: dict | None
) -> Path:
    """Two solved runs whose decks recorded the given setup bindings."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        _run_members(zf, "run_001", displacement=2.4, stress=140.0,
                     geometry_revision=0, bindings=before)
        _run_members(zf, "run_002", displacement=0.3, stress=34.0,
                     geometry_revision=1, bindings=after)
    return path


def _package(
    path: Path,
    *,
    baseline_disp: float | None = 2.4,
    baseline_stress: float = 140.0,
    current_disp: float | None = 0.3,
    current_stress: float = 34.0,
    baseline_revision: int | None = 0,
    current_revision: int | None = 1,
    current_completed: bool = True,
) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        _run_members(
            zf,
            "run_001",
            displacement=baseline_disp,
            stress=baseline_stress,
            geometry_revision=baseline_revision,
        )
        _run_members(
            zf,
            "run_002",
            displacement=current_disp,
            stress=current_stress,
            geometry_revision=current_revision,
            completed=current_completed,
        )
    return path


def _row(result: dict, metric: str) -> dict:
    return next(row for row in result["comparison"] if row["metric"] == metric)


def test_each_side_comes_from_its_own_run(tmp_path: Path) -> None:
    """The point of the module: not one shared computed_metrics.json.

    The package deliberately carries a `results/computed_metrics.json` holding
    the CURRENT run's numbers — which is what a real package looks like after
    the re-solve. If the comparison read that file, the baseline column would
    equal the current one and every change would read as 0%.
    """
    pkg = _package(tmp_path / "beam.aieng")
    with zipfile.ZipFile(pkg, "a") as zf:
        zf.writestr(
            "results/computed_metrics.json",
            json.dumps(
                {
                    "metrics_source": {"run_id": "run_002"},
                    "load_cases": [
                        {
                            "id": "load_case_001",
                            "metrics": {
                                "max_displacement": {"value": 0.3, "unit": "mm"},
                                "max_von_mises_stress": {"value": 34.0, "unit": "MPa"},
                            },
                        }
                    ],
                }
            ),
        )

    result = compare_runs(pkg)

    assert result["status"] == "ok"
    disp = _row(result, "max_displacement")
    assert disp["before"] == pytest.approx(2.4)
    assert disp["after"] == pytest.approx(0.3)
    assert disp["percent_change"] == pytest.approx(-87.5)
    assert disp["unit"] == "mm"
    assert result["baseline"]["result_file"] == "simulation/runs/run_001/outputs/result.frd"
    assert result["current"]["result_file"] == "simulation/runs/run_002/outputs/result.frd"


def test_the_geometry_revision_of_each_deck_is_reported(tmp_path: Path) -> None:
    result = compare_runs(_package(tmp_path / "beam.aieng"))

    assert result["baseline"]["geometry_revision"] == 0
    assert result["current"]["geometry_revision"] == 1
    assert result["geometry_changed"] is True


def test_two_solves_of_the_same_geometry_say_so(tmp_path: Path) -> None:
    """The exact failure P0-2 measured, reported instead of left to be noticed.

    Re-running the pre-edit deck gave 0.0% change on a beam whose thickness had
    doubled, with `status: completed` and nothing flagged. A comparison of two
    decks built for the same revision is a legitimate thing to ask for, but the
    reader must not mistake its near-zero delta for evidence about an edit.
    """
    pkg = _package(
        tmp_path / "same.aieng",
        current_disp=2.4,
        current_stress=140.0,
        current_revision=0,
    )
    result = compare_runs(pkg)

    assert result["geometry_changed"] is False
    assert any("SAME geometry" in w for w in result["warnings"]), result["warnings"]
    assert _row(result, "max_displacement")["percent_change"] == pytest.approx(0.0)


def test_a_deck_with_no_recorded_revision_is_unknown_not_zero(tmp_path: Path) -> None:
    """`None` is "nothing recorded"; 0 is "recorded, and it is the first revision".

    A deck written before `deck_provenance.json` existed cannot say what geometry
    it was built for. Calling that revision 0 would invent the one fact the
    comparison is supposed to make traceable.
    """
    pkg = _package(tmp_path / "old.aieng", baseline_revision=None)
    result = compare_runs(pkg)

    assert result["baseline"]["geometry_revision"] is None
    assert result["geometry_changed"] is None
    assert any("cannot be established" in w for w in result["warnings"])


def test_a_named_run_without_a_result_is_refused_not_substituted(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "one.aieng", current_disp=None)

    result = compare_runs(pkg, current_run="run_002")

    assert result["status"] == "error"
    assert result["code"] == "run_has_no_result"
    assert "run_002" in result["message"]


def test_a_run_that_is_not_there_is_named(tmp_path: Path) -> None:
    result = compare_runs(_package(tmp_path / "beam.aieng"), baseline_run="run_009")

    assert result["code"] == "run_not_found"
    assert "run_001" in result["message"] and "run_002" in result["message"]


def test_one_run_cannot_be_compared(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "single.aieng", current_disp=None)

    result = compare_runs(pkg)

    assert result["code"] == "not_enough_runs"
    assert result["runs"] == ["run_001", "run_002"]


def test_a_run_compared_with_itself_is_refused(tmp_path: Path) -> None:
    result = compare_runs(
        _package(tmp_path / "beam.aieng"), baseline_run="run_001", current_run="run_001"
    )

    assert result["code"] == "same_run"


def test_an_incomplete_run_is_flagged_rather_than_dropped(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "failed.aieng", current_completed=False)

    result = compare_runs(pkg)

    assert result["status"] == "ok"
    assert result["current"]["solver_executed"] is False
    assert any("did not complete" in w for w in result["warnings"])


def test_a_zero_baseline_reports_a_delta_and_no_percentage(tmp_path: Path) -> None:
    """Percent change against zero is undefined, not infinite and not 0."""
    pkg = _package(tmp_path / "zero.aieng", baseline_disp=0.0, baseline_stress=0.0)

    disp = _row(compare_runs(pkg), "max_displacement")

    assert disp["status"] == "compared"
    assert disp["delta"] == pytest.approx(0.3)
    assert disp["percent_change"] is None
    assert "undefined" in disp["reason"]


def test_runs_are_ordered_numerically_not_lexically(tmp_path: Path) -> None:
    """`run_10` is after `run_2`, so a default baseline must not sort by text."""
    pkg = tmp_path / "many.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", "{}")
        for run_id, disp, rev in (("run_2", 2.0, 0), ("run_10", 0.5, 1)):
            _run_members(zf, run_id, displacement=disp, stress=100.0, geometry_revision=rev)

    assert [run["run_id"] for run in list_runs(pkg)] == ["run_2", "run_10"]
    result = compare_runs(pkg)
    assert result["baseline"]["run_id"] == "run_2"
    assert result["current"]["run_id"] == "run_10"


def test_selection_says_whether_it_was_asked_for(tmp_path: Path) -> None:
    result = compare_runs(_package(tmp_path / "beam.aieng"), current_run="run_002")

    assert result["baseline"]["selected"] == "default"
    assert result["current"]["selected"] == "explicit"


def test_a_missing_package_is_named(tmp_path: Path) -> None:
    result = compare_runs(tmp_path / "nope.aieng")

    assert result["code"] == "package_not_found"


# ── did the two runs bind the same number of faces? ─────────────────────────


def test_a_rebound_face_id_is_not_treated_as_a_different_setup(tmp_path: Path) -> None:
    """The regression guard for a wrong diagnosis I made and had to walk back.

    An edit that MOVES a bound face retires its id, so the deterministic
    re-resolution correctly assigns a new one. Comparing ids therefore fires on
    every legitimate rebind — the very shapes the rebind exists to support.
    Measured on a thin-wall housing: the load went from `face_011` (3996 mm² =
    74x54) to `face_013` (3264 mm² = 68x48), which is the SAME inner floor
    face resized exactly as a wall going 3 mm to 6 mm dictates. An id test
    called that a different setup and would have declared a correct comparison
    invalid, on the canonical bracket too.
    """
    pkg = _bindings_package(
        tmp_path / "rebound.aieng",
        before={"load_001": ["face_011"], "bc_001": ["face_005"]},
        after={"load_001": ["face_013"], "bc_001": ["face_005"]},
    )

    result = compare_runs(pkg)

    assert result["status"] == "ok"
    assert result["binding_count_changed"] is False
    assert result["warnings"] == [], result["warnings"]


def test_losing_bolt_holes_between_runs_is_flagged(tmp_path: Path) -> None:
    """No resize turns four faces into two. A different restraint is a different problem."""
    pkg = _bindings_package(
        tmp_path / "holes.aieng",
        before={"bc_001": ["face_007", "face_008", "face_009", "face_010"]},
        after={"bc_001": ["face_011", "face_012"]},
    )

    result = compare_runs(pkg)

    assert result["binding_count_changed"] is True
    blob = " ".join(result["warnings"])
    assert "4 face(s) in run_001 but 2 in run_002" in blob, blob
    assert "may compare different restraints" in blob


def test_a_binding_present_in_only_one_run_is_flagged(tmp_path: Path) -> None:
    pkg = _bindings_package(
        tmp_path / "dropped.aieng",
        before={"load_001": ["face_011"], "bc_001": ["face_005"]},
        after={"load_001": ["face_013"]},
    )

    result = compare_runs(pkg)

    assert result["binding_count_changed"] is True
    assert any("bc_001 is bound in run_001 only" in w for w in result["warnings"])


def test_a_deck_that_recorded_no_bindings_is_unknown_not_false(tmp_path: Path) -> None:
    """`None` = the package cannot say. A deck written before this field existed."""
    pkg = _bindings_package(
        tmp_path / "legacy.aieng",
        before=None,
        after={"load_001": ["face_013"]},
    )

    result = compare_runs(pkg)

    assert result["binding_count_changed"] is None
    assert any("cannot be established" in w for w in result["warnings"])


def test_each_side_reports_the_faces_its_run_bound(tmp_path: Path) -> None:
    """Traceability: the deliverable should say what each run actually held."""
    pkg = _bindings_package(
        tmp_path / "traceable.aieng",
        before={"load_001": ["face_011"]},
        after={"load_001": ["face_013"]},
    )

    result = compare_runs(pkg)

    assert result["baseline"]["setup_bindings"] == {"load_001": ["face_011"]}
    assert result["current"]["setup_bindings"] == {"load_001": ["face_013"]}


_CCX = Path(__file__).parent / "fixtures" / "frd_ccx_2_23"


@pytest.mark.parametrize(
    "analysis, metric, value",
    [("modal", "first_natural_frequency_hz", 1566.868114),
     ("buckling", "lowest_buckling_factor", 155.889809)],
)
def test_eigen_runs_compare_eigenvalues_not_mode_shape_amplitudes(
    tmp_path: Path, analysis: str, metric: str, value: float
) -> None:
    # Real CalculiX 2.23 output. Comparing two modal runs used to produce rows
    # of max_displacement "mm" / max_von_mises_stress "MPa" per mode — the
    # amplitude of a normalised eigenvector — and no frequency at all.
    pkg = tmp_path / f"{analysis}.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        for rev, run_id in enumerate(("run_001", "run_002")):
            zf.writestr(f"simulation/runs/{run_id}/outputs/result.frd",
                        (_CCX / f"{analysis}.frd").read_bytes())
            zf.writestr(f"simulation/runs/{run_id}/solver_run.json", json.dumps(
                {"run_id": run_id, "state": "completed", "solved": True,
                 "analysis_type": analysis}))
            zf.writestr(f"simulation/runs/{run_id}/deck_provenance.json",
                        json.dumps({"run_id": run_id, "geometry_revision": rev}))

    result = compare_runs(pkg)

    rows = {(r["load_case_id"], r["metric"]): r for r in result["comparison"]}
    case = f"{analysis}_001"
    assert rows[(case, metric)]["status"] == "compared"
    assert rows[(case, metric)]["before"] == pytest.approx(value, rel=1e-9)
    assert rows[(case, metric)]["percent_change"] == pytest.approx(0.0)
    mode_shape_rows = [k for k in rows if k[1] in ("max_displacement", "max_von_mises_stress")
                       and k[0] != "load_case_001"]
    assert mode_shape_rows == []


def test_a_higher_mode_change_is_reported(tmp_path: Path) -> None:
    # The eigenvalue LISTS used to be rows of "no extracted value" although both
    # runs had them, so only mode 1 was compared and a change in mode 3 vanished.
    base = (_CCX / "modal.frd").read_text()
    assert base.count("8070.768289") == 3  # mode 3 header, on DISP/STRESS/ERROR blocks
    edited = base.replace("8070.768289", "8100.000000")
    pkg = tmp_path / "modal.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "beam"}))
        for rev, (run_id, text) in enumerate((("run_001", base), ("run_002", edited))):
            zf.writestr(f"simulation/runs/{run_id}/outputs/result.frd", text)
            zf.writestr(f"simulation/runs/{run_id}/solver_run.json", json.dumps(
                {"run_id": run_id, "state": "completed", "solved": True,
                 "analysis_type": "modal"}))
            zf.writestr(f"simulation/runs/{run_id}/deck_provenance.json",
                        json.dumps({"run_id": run_id, "geometry_revision": rev}))

    rows = {r["metric"]: r for r in compare_runs(pkg)["comparison"]}

    mode3 = rows["natural_frequencies_hz[mode_3]"]
    assert mode3["status"] == "compared"
    assert mode3["percent_change"] == pytest.approx(
        (8100.0 - 8070.768289) / 8070.768289 * 100.0, rel=1e-6
    )
    assert rows["natural_frequencies_hz[mode_1]"]["percent_change"] == pytest.approx(0.0)
    assert "natural_frequencies_hz" not in rows  # no "no extracted value" row any more


def test_a_target_only_a_pre_upgrade_deck_lists_is_not_a_change(tmp_path: Path) -> None:
    """A deck recorded before `setup_nsets` listed every mapping entry, stale ones
    included; a later deck lists only what it applied. Comparing the two must not
    report the stale entry as a dropped restraint (independent review of #557)."""
    pkg = _bindings_package(
        tmp_path / "mixed.aieng",
        before={"load_001": ["face_011"], "bc_stale": ["face_099"]},
        after={"load_001": ["face_013"]},
    )
    with zipfile.ZipFile(pkg) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    member = "simulation/runs/run_002/deck_provenance.json"
    provenance = json.loads(members[member])
    provenance["setup_nsets"] = {"LOAD_001": ["load_001"]}
    members[member] = json.dumps(provenance).encode()
    with zipfile.ZipFile(pkg, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)

    result = compare_runs(pkg)

    assert result["binding_count_changed"] is False, result["warnings"]
    assert any("predates per-deck NSET records" in w for w in result["warnings"])
