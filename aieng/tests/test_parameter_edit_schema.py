"""Tests for ``schemas/parameter_edit.schema.json`` (Phase 32 contract).

The implementation of ``cad.edit_parameter`` lives in ``aieng_freecad_mcp``;
the contract — the schema and its const guards — lives here so all
participants validate against the same shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "src" / "aieng" / "schemas" / "parameter_edit.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_edit() -> dict:
    return {
        "schema_version": "0.1",
        "edit_id": "edit_0001",
        "feature_id": "feat_flange_001",
        "parameter_name": "thickness",
        "new_value": 5.0,
        "previous_value": 4.0,
        "unit": "mm",
        "bounds": {"min": 2.0, "max": 10.0, "source": "feature_template"},
        "preflight": {
            "feature_exists": True,
            "parameter_exists": True,
            "editable": True,
            "value_in_bounds": True,
            "topology_changing": False,
            "refusal_reason": None,
        },
        "approval": {
            "required": True,
            "status": "granted",
            "approver": "human@example",
            "decided_at": "2026-05-17T10:00:00Z",
        },
        "execution": {
            "status": "succeeded",
            "step_export": {
                "attempted": True,
                "path": "geometry/modified_edit_0001.step",
                "atomic": True,
            },
            "failure_reason": None,
        },
        "stale_artifacts": [
            "simulation/cae_imports/source_solver_deck.inp",
            "simulation/mesh/mesh_metadata.json",
            "results/field_regions.json",
            "results/field_summary.json",
        ],
        "warnings": [],
        "claim_policy": {
            "approval_gated": True,
            "topology_unchanged": True,
            "no_solver_run": True,
            "no_mesh_run": True,
        },
    }


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_required_fields_present() -> None:
    required = _schema()["required"]
    for field in (
        "schema_version",
        "edit_id",
        "feature_id",
        "parameter_name",
        "new_value",
        "previous_value",
        "preflight",
        "approval",
        "execution",
        "stale_artifacts",
        "warnings",
        "claim_policy",
    ):
        assert field in required, f"{field} must be required"


def test_claim_policy_consts_are_pinned() -> None:
    policy = _schema()["properties"]["claim_policy"]["properties"]
    assert policy["approval_gated"]["const"] is True
    assert policy["topology_unchanged"]["const"] is True
    assert policy["no_solver_run"]["const"] is True
    assert policy["no_mesh_run"]["const"] is True


def test_approval_required_is_const_true() -> None:
    approval = _schema()["properties"]["approval"]["properties"]
    assert approval["required"]["const"] is True


def test_execution_status_enum_covers_all_lifecycle_states() -> None:
    schema = _schema()
    enum = schema["properties"]["execution"]["properties"]["status"]["enum"]
    for value in ("not_attempted", "succeeded", "failed", "refused"):
        assert value in enum


def test_approval_status_enum_covers_benchmark_auto_approval() -> None:
    schema = _schema()
    enum = schema["properties"]["approval"]["properties"]["status"]["enum"]
    for value in ("pending", "granted", "denied", "auto_approved_for_benchmark"):
        assert value in enum


def test_edit_id_pattern_enforces_zero_padded_index() -> None:
    schema = _schema()
    pattern = schema["properties"]["edit_id"]["pattern"]
    import re
    assert re.match(pattern, "edit_0001")
    assert not re.match(pattern, "edit_1")
    assert not re.match(pattern, "patch_0001")


# ---------------------------------------------------------------------------
# Round-trip validation
# ---------------------------------------------------------------------------


def test_valid_edit_passes_schema() -> None:
    jsonschema.validate(_valid_edit(), _schema())


def test_missing_claim_policy_fails() -> None:
    edit = _valid_edit()
    del edit["claim_policy"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(edit, _schema())


def test_approval_gated_false_fails() -> None:
    edit = _valid_edit()
    edit["claim_policy"]["approval_gated"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(edit, _schema())


def test_no_solver_run_false_fails() -> None:
    edit = _valid_edit()
    edit["claim_policy"]["no_solver_run"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(edit, _schema())


def test_unknown_execution_status_fails() -> None:
    edit = _valid_edit()
    edit["execution"]["status"] = "bypassed"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(edit, _schema())


def test_extra_top_level_property_rejected() -> None:
    edit = _valid_edit()
    edit["secret_value"] = "unexpected"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(edit, _schema())
