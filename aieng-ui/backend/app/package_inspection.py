from __future__ import annotations

import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import Settings, ensure_aieng_on_path

PackageArchiveLike = Any


class PackageReadCache:
    """Single-open read cache for a `.aieng` package during one workflow step."""

    def __init__(self, package_path: Path) -> None:
        self.package_path = Path(package_path)
        self._archive = zipfile.ZipFile(self.package_path, "r")
        self._bytes_cache: dict[str, bytes] = {}
        self._names = tuple(self._archive.namelist())

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> "PackageReadCache":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def namelist(self) -> list[str]:
        return list(self._names)

    def has_member(self, member_name: str) -> bool:
        return member_name in self._bytes_cache or member_name in self._names

    def read(self, member_name: str) -> bytes:
        if member_name not in self._bytes_cache:
            self._bytes_cache[member_name] = self._archive.read(member_name)
        return self._bytes_cache[member_name]


def package_member_count(value: Any, preferred_keys: tuple[str, ...] = ()) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return len(candidate)
            if isinstance(candidate, dict):
                return len(candidate)
        numeric_count = value.get("count")
        if isinstance(numeric_count, int):
            return numeric_count
        return len(value)
    return None


def _read_package_bytes(archive: PackageArchiveLike, member_name: str) -> bytes:
    if isinstance(archive, PackageReadCache):
        return archive.read(member_name)
    return archive.read(member_name)


def read_package_json(archive: PackageArchiveLike, member_name: str) -> Any:
    try:
        return json.loads(_read_package_bytes(archive, member_name).decode("utf-8"))
    except KeyError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def read_package_text(archive: PackageArchiveLike, member_name: str) -> str | None:
    try:
        return _read_package_bytes(archive, member_name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def read_package_yaml(archive: PackageArchiveLike, member_name: str) -> Any:
    try:
        return yaml.safe_load(_read_package_bytes(archive, member_name).decode("utf-8", errors="replace"))
    except KeyError:
        return None


def read_package_json_candidates(archive: PackageArchiveLike, member_names: tuple[str, ...]) -> Any:
    for member_name in member_names:
        value = read_package_json(archive, member_name)
        if value is not None:
            return value
    return None


def read_package_yaml_candidates(archive: PackageArchiveLike, member_names: tuple[str, ...]) -> Any:
    for member_name in member_names:
        value = read_package_yaml(archive, member_name)
        if value is not None:
            return value
    return None


def package_member_items(value: Any, preferred_keys: tuple[str, ...] = ()) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        items = value.get("items")
        if isinstance(items, list):
            return items
    return []


#: Both container spellings, and both per-entry vocabularies. The schema says
#: `evidence_items` with an `artifact` sub-object; the workbench's own writer
#: (`aieng_bridge._upsert_evidence`) writes `entries` with flat `path`/`kind`.
#: Reading only the first returned an empty list for every real package, so
#: `evidence_count` was 0 and `results_available` False on all 8 packages that
#: have results.
_EVIDENCE_CONTAINER_KEYS = ("evidence_items", "entries")


def summarize_evidence_items(evidence_index: Any) -> list[dict[str, Any]]:
    # Both containers, concatenated: `package_member_items` returns the first
    # key that matches, which would drop a populated `entries` sitting beside
    # an empty `evidence_items`.
    evidence_items = [
        item
        for key in _EVIDENCE_CONTAINER_KEYS
        for item in package_member_items(evidence_index, (key,))
    ]
    summarized: list[dict[str, Any]] = []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        artifact = item.get("artifact") if isinstance(item.get("artifact"), dict) else {}
        verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
        claim_support = item.get("claim_support") if isinstance(item.get("claim_support"), list) else []
        summarized.append(
            {
                "evidence_id": item.get("evidence_id") or item.get("id"),
                "evidence_type": item.get("evidence_type") or item.get("kind"),
                "artifact_path": artifact.get("path") or item.get("path"),
                "artifact_kind": artifact.get("kind") or item.get("kind"),
                "verification_status": verification.get("status"),
                "notes": item.get("notes") or artifact.get("notes") or verification.get("notes"),
                "claim_support": claim_support or item.get("supports") or [],
                # The index also lists evidence it merely EXPECTS. Carrying the
                # flag through means a consumer can tell an artifact that is
                # there from one that is only planned.
                "artifact_exists": item.get("exists"),
            }
        )
    return summarized


def summarize_cae_payload(
    *,
    constraints: Any,
    parsed_materials: Any,
    parsed_boundary_conditions: Any,
    parsed_loads: Any,
    cae_mapping: Any,
    evidence_index: Any,
    validation_status: Any,
) -> dict[str, Any]:
    constraint_items = [item for item in package_member_items(constraints, ("constraints",)) if isinstance(item, dict)]
    material_items = package_member_items(parsed_materials, ("materials",))
    boundary_condition_items = package_member_items(parsed_boundary_conditions, ("boundary_conditions", "constraints", "bcs"))
    load_items = package_member_items(parsed_loads, ("loads", "forces"))
    evidence_items = summarize_evidence_items(evidence_index)
    # One definition, shared with the library, rather than a second filter over
    # a vocabulary no writer produces: `{"solver_result", "mesh_evidence"}`
    # matched nothing in any real package.
    #
    # Imported here rather than at module scope to keep this module's stated
    # property: it does not require `aieng` to be pip-installed. The first
    # version of this comment claimed that without doing it — the import would
    # have raised in exactly the deployment it described. `ensure_aieng_on_path`
    # exists for lazy importers like this one and is idempotent.
    ensure_aieng_on_path()
    from aieng.cae_result_summary import read_result_evidence

    result_evidence_reading = read_result_evidence(evidence_index)

    available_fields: list[str] = []
    for constraint in constraint_items:
        metric = str(constraint.get("metric") or "").lower()
        if "stress" in metric and "stress" not in available_fields:
            available_fields.append("stress")
        if "displacement" in metric and "displacement" not in available_fields:
            available_fields.append("displacement")

    solver_mesh_status = validation_status.get("solver_mesh_status", {}) if isinstance(validation_status, dict) else {}
    if isinstance(solver_mesh_status, dict):
        if "stress_validation" in solver_mesh_status and "stress" not in available_fields:
            available_fields.append("stress")
        if "displacement_validation" in solver_mesh_status and "displacement" not in available_fields:
            available_fields.append("displacement")

    constraint_type_counts = dict(Counter(str(item.get("type") or "unknown") for item in constraint_items))
    simulation_targets = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "metric": item.get("metric"),
            "operator": item.get("operator"),
            "value": item.get("value"),
            "reason": item.get("reason"),
        }
        for item in constraint_items
        if item.get("type") == "simulation_target"
    ]
    protected_regions = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "type": item.get("type"),
            "reason": item.get("reason"),
        }
        for item in constraint_items
        if str(item.get("type") or "").startswith("protect") or item.get("type") == "preserve_interface"
    ]

    present = any(
        [
            constraint_items,
            material_items,
            boundary_condition_items,
            load_items,
            evidence_items,
            isinstance(cae_mapping, dict) and bool(cae_mapping),
            isinstance(solver_mesh_status, dict) and bool(solver_mesh_status),
        ]
    )

    return {
        "present": present,
        "constraints_count": len(constraint_items),
        "constraint_types": constraint_type_counts,
        "materials_count": len(material_items),
        "boundary_conditions_count": len(boundary_condition_items),
        "loads_count": len(load_items),
        "evidence_count": len(evidence_items),
        "result_evidence_count": result_evidence_reading["result_entry_count"],
        #: Whether the package REGISTERS result evidence that exists. None when
        #: it carries no evidence index at all — "nothing recorded" is not the
        #: same answer as "recorded, and there is none".
        "results_available": result_evidence_reading["registered"],
        "evidence_index_shape": result_evidence_reading["index_shape"],
        "available_fields": available_fields,
        "simulation_targets": simulation_targets,
        "protected_regions": protected_regions,
        "materials": material_items,
        "boundary_conditions": boundary_condition_items,
        "loads": load_items,
        "evidence": evidence_items,
        "mapping": cae_mapping,
        "solver_status": solver_mesh_status if isinstance(solver_mesh_status, dict) else {},
    }


def package_summary_fallback(
    package_path: Path,
    *,
    archive: PackageArchiveLike | None = None,
) -> dict[str, Any]:
    owns_archive = archive is None
    archive = archive or PackageReadCache(package_path)
    try:
        members = sorted(archive.namelist())
        manifest = read_package_json(archive, "manifest.json")
        feature_graph = read_package_json(archive, "graph/feature_graph.json")
        topology = read_package_json(archive, "geometry/topology_map.json")
        interfaces = read_package_json(archive, "objects/interface_graph.json")
        task_spec = read_package_json_candidates(archive, ("task_spec.json", "task/task_spec.json"))
        if task_spec is None:
            task_spec = read_package_yaml_candidates(archive, ("task/task_spec.yaml", "task/task_spec.yml"))
        external_tool_requirements = read_package_json_candidates(
            archive,
            ("external_tool_requirements.json", "task/external_tool_requirements.json"),
        )
        claim_map = read_package_json(archive, "ai/claim_map.json")
        evidence_index = read_package_json(archive, "results/evidence_index.json")
        tool_trace = read_package_json(archive, "provenance/tool_trace.json")
        completeness_report = read_package_json(archive, "validation/completeness_report.json")
        evidence_report = read_package_json(archive, "validation/evidence_report.json")
        constraints = read_package_json(archive, "graph/constraints.json")
        parsed_materials = read_package_json(archive, "simulation/cae_imports/parsed_materials.json")
        parsed_boundary_conditions = read_package_json(archive, "simulation/cae_imports/parsed_boundary_conditions.json")
        parsed_loads = read_package_json(archive, "simulation/cae_imports/parsed_loads.json")
        cae_mapping = read_package_json(archive, "simulation/cae_mapping.json")
        validation_status = read_package_yaml(archive, "validation/status.yaml")
        ai_summary = read_package_text(archive, "ai/summary.md")
    finally:
        if owns_archive and isinstance(archive, PackageReadCache):
            archive.close()

    derived: dict[str, Any] = {}
    feature_count = package_member_count(feature_graph, ("features", "nodes", "items", "elements"))
    topology_count = package_member_count(topology, ("bodies", "solids", "faces", "edges", "vertices"))
    interface_count = package_member_count(interfaces, ("interfaces", "edges", "links"))
    if feature_count is not None:
        derived["feature_graph"] = {"count": feature_count}
    if topology_count is not None:
        derived["topology"] = {"count": topology_count}
    if interface_count is not None:
        derived["interfaces"] = {"count": interface_count}

    warnings = [
        member_name
        for member_name in (
            "geometry/topology_map.json",
            "graph/feature_graph.json",
            "objects/interface_graph.json",
            "validation/completeness_report.json",
            "validation/evidence_report.json",
        )
        if member_name not in members
    ]

    return {
        "members": members,
        "member_count": len(members),
        "manifest": manifest,
        "feature_graph": feature_graph,
        "topology": topology,
        "interfaces": interfaces,
        "constraints": constraints,
        "task_spec": task_spec,
        "external_tool_requirements": external_tool_requirements,
        "claim_map": claim_map,
        "evidence_index": evidence_index,
        "tool_trace": tool_trace,
        "completeness_report": completeness_report,
        "evidence_report": evidence_report,
        "parsed_materials": parsed_materials,
        "parsed_boundary_conditions": parsed_boundary_conditions,
        "parsed_loads": parsed_loads,
        "cae_mapping": cae_mapping,
        "validation_status": validation_status,
        "cae": summarize_cae_payload(
            constraints=constraints,
            parsed_materials=parsed_materials,
            parsed_boundary_conditions=parsed_boundary_conditions,
            parsed_loads=parsed_loads,
            cae_mapping=cae_mapping,
            evidence_index=evidence_index,
            validation_status=validation_status,
        ),
        "ai_summary": ai_summary,
        "derived": derived,
        "warnings": warnings,
    }


def build_cae_review_report(
    *,
    package_path: Path,
    project_id: str,
    preprocessing_summary: dict[str, Any] | None,
    simulation_run_summary: dict[str, Any] | None,
    result_summary: dict[str, Any] | None,
    revalidation_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a read-only, evidence-grounded CAE review report.

    This is an assistant-facing synthesis layer: it combines the existing CAE
    lifecycle summaries but does not execute solvers, mutate packages, or
    advance engineering claims.
    """
    with zipfile.ZipFile(package_path, "r") as zf:
        names = set(zf.namelist())
        evidence_index = read_package_json(zf, "results/evidence_index.json") or {}
        claim_maps_present = [
            path for path in ("ai/claim_map.json", "results/claim_map.json") if path in names
        ]

    pre_status = _dict_at(preprocessing_summary, "status")
    run_status = _dict_at(simulation_run_summary, "status")
    result_status = _dict_at(result_summary, "status")
    computed_values = _dict_at(result_summary, "computed_values")
    design_target_comparisons = (
        result_summary.get("design_target_comparisons")
        if isinstance(result_summary, dict)
        else None
    )
    if not isinstance(design_target_comparisons, dict):
        design_target_comparisons = {"present": False}

    evidence_items = summarize_evidence_items(evidence_index)
    stale_artifacts = _list_at(revalidation_status, "stale_artifacts") or _list_at(
        revalidation_status, "affected_artifacts"
    )
    stale_domains = _list_at(revalidation_status, "affected_domains")
    requires_revalidation = bool(
        isinstance(revalidation_status, dict) and revalidation_status.get("requires_revalidation")
    )
    missing_items = _list_at(pre_status, "missing_items")
    setup_warnings = _list_at(pre_status, "warnings")
    result_warnings = _list_at(result_status, "warnings")
    run_warnings = _list_at(run_status, "warnings")
    limitations = (
        _summary_list(preprocessing_summary, "limitations")
        + _summary_list(simulation_run_summary, "limitations")
        + _summary_list(result_summary, "limitations")
    )
    next_actions = _dedupe_keep_order(
        _summary_list(preprocessing_summary, "recommended_next_actions")
        + _summary_list(simulation_run_summary, "recommended_next_actions")
        + _summary_list(result_summary, "recommended_next_actions")
    )

    facts = [
        f"Setup ready for solver: {_yes_no(pre_status.get('ready_for_solver'))}",
        f"Simulation runs recorded: {_yes_no(run_status.get('has_simulation_runs'))}",
        f"Completed solver run recorded: {_yes_no(run_status.get('has_completed_run'))}",
        f"Computed result metrics present: {_yes_no(computed_values.get('extrema_computed'))}",
        f"Evidence index entries: {len(evidence_items)}",
    ]

    unsupported = [
        "AIENG is not a solver and does not certify physical correctness.",
        "Artifact presence is not proof of mesh quality, convergence, or engineering validity.",
        "Claim advancement is not automatic; evidence and claims remain separate.",
    ]
    if run_status.get("has_converged_run") is not True:
        unsupported.append("Convergence is not proven by the current package metadata.")

    claim_boundary = {
        "claims_advanced": False,
        "claim_maps_present": claim_maps_present,
        "status": "needs_explicit_review" if claim_maps_present else "no_claim_map_advanced",
        "message": (
            "Claim map artifacts are present; this report still does not mark any claim as passed."
            if claim_maps_present
            else "No claim map artifact was found; this report does not create or advance claims."
        ),
    }

    sections = {
        "available_evidence": {
            "facts": facts,
            "evidence_count": len(evidence_items),
            "source_artifacts": _source_artifacts(result_summary),
        },
        "missing_information": {
            "items": missing_items,
            "warnings": setup_warnings + run_warnings + result_warnings,
        },
        "unsupported_information": {
            "items": unsupported,
            "limitations": _dedupe_keep_order(limitations),
        },
        "stale_evidence": {
            "requires_revalidation": requires_revalidation,
            "reason": revalidation_status.get("reason") if isinstance(revalidation_status, dict) else None,
            "triggering_tool": (
                revalidation_status.get("triggering_tool") if isinstance(revalidation_status, dict) else None
            ),
            "domains": stale_domains,
            "artifacts": stale_artifacts,
        },
        "design_targets": {
            "present": bool(design_target_comparisons.get("present")),
            "summary": design_target_comparisons.get("summary") or {},
            "items": design_target_comparisons.get("items") or [],
            "note": "Artifact-level comparison only; not engineering certification.",
        },
        "claim_boundary": claim_boundary,
        "next_actions": {"items": next_actions},
    }

    markdown = _render_cae_review_markdown(sections)
    return {
        "schema_version": "0.1",
        "report_type": "cae_review_report",
        "project_id": project_id,
        "package_name": package_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _one_line(preprocessing_summary, "CAE evidence review generated from package artifacts."),
        "sections": sections,
        "markdown": markdown,
        "source_summaries": {
            "preprocessing": preprocessing_summary,
            "simulation_run": simulation_run_summary,
            "result": result_summary,
            "revalidation_status": revalidation_status,
        },
    }


def _dict_at(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get(key), dict):
        return value[key]
    return {}


def _list_at(value: Any, key: str) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return value[key]
    return []


def _summary_list(summary: Any, key: str) -> list[str]:
    llm_summary = _dict_at(summary, "llm_summary")
    values = llm_summary.get(key)
    if isinstance(values, list):
        return [str(item) for item in values if str(item).strip()]
    return []


def _one_line(summary: Any, fallback: str) -> str:
    llm_summary = _dict_at(summary, "llm_summary")
    value = llm_summary.get("one_line")
    return str(value) if value else fallback


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _source_artifacts(result_summary: Any) -> list[str]:
    artifacts = _dict_at(result_summary, "artifacts")
    values: list[str] = []
    for key in ("mesh_files", "field_files", "result_summary_files", "evidence_files", "validation_files", "setup_files"):
        values.extend(str(item) for item in _list_at(artifacts, key))
    return _dedupe_keep_order(values)


def _render_cae_review_markdown(sections: dict[str, Any]) -> str:
    missing = sections["missing_information"]["items"] or ["No missing setup items reported by current summaries."]
    stale = sections["stale_evidence"]
    targets = sections["design_targets"]
    next_actions = sections["next_actions"]["items"] or ["Review missing/unsupported items before making engineering claims."]
    lines = [
        "# CAE Evidence Review Report",
        "",
        "## Available evidence",
        *[f"- {fact}" for fact in sections["available_evidence"]["facts"]],
        "",
        "## Missing information",
        *[f"- {item}" for item in missing],
        "",
        "## Unsupported / limitations",
        *[f"- {item}" for item in sections["unsupported_information"]["items"]],
        *[f"- {item}" for item in sections["unsupported_information"]["limitations"]],
        "",
        "## Stale evidence",
        f"- Requires revalidation: {_yes_no(stale.get('requires_revalidation'))}",
    ]
    if stale.get("reason"):
        lines.append(f"- Reason: {stale['reason']}")
    for artifact in stale.get("artifacts") or []:
        lines.append(f"- Stale artifact: {artifact}")
    for domain in stale.get("domains") or []:
        lines.append(f"- Stale domain: {domain}")
    lines.extend(
        [
            "",
            "## Design targets",
            f"- Present: {_yes_no(targets.get('present'))}",
            f"- Summary: {json.dumps(targets.get('summary') or {}, sort_keys=True)}",
            "- Note: artifact-level comparison only; not engineering certification.",
            "",
            "## Claim boundary",
            f"- Claims advanced: {_yes_no(sections['claim_boundary']['claims_advanced'])}",
            f"- {sections['claim_boundary']['message']}",
            "",
            "## Recommended next actions",
            *[f"- {item}" for item in next_actions],
        ]
    )
    return "\n".join(lines) + "\n"


def _detect_cae_artifacts(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_artifact_detector and scan the package.

    Uses temporary sys.path injection so the backend does not need
    aieng installed as a pip dependency.
    """
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_artifact_detector import detect_cae_artifacts

        return detect_cae_artifacts(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cae_result_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_result_summary and generate a summary for the package.

    Uses temporary sys.path injection so the backend does not need
    aieng installed as a pip dependency.
    """
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_result_summary import generate_cae_result_summary

        return generate_cae_result_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cae_preprocessing_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_preprocessing_summary and generate a preprocessing summary for the package."""
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_preprocessing_summary import generate_preprocessing_summary

        return generate_preprocessing_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cad_recommendations_with_verification(
    settings: Settings,
    package_path: Path,
    *,
    strictness: str = "default",
) -> dict[str, Any] | None:
    """Run Phase 36 recommendation + Phase 37 verification on a .aieng package.

    Read-only. Returns a combined payload with both the recommendation
    block and the per-proposal verification verdicts. Returns ``None``
    when the aieng source is not importable (so the endpoint can return
    503).

    The verification step is invoked on the same in-memory proposals so
    the caller doesn't need to wire the CLI together by hand.
    """
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_recommendation import (
            generate_cad_modification_recommendations,
        )
        from aieng.cae_verification import (
            STRICTNESS_MODES,
            verify_recommendations,
        )

        if strictness not in STRICTNESS_MODES:
            return {
                "ok": False,
                "package_path": str(package_path),
                "recommendations": None,
                "verification": None,
                "errors": [
                    f"strictness must be one of {list(STRICTNESS_MODES)}; "
                    f"got {strictness!r}."
                ],
            }

        recommendations = generate_cad_modification_recommendations(package_path)
        verification = verify_recommendations(
            recommendations, package_path, strictness=strictness
        )
        return {
            "ok": bool(recommendations.get("ok"))
            and bool(verification.get("ok"))
            and int(verification.get("summary", {}).get("fail", 0)) == 0,
            "package_path": str(package_path),
            "strictness": strictness,
            "recommendations": recommendations,
            "verification": verification,
            "claim_policy": {
                "proposals_are_hypotheses": True,
                "verification_is_pre_execution": True,
                "verification_does_not_replace_resimulation": True,
                "geometry_kernel_checks_not_performed": True,
                "claims_advanced": False,
            },
        }
    except ModuleNotFoundError as exc:
        if exc.name not in {"aieng.cae_recommendation", "aieng.cae_verification"}:
            return None
        return _generate_cad_recommendations_fallback(package_path, strictness=strictness)
    except Exception:
        return _generate_cad_recommendations_fallback(package_path, strictness=strictness)
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cad_recommendations_fallback(
    package_path: Path,
    *,
    strictness: str = "default",
) -> dict[str, Any] | None:
    """Small local fallback for the removed Phase 36/37 aieng modules.

    It is deliberately narrow: only deterministic single-parameter thinning
    hypotheses from package feature/stress evidence. No geometry kernel is run
    and no claim is advanced.
    """
    strictness_modes = {"lenient", "default", "strict"}
    if strictness not in strictness_modes:
        return {
            "ok": False,
            "package_path": str(package_path),
            "strictness": strictness,
            "recommendations": None,
            "verification": None,
            "errors": [
                f"strictness must be one of {sorted(strictness_modes)}; got {strictness!r}."
            ],
        }

    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            parsed_features = read_package_json(archive, "simulation/cae_imports/parsed_features.json")
            feature_graph = read_package_json(archive, "graph/feature_graph.json")
            stress_by_feature = read_package_json(archive, "results/stress_by_feature.json")
            design_targets = read_package_yaml_candidates(
                archive, ("task/design_targets.yaml", "task/design_targets.yml")
            )
            computed_metrics = read_package_json(archive, "results/computed_metrics.json")
    except Exception:
        return None

    features = _recommendation_features(parsed_features, feature_graph)
    stress_lookup = {
        str(item.get("feature_ref")): item
        for item in (stress_by_feature or {}).get("features", [])
        if isinstance(item, dict) and item.get("feature_ref") is not None
    }
    minimum_sf = _minimum_safety_factor(design_targets, stress_by_feature)

    proposals: list[dict[str, Any]] = []
    for feature in features:
        feature_ref = str(feature.get("id") or feature.get("feature_id") or feature.get("name") or "")
        if not feature_ref:
            continue
        params = _recommendation_parameters(feature)
        thickness = params.get("thickness_mm")
        if not isinstance(thickness, (int, float)) or isinstance(thickness, bool):
            continue
        stress = stress_lookup.get(feature_ref) or {}
        safety_factor = stress.get("safety_factor")
        mass = feature.get("mass_contribution_kg")
        reduction_ratio = 0.10
        if strictness == "lenient":
            reduction_ratio = 0.15
        elif strictness == "strict":
            reduction_ratio = 0.05
        new_value = round(float(thickness) * (1.0 - reduction_ratio), 4)
        proposal_id = f"proposal_{len(proposals) + 1:03d}_{feature_ref}_thin"
        proposals.append(
            {
                "proposal_id": proposal_id,
                "feature_ref": feature_ref,
                "action_type": "thin",
                "parameter_change": {
                    "name": "thickness_mm",
                    "from": thickness,
                    "to": new_value,
                    "unit": "mm",
                },
                "estimated_mass_delta_kg": (
                    round(-float(mass) * reduction_ratio, 6)
                    if isinstance(mass, (int, float)) and not isinstance(mass, bool)
                    else None
                ),
                "safety_factor": safety_factor,
                "rationale": (
                    "Reduce declared wall thickness on a feature with available "
                    "stress margin. This is a hypothesis and requires approved CAD "
                    "edit plus downstream revalidation."
                ),
            }
        )

    proposals.sort(
        key=lambda p: (
            -(float(p.get("safety_factor")) if isinstance(p.get("safety_factor"), (int, float)) else -1.0),
            float(p.get("estimated_mass_delta_kg") or 0.0),
            str(p.get("feature_ref") or ""),
        )
    )
    for index, proposal in enumerate(proposals, start=1):
        proposal["rank"] = index

    verdicts: list[dict[str, Any]] = []
    margin_by_mode = {"lenient": 1.0, "default": 1.05, "strict": 1.2}
    required_sf = minimum_sf * margin_by_mode[strictness]
    for proposal in proposals:
        safety_factor = proposal.get("safety_factor")
        blockers: list[dict[str, str]] = []
        warnings_from_checks: list[dict[str, str]] = []
        verdict = "warn"
        if isinstance(safety_factor, (int, float)) and not isinstance(safety_factor, bool):
            estimated_sf = float(safety_factor) * (
                float(proposal["parameter_change"]["to"]) / float(proposal["parameter_change"]["from"])
            )
            if estimated_sf >= required_sf:
                verdict = "pass"
            else:
                verdict = "fail"
                blockers.append({
                    "check": "safety_factor_margin",
                    "message": f"Estimated safety factor {estimated_sf:.3g} is below required {required_sf:.3g}.",
                })
        else:
            estimated_sf = None
            warnings_from_checks.append({
                "check": "safety_factor_available",
                "message": "No feature-level safety factor was available; human review required.",
            })
        verdicts.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "verdict": verdict,
                "estimated_safety_factor": estimated_sf,
                "required_safety_factor": required_sf,
                "blockers": blockers,
                "warnings_from_checks": warnings_from_checks,
            }
        )

    fail_count = sum(1 for item in verdicts if item.get("verdict") == "fail")
    warn_count = sum(1 for item in verdicts if item.get("verdict") == "warn")
    pass_count = sum(1 for item in verdicts if item.get("verdict") == "pass")
    recommendations = {
        "schema_version": "0.1",
        "ok": bool(proposals),
        "source": "aieng-ui.fallback_cad_recommender",
        "package_path": str(package_path),
        "proposals": proposals,
        "warnings": [] if proposals else ["No editable thinning candidates were found."],
        "claim_policy": {
            "proposals_are_hypotheses": True,
            "claims_advanced": False,
        },
    }
    verification = {
        "schema_version": "0.1",
        "ok": fail_count == 0,
        "strictness": strictness,
        "verdicts": verdicts,
        "summary": {"pass": pass_count, "warn": warn_count, "fail": fail_count, "total": len(verdicts)},
    }
    return {
        "ok": bool(proposals) and fail_count == 0,
        "package_path": str(package_path),
        "strictness": strictness,
        "recommendations": recommendations,
        "verification": verification,
        "context": {
            "computed_metrics_available": isinstance(computed_metrics, dict),
            "minimum_safety_factor": minimum_sf,
        },
        "claim_policy": {
            "proposals_are_hypotheses": True,
            "verification_is_pre_execution": True,
            "verification_does_not_replace_resimulation": True,
            "geometry_kernel_checks_not_performed": True,
            "claims_advanced": False,
        },
    }


def _recommendation_features(
    parsed_features: Any,
    feature_graph: Any,
) -> list[dict[str, Any]]:
    parsed = parsed_features.get("features") if isinstance(parsed_features, dict) else None
    if isinstance(parsed, list) and parsed:
        return [item for item in parsed if isinstance(item, dict)]
    graph = feature_graph.get("features") if isinstance(feature_graph, dict) else None
    if isinstance(graph, list):
        return [item for item in graph if isinstance(item, dict)]
    if isinstance(graph, dict):
        return [item for item in graph.values() if isinstance(item, dict)]
    return []


def _recommendation_parameters(feature: dict[str, Any]) -> dict[str, Any]:
    params = feature.get("parameters")
    if isinstance(params, dict):
        return params
    if isinstance(params, list):
        out: dict[str, Any] = {}
        for item in params:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            out[str(item["name"])] = item.get("current_value")
        return out
    return {}


def _minimum_safety_factor(design_targets: Any, stress_by_feature: Any) -> float:
    if isinstance(design_targets, dict):
        for target in design_targets.get("targets") or []:
            if not isinstance(target, dict):
                continue
            if target.get("target_type") == "minimum_safety_factor":
                threshold = target.get("threshold")
                if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
                    return float(threshold)
    minimum = (stress_by_feature or {}).get("minimum_required_safety_factor") if isinstance(stress_by_feature, dict) else None
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
        return float(minimum)
    return 1.5


def _generate_cae_simulation_run_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_simulation_run_summary and generate a simulation run summary for the package."""
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_simulation_run_summary import generate_simulation_run_summary

        return generate_simulation_run_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


