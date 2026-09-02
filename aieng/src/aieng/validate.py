from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
import warnings
import zipfile

import yaml
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from .reference import ref_check_package

from . import FORMAT_VERSION
from .optimization_artifacts import (
    DESIGN_STUDY_PROBLEM_PATH,
    OPTIMIZATION_ARTIFACT_PATHS,
    validate_optimization_artifact_set,
)
from .package import PACKAGE_DIRECTORIES

try:  # jsonschema is a lightweight Phase 0 dependency, with fallback checks below.
    from jsonschema import Draft202012Validator
except Exception:  # pragma: no cover - exercised only when dependency is unavailable.
    Draft202012Validator = None  # type: ignore[assignment]

NUMERIC_TYPES = (int, float)


def _duplicate_values(items: Iterable[Any]) -> list[Any]:
    """Return duplicate values with one linear scan."""
    counts = Counter(items)
    return sorted(
        (item for item, count in counts.items() if count > 1),
        key=lambda item: (type(item).__name__, str(item)),
    )


class Level(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ValidationMessage:
    level: Level
    text: str

    def render(self) -> str:
        return f"{self.level.value} {self.text}"


@dataclass(frozen=True)
class ValidationReport:
    messages: tuple[ValidationMessage, ...]

    @property
    def ok(self) -> bool:
        return not any(message.level is Level.FAIL for message in self.messages)

    def render(self) -> str:
        return "\n".join(message.render() for message in self.messages)


SCHEMA_FILES = {
    "geometry/topology_map.json": "topology_map.schema.json",
    "graph/aag.json": "aag.schema.json",
    "graph/feature_graph.json": "feature_graph.schema.json",
    "graph/allowed_operations_catalog.json": "allowed_operations_catalog.schema.json",
    "graph/constraints.json": "constraints.schema.json",
    "ai/protected_regions.json": "protected_regions.schema.json",
    "visual/annotation_layers.json": "visual_annotation_layers.schema.json",
    "visual/model_manifest.json": "visual_model_manifest.schema.json",
    "objects/object_registry.json": "object_registry.schema.json",
    "objects/interface_graph.json": "interface_graph.schema.json",
    "assembly/assembly_graph.json": "assembly_graph.schema.json",
    "simulation/cae_imports/parsed_materials.json": "parsed_cae_materials.schema.json",
    "simulation/cae_imports/parsed_boundary_conditions.json": "parsed_cae_boundary_conditions.schema.json",
    "simulation/cae_imports/parsed_loads.json": "parsed_cae_loads.schema.json",
    "simulation/cae_mapping.json": "cae_mapping.schema.json",
    "simulation/mesh_handoff_contract.json": "mesh_handoff_contract.schema.json",
    "provenance/tool_trace.json": "tool_trace.schema.json",
    "provenance/conversion_manifest.json": "conversion_manifest.schema.json",
    "provenance/converter_capabilities.json": "converter_capabilities.schema.json",
    "validation/completeness_report.json": "completeness_report.schema.json",
    "validation/evidence_report.json": "evidence_report.schema.json",
    "results/field_regions.json": "field_regions.schema.json",
    "results/field_summary.json": "field_summary.schema.json",
    "analysis/optimization_study.json": "optimization_study.schema.json",
    "analysis/optimization_variables.json": "optimization_variables.schema.json",
    "analysis/optimization_objectives.json": "optimization_objectives.schema.json",
    "analysis/optimization_constraints.json": "optimization_constraints.schema.json",
    "analysis/optimization_decision_log.json": "optimization_decision_log.schema.json",
    "analysis/pareto_front.json": "pareto_front.schema.json",
    "verification/nafems_vv_report.json": "nafems_vv_report.schema.json",
}

TEXT_RESOURCES = (
    "README_FOR_AI.md",
    "ai/summary.md",
)

GEOMETRY_CHANGING_PATCH_OPS = {"add_feature", "modify_parameter", "remove_feature"}
PROTECTED_CONSTRAINT_TYPES = {"protect_geometry", "protect_position", "protect_dimension", "preserve_interface"}
INTERFACE_ROLE_VALUES = {
    "mounting_interface_candidate",
    "fixed_support_interface",
    "load_application_interface",
    "protected_external_interface",
    "cae_mapped_interface",
    "cae_boundary_condition_interface",
    "cae_load_interface",
    "cae_mapped_feature_interface",
}

OPTIONAL_PHASE0_RESOURCES = (
    "README_FOR_AI.md",
    "geometry/source.step",
    "geometry/normalized.step",
    "geometry/topology_map.json",
    "graph/aag.json",
    "graph/feature_graph.json",
    "graph/allowed_operations_catalog.json",
    "graph/semantic_graph.json",
    "graph/constraints.json",
    "engineering_context/material.yaml",
    "simulation/setup.yaml",
    "simulation/material_assignments.json",
    "simulation/solver_deck.inp",
    "simulation/cae_imports/source_solver_deck.inp",
    "simulation/cae_imports/parsed_materials.json",
    "simulation/cae_imports/parsed_boundary_conditions.json",
    "simulation/cae_imports/parsed_loads.json",
    "simulation/cae_mapping.json",
    "simulation/mesh_handoff_contract.json",
    "ai/summary.md",
    "ai/editable_variables.json",
    "ai/protected_regions.json",
    "results/placeholder.json",
    "results/field_regions.json",
    "results/field_summary.json",
    "results/field_summary.md",
    "task/design_targets.yaml",
    "previews/thumbnail.png",
    "previews/model.glb",
    "visual/annotation_layers.json",
    "visual/model_manifest.json",
    "objects/object_registry.json",
    "objects/interface_graph.json",
    "assembly/assembly_graph.json",
    "provenance/tool_trace.json",
    "provenance/conversion_manifest.json",
    "provenance/converter_capabilities.json",
    "validation/completeness_report.json",
    "validation/evidence_report.json",
)



def _cae_mapping_enum(*path: str) -> set[str]:
    """An enum from `cae_mapping.schema.json`, so the rule cannot drift from it.

    These sets used to be literals here AND in the schema. When the workbench
    started authoring setups it needed new `mapping_method` values, and two
    copies meant a schema update alone left the rule rejecting them (#513).
    Falls back to the historical values if the schema cannot be read, so a
    packaging problem degrades to the old behaviour rather than accepting
    anything.
    """
    text = _read_schema_text("cae_mapping.schema.json")
    if text:
        try:
            node: Any = json.loads(text)
            for key in path:
                node = node[key]
            if isinstance(node, list) and node:
                return {str(v) for v in node}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return {"not_inferred_phase_10a", "user_provided"}


_MAPPING_ITEM = ("properties", "mappings", "items", "properties")

#: `mapping_method` values that state how a face was actually chosen. These make
#: the notes' non-inference policy redundant; `not_inferred_phase_10a` does not,
#: because that value IS the claim the note substantiates.
_SELECTION_METHODS = frozenset(
    {"user_provided", "resolved_from_pointer", "resolved_from_intent", "ai_generated"}
)


def validate_package(package_path: str | Path) -> ValidationReport:
    path = Path(package_path)
    messages: list[ValidationMessage] = []

    if not path.exists():
        return ValidationReport((ValidationMessage(Level.FAIL, f"package does not exist: {path}"),))
    if path.suffix != ".aieng":
        messages.append(ValidationMessage(Level.WARN, "package extension is not .aieng"))

    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            messages.extend(_validate_zip_members(package, names))
    except zipfile.BadZipFile:
        messages.append(ValidationMessage(Level.FAIL, "package is not a valid zip archive"))

    return ValidationReport(tuple(messages))


def _validate_zip_members(package: zipfile.ZipFile, names: set[str]) -> Iterable[ValidationMessage]:
    messages: list[ValidationMessage] = []

    if "manifest.json" not in names:
        messages.append(ValidationMessage(Level.FAIL, "manifest.json missing"))
        return messages
    messages.append(ValidationMessage(Level.PASS, "manifest.json exists"))
    messages.append(ValidationMessage(Level.PASS, "global import policy reminder: imported artifacts and parsed facts do not auto-advance claims; claim proposals require human review"))

    manifest = _read_json_member(package, "manifest.json", messages)
    if manifest is None:
        return messages

    messages.extend(_validate_manifest(manifest))
    source_mode = manifest.get("source_mode")
    definition_sourced = source_mode == "definition"
    converter_sourced = source_mode == "converter"

    for directory in PACKAGE_DIRECTORIES:
        if directory in names:
            messages.append(ValidationMessage(Level.PASS, f"{directory} exists"))
        else:
            messages.append(ValidationMessage(Level.WARN, f"{directory} missing"))

    for resource_path in _resource_paths(manifest.get("resources", {})):
        if resource_path in names:
            messages.append(ValidationMessage(Level.PASS, f"{resource_path} exists"))
        else:
            messages.append(ValidationMessage(Level.FAIL, f"required resource {resource_path} missing"))

    for resource_path in OPTIONAL_PHASE0_RESOURCES:
        if resource_path not in names:
            if definition_sourced and resource_path in {
                "geometry/source.step",
                "geometry/normalized.step",
                "geometry/topology_map.json",
            }:
                messages.append(ValidationMessage(Level.WARN, f"{resource_path} missing for definition-sourced package"))
            else:
                messages.append(ValidationMessage(Level.WARN, f"{resource_path} missing"))

    topology_map_data: Any | None = None
    feature_graph_data: Any | None = None
    aag_data: Any | None = None
    constraints_data: Any | None = None
    allowed_operations_catalog_data: Any | None = None
    protected_regions_data: Any | None = None
    simulation_setup_data: Any | None = None
    annotation_layers_data: Any | None = None
    visual_model_manifest_data: Any | None = None
    object_registry_data: Any | None = None
    interface_graph_data: Any | None = None
    assembly_graph_data: Any | None = None
    parsed_cae_materials_data: Any | None = None
    parsed_cae_boundary_conditions_data: Any | None = None
    parsed_cae_loads_data: Any | None = None
    cae_mapping_data: Any | None = None
    mesh_handoff_data: Any | None = None
    tool_trace_data: Any | None = None
    completeness_report_data: Any | None = None
    evidence_report_data: Any | None = None
    conversion_manifest_data: Any | None = None
    converter_capabilities_data: Any | None = None
    optimization_documents: dict[str, dict[str, Any]] = {}
    optimization_kind_by_path = {
        artifact_path: kind for kind, artifact_path in OPTIMIZATION_ARTIFACT_PATHS.items()
    }
    for member, schema_name in SCHEMA_FILES.items():
        if member in names:
            data = _read_json_member(package, member, messages)
            if data is not None:
                messages.extend(_validate_against_schema(member, data, schema_name))
                optimization_kind = optimization_kind_by_path.get(member)
                if optimization_kind and isinstance(data, dict):
                    optimization_documents[optimization_kind] = data
                if member == "geometry/topology_map.json":
                    topology_map_data = data
                    messages.extend(_validate_topology_map_semantics(data))
                if member == "graph/feature_graph.json":
                    feature_graph_data = data
                if member == "graph/allowed_operations_catalog.json":
                    allowed_operations_catalog_data = data
                if member == "graph/aag.json":
                    aag_data = data
                if member == "graph/constraints.json":
                    constraints_data = data
                if member == "ai/protected_regions.json":
                    protected_regions_data = data
                if member == "visual/annotation_layers.json":
                    annotation_layers_data = data
                if member == "visual/model_manifest.json":
                    visual_model_manifest_data = data
                if member == "objects/object_registry.json":
                    object_registry_data = data
                if member == "objects/interface_graph.json":
                    interface_graph_data = data
                if member == "assembly/assembly_graph.json":
                    assembly_graph_data = data
                if member == "simulation/cae_imports/parsed_materials.json":
                    parsed_cae_materials_data = data
                if member == "simulation/cae_imports/parsed_boundary_conditions.json":
                    parsed_cae_boundary_conditions_data = data
                if member == "simulation/cae_imports/parsed_loads.json":
                    parsed_cae_loads_data = data
                if member == "simulation/cae_mapping.json":
                    cae_mapping_data = data
                if member == "simulation/mesh_handoff_contract.json":
                    mesh_handoff_data = data
                if member == "provenance/tool_trace.json":
                    tool_trace_data = data
                if member == "validation/completeness_report.json":
                    completeness_report_data = data
                if member == "validation/evidence_report.json":
                    evidence_report_data = data
                if member == "provenance/conversion_manifest.json":
                    conversion_manifest_data = data
                if member == "provenance/converter_capabilities.json":
                    converter_capabilities_data = data

    if optimization_documents:
        design_study_problem = (
            _read_json_member(package, DESIGN_STUDY_PROBLEM_PATH, messages)
            if DESIGN_STUDY_PROBLEM_PATH in names
            else None
        )
        if design_study_problem is None:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"optimization artifacts require {DESIGN_STUDY_PROBLEM_PATH}",
                )
            )
        else:
            optimization_issues = validate_optimization_artifact_set(
                optimization_documents,
                design_study_problem=design_study_problem,
            )
            if optimization_issues:
                messages.extend(
                    ValidationMessage(Level.FAIL, f"optimization artifact consistency: {issue}")
                    for issue in optimization_issues
                )
            else:
                messages.append(
                    ValidationMessage(
                        Level.PASS,
                        "optimization artifacts are consistent with design-study source",
                    )
                )

    if "simulation/setup.yaml" in names:
        simulation_setup_data = _read_yaml_member(package, "simulation/setup.yaml", messages)
    if "engineering_context/material.yaml" in names:
        messages.extend(_validate_engineering_material(package, "engineering_context/material.yaml"))

    if feature_graph_data is not None:
        messages.extend(
            _validate_feature_graph_semantics(
                feature_graph_data,
                topology_map_data,
                definition_sourced=definition_sourced,
                converter_sourced=converter_sourced,
            )
        )
    if aag_data is not None:
        messages.extend(_validate_aag_semantics(aag_data, topology_map_data))
    if constraints_data is not None:
        messages.extend(_validate_constraints_semantics(constraints_data, feature_graph_data))
    if allowed_operations_catalog_data is not None:
        messages.extend(
            _validate_allowed_operations_catalog_semantics(
                allowed_operations_catalog_data,
                feature_graph_data,
                constraints_data,
            )
        )
    if simulation_setup_data is not None:
        messages.extend(_validate_simulation_setup_semantics(simulation_setup_data, feature_graph_data))
    if annotation_layers_data is not None:
        messages.extend(
            _validate_annotation_layers_semantics(
                annotation_layers_data, feature_graph_data, topology_map_data
            )
        )
    if visual_model_manifest_data is not None:
        messages.extend(_validate_visual_model_manifest_semantics(visual_model_manifest_data, names))
    if object_registry_data is not None:
        messages.extend(_validate_object_registry_semantics(object_registry_data, names))
    if assembly_graph_data is not None:
        messages.extend(_validate_assembly_graph_semantics(assembly_graph_data))
    if interface_graph_data is not None:
        messages.extend(
            _validate_interface_graph_semantics(
                interface_graph_data,
                feature_graph_data,
                topology_map_data,
                constraints_data,
                simulation_setup_data,
                annotation_layers_data,
                protected_regions_data,
                cae_mapping_data,
                names,
            )
        )
    if parsed_cae_materials_data is not None:
        messages.extend(_validate_parsed_cae_materials_semantics(parsed_cae_materials_data))
    if parsed_cae_boundary_conditions_data is not None:
        messages.extend(_validate_parsed_cae_boundary_conditions_semantics(parsed_cae_boundary_conditions_data))
    if parsed_cae_loads_data is not None:
        messages.extend(_validate_parsed_cae_loads_semantics(parsed_cae_loads_data))
    if cae_mapping_data is not None:
        messages.extend(
            _validate_cae_mapping_semantics(
                cae_mapping_data,
                feature_graph_data,
                interface_graph_data,
                _cae_target_ids(
                    (parsed_cae_boundary_conditions_data, "boundary_conditions"),
                    (parsed_cae_loads_data, "loads"),
                    (simulation_setup_data, "boundary_conditions"),
                    (simulation_setup_data, "loads"),
                ),
            )
        )
    for text_resource in TEXT_RESOURCES:
        if text_resource in names and text_resource in _resource_paths(manifest.get("resources", {})):
            messages.extend(_validate_text_resource(package, text_resource))

    if protected_regions_data is not None:
        messages.extend(_validate_protected_regions_semantics(protected_regions_data, feature_graph_data))

    solver_deck_path = "simulation/solver_deck.inp"
    manifest_resource_paths = _resource_paths(manifest.get("resources", {}))
    if solver_deck_path in manifest_resource_paths and solver_deck_path in names:
        messages.extend(_validate_solver_deck(package, solver_deck_path))

    validation_status_path = "validation/status.yaml"
    validation_status_data: Any | None = None
    if validation_status_path in manifest_resource_paths and validation_status_path in names:
        messages.extend(_validate_validation_status(package, validation_status_path))
        validation_status_data = _read_yaml_member(package, validation_status_path, [])

    ext_tool_req_path = "task/external_tool_requirements.json"
    ext_tool_req_data: Any | None = None
    if ext_tool_req_path in names:
        messages.extend(_validate_external_tool_requirements(package, ext_tool_req_path))
        ext_tool_req_data = _read_json_member(package, ext_tool_req_path, [])

    task_spec_path = "task/task_spec.yaml"
    task_spec_data: Any | None = None
    if task_spec_path in names:
        messages.extend(_validate_task_spec(package, task_spec_path))
        task_spec_data = _read_yaml_member(package, task_spec_path, [])

    design_targets_path = "task/design_targets.yaml"
    if design_targets_path in names:
        messages.extend(_validate_design_targets(package, design_targets_path))

    evidence_index_path = "results/evidence_index.json"
    evidence_index_data: Any | None = None
    if evidence_index_path in names:
        evidence_index_data = _read_json_member(package, evidence_index_path, messages)
        if evidence_index_data is not None:
            messages.extend(_validate_evidence_index(evidence_index_path, evidence_index_data, names))
    if mesh_handoff_data is not None:
        messages.extend(_validate_mesh_handoff_semantics(mesh_handoff_data, names, topology_map_data))

    if tool_trace_data is not None:
        messages.extend(
            _validate_tool_trace_semantics(
                "provenance/tool_trace.json",
                tool_trace_data,
                evidence_index_data,
                names,
            )
        )

    if completeness_report_data is not None:
        messages.extend(
            _validate_completeness_report_semantics(
                "validation/completeness_report.json",
                completeness_report_data,
                names,
            )
        )

    if conversion_manifest_data is not None:
        messages.extend(
            _validate_conversion_manifest_semantics(
                "provenance/conversion_manifest.json",
                conversion_manifest_data,
                manifest,
            )
        )

    if converter_capabilities_data is not None:
        messages.extend(
            _validate_converter_capabilities_semantics(
                "provenance/converter_capabilities.json",
                converter_capabilities_data,
            )
        )

    if manifest.get("source_mode") == "converter" and conversion_manifest_data is None:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                "source_mode=converter requires provenance/conversion_manifest.json",
            )
        )

    if evidence_report_data is not None:
        messages.extend(
            _validate_evidence_report_semantics(
                "validation/evidence_report.json",
                evidence_report_data,
                names,
                validation_status_data,
                evidence_index_data,
            )
        )

    messages.extend(
        _validate_cross_resource_consistency(
            validation_status_data=validation_status_data,
            task_spec_data=task_spec_data,
            ext_tool_req_data=ext_tool_req_data,
            evidence_index_data=evidence_index_data,
            tool_trace_data=tool_trace_data,
            names=names,
        )
    )

    patch_members = sorted(
        name for name in manifest_resource_paths if name.startswith("ai/patches/") and name.endswith(".json")
    )
    for patch_member in patch_members:
        if patch_member not in names:
            continue
        data = _read_json_member(package, patch_member, messages)
        if data is not None:
            messages.extend(_validate_against_schema(patch_member, data, "patch_proposal.schema.json"))
            messages.extend(
                _validate_patch_proposal_semantics(
                    patch_member,
                    data,
                    feature_graph_data,
                    constraints_data,
                    protected_regions_data,
                    allowed_operations_catalog_data,
                )
            )

    ref_ok, ref_messages = ref_check_package(Path(package.filename))
    for ref_message in ref_messages:
        level = Level(ref_message.level)
        messages.append(ValidationMessage(level, ref_message.text))
    if not ref_ok:
        messages.append(ValidationMessage(Level.FAIL, "ref-check failed"))

    return messages


def _validate_aag_semantics(
    aag_data: Any,
    topology_map: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(aag_data, dict):
        return [ValidationMessage(Level.FAIL, "graph/aag.json must be a JSON object")]

    nodes = aag_data.get("nodes")
    arcs = aag_data.get("arcs")
    notes = aag_data.get("notes")
    if not isinstance(nodes, list):
        return [ValidationMessage(Level.FAIL, "graph/aag.json nodes must be an array")]
    if not isinstance(arcs, list):
        return [ValidationMessage(Level.FAIL, "graph/aag.json arcs must be an array")]

    topology_ids = _topology_ids_by_type(topology_map)
    if topology_ids is None:
        messages.append(ValidationMessage(Level.FAIL, "AAG validation requires geometry/topology_map.json"))
        topology_face_ids: set[str] = set()
        topology_edge_ids: set[str] = set()
    else:
        topology_face_ids = topology_ids["face"]
        topology_edge_ids = topology_ids["edge"]

    node_ids: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            messages.append(ValidationMessage(Level.FAIL, f"AAG node at index {index} must be an object"))
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            messages.append(ValidationMessage(Level.FAIL, f"AAG node at index {index} missing string id"))
            continue
        node_ids.append(node_id)

        topology_entity_id = node.get("topology_entity_id")
        if isinstance(topology_entity_id, str):
            if topology_entity_id not in topology_face_ids:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"AAG node {node_id} references unknown or non-face topology entity {topology_entity_id}",
                    )
                )

    duplicate_node_ids = _duplicate_values(node_ids)
    if duplicate_node_ids:
        messages.append(ValidationMessage(Level.FAIL, f"AAG node IDs are not unique: {', '.join(duplicate_node_ids)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, "AAG node IDs are unique"))

    known_node_ids = set(node_ids)
    arc_ids: list[str] = []
    for index, arc in enumerate(arcs):
        if not isinstance(arc, dict):
            messages.append(ValidationMessage(Level.FAIL, f"AAG arc at index {index} must be an object"))
            continue
        arc_id = arc.get("id")
        if not isinstance(arc_id, str) or not arc_id:
            messages.append(ValidationMessage(Level.FAIL, f"AAG arc at index {index} missing string id"))
            continue
        arc_ids.append(arc_id)

        source_node = arc.get("source_node")
        target_node = arc.get("target_node")
        if isinstance(source_node, str) and source_node not in known_node_ids:
            messages.append(ValidationMessage(Level.FAIL, f"AAG arc {arc_id} references unknown source_node {source_node}"))
        if isinstance(target_node, str) and target_node not in known_node_ids:
            messages.append(ValidationMessage(Level.FAIL, f"AAG arc {arc_id} references unknown target_node {target_node}"))

        shared_edge_ids = arc.get("shared_edge_ids")
        if isinstance(shared_edge_ids, list):
            unknown_edges = sorted(
                edge_id for edge_id in shared_edge_ids
                if isinstance(edge_id, str) and edge_id not in topology_edge_ids
            )
            if unknown_edges:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"AAG arc {arc_id} references unknown shared_edge_ids: {', '.join(unknown_edges)}",
                    )
                )

    duplicate_arc_ids = _duplicate_values(arc_ids)
    if duplicate_arc_ids:
        messages.append(ValidationMessage(Level.FAIL, f"AAG arc IDs are not unique: {', '.join(duplicate_arc_ids)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, "AAG arc IDs are unique"))

    if isinstance(notes, list):
        notes_text = " ".join(note.lower() for note in notes if isinstance(note, str))
        has_generated_index = "generated" in notes_text and "index" in notes_text
        has_not_source = "not" in notes_text and "source of truth" in notes_text
        has_topology_source = "topology_map" in notes_text and "source" in notes_text
        has_unknown_convexity = "convexity" in notes_text and "unknown" in notes_text
        if has_generated_index and has_not_source and has_topology_source and has_unknown_convexity:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "graph/aag.json notes state generated-index, source-of-truth policy, and continuity uncertainty",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "graph/aag.json notes must state generated-index, not-source-of-truth policy, topology_map authority, and unknown convexity/continuity caveat",
                )
            )
    else:
        messages.append(ValidationMessage(Level.FAIL, "graph/aag.json notes must be an array"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "graph/aag.json semantic checks passed"))
    return messages


def _validate_mesh_handoff_semantics(
    handoff: Any,
    names: set[str],
    topology_map: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(handoff, dict):
        return [ValidationMessage(Level.FAIL, "simulation/mesh_handoff_contract.json must be a JSON object")]

    geometry_source = handoff.get("geometry_source")
    if not isinstance(geometry_source, str) or not geometry_source:
        messages.append(ValidationMessage(Level.FAIL, "mesh_handoff_contract geometry_source must be a non-empty string"))
    elif geometry_source not in names:
        messages.append(ValidationMessage(Level.FAIL, f"mesh_handoff_contract geometry_source missing in package: {geometry_source}"))
    else:
        messages.append(ValidationMessage(Level.PASS, f"mesh_handoff_contract geometry_source exists: {geometry_source}"))

    topology_refs = handoff.get("topology_refs")
    if not isinstance(topology_refs, dict):
        messages.append(ValidationMessage(Level.FAIL, "mesh_handoff_contract topology_refs must be an object"))
        return messages

    face_ids = topology_refs.get("face_ids")
    edge_ids = topology_refs.get("edge_ids")
    if not isinstance(face_ids, list) or not all(isinstance(v, str) for v in face_ids):
        messages.append(ValidationMessage(Level.FAIL, "mesh_handoff_contract topology_refs.face_ids must be a string array"))
    if not isinstance(edge_ids, list) or not all(isinstance(v, str) for v in edge_ids):
        messages.append(ValidationMessage(Level.FAIL, "mesh_handoff_contract topology_refs.edge_ids must be a string array"))

    topology_ids = _topology_ids_by_type(topology_map)
    if topology_ids is None:
        messages.append(ValidationMessage(Level.FAIL, "mesh_handoff_contract validation requires geometry/topology_map.json"))
        return messages

    if isinstance(face_ids, list):
        unknown_faces = sorted(fid for fid in face_ids if fid not in topology_ids["face"])
        if unknown_faces:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"mesh_handoff_contract references unknown face IDs: {', '.join(unknown_faces)}",
                )
            )
        else:
            messages.append(ValidationMessage(Level.PASS, "mesh_handoff_contract face references are valid"))

    if isinstance(edge_ids, list):
        unknown_edges = sorted(eid for eid in edge_ids if eid not in topology_ids["edge"])
        if unknown_edges:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"mesh_handoff_contract references unknown edge IDs: {', '.join(unknown_edges)}",
                )
            )
        else:
            messages.append(ValidationMessage(Level.PASS, "mesh_handoff_contract edge references are valid"))

    return messages


def _validate_allowed_operations_catalog_semantics(
    catalog: Any,
    feature_graph: Any | None,
    constraints_data: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(catalog, dict):
        return [ValidationMessage(Level.FAIL, "graph/allowed_operations_catalog.json must be a JSON object")]

    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        messages.append(ValidationMessage(Level.FAIL, "allowed_operations_catalog validation requires graph/feature_graph.json"))
        feature_ids = set()

    constraint_ids = _constraint_ids(constraints_data)

    entries = catalog.get("feature_operations")
    if not isinstance(entries, list):
        return [ValidationMessage(Level.FAIL, "allowed_operations_catalog feature_operations must be an array")]

    seen_feature_ids: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            messages.append(
                ValidationMessage(Level.FAIL, f"allowed_operations_catalog feature_operations[{index}] must be an object")
            )
            continue

        feature_id = entry.get("feature_id")
        if not isinstance(feature_id, str) or not feature_id:
            messages.append(
                ValidationMessage(Level.FAIL, f"allowed_operations_catalog feature_operations[{index}] missing feature_id")
            )
            continue
        seen_feature_ids.append(feature_id)

        if feature_id not in feature_ids:
            messages.append(
                ValidationMessage(Level.FAIL, f"allowed_operations_catalog references unknown feature_id {feature_id}")
            )

        operations = entry.get("operations")
        interface_roles = entry.get("interface_roles")
        if isinstance(interface_roles, list):
            unknown_roles = sorted(
                role for role in interface_roles if isinstance(role, str) and role not in INTERFACE_ROLE_VALUES
            )
            if unknown_roles:
                messages.append(
                    ValidationMessage(
                        Level.WARN,
                        f"allowed_operations_catalog feature {feature_id} has unknown interface roles: {', '.join(unknown_roles)}",
                    )
                )
        if not isinstance(operations, list):
            messages.append(
                ValidationMessage(Level.FAIL, f"allowed_operations_catalog feature {feature_id} operations must be an array")
            )
            continue
        for op_index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"allowed_operations_catalog feature {feature_id} operation at index {op_index} must be an object",
                    )
                )
                continue
            blocked = operation.get("blocked_by_constraints")
            if isinstance(blocked, list) and constraint_ids:
                unknown_constraints = sorted(
                    cid for cid in blocked if isinstance(cid, str) and cid not in constraint_ids
                )
                if unknown_constraints:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            "allowed_operations_catalog feature "
                            f"{feature_id} operation {op_index} references unknown constraints: "
                            f"{', '.join(unknown_constraints)}",
                        )
                    )

    duplicates = _duplicate_values(seen_feature_ids)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"allowed_operations_catalog feature_ids are not unique: {', '.join(duplicates)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "allowed_operations_catalog feature_ids are unique"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "graph/allowed_operations_catalog.json semantic checks passed"))
    return messages


def _constraint_ids(constraints_data: Any | None) -> set[str]:
    if not isinstance(constraints_data, dict):
        return set()
    constraints = constraints_data.get("constraints")
    if not isinstance(constraints, list):
        return set()
    return {
        item["id"]
        for item in constraints
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _read_json_member(
    package: zipfile.ZipFile,
    member: str,
    messages: list[ValidationMessage],
) -> Any | None:
    try:
        with package.open(member) as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        messages.append(ValidationMessage(Level.FAIL, f"{member} is invalid JSON: {exc.msg}"))
    return None

def _read_yaml_member(
    package: zipfile.ZipFile,
    member: str,
    messages: list[ValidationMessage],
) -> Any | None:
    try:
        with package.open(member) as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        messages.append(ValidationMessage(Level.FAIL, f"{member} is invalid YAML: {exc}"))
    return None


def _validate_text_resource(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    try:
        text = package.read(member).decode("utf-8")
    except UnicodeDecodeError as exc:
        return [ValidationMessage(Level.FAIL, f"{member} is not valid UTF-8 text: {exc}")]
    if text.strip():
        return [ValidationMessage(Level.PASS, f"{member} is non-empty text")]
    return [ValidationMessage(Level.FAIL, f"{member} is empty")]


def _validate_manifest(manifest: dict[str, Any]) -> list[ValidationMessage]:
    messages = list(_validate_against_schema("manifest.json", manifest, "manifest.schema.json"))

    format_version = manifest.get("format_version")
    if format_version == FORMAT_VERSION:
        messages.append(ValidationMessage(Level.PASS, f"format_version = {FORMAT_VERSION}"))
    else:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"unsupported format_version {format_version!r}; expected {FORMAT_VERSION}",
            )
        )

    units = manifest.get("units")
    if isinstance(units, dict) and all(units.get(key) for key in ("length", "mass", "force", "stress")):
        messages.append(ValidationMessage(Level.PASS, "units are present"))
    else:
        messages.append(ValidationMessage(Level.FAIL, "units are missing or incomplete"))

    return messages


def _validate_against_schema(member: str, data: Any, schema_name: str) -> list[ValidationMessage]:
    schema = _schema_document(schema_name)
    if schema is None:
        return [ValidationMessage(Level.FAIL, f"schema {schema_name} missing")]

    if Draft202012Validator is None:
        return _fallback_schema_check(member, data)

    validator = _schema_validator(schema_name)
    if validator is None:
        return [ValidationMessage(Level.FAIL, f"schema {schema_name} missing")]

    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if not errors:
        return [ValidationMessage(Level.PASS, f"{member} conforms to {schema_name}")]
    return [
        ValidationMessage(Level.FAIL, f"{member} schema error at {_error_path(error.path)}: {error.message}")
        for error in errors
    ]


def _validate_topology_map_semantics(data: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, "geometry/topology_map.json must be a JSON object")]

    entities = data.get("entities")
    if not isinstance(entities, list):
        return [ValidationMessage(Level.FAIL, "topology_map entities must be an array")]

    ids: list[str] = []
    for index, entity in enumerate(entities):
        if isinstance(entity, dict) and isinstance(entity.get("id"), str):
            ids.append(entity["id"])
        else:
            messages.append(ValidationMessage(Level.FAIL, f"topology entity at index {index} missing string id"))

    duplicates = _duplicate_values(ids)
    if duplicates:
        messages.append(ValidationMessage(Level.FAIL, f"topology entity IDs are not unique: {', '.join(duplicates)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, "topology entity IDs are unique"))

    known_ids = set(ids)
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            messages.append(ValidationMessage(Level.FAIL, f"topology entity at index {index} must be an object"))
            continue
        entity_id = entity.get("id", f"index {index}")
        messages.extend(_validate_topology_entity_required_fields(entity_id, entity))
        adjacent_ids = entity.get("adjacent_entity_ids", [])
        if isinstance(adjacent_ids, list):
            unknown = sorted(ref for ref in adjacent_ids if isinstance(ref, str) and ref not in known_ids)
            if unknown:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"topology entity {entity_id} references unknown adjacent IDs: {', '.join(unknown)}",
                    )
                )

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        extraction_backend = metadata.get("extraction_backend")
        if extraction_backend is not None and not isinstance(extraction_backend, str):
            messages.append(ValidationMessage(Level.WARN, "geometry/topology_map.json metadata.extraction_backend should be a string"))
        real_step_parsing = metadata.get("real_step_parsing")
        if real_step_parsing is not None and not isinstance(real_step_parsing, bool):
            messages.append(ValidationMessage(Level.WARN, "geometry/topology_map.json metadata.real_step_parsing should be a boolean"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "topology entity type-specific fields are present"))
    return messages


def _validate_topology_entity_required_fields(entity_id: Any, entity: dict[str, Any]) -> list[ValidationMessage]:
    entity_type = entity.get("type")
    required_by_type: dict[str, tuple[str, ...]] = {
        "solid": ("bounding_box",),
        "shell": ("bounding_box",),
        "face": ("surface_type", "bounding_box", "area"),
        "edge": ("bounding_box",),
        "wire": ("bounding_box",),
        "vertex": ("bounding_box",),
    }
    required = required_by_type.get(entity_type, ())
    messages = [
        ValidationMessage(Level.FAIL, f"topology entity {entity_id} missing required field {field}")
        for field in required
        if field not in entity
    ]

    surface_type = entity.get("surface_type")
    if entity_type == "face" and surface_type == "plane" and "normal" not in entity:
        messages.append(ValidationMessage(Level.FAIL, f"topology plane face {entity_id} missing normal"))
    if entity_type == "face" and surface_type == "cylinder":
        for field in ("radius", "axis"):
            if field not in entity:
                messages.append(ValidationMessage(Level.FAIL, f"topology cylindrical face {entity_id} missing {field}"))

    return messages


def _validate_feature_graph_semantics(
    feature_graph: Any,
    topology_map: Any | None,
    *,
    definition_sourced: bool = False,
    converter_sourced: bool = False,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(feature_graph, dict):
        return [ValidationMessage(Level.FAIL, "graph/feature_graph.json must be a JSON object")]

    features = feature_graph.get("features")
    if not isinstance(features, list):
        return [ValidationMessage(Level.FAIL, "feature_graph features must be an array")]

    feature_ids: list[str] = []
    for index, feature in enumerate(features):
        if isinstance(feature, dict) and isinstance(feature.get("id"), str):
            feature_ids.append(feature["id"])
        else:
            messages.append(ValidationMessage(Level.FAIL, f"feature at index {index} missing string id"))

    duplicate_ids = _duplicate_values(feature_ids)
    if duplicate_ids:
        messages.append(ValidationMessage(Level.FAIL, f"feature IDs are not unique: {', '.join(duplicate_ids)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, "feature IDs are unique"))

    topology_ids_by_type = _topology_ids_by_type(topology_map)
    if topology_ids_by_type is None:
        if definition_sourced:
            messages.append(
                ValidationMessage(
                    Level.WARN,
                    "feature graph has no topology map because package is definition-sourced",
                )
            )
        elif converter_sourced:
            messages.append(
                ValidationMessage(
                    Level.WARN,
                    "feature graph has no topology map because the package was produced by an offline converter; "
                    "run aieng extract-topology on a STEP export to reach L1",
                )
            )
        else:
            messages.append(ValidationMessage(Level.FAIL, "feature graph validation requires geometry/topology_map.json"))
        topology_ids_by_type = {"face": set(), "edge": set(), "all": set()}

    _VALID_PARAMETER_SOURCES = {
        "mock",
        "ocp_extracted",
        "user_provided",
        "agent_defined",
        "cadquery_parametric",
        "converter_extracted",
    }
    _VALID_EDITABILITY = {
        "not_editable",
        "semantic_only",
        "proposal_allowed",
        "executable_by_regeneration",
        "executable_by_direct_modeling",
    }
    _VALID_WRITEBACK_STRATEGIES = {
        "none",
        "semantic_parameter_update_only",
        "cadquery_regeneration",
        "direct_modeling",
    }

    known_feature_ids = set(feature_ids)
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            messages.append(ValidationMessage(Level.FAIL, f"feature at index {index} must be an object"))
            continue
        feature_id = feature.get("id", f"index {index}")
        messages.extend(_validate_feature_geometry_refs(feature_id, feature, topology_ids_by_type))
        messages.extend(_validate_feature_children(feature_id, feature, known_feature_ids))
        messages.extend(_validate_feature_relationships(feature_id, feature, known_feature_ids))

        param_source = feature.get("parameter_source")
        if param_source is not None and param_source not in _VALID_PARAMETER_SOURCES:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"feature {feature_id}: parameter_source '{param_source}' must be one of {sorted(_VALID_PARAMETER_SOURCES)}",
            ))

        editability = feature.get("editability")
        if editability is not None and editability not in _VALID_EDITABILITY:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"feature {feature_id}: editability '{editability}' must be one of {sorted(_VALID_EDITABILITY)}",
            ))

        writeback_strategy = feature.get("writeback_strategy")
        if writeback_strategy is not None and writeback_strategy not in _VALID_WRITEBACK_STRATEGIES:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"feature {feature_id}: writeback_strategy '{writeback_strategy}' must be one of {sorted(_VALID_WRITEBACK_STRATEGIES)}",
            ))

        if editability in {"executable_by_regeneration", "executable_by_direct_modeling"}:
            if param_source != "cadquery_parametric":
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"feature {feature_id}: executable editability requires parameter_source='cadquery_parametric'",
                ))
            if editability == "executable_by_regeneration" and writeback_strategy != "cadquery_regeneration":
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"feature {feature_id}: executable_by_regeneration requires writeback_strategy='cadquery_regeneration'",
                ))
            if editability == "executable_by_direct_modeling":
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"feature {feature_id}: executable_by_direct_modeling is reserved for future CAD-kernel-backed implementations",
                ))

        if param_source in {"mock", "ocp_extracted"} and writeback_strategy in {"cadquery_regeneration", "direct_modeling"}:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"feature {feature_id}: {param_source} parameters cannot declare CAD write-back strategy '{writeback_strategy}'",
            ))

        if feature.get("editable") is True:
            params = feature.get("parameters")
            if not isinstance(params, dict) or not params:
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"feature {feature_id}: editable=true requires a non-empty parameters object",
                ))

    if not any(message.level is Level.FAIL for message in messages):
        if definition_sourced and topology_map is None:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "feature graph definition-sourced geometry references are semantic-only",
                )
            )
        else:
            messages.append(ValidationMessage(Level.PASS, "feature geometry references resolve to topology IDs"))
        messages.append(ValidationMessage(Level.PASS, "feature child and relationship references resolve"))
    return messages


def _topology_ids_by_type(topology_map: Any | None) -> dict[str, set[str]] | None:
    if not isinstance(topology_map, dict) or not isinstance(topology_map.get("entities"), list):
        return None
    ids_by_type: dict[str, set[str]] = {"face": set(), "edge": set(), "all": set()}
    for entity in topology_map["entities"]:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str):
            continue
        entity_id = entity["id"]
        ids_by_type["all"].add(entity_id)
        entity_type = entity.get("type")
        if entity_type == "face":
            ids_by_type["face"].add(entity_id)
        elif entity_type == "edge":
            ids_by_type["edge"].add(entity_id)
    return ids_by_type


def _validate_feature_geometry_refs(
    feature_id: Any,
    feature: dict[str, Any],
    topology_ids_by_type: dict[str, set[str]],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    refs = feature.get("geometry_refs")
    if isinstance(refs, list):
        unknown = sorted(ref for ref in refs if isinstance(ref, str) and ref not in topology_ids_by_type["all"])
        if unknown:
            messages.append(
                ValidationMessage(Level.FAIL, f"feature {feature_id} references unknown topology IDs: {', '.join(unknown)}")
            )
        return messages

    if not isinstance(refs, dict):
        return [ValidationMessage(Level.FAIL, f"feature {feature_id} geometry_refs must be an object or array")]

    face_refs = refs.get("faces", [])
    if isinstance(face_refs, list):
        unknown_faces = sorted(ref for ref in face_refs if isinstance(ref, str) and ref not in topology_ids_by_type["face"])
        if unknown_faces:
            messages.append(
                ValidationMessage(Level.FAIL, f"feature {feature_id} references unknown faces: {', '.join(unknown_faces)}")
            )

    edge_refs = refs.get("edges", [])
    if isinstance(edge_refs, list):
        unknown_edges = sorted(ref for ref in edge_refs if isinstance(ref, str) and ref not in topology_ids_by_type["edge"])
        if unknown_edges:
            messages.append(
                ValidationMessage(Level.FAIL, f"feature {feature_id} references unknown edges: {', '.join(unknown_edges)}")
            )

    entity_refs = refs.get("entities", [])
    if isinstance(entity_refs, list):
        unknown_entities = sorted(ref for ref in entity_refs if isinstance(ref, str) and ref not in topology_ids_by_type["all"])
        if unknown_entities:
            messages.append(
                ValidationMessage(Level.FAIL, f"feature {feature_id} references unknown topology entities: {', '.join(unknown_entities)}")
            )

    return messages


def _validate_feature_children(
    feature_id: Any,
    feature: dict[str, Any],
    known_feature_ids: set[str],
) -> list[ValidationMessage]:
    children = feature.get("children", [])
    if not isinstance(children, list):
        return [ValidationMessage(Level.FAIL, f"feature {feature_id} children must be an array")]
    unknown = sorted(child for child in children if isinstance(child, str) and child not in known_feature_ids)
    if unknown:
        return [ValidationMessage(Level.FAIL, f"feature {feature_id} references unknown children: {', '.join(unknown)}")]
    return []


def _validate_feature_relationships(
    feature_id: Any,
    feature: dict[str, Any],
    known_feature_ids: set[str],
) -> list[ValidationMessage]:
    relationships = feature.get("relationships", [])
    if not isinstance(relationships, list):
        return [ValidationMessage(Level.FAIL, f"feature {feature_id} relationships must be an array")]

    messages: list[ValidationMessage] = []
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            messages.append(ValidationMessage(Level.FAIL, f"feature {feature_id} relationship at index {index} must be an object"))
            continue
        for key in ("source", "target", "source_feature_id", "target_feature_id", "feature_id"):
            value = relationship.get(key)
            if isinstance(value, str) and value not in known_feature_ids:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"feature {feature_id} relationship at index {index} references unknown feature {value}",
                    )
                )
    return messages


def _validate_constraints_semantics(
    constraints_data: Any,
    feature_graph: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        return [ValidationMessage(Level.FAIL, "constraints validation requires graph/feature_graph.json")]
    constraints = constraints_data.get("constraints") if isinstance(constraints_data, dict) else None
    if not isinstance(constraints, list):
        return [ValidationMessage(Level.FAIL, "constraints must be an array")]
    for constraint in constraints:
        if not isinstance(constraint, dict):
            messages.append(ValidationMessage(Level.FAIL, "constraint must be an object"))
            continue
        target = constraint.get("target")
        if constraint.get("type") != "simulation_target" and isinstance(target, str) and target not in feature_ids:
            messages.append(ValidationMessage(Level.FAIL, f"constraint references unknown feature {target}"))
    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "constraints reference known features"))
    return messages


def _validate_simulation_setup_semantics(
    simulation_setup: Any,
    feature_graph: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(simulation_setup, dict):
        return [ValidationMessage(Level.FAIL, "simulation/setup.yaml must be a mapping")]
    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        return [ValidationMessage(Level.FAIL, "simulation setup validation requires graph/feature_graph.json")]

    materials = simulation_setup.get("materials")
    if not isinstance(materials, dict) or not materials:
        messages.append(ValidationMessage(Level.FAIL, "simulation setup materials missing"))
    else:
        for material_name, material in materials.items():
            if not isinstance(material, dict):
                messages.append(ValidationMessage(Level.FAIL, f"material {material_name} properties must be a mapping"))
                continue
            for key in ("youngs_modulus_mpa", "poisson_ratio", "density_kg_m3", "yield_strength_mpa"):
                if not isinstance(material.get(key), NUMERIC_TYPES):
                    messages.append(ValidationMessage(Level.FAIL, f"material {material_name} missing numeric {key}"))

    for bc in simulation_setup.get("boundary_conditions", []) or []:
        if isinstance(bc, dict):
            target = bc.get("target_feature")
            if isinstance(target, str) and target not in feature_ids:
                messages.append(ValidationMessage(Level.FAIL, f"boundary condition references unknown feature {target}"))

    for load in simulation_setup.get("loads", []) or []:
        if not isinstance(load, dict):
            messages.append(ValidationMessage(Level.FAIL, "load must be a mapping"))
            continue
        target = load.get("target_feature")
        if isinstance(target, str) and target not in feature_ids:
            messages.append(ValidationMessage(Level.FAIL, f"load references unknown feature {target}"))
        direction = load.get("direction")
        if not (
            isinstance(direction, list)
            and len(direction) == 3
            and all(isinstance(component, NUMERIC_TYPES) for component in direction)
        ):
            messages.append(ValidationMessage(Level.FAIL, f"load {load.get('id', '')} direction must be a length-3 numeric array"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "simulation setup references known features and material properties"))
    return messages


def _validate_protected_regions_semantics(
    protected_regions_data: Any,
    feature_graph: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        return [ValidationMessage(Level.FAIL, "protected regions validation requires graph/feature_graph.json")]
    regions = protected_regions_data.get("protected_regions") if isinstance(protected_regions_data, dict) else None
    if not isinstance(regions, list):
        return [ValidationMessage(Level.FAIL, "protected_regions must be an array")]
    for region in regions:
        if not isinstance(region, dict):
            messages.append(ValidationMessage(Level.FAIL, "protected region must be an object"))
            continue
        feature_id = region.get("feature_id")
        if isinstance(feature_id, str) and feature_id not in feature_ids:
            messages.append(ValidationMessage(Level.FAIL, f"protected region references unknown feature {feature_id}"))
    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "protected regions reference known features"))
    return messages


def _validate_patch_proposal_semantics(
    patch_member: str,
    patch: Any,
    feature_graph: Any | None,
    constraints_data: Any | None,
    protected_regions_data: Any | None,
    allowed_operations_catalog_data: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(patch, dict):
        return [ValidationMessage(Level.FAIL, f"{patch_member} must be a JSON object")]
    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        return [ValidationMessage(Level.FAIL, f"{patch_member} validation requires graph/feature_graph.json")]

    protected_ids = _protected_feature_ids_from_resources(constraints_data, protected_regions_data) & feature_ids
    status = patch.get("status")

    for field_name in ("protected_targets_checked", "protected_targets_avoided"):
        values = patch.get(field_name, [])
        if not isinstance(values, list):
            messages.append(ValidationMessage(Level.FAIL, f"{patch_member} {field_name} must be an array"))
            continue
        unknown = sorted(value for value in values if isinstance(value, str) and value not in feature_ids)
        if unknown:
            messages.append(ValidationMessage(Level.FAIL, f"{patch_member} {field_name} references unknown features: {', '.join(unknown)}"))

    operations = patch.get("operations", [])
    catalog_policies = _catalog_operation_policies(allowed_operations_catalog_data)
    if not isinstance(operations, list):
        messages.append(ValidationMessage(Level.FAIL, f"{patch_member} operations must be an array"))
    else:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                messages.append(ValidationMessage(Level.FAIL, f"{patch_member} operation {index} must be an object"))
                continue
            op_name = operation.get("op", operation.get("type"))
            target = operation.get("target", operation.get("target_feature_id"))
            if isinstance(target, str) and target not in feature_ids:
                messages.append(ValidationMessage(Level.FAIL, f"{patch_member} operation {index} references unknown feature {target}"))
            if (
                isinstance(op_name, str)
                and op_name in GEOMETRY_CHANGING_PATCH_OPS
                and isinstance(target, str)
                and target in protected_ids
                and status not in {"violation", "violates_protected_target", "needs_review"}
            ):
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"{patch_member} geometry-changing operation {index} targets protected feature {target}",
                    )
                )

            if isinstance(op_name, str) and isinstance(target, str) and target in catalog_policies:
                op_policy = catalog_policies[target].get(op_name)
                if op_policy is None:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"{patch_member} operation {index} uses {op_name} on {target}, but allowed_operations_catalog has no policy entry",
                        )
                    )
                    continue

                policy_status = op_policy.get("status")
                if (
                    policy_status == "forbidden"
                    and status not in {"needs_review", "violation", "violates_protected_target"}
                ):
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"{patch_member} operation {index} conflicts with allowed_operations_catalog forbidden policy for {target}:{op_name}",
                        )
                    )

                if policy_status == "conditional":
                    parameters = operation.get("parameters")
                    has_preconditions = (
                        isinstance(parameters, dict)
                        and isinstance(parameters.get("policy_preconditions"), list)
                        and len(parameters.get("policy_preconditions")) > 0
                    )
                    if not has_preconditions:
                        messages.append(
                            ValidationMessage(
                                Level.WARN,
                                f"{patch_member} operation {index} is conditional by allowed_operations_catalog but policy_preconditions are missing",
                            )
                        )

    if patch.get("no_geometry_modified") is not True:
        execution_record = patch.get("execution_record")
        step_output = execution_record.get("step_output") if isinstance(execution_record, dict) else None
        if patch.get("status") == "applied" and isinstance(step_output, str) and step_output:
            messages.append(ValidationMessage(
                Level.WARN,
                f"{patch_member} records geometry write-back to {step_output}; round-trip validation evidence is required",
            ))
        else:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"{patch_member} no_geometry_modified=false requires applied status and execution_record.step_output",
            ))
    if patch.get("no_solver_run") is not True:
        messages.append(ValidationMessage(Level.FAIL, f"{patch_member} no_solver_run must be true for Phase 5B"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{patch_member} references known features and respects protected targets"))
    return messages


def _protected_feature_ids_from_resources(constraints_data: Any | None, protected_regions_data: Any | None) -> set[str]:
    protected: set[str] = set()
    if isinstance(protected_regions_data, dict) and isinstance(protected_regions_data.get("protected_regions"), list):
        for region in protected_regions_data["protected_regions"]:
            if isinstance(region, dict) and isinstance(region.get("feature_id"), str):
                protected.add(region["feature_id"])
    if isinstance(constraints_data, dict) and isinstance(constraints_data.get("constraints"), list):
        for constraint in constraints_data["constraints"]:
            if not isinstance(constraint, dict) or constraint.get("type") not in PROTECTED_CONSTRAINT_TYPES:
                continue
            target = constraint.get("target")
            if isinstance(target, str):
                protected.add(target)
    return protected


def _feature_ids_from_graph(feature_graph: Any | None) -> set[str] | None:
    if not isinstance(feature_graph, dict) or not isinstance(feature_graph.get("features"), list):
        return None
    return {
        feature["id"]
        for feature in feature_graph["features"]
        if isinstance(feature, dict) and isinstance(feature.get("id"), str)
    }


def _catalog_operation_policies(catalog: Any | None) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(catalog, dict):
        return result
    entries = catalog.get("feature_operations")
    if not isinstance(entries, list):
        return result

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        feature_id = entry.get("feature_id")
        operations = entry.get("operations")
        if not isinstance(feature_id, str) or not isinstance(operations, list):
            continue
        per_feature: dict[str, dict[str, Any]] = {}
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            op_type = operation.get("operation_type")
            if isinstance(op_type, str):
                per_feature[op_type] = operation
        result[feature_id] = per_feature

    return result


def _validate_solver_deck(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    try:
        text = package.read(member).decode("utf-8")
    except UnicodeDecodeError as exc:
        return [ValidationMessage(Level.FAIL, f"{member} is not valid UTF-8 text: {exc}")]

    if not text.strip():
        return [ValidationMessage(Level.FAIL, f"{member} is empty")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is non-empty text")]

    scaffold_marker = "This is not a complete runnable FEA model."
    if scaffold_marker in text:
        messages.append(ValidationMessage(Level.PASS, f"{member} contains scaffold warning"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} is missing scaffold warning"))

    # Flag decks that positively assert solver completion without also carrying the scaffold warning.
    # The scaffold warning itself already conveys "no solver has run", so only flag the absence
    # of that warning combined with an affirmative solver-result claim.
    affirmative_solver_claims = ("analysis complete", "solver run completed", "END RESULTS")
    if scaffold_marker not in text and any(claim in text for claim in affirmative_solver_claims):
        messages.append(ValidationMessage(Level.FAIL, f"{member} contains unsupported solver claim without scaffold warning"))
    else:
        messages.append(ValidationMessage(Level.PASS, f"{member} does not claim a solver has run"))

    return messages


_EXT_TOOL_KNOWN_TOOL_ROLES = frozenset({"agent_runtime", "cad_runtime", "cae_runtime", "cae_preprocessor", "solver"})
_EXT_TOOL_KNOWN_STATUSES = frozenset({"candidate", "active", "unavailable"})
_EXT_TOOL_HANDOFF_POLICY_TRUE_FLAGS = (
    "bounded_steps_only",
    "inspect_before_execution",
    "reinspect_after_external_change",
    "record_artifacts",
    "record_tool_trace",
    "external_tools_execute",
)

_TASK_RECOGNIZED_MODES = frozenset({"proposal_only", "analysis_ready", "execution_ready"})
_TASK_RECOGNIZED_OUTPUTS = frozenset({"patch_proposal", "updated_deck", "validation_report", "summary"})
_TASK_CLAIM_POLICY_FLAGS = ("no_solver_run_claim", "no_mesh_generation_claim", "no_geometry_modification_claim", "external_tools_execute")


def _validate_external_tool_requirements(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    data = _read_json_member(package, member, [])
    if data is None:
        return [ValidationMessage(Level.FAIL, f"{member} is invalid JSON")]
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is valid JSON")]

    messages.extend(_validate_against_schema(member, data, "external_tool_requirements.schema.json"))

    handoff_policy = data.get("handoff_policy")
    if isinstance(handoff_policy, dict):
        bad_true = [f for f in _EXT_TOOL_HANDOFF_POLICY_TRUE_FLAGS if handoff_policy.get(f) is not True]
        if bad_true:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"{member} handoff_policy must set {bad_true} to true",
            ))
        else:
            messages.append(ValidationMessage(
                Level.PASS,
                f"{member} handoff_policy correctly sets execution-boundary true flags",
            ))
        if handoff_policy.get("aieng_core_executes_external_tools") is not False:
            messages.append(ValidationMessage(
                Level.FAIL,
                f"{member} handoff_policy.aieng_core_executes_external_tools must be false; "
                ".aieng core does not invoke CAD kernels, meshers, solvers, or manufacturing checkers",
            ))
        else:
            messages.append(ValidationMessage(
                Level.PASS,
                f"{member} handoff_policy.aieng_core_executes_external_tools is false",
            ))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} handoff_policy must be an object"))

    required_capabilities = data.get("required_capabilities")
    if isinstance(required_capabilities, list):
        for index, cap in enumerate(required_capabilities):
            if not isinstance(cap, dict):
                messages.append(ValidationMessage(Level.FAIL, f"{member} required_capability at index {index} must be an object"))
                continue
            tool_role = cap.get("tool_role")
            if isinstance(tool_role, str) and tool_role not in _EXT_TOOL_KNOWN_TOOL_ROLES:
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"{member} required_capability at index {index} has unrecognized tool_role '{tool_role}'",
                ))

    candidate_tools = data.get("candidate_tools")
    if isinstance(candidate_tools, list):
        for index, tool in enumerate(candidate_tools):
            if not isinstance(tool, dict):
                messages.append(ValidationMessage(Level.FAIL, f"{member} candidate_tool at index {index} must be an object"))
                continue
            tool_role = tool.get("tool_role")
            if isinstance(tool_role, str) and tool_role not in _EXT_TOOL_KNOWN_TOOL_ROLES:
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"{member} candidate_tool at index {index} has unrecognized tool_role '{tool_role}'",
                ))
            status = tool.get("status")
            if isinstance(status, str) and status not in _EXT_TOOL_KNOWN_STATUSES:
                messages.append(ValidationMessage(
                    Level.FAIL,
                    f"{member} candidate_tool at index {index} has unrecognized status '{status}'",
                ))

    forbidden_core_actions = data.get("forbidden_core_actions")
    if isinstance(forbidden_core_actions, list) and forbidden_core_actions:
        messages.append(ValidationMessage(Level.PASS, f"{member} forbidden_core_actions is non-empty"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} forbidden_core_actions must be a non-empty list"))

    writeback_requirements = data.get("writeback_requirements")
    if isinstance(writeback_requirements, list) and writeback_requirements:
        messages.append(ValidationMessage(Level.PASS, f"{member} writeback_requirements is non-empty"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} writeback_requirements must be a non-empty list"))

    source_task_id = data.get("source_task_id")
    if isinstance(source_task_id, str):
        task_spec_names: set[str] = set()
        try:
            task_spec_names = set(package.namelist())
        except Exception:
            pass
        if "task/task_spec.yaml" not in task_spec_names:
            messages.append(ValidationMessage(
                Level.WARN,
                f"{member} source_task_id '{source_task_id}' is set but task/task_spec.yaml is not present in this package",
            ))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_task_spec(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    data = _read_yaml_member(package, member, [])
    if data is None:
        return [ValidationMessage(Level.FAIL, f"{member} is invalid YAML")]
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a YAML mapping")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is valid YAML")]

    messages.extend(_validate_against_schema(member, data, "task_spec.schema.json"))

    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in _TASK_RECOGNIZED_MODES:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"{member} mode '{mode}' is not a recognized task mode; "
                f"expected one of: {sorted(_TASK_RECOGNIZED_MODES)}",
            )
        )
    elif mode != "proposal_only":
        messages.append(
            ValidationMessage(
                Level.WARN,
                f"{member} mode '{mode}' implies external tool execution; "
                "ensure evidence_required_before_acceptance is populated",
            )
        )
    else:
        messages.append(
            ValidationMessage(Level.PASS, f"{member} mode 'proposal_only' is the conservative default")
        )

    required_outputs = data.get("required_outputs")
    if isinstance(required_outputs, list):
        unknown = sorted(
            o for o in required_outputs
            if isinstance(o, str) and o not in _TASK_RECOGNIZED_OUTPUTS
        )
        if unknown:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} required_outputs contains unrecognized values: {', '.join(unknown)}",
                )
            )
        else:
            messages.append(
                ValidationMessage(Level.PASS, f"{member} required_outputs are all recognized")
            )

    forbidden_claims = data.get("forbidden_claims")
    if isinstance(forbidden_claims, list) and forbidden_claims:
        messages.append(
            ValidationMessage(Level.PASS, f"{member} forbidden_claims is non-empty")
        )
    else:
        messages.append(
            ValidationMessage(Level.FAIL, f"{member} forbidden_claims must be a non-empty list")
        )

    claim_policy = data.get("claim_policy")
    if isinstance(claim_policy, dict):
        bad_flags = [f for f in _TASK_CLAIM_POLICY_FLAGS if claim_policy.get(f) is not True]
        if bad_flags:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} claim_policy must set {bad_flags} to true; "
                    ".aieng does not run solvers, generate meshes, or directly modify CAD geometry",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    f"{member} claim_policy correctly declares no solver, mesh, or geometry-modification claims",
                )
            )
    else:
        messages.append(
            ValidationMessage(Level.FAIL, f"{member} claim_policy must be a mapping")
        )

    return messages


def _validate_engineering_material(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    data = _read_yaml_member(package, member, [])
    if data is None:
        return [ValidationMessage(Level.FAIL, f"{member} is invalid YAML")]
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a YAML mapping")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is valid YAML")]
    if isinstance(data.get("name"), str) and data["name"].strip():
        messages.append(ValidationMessage(Level.PASS, f"{member} material name is present"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} material name is missing"))

    properties = data.get("properties")
    if isinstance(properties, dict) and properties:
        messages.append(ValidationMessage(Level.PASS, f"{member} material properties are present"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} material properties are missing"))

    if data.get("source_mode") == "definition":
        messages.append(ValidationMessage(Level.PASS, f"{member} records definition source mode"))
    return messages


def _validate_validation_status(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    data = _read_yaml_member(package, member, [])
    if data is None:
        return [ValidationMessage(Level.FAIL, f"{member} is invalid YAML")]
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a YAML mapping")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is valid YAML")]

    required_sections = (
        "package_validation",
        "geometry_status",
        "topology_status",
        "feature_status",
        "engineering_context_status",
        "solver_mesh_status",
        "patch_status",
        "claim_policy",
    )
    missing = [s for s in required_sections if s not in data]
    if missing:
        messages.append(ValidationMessage(Level.FAIL, f"{member} missing sections: {', '.join(missing)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, f"{member} contains all required sections"))

    geometry_status = data.get("geometry_status")
    if isinstance(geometry_status, dict):
        if geometry_status.get("definition_sourced") is True and geometry_status.get("step_imported") is False:
            messages.append(ValidationMessage(Level.PASS, f"{member} records definition-sourced geometry status"))
        if geometry_status.get("definition_sourced") is True and geometry_status.get("source_geometry_present") is True:
            messages.append(ValidationMessage(Level.FAIL, f"{member} definition-sourced status must not claim source geometry"))
        if geometry_status.get("definition_sourced") is True and geometry_status.get("real_geometry_validity") not in {None, "not_run"}:
            messages.append(ValidationMessage(Level.FAIL, f"{member} definition-sourced status must not claim geometry validity"))

    claim_policy = data.get("claim_policy")
    if isinstance(claim_policy, dict):
        forbidden = claim_policy.get("forbidden_claims", [])
        allowed = claim_policy.get("allowed_claims", [])
        if isinstance(forbidden, list) and forbidden:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy.forbidden_claims is present"))
        else:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy.forbidden_claims must be a non-empty list"))
        if isinstance(allowed, list) and allowed:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy.allowed_claims is present"))
        else:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy.allowed_claims must be a non-empty list"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be a mapping"))

    solver_mesh = data.get("solver_mesh_status")
    if isinstance(solver_mesh, dict):
        false_claims = []
        for field, forbidden_value in (
            ("mesh_generation", "done"),
            ("solver_execution", "done"),
            ("stress_validation", "validated"),
        ):
            if solver_mesh.get(field) == forbidden_value:
                false_claims.append(field)
        if false_claims:
            messages.append(
                ValidationMessage(Level.FAIL, f"{member} solver_mesh_status makes unsupported claims: {', '.join(false_claims)}")
            )
        else:
            messages.append(ValidationMessage(Level.PASS, f"{member} solver_mesh_status does not claim solver execution"))

    patch_status = data.get("patch_status")
    if isinstance(patch_status, dict):
        if patch_status.get("geometry_modified_by_patch") is True:
            messages.append(ValidationMessage(
                Level.WARN,
                f"{member} records geometry_modified_by_patch=true; round-trip validation evidence is required",
            ))
        elif patch_status.get("geometry_modified_by_patch") is False:
            messages.append(ValidationMessage(Level.PASS, f"{member} patch_status correctly records no geometry modification"))
        if patch_status.get("solver_run_for_patch") is True:
            messages.append(ValidationMessage(Level.FAIL, f"{member} patch_status.solver_run_for_patch must not be true"))

    return messages


def _validate_annotation_layers_semantics(
    annotation_layers: Any,
    feature_graph: Any | None,
    topology_map: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(annotation_layers, dict):
        return [ValidationMessage(Level.FAIL, "visual/annotation_layers.json must be a JSON object")]

    layers = annotation_layers.get("layers")
    if not isinstance(layers, list):
        return [ValidationMessage(Level.FAIL, "visual/annotation_layers.json layers must be an array")]

    # Check layer IDs are unique
    layer_ids: list[str] = []
    for index, layer in enumerate(layers):
        if isinstance(layer, dict) and isinstance(layer.get("id"), str):
            layer_ids.append(layer["id"])
        else:
            messages.append(ValidationMessage(Level.FAIL, f"annotation layer at index {index} missing string id"))
    duplicates = _duplicate_values(layer_ids)
    if duplicates:
        messages.append(ValidationMessage(Level.FAIL, f"annotation layer IDs are not unique: {', '.join(duplicates)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, "annotation layer IDs are unique"))

    # Collect all item IDs to check uniqueness across layers
    all_item_ids: list[str] = []
    feature_ids = _feature_ids_from_graph(feature_graph) or set()
    topology_ids_by_type = _topology_ids_by_type(topology_map) or {"face": set(), "edge": set(), "all": set()}
    has_topology = topology_map is not None

    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layer_id = layer.get("id", "unknown")
        items = layer.get("items", [])
        if not isinstance(items, list):
            messages.append(ValidationMessage(Level.FAIL, f"annotation layer {layer_id} items must be an array"))
            continue
        for item in items:
            if not isinstance(item, dict):
                messages.append(ValidationMessage(Level.FAIL, f"annotation layer {layer_id} contains non-object item"))
                continue
            item_id = item.get("id", "")
            if isinstance(item_id, str) and item_id:
                all_item_ids.append(item_id)

            # feature_id reference check
            feature_id = item.get("feature_id")
            if isinstance(feature_id, str) and feature_ids and feature_id not in feature_ids:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"annotation item {item_id!r} in layer {layer_id!r} references unknown feature_id {feature_id!r}",
                    )
                )

            # topology_refs checks (only when topology_map is present)
            if has_topology:
                refs = item.get("topology_refs", {})
                if isinstance(refs, dict):
                    for face_id in refs.get("faces", []):
                        if isinstance(face_id, str) and face_id not in topology_ids_by_type["face"]:
                            messages.append(
                                ValidationMessage(
                                    Level.FAIL,
                                    f"annotation item {item_id!r} in layer {layer_id!r} references unknown face {face_id!r}",
                                )
                            )
                    for edge_id in refs.get("edges", []):
                        if isinstance(edge_id, str) and edge_id not in topology_ids_by_type["edge"]:
                            messages.append(
                                ValidationMessage(
                                    Level.FAIL,
                                    f"annotation item {item_id!r} in layer {layer_id!r} references unknown edge {edge_id!r}",
                                )
                            )

    # Check item IDs are unique across all layers
    dup_item_ids = _duplicate_values(all_item_ids)
    if dup_item_ids:
        messages.append(
            ValidationMessage(Level.FAIL, f"annotation item IDs are not unique: {', '.join(dup_item_ids)}")
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "annotation item IDs are unique"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "visual/annotation_layers.json references resolve"))
    return messages


def _validate_visual_model_manifest_semantics(
    model_manifest: Any,
    names: set[str],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(model_manifest, dict):
        return [ValidationMessage(Level.FAIL, "visual/model_manifest.json must be a JSON object")]

    visual_resources = model_manifest.get("visual_resources")
    if not isinstance(visual_resources, dict):
        return [ValidationMessage(Level.FAIL, "visual/model_manifest.json visual_resources must be an object")]

    for resource_name, resource in visual_resources.items():
        if not isinstance(resource, dict):
            messages.append(
                ValidationMessage(Level.FAIL, f"visual/model_manifest.json visual_resources.{resource_name} must be an object")
            )
            continue
        status = resource.get("status")
        resource_path = resource.get("path")
        if status == "present" and isinstance(resource_path, str):
            if resource_path.endswith("/"):
                has_dir = resource_path in names or any(name.startswith(resource_path) for name in names)
                if not has_dir:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"visual/model_manifest.json marks {resource_name} as present but {resource_path} is missing",
                        )
                    )
            elif resource_path not in names:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"visual/model_manifest.json marks {resource_name} as present but {resource_path} is missing",
                    )
                )

    rendering_status = model_manifest.get("rendering_status")
    if isinstance(rendering_status, dict):
        if rendering_status.get("rendered_geometry_present") is not False:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "visual/model_manifest.json rendering_status.rendered_geometry_present must be false in Phase 8B",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "visual/model_manifest.json rendering_status.rendered_geometry_present is false",
                )
            )

        if rendering_status.get("viewer_ready") is not False:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "visual/model_manifest.json rendering_status.viewer_ready must be false in Phase 8B",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "visual/model_manifest.json rendering_status.viewer_ready is false",
                )
            )
    else:
        messages.append(ValidationMessage(Level.FAIL, "visual/model_manifest.json rendering_status must be an object"))

    claim_policy = model_manifest.get("claim_policy")
    if isinstance(claim_policy, dict) and isinstance(claim_policy.get("forbidden_claims"), list):
        forbidden_claims = [str(item).lower() for item in claim_policy["forbidden_claims"]]
        has_rendered_model_forbidden = any("rendered 3d model" in claim for claim in forbidden_claims)
        has_model_glb_forbidden = any("model.glb" in claim for claim in forbidden_claims)
        if not has_rendered_model_forbidden or not has_model_glb_forbidden:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "visual/model_manifest.json claim_policy.forbidden_claims must forbid rendered 3D model and model.glb claims",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "visual/model_manifest.json claim_policy.forbidden_claims blocks rendered geometry claims",
                )
            )
    else:
        messages.append(ValidationMessage(Level.FAIL, "visual/model_manifest.json claim_policy.forbidden_claims must be a list"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "visual/model_manifest.json semantic checks passed"))
    return messages


def _validate_object_registry_semantics(
    object_registry: Any,
    names: set[str],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(object_registry, dict):
        return [ValidationMessage(Level.FAIL, "objects/object_registry.json must be a JSON object")]

    objects = object_registry.get("objects")
    if not isinstance(objects, list):
        return [ValidationMessage(Level.FAIL, "objects/object_registry.json objects must be an array")]

    relationships = object_registry.get("relationships")
    if not isinstance(relationships, list):
        return [ValidationMessage(Level.FAIL, "objects/object_registry.json relationships must be an array")]

    object_ids: list[str] = []
    object_ids_set: set[str] = set()

    for index, obj in enumerate(objects):
        if not isinstance(obj, dict):
            messages.append(ValidationMessage(Level.FAIL, f"object at index {index} must be an object"))
            continue
        object_id = obj.get("id")
        if not isinstance(object_id, str) or not object_id:
            messages.append(ValidationMessage(Level.FAIL, f"object at index {index} missing string id"))
            continue

        object_ids.append(object_id)
        object_ids_set.add(object_id)

        defined_in = obj.get("defined_in")
        if isinstance(defined_in, str) and defined_in and defined_in not in names:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"objects/object_registry.json object {object_id!r} defined_in file missing: {defined_in}",
                )
            )

        referenced_by = obj.get("referenced_by")
        if isinstance(referenced_by, list):
            for ref_file in referenced_by:
                if isinstance(ref_file, str) and ref_file and ref_file not in names:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"objects/object_registry.json object {object_id!r} references missing file in referenced_by: {ref_file}",
                        )
                    )

    duplicate_object_ids = _duplicate_values(object_ids)
    if duplicate_object_ids:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"objects/object_registry.json object IDs are not unique: {', '.join(duplicate_object_ids)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "objects/object_registry.json object IDs are unique"))

    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            messages.append(ValidationMessage(Level.FAIL, f"relationship at index {index} must be an object"))
            continue

        from_id = relationship.get("from")
        to_id = relationship.get("to")
        source_file = relationship.get("source_file")

        if isinstance(from_id, str) and from_id not in object_ids_set:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"objects/object_registry.json relationship endpoint 'from' unknown and not unresolved: {from_id}",
                )
            )
        if isinstance(to_id, str) and to_id not in object_ids_set:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"objects/object_registry.json relationship endpoint 'to' unknown and not unresolved: {to_id}",
                )
            )
        if isinstance(source_file, str) and source_file not in names:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"objects/object_registry.json relationship source_file missing: {source_file}",
                )
            )

    notes = object_registry.get("notes")
    if isinstance(notes, list) and any(
        isinstance(note, str) and "source of truth" in note.lower() and "not" in note.lower()
        for note in notes
    ):
        messages.append(
            ValidationMessage(
                Level.PASS,
                "objects/object_registry.json notes state that the registry is not the source of truth",
            )
        )
    else:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                "objects/object_registry.json notes must state that the registry is not the source of truth",
            )
        )

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "objects/object_registry.json semantic checks passed"))
    return messages


def _validate_interface_graph_semantics(
    interface_graph: Any,
    feature_graph: Any | None,
    topology_map: Any | None,
    constraints_data: Any | None,
    simulation_setup_data: Any | None,
    annotation_layers_data: Any | None,
    protected_regions_data: Any | None,
    cae_mapping_data: Any | None,
    names: set[str],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(interface_graph, dict):
        return [ValidationMessage(Level.FAIL, "objects/interface_graph.json must be a JSON object")]

    interfaces = interface_graph.get("interfaces")
    if not isinstance(interfaces, list):
        return [ValidationMessage(Level.FAIL, "objects/interface_graph.json interfaces must be an array")]

    interface_ids: list[str] = []
    feature_ids = _feature_ids_from_graph(feature_graph)
    if feature_ids is None:
        messages.append(ValidationMessage(Level.FAIL, "interface graph validation requires graph/feature_graph.json"))
        feature_ids = set()

    topology_ids = _topology_ids_by_type(topology_map)
    constraint_ids = _constraint_ids(constraints_data)
    simulation_ids = _simulation_reference_ids(simulation_setup_data)
    visual_ids = _annotation_item_ids(annotation_layers_data)
    protected_feature_ids = _protected_region_feature_ids(protected_regions_data)
    cae_entities = _cae_mapping_entities(cae_mapping_data)
    valid_cae_statuses = {"mapped", "unmapped", "partially_mapped", "unresolved"}
    valid_cae_methods = _cae_mapping_enum(*_MAPPING_ITEM, "mapping_method", "enum")
    valid_cae_confidence = {"none", "low", "medium", "high"}

    for index, interface in enumerate(interfaces):
        if not isinstance(interface, dict):
            messages.append(ValidationMessage(Level.FAIL, f"interface at index {index} must be an object"))
            continue

        interface_id = interface.get("id")
        if not isinstance(interface_id, str) or not interface_id:
            messages.append(ValidationMessage(Level.FAIL, f"interface at index {index} missing string id"))
            continue
        interface_ids.append(interface_id)

        member_feature_ids = interface.get("feature_ids", [])
        if isinstance(member_feature_ids, list):
            unknown_features = sorted(
                item
                for item in member_feature_ids
                if isinstance(item, str) and item and item not in feature_ids
            )
            if unknown_features:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown feature IDs: {', '.join(unknown_features)}",
                    )
                )

        refs = interface.get("topology_refs", {})
        if topology_ids is not None and isinstance(refs, dict):
            unknown_faces = sorted(
                item
                for item in refs.get("faces", [])
                if isinstance(item, str) and item and item not in topology_ids["face"]
            )
            unknown_edges = sorted(
                item
                for item in refs.get("edges", [])
                if isinstance(item, str) and item and item not in topology_ids["edge"]
            )
            if unknown_faces:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown topology faces: {', '.join(unknown_faces)}",
                    )
                )
            if unknown_edges:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown topology edges: {', '.join(unknown_edges)}",
                    )
                )

        constraint_refs = interface.get("constraint_refs", [])
        if constraint_ids is not None and isinstance(constraint_refs, list):
            unknown_constraints = sorted(
                item
                for item in constraint_refs
                if isinstance(item, str) and item and item not in constraint_ids
            )
            if unknown_constraints:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown constraints: {', '.join(unknown_constraints)}",
                    )
                )

        simulation_refs = interface.get("simulation_refs", [])
        if simulation_ids is not None and isinstance(simulation_refs, list):
            unknown_sim = sorted(
                item
                for item in simulation_refs
                if isinstance(item, str) and item and item not in simulation_ids
            )
            if unknown_sim:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown simulation IDs: {', '.join(unknown_sim)}",
                    )
                )

        visual_refs = interface.get("visual_refs", [])
        if visual_ids is not None and isinstance(visual_refs, list):
            unknown_visual = sorted(
                item
                for item in visual_refs
                if isinstance(item, str) and item and item not in visual_ids
            )
            if unknown_visual:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"interface {interface_id} references unknown visual annotation IDs: {', '.join(unknown_visual)}",
                    )
                )

        cae_refs = interface.get("cae_refs", [])
        if cae_refs is not None:
            if not isinstance(cae_refs, list):
                messages.append(ValidationMessage(Level.FAIL, f"interface {interface_id} cae_refs must be an array"))
            else:
                for ref_index, cae_ref in enumerate(cae_refs):
                    if not isinstance(cae_ref, dict):
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref at index {ref_index} must be an object",
                            )
                        )
                        continue

                    source_file = cae_ref.get("source_file")
                    if isinstance(source_file, str) and source_file not in names:
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref source_file missing: {source_file}",
                            )
                        )

                    cae_entity = cae_ref.get("cae_entity")
                    if not isinstance(cae_mapping_data, dict):
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref requires simulation/cae_mapping.json",
                            )
                        )
                    elif isinstance(cae_entity, str) and cae_entity not in cae_entities:
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref references unknown CAE entity: {cae_entity}",
                            )
                        )

                    mapping_status = cae_ref.get("mapping_status")
                    if isinstance(mapping_status, str) and mapping_status not in valid_cae_statuses:
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref has invalid mapping_status: {mapping_status}",
                            )
                        )
                    mapping_method = cae_ref.get("mapping_method")
                    if isinstance(mapping_method, str) and mapping_method not in valid_cae_methods:
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref has invalid mapping_method: {mapping_method}",
                            )
                        )
                    confidence = cae_ref.get("confidence")
                    if isinstance(confidence, str) and confidence not in valid_cae_confidence:
                        messages.append(
                            ValidationMessage(
                                Level.FAIL,
                                f"interface {interface_id} cae_ref has invalid confidence: {confidence}",
                            )
                        )

                    maps_to = cae_ref.get("maps_to")
                    if isinstance(maps_to, dict):
                        feature_id = maps_to.get("feature_id")
                        if isinstance(feature_id, str) and feature_id not in feature_ids:
                            messages.append(
                                ValidationMessage(
                                    Level.FAIL,
                                    f"interface {interface_id} cae_ref references unknown feature_id {feature_id}",
                                )
                            )
                        mapped_interface_id = maps_to.get("interface_id")
                        if isinstance(mapped_interface_id, str) and mapped_interface_id != interface_id:
                            messages.append(
                                ValidationMessage(
                                    Level.FAIL,
                                    f"interface {interface_id} cae_ref maps_to.interface_id does not match containing interface: {mapped_interface_id}",
                                )
                            )

        if interface.get("protected") is True:
            forbidden_ops = interface.get("forbidden_operations", [])
            has_forbidden_ops = isinstance(forbidden_ops, list) and any(
                isinstance(item, str) and item for item in forbidden_ops
            )
            has_protected_link = isinstance(member_feature_ids, list) and any(
                isinstance(fid, str) and fid in protected_feature_ids for fid in member_feature_ids
            )
            if not has_forbidden_ops and not has_protected_link:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"protected interface {interface_id} must include forbidden_operations or reference a protected region feature",
                    )
                )

    duplicates = _duplicate_values(interface_ids)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"objects/interface_graph.json interface IDs are not unique: {', '.join(duplicates)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "objects/interface_graph.json interface IDs are unique"))

    notes = interface_graph.get("notes")
    if isinstance(notes, list):
        notes_text = " ".join(item.lower() for item in notes if isinstance(item, str))
        has_generated = "generated" in notes_text and "index" in notes_text
        has_source_of_truth = "source of truth" in notes_text and "not" in notes_text
        if has_generated and has_source_of_truth:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "objects/interface_graph.json notes state generated-index and not-source-of-truth policy",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "objects/interface_graph.json notes must state generated-index and not-source-of-truth policy",
                )
            )
    else:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                "objects/interface_graph.json notes must be a list",
            )
        )

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "objects/interface_graph.json semantic checks passed"))
    return messages


def _constraint_ids(constraints_data: Any | None) -> set[str] | None:
    if not isinstance(constraints_data, dict):
        return None
    constraints = constraints_data.get("constraints")
    if not isinstance(constraints, list):
        return set()
    return {
        constraint["id"]
        for constraint in constraints
        if isinstance(constraint, dict) and isinstance(constraint.get("id"), str)
    }


def _simulation_reference_ids(simulation_setup_data: Any | None) -> set[str] | None:
    if not isinstance(simulation_setup_data, dict):
        return None
    result: set[str] = set()
    for bc in simulation_setup_data.get("boundary_conditions", []) or []:
        if isinstance(bc, dict) and isinstance(bc.get("id"), str):
            result.add(bc["id"])
    for load in simulation_setup_data.get("loads", []) or []:
        if isinstance(load, dict) and isinstance(load.get("id"), str):
            result.add(load["id"])
    return result


def _annotation_item_ids(annotation_layers_data: Any | None) -> set[str] | None:
    if not isinstance(annotation_layers_data, dict):
        return None
    layers = annotation_layers_data.get("layers")
    if not isinstance(layers, list):
        return set()
    result: set[str] = set()
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        items = layer.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                result.add(item["id"])
    return result


def _protected_region_feature_ids(protected_regions_data: Any | None) -> set[str]:
    result: set[str] = set()
    if not isinstance(protected_regions_data, dict):
        return result
    regions = protected_regions_data.get("protected_regions")
    if not isinstance(regions, list):
        return result
    for region in regions:
        if isinstance(region, dict) and isinstance(region.get("feature_id"), str):
            result.add(region["feature_id"])
    return result


def _validate_parsed_cae_materials_semantics(parsed_materials: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(parsed_materials, dict):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_materials.json must be a JSON object")]
    materials = parsed_materials.get("materials")
    if not isinstance(materials, list):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_materials.json materials must be an array")]

    names = [item.get("name") for item in materials if isinstance(item, dict) and isinstance(item.get("name"), str)]
    duplicates = _duplicate_values(names)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"parsed CAE material names are not unique: {', '.join(duplicates)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "parsed CAE material names are unique"))
    return messages


def _validate_parsed_cae_boundary_conditions_semantics(parsed_bcs: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(parsed_bcs, dict):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_boundary_conditions.json must be a JSON object")]
    boundary_conditions = parsed_bcs.get("boundary_conditions")
    if not isinstance(boundary_conditions, list):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_boundary_conditions.json boundary_conditions must be an array")]

    ids = [item.get("id") for item in boundary_conditions if isinstance(item, dict) and isinstance(item.get("id"), str)]
    duplicates = _duplicate_values(ids)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"parsed CAE boundary condition IDs are not unique: {', '.join(duplicates)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "parsed CAE boundary condition IDs are unique"))
    return messages


def _validate_parsed_cae_loads_semantics(parsed_loads: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(parsed_loads, dict):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_loads.json must be a JSON object")]
    loads = parsed_loads.get("loads")
    if not isinstance(loads, list):
        return [ValidationMessage(Level.FAIL, "simulation/cae_imports/parsed_loads.json loads must be an array")]

    ids = [item.get("id") for item in loads if isinstance(item, dict) and isinstance(item.get("id"), str)]
    duplicates = _duplicate_values(ids)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"parsed CAE load IDs are not unique: {', '.join(duplicates)}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, "parsed CAE load IDs are unique"))
    return messages


#: How a boundary condition or load names itself. Both are legitimate values for
#: `maps_to.cae_target_id`, because the two producers pick different ones and
#: each is self-consistent: `normalize_cae_bindings` writes the record `id`,
#: while AI preprocessing writes the selection key it also stores as
#: `target_feature` — which is what every consumer joins on. Accepting one and
#: not the other would fail whichever path lost the coin toss.
_CAE_TARGET_ID_KEYS = ("id", "target_feature")


def _cae_target_ids(*documents_and_keys: tuple[Any, str]) -> set[str]:
    """Every name by which a boundary condition or load can be addressed."""
    ids: set[str] = set()
    for document, key in documents_and_keys:
        items = document.get(key) if isinstance(document, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            for id_key in _CAE_TARGET_ID_KEYS:
                value = item.get(id_key)
                if isinstance(value, str) and value:
                    ids.add(value)
    return ids


def _validate_cae_mapping_semantics(
    cae_mapping: Any,
    feature_graph: Any | None,
    interface_graph: Any | None,
    cae_target_ids: set[str] | None = None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(cae_mapping, dict):
        return [ValidationMessage(Level.FAIL, "simulation/cae_mapping.json must be a JSON object")]

    mappings = cae_mapping.get("mappings")
    if not isinstance(mappings, list):
        return [ValidationMessage(Level.FAIL, "simulation/cae_mapping.json mappings must be an array")]

    valid_statuses = {"mapped", "unmapped", "partially_mapped", "unresolved"}
    valid_methods = _cae_mapping_enum(*_MAPPING_ITEM, "mapping_method", "enum")
    valid_confidence = {"none", "low", "medium", "high"}
    feature_ids = _feature_ids_from_graph(feature_graph) or set()
    interface_ids = _interface_ids_from_graph(interface_graph)
    cae_target_ids = cae_target_ids or set()

    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            messages.append(ValidationMessage(Level.FAIL, f"CAE mapping at index {index} must be an object"))
            continue

        mapping_status = mapping.get("mapping_status")
        if isinstance(mapping_status, str) and mapping_status not in valid_statuses:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} has invalid mapping_status: {mapping_status}",
                )
            )

        mapping_method = mapping.get("mapping_method")
        if isinstance(mapping_method, str) and mapping_method not in valid_methods:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} has invalid mapping_method: {mapping_method}",
                )
            )

        confidence = mapping.get("confidence")
        if isinstance(confidence, str) and confidence not in valid_confidence:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} has invalid confidence: {confidence}",
                )
            )

        maps_to = mapping.get("maps_to")
        if mapping_status in {"mapped", "partially_mapped"} and not isinstance(maps_to, dict):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} is {mapping_status} but maps_to is not an object",
                )
            )
        if mapping_status in {"mapped", "partially_mapped"} and confidence == "none":
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} is {mapping_status} but confidence is none",
                )
            )
        if mapping_status == "unmapped" and maps_to not in (None, {}):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"CAE mapping at index {index} is unmapped but maps_to is not null/empty",
                )
            )

        if isinstance(maps_to, dict):
            target_id = maps_to.get("cae_target_id")
            # No truthiness guard on `cae_target_ids`: an empty set means the
            # package declares no boundary condition or load at all, so a
            # mapping claiming to serve one is dangling — exactly the case a
            # reference check exists for. Skipping it there would make the rule
            # unfireable for a whole class of package.
            if isinstance(target_id, str) and target_id not in cae_target_ids:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"CAE mapping at index {index} references unknown cae_target_id {target_id}",
                    )
                )
            feature_id = maps_to.get("feature_id")
            if isinstance(feature_id, str) and feature_id not in feature_ids:
                # A package written before `cae_target_id` existed put the CAE
                # setup entity's id here, so this is only a dangling FEATURE
                # reference when it is not a known CAE target either. Reporting
                # it as one regardless is what made every workbench package fail
                # (#513) — the check was right about the field name and wrong
                # about the data.
                if target_id is None and feature_id in cae_target_ids:
                    pass
                else:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"CAE mapping at index {index} references unknown feature_id {feature_id}",
                        )
                    )
            interface_id = maps_to.get("interface_id")
            if isinstance(interface_id, str) and interface_id not in interface_ids:
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"CAE mapping at index {index} references unknown interface_id {interface_id}",
                    )
                )
            if not any(
                isinstance(value, str) for value in (target_id, feature_id, interface_id)
            ):
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"CAE mapping at index {index} maps_to must include at "
                        "least one of cae_target_id, feature_id, interface_id",
                    )
                )

    notes = cae_mapping.get("notes")
    if isinstance(notes, list):
        notes_text = " ".join(item.lower() for item in notes if isinstance(item, str))
        # The prose requirement was a stand-in for provenance: it forced the
        # document to SAY that mappings were not auto-inferred. A mapping that
        # states HOW its face was chosen has answered that in structured data,
        # and demanding the sentence too would make an authored mapping assert
        # something false about itself (#513).
        #
        # Deliberately NOT satisfied by `not_inferred_phase_10a`: that value
        # means "we did not infer this", which is the very claim the policy note
        # backs up. Relaxing it there would weaken the import path's guard, so
        # only the methods that affirmatively describe a selection count.
        entries = cae_mapping.get("mappings")
        declares_method = bool(entries) and isinstance(entries, list) and all(
            isinstance(m, dict) and m.get("mapping_method") in _SELECTION_METHODS
            for m in entries
        )
        has_policy = declares_method or (
            "phase 10a" in notes_text
            and ("does not automatically map" in notes_text or "not automatically map" in notes_text)
        )
        if has_policy:
            messages.append(
                ValidationMessage(
                    Level.PASS,
                    "simulation/cae_mapping.json records how each mapping was produced",
                )
            )
        else:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    "simulation/cae_mapping.json must record provenance: either a "
                    "mapping_method on every mapping, or a note stating that "
                    "Phase 10A does not automatically infer mappings",
                )
            )
    else:
        messages.append(ValidationMessage(Level.FAIL, "simulation/cae_mapping.json notes must be an array"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "simulation/cae_mapping.json semantic checks passed"))
    return messages


def _interface_ids_from_graph(interface_graph: Any | None) -> set[str]:
    if not isinstance(interface_graph, dict):
        return set()
    interfaces = interface_graph.get("interfaces")
    if not isinstance(interfaces, list):
        return set()
    return {
        interface["id"]
        for interface in interfaces
        if isinstance(interface, dict) and isinstance(interface.get("id"), str)
    }


def _cae_mapping_entities(cae_mapping: Any | None) -> set[str]:
    if not isinstance(cae_mapping, dict):
        return set()
    mappings = cae_mapping.get("mappings")
    if not isinstance(mappings, list):
        return set()
    return {
        mapping["cae_entity"]
        for mapping in mappings
        if isinstance(mapping, dict) and isinstance(mapping.get("cae_entity"), str)
    }


def _fallback_schema_check(member: str, data: Any) -> list[ValidationMessage]:
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]
    if member == "manifest.json":
        required = {"model_id", "format_version", "units", "resources", "created_by"}
        missing = sorted(required - set(data))
        if missing:
            return [ValidationMessage(Level.FAIL, f"manifest.json missing required fields: {', '.join(missing)}")]
    return [ValidationMessage(Level.PASS, f"{member} passed built-in schema checks")]


def _schema_path(schema_name: str) -> Path:
    """Filesystem path to a packaged schema.

    Prefer :func:`_read_schema_text` for actual loading so the code works for
    wheels installed in non-filesystem contexts. This helper is retained for
    backward compatibility with callers that expect a Path.
    """
    return Path(__file__).resolve().parent / "schemas" / schema_name


def _read_schema_text(schema_name: str) -> str | None:
    """Read a packaged schema via :mod:`importlib.resources` with a filesystem fallback."""
    try:
        from importlib.resources import files

        resource = files("aieng.schemas").joinpath(schema_name)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, AttributeError):
        pass
    fallback = Path(__file__).resolve().parent / "schemas" / schema_name
    if fallback.exists():
        warnings.warn(
            f"Schema {schema_name!r} was loaded from the source tree fallback "
            f"({fallback}); the installed package may be missing its schema data. "
            "This can mask a packaging regression.",
            RuntimeWarning,
            stacklevel=2,
        )
        return fallback.read_text(encoding="utf-8")
    return None


@lru_cache(maxsize=None)
def _schema_document(schema_name: str) -> dict[str, Any] | None:
    schema_text = _read_schema_text(schema_name)
    if schema_text is None:
        return None
    return json.loads(schema_text)


@lru_cache(maxsize=None)
def _schema_validator(schema_name: str) -> Any | None:
    if Draft202012Validator is None:
        return None
    schema = _schema_document(schema_name)
    if schema is None:
        return None
    return Draft202012Validator(schema)


def _error_path(path_parts: Iterable[Any]) -> str:
    parts = list(path_parts)
    if not parts:
        return "$"
    return "$" + "".join(f"[{part!r}]" if isinstance(part, int) else f".{part}" for part in parts)


def _resource_paths(resources: Any) -> list[str]:
    paths: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(resources)
    return sorted(set(paths))


_EVIDENCE_KNOWN_TYPES = frozenset({
    "task_spec",
    "external_tool_requirements",
    "solver_result",
    "mesh_evidence",
    "geometry_modification",
    "validation_report",
})
_EVIDENCE_KNOWN_PRODUCER_KINDS = frozenset({
    "aieng_core",
    "external_cad",
    "external_cae",
    "external_solver",
    "external_agent",
})
_EVIDENCE_KNOWN_ARTIFACT_KINDS = frozenset({"yaml", "json", "inp", "step", "result_file"})
_EVIDENCE_KNOWN_VERIFICATION_STATUSES = frozenset({"available", "missing", "unverified", "schema_validated"})

_CLAIM_VERIFICATION_STATUSES = frozenset({"pass", "fail", "unsupported", "partially_supported", "needs_review"})

_EVIDENCE_CLAIM_POLICY_TRUE_FLAGS = ("external_tools_execute",)
_EVIDENCE_CLAIM_POLICY_FALSE_FLAGS = (
    "aieng_core_generates_solver_evidence",
    "aieng_core_generates_mesh_evidence",
    "aieng_core_modifies_cad_geometry",
)
_EVIDENCE_CORE_FORBIDDEN_TYPES = frozenset({"solver_result", "mesh_evidence", "geometry_modification"})


_TOOL_TRACE_TOOL_ROLES = frozenset({
    "agent_runtime",
    "cad_runtime",
    "cae_runtime",
    "cae_preprocessor",
    "solver",
    "postprocessor",
    "manufacturing_checker",
})
_TOOL_TRACE_EXIT_STATUSES = frozenset({"success", "failure", "skipped"})


_COMPLETENESS_STATUSES = frozenset({
    "available",
    "partial",
    "missing",
    "unknown",
    "unsupported",
    "conflicting",
    "not_applicable",
})

_COMPLETENESS_TRUE_FLAGS = (
    "best_effort_conversion",
    "missingness_explicit",
    "do_not_infer_missing_information",
    "unsupported_is_not_false",
    "external_tools_execute",
)

_COMPLETENESS_FALSE_FLAGS = (
    "aieng_core_executes_external_tools",
)


def _validate_completeness_report_semantics(
    member: str,
    data: Any,
    names: set[str],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    policy = data.get("claim_policy")
    if isinstance(policy, dict):
        bad_true = [flag for flag in _COMPLETENESS_TRUE_FLAGS if policy.get(flag) is not True]
        bad_false = [flag for flag in _COMPLETENESS_FALSE_FLAGS if policy.get(flag) is not False]
        if bad_true:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_true} to true"))
        if bad_false:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_false} to false"))
        if not bad_true and not bad_false:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy enforces explicit missingness and execution boundary"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be an object"))

    categories = data.get("categories")
    if not isinstance(categories, list):
        return messages + [ValidationMessage(Level.FAIL, f"{member} categories must be an array")]

    category_names: list[str] = []
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            messages.append(ValidationMessage(Level.FAIL, f"{member} category at index {index} must be an object"))
            continue
        category_name = category.get("category")
        if isinstance(category_name, str):
            category_names.append(category_name)
        status = category.get("status")
        resources = category.get("resources")
        missing_items = category.get("missing_items")

        if isinstance(status, str) and status not in _COMPLETENESS_STATUSES:
            messages.append(ValidationMessage(Level.FAIL, f"{member} category {category_name!r} has unknown status '{status}'"))
        if not isinstance(resources, list):
            messages.append(ValidationMessage(Level.FAIL, f"{member} category {category_name!r} resources must be an array"))
            resources = []
        if not isinstance(missing_items, list):
            messages.append(ValidationMessage(Level.FAIL, f"{member} category {category_name!r} missing_items must be an array"))
            missing_items = []

        unknown_resources = sorted(
            path for path in resources
            if isinstance(path, str) and path not in names
        )
        if unknown_resources:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} category {category_name!r} references resources not present in package: {', '.join(unknown_resources)}",
                )
            )
        if status in {"available", "partial"} and not resources:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} category {category_name!r} status '{status}' requires at least one present resource",
                )
            )
        if status == "missing" and not missing_items:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} category {category_name!r} status 'missing' requires explicit missing_items",
                )
            )

    duplicate_categories = _duplicate_values(category_names)
    if duplicate_categories:
        messages.append(ValidationMessage(Level.FAIL, f"{member} category names are not unique: {', '.join(duplicate_categories)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, f"{member} category names are unique"))

    if data.get("conversion_mode") != "best_effort":
        messages.append(ValidationMessage(Level.FAIL, f"{member} conversion_mode must be 'best_effort'"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_design_targets(package: zipfile.ZipFile, member: str) -> list[ValidationMessage]:
    data = _read_yaml_member(package, member, [])
    if data is None:
        return [ValidationMessage(Level.FAIL, f"{member} is not valid YAML")]
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a YAML object")]

    messages: list[ValidationMessage] = [ValidationMessage(Level.PASS, f"{member} is valid YAML")]
    messages.extend(_validate_against_schema(member, data, "design_targets.schema.json"))

    target_ids = [
        (t.get("target_id") or t.get("id"))
        for t in data.get("targets", [])
        if isinstance(t, dict) and isinstance((t.get("target_id") or t.get("id")), str)
    ]
    duplicates = _duplicate_values(target_ids)
    if duplicates:
        messages.append(
            ValidationMessage(Level.FAIL, f"{member} target IDs are not unique: {', '.join(duplicates)}")
        )
    elif target_ids:
        messages.append(ValidationMessage(Level.PASS, f"{member} target IDs are unique"))

    # Phase 35 PR 2 - cross-field semantic checks. Schema enum already
    # constrains comparator validity; these checks add the per-comparator
    # cross-field requirements (within_range needs both thresholds, etc.)
    # that JSON Schema does not express directly. Backward-compatible: each
    # check accepts EITHER the legacy field name OR the modern field name.
    targets_list = data.get("targets")
    if isinstance(targets_list, list):
        for index, target in enumerate(targets_list):
            if not isinstance(target, dict):
                continue
            tid = (
                target.get("target_id")
                or target.get("id")
                or f"<targets[{index}]>"
            )
            comparator = target.get("comparator") or target.get("operator")
            target_type = target.get("target_type") or target.get("metric")
            messages.extend(
                _validate_design_target_semantics(member, tid, target, comparator, target_type)
            )

    policy = data.get("claim_policy")
    if isinstance(policy, dict):
        for flag in (
            "targets_are_acceptance_criteria",
            "compliance_requires_evidence",
            "physical_correctness_not_claimed",
        ):
            if policy.get(flag) is not True:
                messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy.{flag} must be true"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


_DESIGN_TARGET_QUANTITATIVE_COMPARATORS = frozenset(
    {"<=", "<", ">=", ">", "==", "reduce_by_at_least"}
)


def _validate_design_target_semantics(
    member: str,
    tid: str,
    target: dict[str, Any],
    comparator: Any,
    target_type: Any,
) -> list[ValidationMessage]:
    """Cross-field semantic checks for one design target entry.

    Rules enforced:

    - ``within_range`` requires both ``threshold_min`` and ``threshold_max``.
    - Quantitative comparators (``<=``, ``<``, ``>=``, ``>``, ``==``,
      ``reduce_by_at_least``) require ``threshold`` OR legacy ``value``.
    - ``preserve`` / ``preserved_interface`` requires at least one entry
      in ``protected_features`` or ``protected_interfaces``.
    - ``priority`` / ``objective_priority`` requires non-empty
      ``objective_order``.
    """
    messages: list[ValidationMessage] = []

    if comparator == "within_range":
        if not isinstance(target.get("threshold_min"), (int, float)):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} target '{tid}': comparator 'within_range' requires "
                    "threshold_min",
                )
            )
        if not isinstance(target.get("threshold_max"), (int, float)):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} target '{tid}': comparator 'within_range' requires "
                    "threshold_max",
                )
            )

    if isinstance(comparator, str) and comparator in _DESIGN_TARGET_QUANTITATIVE_COMPARATORS:
        has_threshold = isinstance(target.get("threshold"), (int, float))
        has_legacy_value = isinstance(target.get("value"), (int, float))
        if not (has_threshold or has_legacy_value):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} target '{tid}': quantitative comparator "
                    f"'{comparator}' requires threshold (or legacy value)",
                )
            )

    is_preserve = comparator == "preserve" or target_type == "preserved_interface"
    if is_preserve:
        pf = target.get("protected_features")
        pi = target.get("protected_interfaces")
        has_pf = isinstance(pf, list) and any(isinstance(x, dict) for x in pf)
        has_pi = isinstance(pi, list) and any(isinstance(x, dict) for x in pi)
        if not (has_pf or has_pi):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} target '{tid}': preserve / preserved_interface "
                    "requires at least one protected_features or "
                    "protected_interfaces entry",
                )
            )

    is_priority = comparator == "priority" or target_type == "objective_priority"
    if is_priority:
        order = target.get("objective_order")
        if not (isinstance(order, list) and any(isinstance(x, str) for x in order)):
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} target '{tid}': priority / objective_priority "
                    "requires a non-empty objective_order",
                )
            )

    return messages


def _validate_evidence_report_semantics(
    member: str,
    report: Any,
    names: set[str],
    validation_status_data: Any | None,
    evidence_index_data: Any | None,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(report, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    expected_sources = [
        "validation/status.yaml",
        "results/evidence_index.json",
    ]
    source_files = report.get("source_files")
    if not isinstance(source_files, list):
        return [ValidationMessage(Level.FAIL, f"{member} source_files must be an array")]

    missing_sources_in_report = sorted(path for path in expected_sources if path not in source_files)
    if missing_sources_in_report:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"{member} source_files missing required source entries: {', '.join(missing_sources_in_report)}",
            )
        )

    missing_sources_in_package = sorted(path for path in expected_sources if path not in names)
    if missing_sources_in_package:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"{member} requires source members in package: {', '.join(missing_sources_in_package)}",
            )
        )

    if not isinstance(validation_status_data, dict):
        messages.append(ValidationMessage(Level.FAIL, f"{member} requires parseable validation/status.yaml"))
    if not isinstance(evidence_index_data, dict):
        messages.append(ValidationMessage(Level.FAIL, f"{member} requires parseable results/evidence_index.json"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} is consistent with validation/status.yaml and evidence_index"))
    return messages


def _validate_tool_trace_semantics(
    member: str,
    data: Any,
    evidence_index_data: Any | None,
    names: set[str],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    claim_policy = data.get("claim_policy")
    if isinstance(claim_policy, dict):
        if claim_policy.get("external_tools_execute") is not True:
            messages.append(
                ValidationMessage(Level.FAIL, f"{member} claim_policy.external_tools_execute must be true")
            )
        if claim_policy.get("aieng_core_executes_external_tools") is not False:
            messages.append(
                ValidationMessage(Level.FAIL, f"{member} claim_policy.aieng_core_executes_external_tools must be false")
            )
        if (
            claim_policy.get("external_tools_execute") is True
            and claim_policy.get("aieng_core_executes_external_tools") is False
        ):
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy enforces correct execution boundary"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be an object"))

    entries = data.get("entries")
    if not isinstance(entries, list):
        return messages + [ValidationMessage(Level.FAIL, f"{member} entries must be an array")]

    entry_ids: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            messages.append(ValidationMessage(Level.FAIL, f"{member} entry at index {index} must be an object"))
            continue
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            messages.append(ValidationMessage(Level.FAIL, f"{member} entry at index {index} missing string entry_id"))
        else:
            entry_ids.append(entry_id)
        tool = entry.get("tool")
        if isinstance(tool, dict):
            role = tool.get("tool_role")
            if isinstance(role, str) and role not in _TOOL_TRACE_TOOL_ROLES:
                messages.append(
                    ValidationMessage(Level.FAIL, f"{member} entry {entry_id!r} tool_role '{role}' is unrecognized")
                )
        step = entry.get("step")
        if isinstance(step, dict):
            exit_status = step.get("exit_status")
            if isinstance(exit_status, str) and exit_status not in _TOOL_TRACE_EXIT_STATUSES:
                messages.append(
                    ValidationMessage(Level.FAIL, f"{member} entry {entry_id!r} exit_status '{exit_status}' is unrecognized")
                )

    dup_entry_ids = _duplicate_values(entry_ids)
    if dup_entry_ids:
        messages.append(
            ValidationMessage(Level.FAIL, f"{member} entry IDs are not unique: {', '.join(dup_entry_ids)}")
        )
    else:
        messages.append(ValidationMessage(Level.PASS, f"{member} entry IDs are unique"))

    # Cross-reference artifacts_recorded against evidence_index if present
    if isinstance(evidence_index_data, dict):
        known_evidence_ids = {
            item["evidence_id"]
            for item in evidence_index_data.get("evidence_items", [])
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
        }
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("entry_id", "<unknown>")
            for artifact_id in entry.get("artifacts_recorded", []):
                if isinstance(artifact_id, str) and artifact_id not in known_evidence_ids:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"{member} entry {entry_id!r} artifacts_recorded references unknown evidence ID {artifact_id!r}",
                        )
                    )

    # Alpha contract: no claim maps, so claims_advanced is not cross-checked
    # against any in-package claim ledger. The package_consistency module
    # surfaces the presence of any forbidden claim_map artifact separately.

    # WARN if source_task_id references a task_spec that is absent
    source_task_id = data.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id and "task/task_spec.yaml" not in names:
        messages.append(
            ValidationMessage(
                Level.WARN,
                f"{member} source_task_id '{source_task_id}' references absent task/task_spec.yaml",
            )
        )

    # WARN if source_handoff_id references an external tool requirements file that is absent
    source_handoff_id = data.get("source_handoff_id")
    if (
        isinstance(source_handoff_id, str)
        and source_handoff_id
        and "task/external_tool_requirements.json" not in names
    ):
        messages.append(
            ValidationMessage(
                Level.WARN,
                f"{member} source_handoff_id '{source_handoff_id}' references absent task/external_tool_requirements.json",
            )
        )

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_evidence_index(member: str, data: Any, names: set[str]) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    messages.extend(_validate_against_schema(member, data, "evidence_index.schema.json"))

    claim_policy = data.get("claim_policy")
    if isinstance(claim_policy, dict):
        bad_true = [f for f in _EVIDENCE_CLAIM_POLICY_TRUE_FLAGS if claim_policy.get(f) is not True]
        if bad_true:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_true} to true"))
        bad_false = [f for f in _EVIDENCE_CLAIM_POLICY_FALSE_FLAGS if claim_policy.get(f) is not False]
        if bad_false:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_false} to false"))
        if not bad_true and not bad_false:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy correctly enforces execution boundary"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be an object"))

    items = data.get("evidence_items")
    if not isinstance(items, list):
        return messages + [ValidationMessage(Level.FAIL, f"{member} evidence_items must be an array")]

    evidence_ids: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item at index {index} must be an object"))
            continue
        ev_id = item.get("evidence_id")
        if not isinstance(ev_id, str) or not ev_id:
            messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item at index {index} missing string evidence_id"))
        else:
            evidence_ids.append(ev_id)
        ev_type = item.get("evidence_type")
        if isinstance(ev_type, str) and ev_type not in _EVIDENCE_KNOWN_TYPES:
            messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item {ev_id!r} has unrecognized evidence_type '{ev_type}'"))
        producer = item.get("producer")
        if isinstance(producer, dict):
            kind = producer.get("kind")
            if isinstance(kind, str) and kind not in _EVIDENCE_KNOWN_PRODUCER_KINDS:
                messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item {ev_id!r} producer.kind '{kind}' is unrecognized"))
            if ev_type in _EVIDENCE_CORE_FORBIDDEN_TYPES and kind == "aieng_core":
                messages.append(
                    ValidationMessage(
                        Level.FAIL,
                        f"{member} evidence_item {ev_id!r} uses forbidden producer.kind 'aieng_core' for evidence_type '{ev_type}'",
                    )
                )
        artifact = item.get("artifact")
        if isinstance(artifact, dict):
            art_kind = artifact.get("kind")
            if isinstance(art_kind, str) and art_kind not in _EVIDENCE_KNOWN_ARTIFACT_KINDS:
                messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item {ev_id!r} artifact.kind '{art_kind}' is unrecognized"))
        verification = item.get("verification")
        if isinstance(verification, dict):
            vstatus = verification.get("status")
            if isinstance(vstatus, str) and vstatus not in _EVIDENCE_KNOWN_VERIFICATION_STATUSES:
                messages.append(ValidationMessage(Level.FAIL, f"{member} evidence_item {ev_id!r} verification.status '{vstatus}' is unrecognized"))
        if ev_type == "mesh_evidence":
            messages.extend(_validate_mesh_evidence_payload(member, item, names))

    dup_ev_ids = _duplicate_values(evidence_ids)
    if dup_ev_ids:
        messages.append(ValidationMessage(Level.FAIL, f"{member} evidence IDs are not unique: {', '.join(dup_ev_ids)}"))
    else:
        messages.append(ValidationMessage(Level.PASS, f"{member} evidence IDs are unique"))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_mesh_evidence_payload(member: str, item: dict[str, Any], names: set[str]) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    ev_id = item.get("evidence_id", "<unknown>")
    payload = item.get("structured_payload")
    if not isinstance(payload, dict):
        return [
            ValidationMessage(
                Level.FAIL,
                f"{member} mesh_evidence item {ev_id!r} requires structured_payload",
            )
        ]

    if payload.get("payload_type") != "mesh_artifact_summary":
        messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} structured_payload.payload_type must be 'mesh_artifact_summary'"))
    expected_mesh_format = "g" + "msh_msh"
    expected_parser_id = "g" + "msh_msh_ascii_summary_v1"
    if payload.get("mesh_format") != expected_mesh_format:
        messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} structured_payload.mesh_format is unsupported"))

    parser = payload.get("parser")
    if not isinstance(parser, dict):
        messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} structured_payload.parser must be an object"))
    else:
        if parser.get("parser_id") != expected_parser_id:
            messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} parser_id is unsupported"))
        if parser.get("status") not in {"matched", "unsupported"}:
            messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} parser.status is unsupported"))

    artifact_payload = payload.get("artifact")
    evidence_artifact = item.get("artifact")
    evidence_path = evidence_artifact.get("path") if isinstance(evidence_artifact, dict) else None
    if not isinstance(artifact_payload, dict):
        messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} structured_payload.artifact must be an object"))
    else:
        storage_mode = artifact_payload.get("storage_mode")
        if storage_mode not in {"copied_into_package", "external_reference"}:
            messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} artifact.storage_mode is unsupported"))
        elif storage_mode == "copied_into_package":
            package_path = artifact_payload.get("package_path")
            if not isinstance(package_path, str) or not package_path:
                messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} copied artifact requires package_path"))
            else:
                if evidence_path != package_path:
                    messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} copied artifact package_path must match artifact.path"))
                if package_path not in names:
                    messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} copied mesh artifact {package_path!r} is not present in package"))
        elif storage_mode == "external_reference":
            external_path = artifact_payload.get("external_path")
            if not isinstance(external_path, str) or not external_path:
                messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} external reference requires external_path"))
            elif evidence_path != external_path:
                messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} external_path must match artifact.path"))

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} structured_payload.summary must be an object"))
    else:
        quality = summary.get("quality_metrics")
        if not isinstance(quality, dict):
            messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} summary.quality_metrics must be an object"))
        elif quality.get("status") != "unknown":
            messages.append(ValidationMessage(Level.FAIL, f"{member} mesh_evidence item {ev_id!r} quality_metrics.status must remain 'unknown'"))

    return messages


def _validate_cross_resource_consistency(
    *,
    validation_status_data: Any | None,
    task_spec_data: Any | None,
    ext_tool_req_data: Any | None,
    evidence_index_data: Any | None,
    tool_trace_data: Any | None,
    names: set[str],
) -> list[ValidationMessage]:
    """Check inter-resource consistency across the remaining alpha ledgers.

    Under the alpha "no claim maps" contract this no longer cross-references
    claim_map.json. Rules that depended on it (solver_execution vs solver
    claims, forbidden_claims vs passing claims, tool_trace.claims_advanced
    vs claim status) are intentionally retired together with the artifact.
    """
    messages: list[ValidationMessage] = []
    any_data = any(d is not None for d in (
        validation_status_data, task_spec_data, ext_tool_req_data,
        evidence_index_data, tool_trace_data,
    ))
    if not any_data:
        return messages

    # Rule: forbidden_core_actions contains "run_solver" + aieng_core solver evidence -> FAIL
    if isinstance(ext_tool_req_data, dict) and isinstance(evidence_index_data, dict):
        forbidden_core_actions = ext_tool_req_data.get("forbidden_core_actions", [])
        if isinstance(forbidden_core_actions, list) and "run_solver" in forbidden_core_actions:
            for item in evidence_index_data.get("evidence_items", []):
                if not isinstance(item, dict):
                    continue
                producer = item.get("producer")
                if (
                    isinstance(producer, dict)
                    and producer.get("kind") == "aieng_core"
                    and item.get("evidence_type") == "solver_result"
                ):
                    eid = item.get("evidence_id")
                    messages.append(ValidationMessage(
                        Level.FAIL,
                        f"cross-resource: evidence item {eid!r} is a solver_result produced by aieng_core, "
                        "but task/external_tool_requirements.json forbidden_core_actions includes 'run_solver' - "
                        ".aieng core must not generate solver evidence",
                    ))

    # Rule: in-package artifact paths in evidence_index not present in ZIP -> WARN
    if isinstance(evidence_index_data, dict):
        for item in evidence_index_data.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            artifact = item.get("artifact")
            if not isinstance(artifact, dict):
                continue
            path = artifact.get("path", "")
            if not isinstance(path, str) or not path:
                continue
            # Skip external refs (URLs or absolute paths)
            if path.startswith(("http://", "https://", "/", "\\")) or (len(path) > 1 and path[1] == ":"):
                continue
            if path not in names:
                eid = item.get("evidence_id")
                messages.append(ValidationMessage(
                    Level.WARN,
                    f"cross-resource: evidence item {eid!r} references artifact path '{path}' "
                    "which is not present in the package ZIP",
                ))

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, "cross-resource consistency checks passed"))
    return messages


_CONVERTER_TRUE_FLAGS = (
    "best_effort_conversion",
    "missingness_explicit",
    "do_not_infer_missing_information",
    "unsupported_is_not_false",
    "external_tools_execute",
)

_CONVERTER_FALSE_FLAGS = (
    "aieng_core_executes_external_tools",
    "aieng_core_executes_solvers_meshers_or_optimizers",
    "aieng_core_performs_cad_edits",
)


def _validate_conversion_manifest_semantics(
    member: str,
    data: Any,
    manifest: dict[str, Any],
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    policy = data.get("claim_policy")
    if isinstance(policy, dict):
        bad_true = [flag for flag in _CONVERTER_TRUE_FLAGS if policy.get(flag) is not True]
        bad_false = [flag for flag in _CONVERTER_FALSE_FLAGS if policy.get(flag) is not False]
        if bad_true:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_true} to true"))
        if bad_false:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_false} to false"))
        if not bad_true and not bad_false:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy enforces converter boundary"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be an object"))

    coverage = data.get("coverage_categories")
    if isinstance(coverage, list) and coverage:
        messages.append(
            ValidationMessage(Level.PASS, f"{member} coverage_categories present ({len(coverage)} entries)")
        )

    declared = data.get("declared_capability_levels")
    achieved = data.get("achieved_capability_levels")
    if isinstance(declared, list) and isinstance(achieved, list):
        declared_levels = {
            entry.get("level")
            for entry in declared
            if isinstance(entry, dict) and isinstance(entry.get("level"), int)
        }
        achieved_levels = {
            entry.get("level")
            for entry in achieved
            if isinstance(entry, dict) and isinstance(entry.get("level"), int)
        }
        extra = achieved_levels - declared_levels
        if extra:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} achieved capability levels {sorted(extra)} were not declared in declared_capability_levels",
                )
            )
        else:
            messages.append(
                ValidationMessage(Level.PASS, f"{member} achieved levels are a subset of declared levels")
            )

    converter_block = data.get("converter")
    source_block = data.get("source")
    if isinstance(converter_block, dict) and isinstance(source_block, dict):
        cs = converter_block.get("source_system")
        ss = source_block.get("source_system")
        if isinstance(cs, str) and isinstance(ss, str) and cs and ss and cs != ss:
            messages.append(
                ValidationMessage(
                    Level.WARN,
                    f"{member} converter.source_system ({cs!r}) differs from source.source_system ({ss!r})",
                )
            )

    if manifest.get("source_mode") not in {"converter", None}:
        messages.append(
            ValidationMessage(
                Level.WARN,
                f"{member} present but manifest source_mode is {manifest.get('source_mode')!r}; "
                "converter-produced packages should declare source_mode='converter'",
            )
        )

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_converter_capabilities_semantics(
    member: str,
    data: Any,
) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(data, dict):
        return [ValidationMessage(Level.FAIL, f"{member} must be a JSON object")]

    policy = data.get("claim_policy")
    if isinstance(policy, dict):
        bad_true = [flag for flag in _CONVERTER_TRUE_FLAGS if policy.get(flag) is not True]
        bad_false = [flag for flag in _CONVERTER_FALSE_FLAGS if policy.get(flag) is not False]
        if bad_true:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_true} to true"))
        if bad_false:
            messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must set {bad_false} to false"))
        if not bad_true and not bad_false:
            messages.append(ValidationMessage(Level.PASS, f"{member} claim_policy enforces converter boundary"))
    else:
        messages.append(ValidationMessage(Level.FAIL, f"{member} claim_policy must be an object"))

    levels = data.get("supported_levels")
    if isinstance(levels, list):
        level_pairs = [
            (entry.get("level"), entry.get("name"))
            for entry in levels
            if isinstance(entry, dict)
        ]
        seen: set[tuple[Any, Any]] = set()
        duplicates: set[tuple[Any, Any]] = set()
        for pair in level_pairs:
            if pair in seen:
                duplicates.add(pair)
            seen.add(pair)
        if duplicates:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{member} supported_levels has duplicate (level,name) entries: {sorted(duplicates)}",
                )
            )

    if not any(message.level is Level.FAIL for message in messages):
        messages.append(ValidationMessage(Level.PASS, f"{member} semantic checks passed"))
    return messages


def _validate_assembly_graph_semantics(assembly_graph: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    resource = "assembly/assembly_graph.json"

    if not isinstance(assembly_graph, dict):
        return [ValidationMessage(Level.FAIL, f"{resource} must be a JSON object")]

    parts = assembly_graph.get("parts")
    if not isinstance(parts, list):
        messages.append(ValidationMessage(Level.FAIL, f"{resource} parts must be an array"))
        return messages

    part_ids = [p.get("part_id") for p in parts if isinstance(p, dict)]
    duplicates = _duplicate_values(part_ids)
    if duplicates:
        messages.append(
            ValidationMessage(
                Level.FAIL,
                f"{resource} part_id values are not unique: {', '.join(sorted(str(d) for d in duplicates))}",
            )
        )
    else:
        messages.append(ValidationMessage(Level.PASS, f"{resource} part_id values are unique"))

    mates = assembly_graph.get("mates", [])
    if isinstance(mates, list):
        mate_ids = [m.get("mate_id") for m in mates if isinstance(m, dict)]
        dup_mates = _duplicate_values(mate_ids)
        if dup_mates:
            messages.append(
                ValidationMessage(
                    Level.FAIL,
                    f"{resource} mate_id values are not unique: {', '.join(sorted(str(d) for d in dup_mates))}",
                )
            )
        else:
            messages.append(ValidationMessage(Level.PASS, f"{resource} mate_id values are unique"))

        part_id_set = set(part_ids)
        for mate in mates:
            if not isinstance(mate, dict):
                continue
            for field in ("part_a", "part_b"):
                ref = mate.get(field)
                if ref and ref not in part_id_set:
                    messages.append(
                        ValidationMessage(
                            Level.FAIL,
                            f"{resource} mate {mate.get('mate_id')!r} {field}={ref!r} does not reference a known part_id",
                        )
                    )

    policy = assembly_graph.get("claim_policy")
    if not isinstance(policy, dict) or not policy.get("allowed"):
        messages.append(
            ValidationMessage(Level.FAIL, f"{resource} claim_policy must have a non-empty 'allowed' list")
        )
    else:
        messages.append(ValidationMessage(Level.PASS, f"{resource} claim_policy is present and non-empty"))

    if not any(m.level is Level.FAIL for m in messages):
        messages.append(ValidationMessage(Level.PASS, f"{resource} semantic checks passed"))
    return messages
