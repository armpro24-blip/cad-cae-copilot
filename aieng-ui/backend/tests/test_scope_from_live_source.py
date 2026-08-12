"""A stored `local` scope must be re-checked against the live source (M3 dogfood).

The feature graph is an artifact: it is written once, by whatever binder existed
at the time, and is served as current forever after. Measured on a bracket built
before the constant→part fix of 2026-08-11 — `PLATE_THICKNESS` dimensions the
plate AND positions the rib, but the stored graph attached it to `rib_main` as a
`named_part`, so:

- `cad.list_editable_parameters` reported `scope: "local"` — documented as "the
  safe single-part edit";
- `cad.edit_parameter` resolves its scope-risk gate from that same graph, so
  editing "the rib's thickness" skipped `confirmScopeRisk` entirely and resized
  the plate.

`regression_diff` still reports `collateral_change` afterwards and the pre-edit
snapshot makes it recoverable — but the flag whose whole job is to warn BEFORE
was reading yesterday's answer. Constant→part binding is pure text analysis, so
the live answer is available at read time.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from app.agent_autopilot.parameter_binding import build_parameter_index, scopes_from_source
from app.cad_generation import _constant_is_shared_in_source

# PLATE_THICKNESS dimensions the plate and positions the rib; RIB_THICKNESS is
# the rib's alone. This is the shape the fixture in AGENTS.md recommends.
_SOURCE = """from build123d import *

PLATE_LENGTH = 120.0
PLATE_THICKNESS = 6.0
RIB_THICKNESS = 5.0

with BuildPart() as bp:
    Box(PLATE_LENGTH, 80.0, PLATE_THICKNESS, align=(Align.CENTER, Align.CENTER, Align.MIN))
base_plate = bp.part
base_plate.label = "base_plate"

rib_main = rib(40.0, 25.0, RIB_THICKNESS)
rib_main = rib_main.moved(Location((0, 0, PLATE_THICKNESS)))
rib_main.label = "rib_main"

result = Compound(children=[base_plate, rib_main])
"""

# The STALE graph: PLATE_THICKNESS attached to the rib as a plain named part.
_STALE_GRAPH = {
    "format_version": "0.1.0",
    "features": [
        {"id": "feat_body_001", "name": "base_plate", "type": "named_part",
         "parameters": {"length_mm": {"cad_parameter_name": "PLATE_LENGTH",
                                      "current_value": 120.0}}},
        {"id": "feat_body_002", "name": "rib_main", "type": "named_part",
         "parameters": {"thickness_mm": {"cad_parameter_name": "PLATE_THICKNESS",
                                         "current_value": 6.0},
                        "rib_thickness": {"cad_parameter_name": "RIB_THICKNESS",
                                          "current_value": 5.0}}},
    ],
}


def _by_constant(entries: list[dict]) -> dict[str, dict]:
    return {e["cad_parameter_name"]: e for e in entries}


def test_without_the_source_the_stale_scope_is_reported_as_is() -> None:
    """Baseline — this is what an agent saw, and why it is not enough."""
    entries = _by_constant(build_parameter_index(_STALE_GRAPH))
    assert entries["PLATE_THICKNESS"]["scope"] == "local"


def test_a_shared_constant_is_corrected_to_global_and_says_why() -> None:
    entries = _by_constant(build_parameter_index(_STALE_GRAPH, _SOURCE))
    shared = entries["PLATE_THICKNESS"]
    assert shared["scope"] == "global", "a constant touching two parts is not a local edit"
    assert shared["scope_source"] == "source_usage"
    assert "more than one named part" in shared["scope_note"]
    assert "rib_main" in shared["scope_note"], "name the feature it was filed under"


def test_genuinely_local_parameters_are_left_alone() -> None:
    """The correction must not turn every parameter into a scary one."""
    entries = _by_constant(build_parameter_index(_STALE_GRAPH, _SOURCE))
    for constant in ("PLATE_LENGTH", "RIB_THICKNESS"):
        assert entries[constant]["scope"] == "local", constant
        assert "scope_note" not in entries[constant]


def test_scope_is_only_ever_widened(tmp_path: Path) -> None:
    """A stored global/unscoped already demands confirmation — never relax it."""
    graph = {"features": [
        {"id": "feat_global_params", "name": "Global Parameters", "type": "global_params",
         "parameters": {"radius_mm": {"cad_parameter_name": "FILLET_RADIUS"}}},
        {"id": "feat_model_params", "name": "Model Parameters", "type": "model_params",
         "parameters": {"diameter_mm": {"cad_parameter_name": "HOLE_DIAMETER"}}},
    ]}
    entries = _by_constant(build_parameter_index(graph, _SOURCE))
    assert entries["FILLET_RADIUS"]["scope"] == "global"
    assert entries["HOLE_DIAMETER"]["scope"] == "unscoped"


def test_scopes_from_source_degrades_quietly() -> None:
    assert scopes_from_source("", {"X"}) == {}
    assert scopes_from_source(_SOURCE, set()) == {}
    assert scopes_from_source(None, {"PLATE_THICKNESS"}) == {}


# ── the edit gate ────────────────────────────────────────────────────────────

def _package(tmp_path: Path, source: str | None) -> Path:
    pkg = tmp_path / "p.aieng"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("metadata.json", "{}")
        if source is not None:
            zf.writestr("geometry/source.py", source)
    return pkg


def test_the_edit_gate_sees_the_shared_constant(tmp_path: Path) -> None:
    pkg = _package(tmp_path, _SOURCE)
    assert _constant_is_shared_in_source(pkg, "PLATE_THICKNESS") is True
    assert _constant_is_shared_in_source(pkg, "RIB_THICKNESS") is False
    assert _constant_is_shared_in_source(pkg, "PLATE_LENGTH") is False


def test_the_edit_gate_never_fails_an_edit_on_a_missing_source(tmp_path: Path) -> None:
    """Best-effort: without a readable source the gate behaves exactly as before."""
    assert _constant_is_shared_in_source(_package(tmp_path, None), "PLATE_THICKNESS") is False
    assert _constant_is_shared_in_source(tmp_path / "absent.aieng", "PLATE_THICKNESS") is False
    assert _constant_is_shared_in_source(_package(tmp_path, _SOURCE), "") is False
