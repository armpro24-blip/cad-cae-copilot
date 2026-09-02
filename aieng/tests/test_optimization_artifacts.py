"""Contract tests for agent-guided optimization front-end artifacts."""

from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

from aieng.optimization_artifacts import (
    OPTIMIZATION_ARTIFACT_PATHS,
    OPTIMIZATION_REASON_CODES,
    OPTIMIZATION_SCHEMA_FILES,
    validate_optimization_artifact,
    validate_optimization_artifact_set,
)
from aieng.validate import SCHEMA_FILES, validate_package

ROOT = Path(__file__).resolve().parents[1]


def _provenance() -> dict:
    return {
        "created_at": "2026-06-10T00:00:00Z",
        "created_by": "test",
        "claim_advancement": "none",
    }


def _claim_policy() -> dict:
    return {
        "advisory_only": True,
        "baseline_unchanged": True,
        "human_approval_required_for_acceptance": True,
        "claim_advancement": "none",
    }


def _design_study_problem() -> dict:
    return {
        "format": "aieng.design_study_problem",
        "schema_version": "0.1",
        "id": "study_source_001",
        "variables": [
            {
                "id": "wall_t",
                "path": "parts/0/params/WALL_THICKNESS",
                "type": "continuous",
                "current_value": 3.0,
                "min_value": 2.0,
                "max_value": 8.0,
                "unit": "mm",
                "safe_to_modify": True,
            }
        ],
        "objective": {"sense": "minimize", "metric": "volume"},
        "constraints": [
            {"id": "c_stress", "type": "max_stress", "limit": 200.0, "unit": "MPa"}
        ],
    }


def valid_documents() -> dict[str, dict]:
    common = {
        "schema_version": "0.1",
        "study_id": "opt_study_001",
        "design_study_problem_ref": "analysis/design_study_problem.json",
        "design_study_problem_id": "study_source_001",
        "candidate_ids": ["candidate_001"],
        "provenance": _provenance(),
        "claim_policy": _claim_policy(),
    }
    return {
        "study": {
            **copy.deepcopy(common),
            "format": "aieng.optimization_study",
            "algorithm": {"name": "latin_hypercube", "phase": 1, "bounded_step": True, "seed": 7},
            "sampling": {"requested_candidate_count": 5, "max_candidate_count": 20, "seed": 7},
            "budget": {"max_candidates": 20, "max_iterations": 1, "max_solver_runs": 0},
            "status": "defined",
            "artifact_refs": {
                "variables": "analysis/optimization_variables.json",
                "objectives": "analysis/optimization_objectives.json",
                "constraints": "analysis/optimization_constraints.json",
                "decision_log": "analysis/optimization_decision_log.json",
            },
        },
        "variables": {
            **copy.deepcopy(common),
            "schema_version": "0.2",
            "format": "aieng.optimization_variables",
            "variables": [
                {
                    "id": "wall_t",
                    "path": "parts/0/params/WALL_THICKNESS",
                    "type": "continuous",
                    "featureId": "feat_wall",
                    "parameterName": "thickness",
                    "cad_parameter_name": "WALL_THICKNESS",
                    "binding_status": "bound",
                    "current_value": 3.0,
                    "min_value": 2.0,
                    "max_value": 8.0,
                    "allowed_values": None,
                    "unit": "mm",
                    "scope": "local",
                    "safe_to_modify": True,
                    "candidate_ids": ["candidate_001"],
                }
            ],
        },
        "objectives": {
            **copy.deepcopy(common),
            "format": "aieng.optimization_objectives",
            "objectives": [
                {
                    "id": "objective_volume",
                    "metric": "volume",
                    "direction": "minimize",
                    "weight": 1.0,
                    "unit": "mm3",
                    "candidate_ids": ["candidate_001"],
                    "evidence_refs": ["candidates/candidate_001/analysis/evaluation.json"],
                }
            ],
        },
        "constraints": {
            **copy.deepcopy(common),
            "format": "aieng.optimization_constraints",
            "constraints": [
                {
                    "id": "c_stress",
                    "metric": "max_stress",
                    "operator": "<=",
                    "limit": 200.0,
                    "unit": "MPa",
                    "hard": True,
                    "unknown_policy": "needs_user_input",
                    "candidate_ids": ["candidate_001"],
                    "evidence_refs": ["candidates/candidate_001/analysis/evaluation.json"],
                }
            ],
        },
        "decision_log": {
            **copy.deepcopy(common),
            "format": "aieng.optimization_decision_log",
            "entries": [
                {
                    "decision_id": "decision_001",
                    "timestamp": "2026-06-10T00:00:01Z",
                    "decision": "select_latin_hypercube",
                    "reason_codes": ["initial_mvp", "small_number_of_design_variables"],
                    "requires_human_review": True,
                    "candidate_ids": ["candidate_001"],
                    "metric_refs": ["volume"],
                    "note": "Bounded Phase 1 exploration.",
                }
            ],
        },
    }


def test_all_valid_documents_pass_schema_and_consistency() -> None:
    documents = valid_documents()
    for kind, document in documents.items():
        assert validate_optimization_artifact(kind, document) == []
    assert validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem(),
    ) == []


def test_variable_contract_requires_parameter_binding_fields() -> None:
    document = valid_documents()["variables"]
    del document["variables"][0]["cad_parameter_name"]
    issues = validate_optimization_artifact("variables", document)
    assert any("cad_parameter_name" in issue for issue in issues)


def test_bound_variable_rejects_null_feature_id() -> None:
    document = valid_documents()["variables"]
    document["variables"][0]["featureId"] = None
    issues = validate_optimization_artifact("variables", document)
    assert any("featureId" in issue for issue in issues)


def test_decision_log_rejects_unknown_reason_code() -> None:
    document = valid_documents()["decision_log"]
    document["entries"][0]["reason_codes"] = ["invented_reason"]
    issues = validate_optimization_artifact("decision_log", document)
    assert any("invented_reason" in issue for issue in issues)


def test_reason_code_vocabulary_matches_schema() -> None:
    schema_path = ROOT / "src" / "aieng" / "schemas" / "optimization_decision_log.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    enum = set(
        schema["properties"]["entries"]["items"]["properties"]["reason_codes"]["items"]["enum"]
    )
    assert enum == set(OPTIMIZATION_REASON_CODES)


def test_artifact_set_rejects_inconsistent_study_ids() -> None:
    documents = valid_documents()
    documents["objectives"]["study_id"] = "different_study"
    issues = validate_optimization_artifact_set(documents)
    assert any("inconsistent study_id" in issue for issue in issues)


def test_artifact_set_rejects_design_study_fork() -> None:
    documents = valid_documents()
    documents["variables"]["variables"][0]["max_value"] = 99.0
    issues = validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem(),
    )
    assert any("max_value does not match design study" in issue for issue in issues)


def test_claim_policy_guards_cannot_be_relaxed() -> None:
    document = valid_documents()["study"]
    document["claim_policy"]["baseline_unchanged"] = False
    issues = validate_optimization_artifact("study", document)
    assert any("baseline_unchanged" in issue for issue in issues)


def test_package_validator_registers_all_optimization_schemas() -> None:
    for kind, artifact_path in OPTIMIZATION_ARTIFACT_PATHS.items():
        assert SCHEMA_FILES[artifact_path] == OPTIMIZATION_SCHEMA_FILES[kind]


# `test_canonical_and_packaged_schemas_match` lived here: it compared the
# repo-root schema tree with the packaged one. It passed — because it compared
# only the OPTIMIZATION schemas, which happened to be identical, while 14 other
# schemas had diverged. A synchronisation check scoped to a subset that is in
# sync gives false assurance about the whole. There is now one tree (#517), and
# `test_schema_tree_is_singular.py` keeps it that way.


def test_package_validation_checks_design_study_consistency(tmp_path: Path) -> None:
    package_path = tmp_path / "valid.aieng"
    documents = valid_documents()
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", "{}")
        package.writestr("analysis/design_study_problem.json", json.dumps(_design_study_problem()))
        package.writestr(
            "analysis/optimization_variables.json",
            json.dumps(documents["variables"]),
        )

    report = validate_package(package_path)
    assert any(
        message.text == "optimization artifacts are consistent with design-study source"
        for message in report.messages
    )


def test_package_validation_rejects_design_study_fork(tmp_path: Path) -> None:
    package_path = tmp_path / "invalid.aieng"
    document = valid_documents()["variables"]
    document["variables"][0]["max_value"] = 99.0
    with zipfile.ZipFile(package_path, "w") as package:
        package.writestr("manifest.json", "{}")
        package.writestr("analysis/design_study_problem.json", json.dumps(_design_study_problem()))
        package.writestr("analysis/optimization_variables.json", json.dumps(document))

    report = validate_package(package_path)
    assert any(
        message.level.value == "FAIL"
        and "max_value does not match design study" in message.text
        for message in report.messages
    )


def _design_study_problem_multi_objective() -> dict:
    return {
        "format": "aieng.design_study_problem",
        "schema_version": "0.1",
        "id": "study_source_001",
        "variables": [
            {
                "id": "wall_t",
                "path": "parts/0/params/WALL_THICKNESS",
                "type": "continuous",
                "current_value": 3.0,
                "min_value": 2.0,
                "max_value": 8.0,
                "unit": "mm",
                "safe_to_modify": True,
            }
        ],
        "objectives": [
            {"sense": "minimize", "metric": "volume"},
            {"sense": "minimize", "metric": "mass"},
        ],
        "constraints": [
            {"id": "c_stress", "type": "max_stress", "limit": 200.0, "unit": "MPa"}
        ],
    }


def _valid_documents_multi_objective() -> dict[str, dict]:
    common = {
        "schema_version": "0.1",
        "study_id": "opt_study_001",
        "design_study_problem_ref": "analysis/design_study_problem.json",
        "design_study_problem_id": "study_source_001",
        "candidate_ids": ["candidate_001"],
        "provenance": _provenance(),
        "claim_policy": _claim_policy(),
    }
    return {
        "objectives": {
            **copy.deepcopy(common),
            "format": "aieng.optimization_objectives",
            "objectives": [
                {
                    "id": "objective_volume",
                    "metric": "volume",
                    "direction": "minimize",
                    "weight": 0.6,
                    "unit": "mm3",
                    "candidate_ids": ["candidate_001"],
                    "evidence_refs": ["candidates/candidate_001/analysis/evaluation.json"],
                },
                {
                    "id": "objective_mass",
                    "metric": "mass",
                    "direction": "minimize",
                    "weight": 0.4,
                    "unit": "kg",
                    "candidate_ids": ["candidate_001"],
                    "evidence_refs": ["candidates/candidate_001/analysis/evaluation.json"],
                },
            ],
        },
    }


def test_multi_objective_problem_validates_and_checks_consistency() -> None:
    documents = _valid_documents_multi_objective()
    assert validate_optimization_artifact("objectives", documents["objectives"]) == []
    assert validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem_multi_objective(),
    ) == []


def test_multi_objective_rejects_direction_mismatch() -> None:
    documents = _valid_documents_multi_objective()
    documents["objectives"]["objectives"][1]["direction"] = "maximize"
    issues = validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem_multi_objective(),
    )
    assert any("objectives[1]: direction does not match" in issue for issue in issues)


def test_multi_objective_rejects_metric_mismatch() -> None:
    documents = _valid_documents_multi_objective()
    documents["objectives"]["objectives"][0]["metric"] = "compliance"
    issues = validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem_multi_objective(),
    )
    assert any("objectives[0]: metric does not match" in issue for issue in issues)


def test_multi_objective_rejects_extra_optimization_objective() -> None:
    documents = _valid_documents_multi_objective()
    documents["objectives"]["objectives"].append(
        {
            "id": "objective_stress",
            "metric": "max_stress",
            "direction": "minimize",
            "weight": 1.0,
            "candidate_ids": ["candidate_001"],
            "evidence_refs": [],
        }
    )
    issues = validate_optimization_artifact_set(
        documents,
        design_study_problem=_design_study_problem_multi_objective(),
    )
    assert any("objectives[2]: no corresponding objective" in issue for issue in issues)


def test_single_objective_rejects_extra_optimization_objectives() -> None:
    """Back-compat: a single problem objective does not allow extra opt objectives."""
    documents = _valid_documents_multi_objective()
    problem = _design_study_problem()
    issues = validate_optimization_artifact_set(documents, design_study_problem=problem)
    assert any("objectives[1]: no corresponding objective" in issue for issue in issues)


def test_objectives_array_takes_precedence_over_single_objective() -> None:
    """When both objective and objectives are present, objectives takes precedence."""
    documents = _valid_documents_multi_objective()
    problem = _design_study_problem_multi_objective()
    # Add a conflicting single objective that would fail if checked
    problem["objective"] = {"sense": "maximize", "metric": "stress"}

    # Should validate against objectives array, not the single objective
    issues = validate_optimization_artifact_set(documents, design_study_problem=problem)
    assert issues == []
