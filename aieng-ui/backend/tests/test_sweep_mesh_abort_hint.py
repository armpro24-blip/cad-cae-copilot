"""A variant killed by its mesh must say so, not just "not solved".

Measured on the reference bracket (2026-08-11): sweeping plate thickness at
mesh_size 4 mm, values 4/5/6 solved and 8 failed with "CalculiX returned code
201 or produced no FRD". The same 8 mm variant solved cleanly at mesh_size 3 mm
(11.3746 MPa, 0.001099 mm) — identical geometry, BCs and load. Reporting only
`solver_executed: false` sends the engineer to inspect a design that is fine.
"""
from __future__ import annotations

from app.sizing_sweep_runner import _is_solver_abort


def test_ccx_abort_is_recognised() -> None:
    assert _is_solver_abort(
        {"solver_executed": False, "status": "error",
         "error": "CalculiX returned code 201 or produced no FRD"}
    )


def test_solver_failed_status_is_recognised() -> None:
    assert _is_solver_abort({"solver_executed": False, "status": "solver_failed", "error": ""})


def test_setup_gaps_are_not_blamed_on_the_mesh() -> None:
    """These have their own explanations; a mesh hint would be misleading."""
    assert not _is_solver_abort(
        {"solver_executed": False, "status": "no_setup",
         "error": "no CAE setup found — write simulation/setup.yaml, or author ..."}
    )
    assert not _is_solver_abort(
        {"solver_executed": False, "status": "tools_unavailable",
         "error": "required tools unavailable: ccx"}
    )
    assert not _is_solver_abort(
        {"solver_executed": False, "status": "stale_topology_references",
         "error": "CAE face references do not match current topology"}
    )


def test_successful_solve_is_not_an_abort() -> None:
    assert not _is_solver_abort({"solver_executed": True, "status": "ok", "error": None})
