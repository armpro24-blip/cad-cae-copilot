"""Deterministic /simulate readiness report — pure helper coverage (v1.5 + v2)."""

from app.agent_autopilot.mention_binding import build_mention_bindings
from app.agent_autopilot.simulation_readiness import (
    build_simulation_readiness_report,
    load_simulation_setup,
    REQUIRED_INPUTS,
)


def _reader(files: dict[str, str]):
    return lambda name: files.get(name)


def _statuses(report: dict) -> dict[str, str]:
    return {name: meta["status"] for name, meta in report["inputs"].items()}


def test_no_setup_is_not_found_with_required_missing() -> None:
    report = build_simulation_readiness_report(None)
    assert report["setup_source"] == "not_found"
    assert report["solver_executed"] is False
    assert report["ready_for_solver"] is False
    s = _statuses(report)
    assert s["material"] == "missing"
    assert s["loads"] == "missing"
    assert s["constraints"] == "missing"
    # Defaultable inputs still default even without a setup.
    assert s["analysis_type"] == "defaultable"
    assert s["mesh"] == "defaultable"
    assert s["solver"] == "defaultable"
    assert report["missing_required_inputs"] == list(REQUIRED_INPUTS)
    assert "Missing REQUIRED inputs" in report["summary"]
    assert "do NOT claim" in report["summary"]


def test_empty_cae_block_is_not_found() -> None:
    report = build_simulation_readiness_report({})
    assert report["setup_source"] == "not_found"
    assert report["missing_required_inputs"] == list(REQUIRED_INPUTS)


def test_partial_setup_material_only() -> None:
    report = build_simulation_readiness_report({"present": True, "materials_count": 1})
    assert report["setup_source"] == "cae_setup"
    s = _statuses(report)
    assert s["material"] == "present"
    assert s["loads"] == "missing"
    assert s["constraints"] == "missing"
    assert report["missing_required_inputs"] == ["loads", "constraints"]
    assert report["ready_for_solver"] is False


def test_complete_setup_is_ready_but_solver_not_run() -> None:
    cae = {
        "present": True,
        "materials": ["steel"],
        "loads": [{"type": "force", "magnitude": 500}],
        "boundary_conditions": [{"type": "fixed"}],
    }
    report = build_simulation_readiness_report(cae)
    assert report["setup_source"] == "cae_setup"
    s = _statuses(report)
    assert s["material"] == "present"
    assert s["loads"] == "present"
    assert s["constraints"] == "present"
    # mesh/solver/analysis_type are defaultable for planning.
    assert s["mesh"] == "defaultable"
    assert s["solver"] == "defaultable"
    assert s["analysis_type"] == "defaultable"
    assert report["missing_required_inputs"] == []
    assert report["ready_for_solver"] is True
    assert report["solver_executed"] is False
    assert "All required inputs" in report["summary"]
    assert "solver has NOT been run" in report["summary"]


def test_constraints_via_count_key() -> None:
    cae = {"materials_count": 1, "loads_count": 2, "constraints_count": 3}
    report = build_simulation_readiness_report(cae)
    assert _statuses(report)["constraints"] == "present"
    assert report["missing_required_inputs"] == []


def test_explicit_analysis_mesh_solver_present() -> None:
    cae = {
        "materials": ["al"],
        "loads": ["L1"],
        "boundary_conditions": ["B1"],
        "analysis_type": "modal",
        "mesh": {"element_size": 2.0},
        "solver": "CalculiX",
    }
    report = build_simulation_readiness_report(cae)
    s = _statuses(report)
    assert s["analysis_type"] == "present"
    assert s["mesh"] == "present"
    assert s["solver"] == "present"
    assert report["inputs"]["analysis_type"]["detail"] == "modal"
    assert report["defaultable_inputs"] == []


def test_explicitly_unavailable_mesh_and_solver_are_unknown() -> None:
    cae = {
        "materials": ["al"],
        "loads": ["L1"],
        "boundary_conditions": ["B1"],
        "mesh": False,  # explicitly unavailable
        "solver_status": {"available": False},
    }
    report = build_simulation_readiness_report(cae)
    s = _statuses(report)
    assert s["mesh"] == "unknown"
    assert s["solver"] == "unknown"
    # Required still satisfied — unknown defaultables do not block readiness.
    assert report["ready_for_solver"] is True


def test_fea_setup_draft_satisfies_required_inputs() -> None:
    cae = {
        "fea_setup_draft": {
            "material": {"name": "steel"},
            "load_cases": [{"force": 100}],
            "supports": [{"face": "f1"}],
        }
    }
    report = build_simulation_readiness_report(cae)
    assert report["setup_source"] == "cae_setup"
    assert report["missing_required_inputs"] == []


def test_mention_targets_consume_bindings() -> None:
    mentions = [
        {"kind": "part", "raw": "@part:bracket", "value": "bracket"},
        {"kind": "part", "raw": "@part:ghost", "value": "ghost"},
        {"kind": "artifact", "raw": "@artifact:model.glb", "value": "model.glb"},
    ]
    bindings = build_mention_bindings(
        mentions,
        part_indexes=[("cad.named_parts", ["bracket", "rib"])],
        artifact_indexes=None,  # cannot determine → unverified (known=None)
    )
    report = build_simulation_readiness_report(
        {"materials": ["s"], "loads": ["l"], "boundary_conditions": ["b"]},
        mention_bindings=bindings,
    )
    parts = {t["value"]: t["known"] for t in report["targets"]["parts"]}
    assert parts["bracket"] is True
    assert parts["ghost"] is False
    arts = {t["value"]: t["known"] for t in report["targets"]["artifacts"]}
    assert arts["model.glb"] is None
    # canonical_id / source propagate into the readiness targets.
    bracket = next(t for t in report["targets"]["parts"] if t["value"] == "bracket")
    assert bracket["source"] == "cad.named_parts"
    assert bracket["canonical_id"] == "bracket"
    assert "bracket (known)" in report["summary"]
    assert "ghost (not found)" in report["summary"]
    assert "model.glb (unverified)" in report["summary"]


# --- analysis-type-aware required inputs (modal / buckling) -----------------


def test_modal_does_not_require_loads() -> None:
    """Modal: material + constraints required, loads NOT required."""
    cae = {
        "present": True,
        "materials": ["steel"],
        "boundary_conditions": [{"type": "fixed"}],
        "analysis_type": "modal",
        # deliberately no loads
    }
    report = build_simulation_readiness_report(cae)
    assert report["analysis_type"] == "modal"
    assert report["required_inputs"] == ["material", "constraints"]
    s = _statuses(report)
    assert s["material"] == "present"
    assert s["constraints"] == "present"
    # loads absent but not required → not missing
    assert s["loads"] != "missing"
    assert report["missing_required_inputs"] == []
    assert report["ready_for_solver"] is True
    assert "modal" in report["summary"].lower()
    assert any("density" in n.lower() for n in report["notes"])


def test_modal_still_requires_material_and_constraints() -> None:
    report = build_simulation_readiness_report(
        {"present": True, "analysis_type": "frequency"}
    )
    assert report["analysis_type"] == "modal"
    assert set(report["missing_required_inputs"]) == {"material", "constraints"}
    assert "loads" not in report["missing_required_inputs"]


def test_buckling_requires_loads() -> None:
    """Buckling: loads required (reference perturbation load)."""
    cae = {
        "present": True,
        "materials": ["steel"],
        "boundary_conditions": [{"type": "fixed"}],
        "analysis_type": "buckle",
        # no loads
    }
    report = build_simulation_readiness_report(cae)
    assert report["analysis_type"] == "buckling"
    assert report["required_inputs"] == ["material", "loads", "constraints"]
    assert report["missing_required_inputs"] == ["loads"]
    assert report["ready_for_solver"] is False


# --- v2: direct setup artifact reading --------------------------------------

_YAML_COMPLETE = """
analysis_type: linear_static
material:
  name: steel
loads:
  - type: force
    magnitude: 500
constraints:
  - type: fixed
mesh:
  element_size: 2.0
solver: CalculiX
"""

_JSON_PARTIAL = '{"material": {"name": "al"}, "loads": [{"type": "pressure"}]}'


def test_load_yaml_setup_from_simulation_path() -> None:
    loaded = load_simulation_setup(_reader({"simulation/setup.yaml": _YAML_COMPLETE}))
    assert loaded is not None
    assert loaded["setup_source"] == "simulation/setup.yaml"
    assert loaded["setup_source_kind"] == "setup_artifact"
    assert loaded["data"]["material"]["name"] == "steel"


def test_load_json_setup_from_cae_path() -> None:
    loaded = load_simulation_setup(_reader({"cae/setup.json": _JSON_PARTIAL}))
    assert loaded is not None
    assert loaded["setup_source"] == "cae/setup.json"
    assert loaded["data"]["material"]["name"] == "al"


def test_setup_path_priority_simulation_over_cae() -> None:
    loaded = load_simulation_setup(
        _reader({"simulation/setup.yaml": _YAML_COMPLETE, "cae/setup.json": _JSON_PARTIAL})
    )
    assert loaded["setup_source"] == "simulation/setup.yaml"


def test_malformed_setup_is_safe_and_returns_none() -> None:
    assert load_simulation_setup(_reader({"simulation/setup.yaml": "::: not : yaml :::"})) is None
    assert load_simulation_setup(_reader({})) is None
    assert load_simulation_setup(None) is None


def test_workspace_artifact_inline_setup() -> None:
    artifacts = [
        {"kind": "viewer", "name": "preview.glb"},
        {"kind": "cae_setup", "name": "setup-1", "data": {"material": ["steel"], "loads": ["L"], "constraints": ["C"]}},
    ]
    loaded = load_simulation_setup(_reader({}), artifacts=artifacts)
    assert loaded is not None
    assert loaded["setup_source_kind"] == "workspace_artifact"
    assert loaded["setup_source"] == "setup-1"


def test_complete_setup_artifact_is_ready() -> None:
    setup = load_simulation_setup(_reader({"simulation/setup.yaml": _YAML_COMPLETE}))
    report = build_simulation_readiness_report(setup_artifact=setup)
    assert report["setup_source"] == "simulation/setup.yaml"
    assert report["setup_source_kind"] == "setup_artifact"
    s = _statuses(report)
    assert s["material"] == "present"
    assert s["loads"] == "present"
    assert s["constraints"] == "present"
    assert s["analysis_type"] == "present"
    assert s["mesh"] == "present"
    assert s["solver"] == "present"
    assert report["missing_required_inputs"] == []
    assert report["ready_for_solver"] is True
    assert report["solver_executed"] is False


def test_partial_setup_artifact_misses_required() -> None:
    setup = load_simulation_setup(_reader({"cae/setup.json": _JSON_PARTIAL}))
    report = build_simulation_readiness_report(setup_artifact=setup)
    s = _statuses(report)
    assert s["material"] == "present"
    assert s["loads"] == "present"
    assert s["constraints"] == "missing"
    assert report["missing_required_inputs"] == ["constraints"]
    assert report["ready_for_solver"] is False


def test_setup_artifact_takes_priority_over_cae_block() -> None:
    # A partial direct setup must win over a complete agent_context cae block.
    setup = {
        "data": {"material": ["steel"]},
        "setup_source": "simulation/setup.yaml",
        "setup_source_kind": "setup_artifact",
    }
    complete_cae = {"present": True, "materials": ["s"], "loads": ["l"], "boundary_conditions": ["b"]}
    report = build_simulation_readiness_report(complete_cae, setup_artifact=setup)
    assert report["setup_source_kind"] == "setup_artifact"
    # constraints/loads come from the direct setup (absent there), not the cae block.
    assert set(report["missing_required_inputs"]) == {"loads", "constraints"}


def test_missing_setup_falls_back_to_cae_block() -> None:
    report = build_simulation_readiness_report(
        {"present": True, "materials": ["s"], "loads": ["l"], "boundary_conditions": ["b"]},
        setup_artifact=None,
    )
    assert report["setup_source"] == "cae_setup"
    assert report["setup_source_kind"] == "agent_context"
    assert report["ready_for_solver"] is True


def test_no_source_at_all_is_not_found() -> None:
    report = build_simulation_readiness_report(None, setup_artifact=None)
    assert report["setup_source"] == "not_found"
    assert report["setup_source_kind"] == "none"
    assert report["missing_required_inputs"] == list(REQUIRED_INPUTS)


def test_explicit_unavailable_mesh_solver_in_setup_artifact() -> None:
    setup = {
        "data": {
            "material": ["s"], "loads": ["l"], "constraints": ["c"],
            "mesh": False,
            "solver": {"available": False},
        },
        "setup_source": "simulation/setup.yaml",
        "setup_source_kind": "setup_artifact",
    }
    report = build_simulation_readiness_report(setup_artifact=setup)
    s = _statuses(report)
    assert s["mesh"] == "unknown"
    assert s["solver"] == "unknown"
    # Required still satisfied — unknown defaultables do not block readiness.
    assert report["ready_for_solver"] is True


# --- blocked_reason_codes ---------------------------------------------------


def test_blocked_reason_codes_for_missing_required_inputs() -> None:
    report = build_simulation_readiness_report(None)
    assert "blocked_reason_codes" in report
    assert set(report["blocked_reason_codes"]) == {
        "missing_material",
        "missing_loads",
        "missing_constraints",
    }


def test_blocked_reason_codes_for_unknown_defaultables() -> None:
    report = build_simulation_readiness_report({
        "present": True,
        "materials": ["steel"],
        "loads": [{"type": "force"}],
        "boundary_conditions": [{"type": "fixed"}],
        "mesh": False,
        "solver": {"available": False},
    })
    assert report["ready_for_solver"] is True
    codes = set(report["blocked_reason_codes"])
    assert "missing_mesh" in codes
    assert "solver_unavailable" in codes


def test_blocked_reason_codes_for_unknown_analysis_type() -> None:
    report = build_simulation_readiness_report({
        "present": True,
        "materials": ["steel"],
        "loads": [{"type": "force"}],
        "boundary_conditions": [{"type": "fixed"}],
        "analysis_type": False,
    })
    assert "missing_analysis_type" in report["blocked_reason_codes"]


def test_blocked_reason_codes_for_target_not_found() -> None:
    mentions = [{"kind": "part", "raw": "@part:ghost", "value": "ghost"}]
    bindings = build_mention_bindings(
        mentions,
        part_indexes=[("cad.named_parts", ["bracket"])],
        artifact_indexes=None,
    )
    report = build_simulation_readiness_report(
        {"materials": ["s"], "loads": ["l"], "boundary_conditions": ["b"]},
        mention_bindings=bindings,
    )
    assert "target_not_found" in report["blocked_reason_codes"]
