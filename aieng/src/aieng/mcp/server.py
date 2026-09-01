from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import yaml
from aieng.reference import format_ref, inspect_ref

_MANIFEST = "manifest.json"
_FEATURE_GRAPH = "graph/feature_graph.json"
_TOPOLOGY_MAP = "geometry/topology_map.json"
_INTERFACE_GRAPH = "objects/interface_graph.json"
_VALIDATION_STATUS = "validation/status.yaml"
_AAG = "graph/aag.json"
_TASK_SPEC = "task/task_spec.yaml"
_EXTERNAL_TOOL_REQUIREMENTS = "task/external_tool_requirements.json"
_EVIDENCE_INDEX = "results/evidence_index.json"
_TOOL_TRACE = "provenance/tool_trace.json"
_COMPLETENESS_REPORT = "validation/completeness_report.json"
_EVIDENCE_REPORT = "validation/evidence_report.json"
_SUMMARY = "ai/summary.md"
_PATCHES_DIR = "ai/patches/"


class PackageNotReadable(Exception):
    """Raised when a required package member is absent or unreadable."""


class OperationForbidden(Exception):
    """Raised when claim_policy.forbidden_operations blocks the requested tool."""


# ---------------------------------------------------------------------------
# Internal readers
# ---------------------------------------------------------------------------

def _read_json(package_path: Path, member: str) -> Any:
    with zipfile.ZipFile(package_path) as zf:
        return json.loads(_read_member(zf, member))


def _read_yaml(package_path: Path, member: str) -> Any:
    with zipfile.ZipFile(package_path) as zf:
        return yaml.safe_load(_read_member(zf, member))


def _read_text(package_path: Path, member: str) -> str:
    with zipfile.ZipFile(package_path) as zf:
        return _read_member(zf, member).decode("utf-8", errors="replace")


def _read_member(zf: zipfile.ZipFile, member: str) -> bytes:
    try:
        return zf.read(member)
    except KeyError as exc:
        raise PackageNotReadable(f"{member} not found in package") from exc


def _check_claim_policy(package_path: Path, operation: str) -> None:
    """Enforce claim_policy.forbidden_operations from validation/status.yaml."""
    try:
        status = _read_yaml(package_path, _VALIDATION_STATUS)
    except PackageNotReadable:
        return
    if not isinstance(status, dict):
        return
    policy = status.get("claim_policy")
    if not isinstance(policy, dict):
        return
    forbidden = policy.get("forbidden_operations", [])
    if not isinstance(forbidden, list):
        return
    if operation in forbidden:
        rationale = policy.get("rationale", "see validation/status.yaml")
        raise OperationForbidden(
            f"operation '{operation}' is forbidden by claim_policy: {rationale}"
        )


# ---------------------------------------------------------------------------
# Tool implementations — plain functions, testable without the MCP runtime
# ---------------------------------------------------------------------------

def tool_get_manifest(package_path: Path) -> dict[str, Any]:
    """Return parsed manifest.json for the loaded .aieng package."""
    return _read_json(package_path, _MANIFEST)


def tool_get_feature(package_path: Path, feature_id: str) -> dict[str, Any]:
    """Return a specific feature by ID from graph/feature_graph.json."""
    fg = _read_json(package_path, _FEATURE_GRAPH)
    feature = next(
        (f for f in fg.get("features", []) if isinstance(f, dict) and f.get("id") == feature_id),
        None,
    )
    if feature is None:
        raise PackageNotReadable(f"feature '{feature_id}' not found in feature graph")
    return {**feature, "ref": format_ref(_FEATURE_GRAPH, feature_id)}


def tool_get_topology(package_path: Path, entity_type: str | None = None) -> dict[str, Any]:
    """Return topology entities, optionally filtered by type (face, edge, vertex)."""
    tmap = _read_json(package_path, _TOPOLOGY_MAP)
    if entity_type is None:
        return tmap
    entities = [e for e in tmap.get("entities", []) if isinstance(e, dict) and e.get("type") == entity_type]
    return {**tmap, "entities": entities, "_filter_applied": {"type": entity_type}}


def tool_get_interfaces(package_path: Path, role: str | None = None) -> dict[str, Any]:
    """Return interface graph entries, optionally filtered by role."""
    ig = _read_json(package_path, _INTERFACE_GRAPH)
    if role is None:
        return ig
    interfaces = [
        i for i in ig.get("interfaces", [])
        if isinstance(i, dict) and role in i.get("roles", [])
    ]
    return {**ig, "interfaces": interfaces, "_filter_applied": {"role": role}}


def tool_get_validation_status(package_path: Path) -> dict[str, Any]:
    """Return validation/status.yaml as a structured dict."""
    result = _read_yaml(package_path, _VALIDATION_STATUS)
    if not isinstance(result, dict):
        return {"raw": result}
    return result


def tool_get_aag_neighbors(package_path: Path, face_id: str) -> dict[str, Any]:
    """Return AAG arcs and neighbor nodes adjacent to the given topology face ID."""
    aag = _read_json(package_path, _AAG)
    node_id = f"node_{face_id}"

    arcs = [
        arc for arc in aag.get("arcs", [])
        if isinstance(arc, dict)
        and (arc.get("source_node") == node_id or arc.get("target_node") == node_id)
    ]

    neighbor_node_ids: set[str] = set()
    for arc in arcs:
        neighbor_node_ids.add(arc.get("source_node", ""))
        neighbor_node_ids.add(arc.get("target_node", ""))
    neighbor_node_ids.discard(node_id)
    neighbor_node_ids.discard("")

    relevant_node_ids = set(neighbor_node_ids)
    relevant_node_ids.add(node_id)
    relevant_nodes = [
        n for n in aag.get("nodes", [])
        if isinstance(n, dict) and n.get("id") in relevant_node_ids
    ]

    return {
        "query_face_id": face_id,
        "query_node_id": node_id,
        "adjacency_arcs": arcs,
        "neighbor_count": len(neighbor_node_ids),
        "neighbor_nodes": relevant_nodes,
    }


def tool_get_task_spec(package_path: Path) -> dict[str, Any]:
    """Return task/task_spec.yaml, or a structured not-found response if absent."""
    try:
        result = _read_yaml(package_path, _TASK_SPEC)
        if isinstance(result, dict):
            task_id = result.get("task_id")
            if isinstance(task_id, str) and task_id:
                return {**result, "ref": format_ref(_TASK_SPEC, task_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {"status": "not_found", "member": _TASK_SPEC, "message": "No task_spec.yaml in this package."}


def tool_get_external_tool_requirements(package_path: Path) -> dict[str, Any]:
    """Return task/external_tool_requirements.json, or a structured not-found response if absent."""
    try:
        result = _read_json(package_path, _EXTERNAL_TOOL_REQUIREMENTS)
        if isinstance(result, dict):
            handoff_id = result.get("handoff_id")
            if isinstance(handoff_id, str) and handoff_id:
                return {**result, "ref": format_ref(_EXTERNAL_TOOL_REQUIREMENTS, handoff_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {
            "status": "not_found",
            "member": _EXTERNAL_TOOL_REQUIREMENTS,
            "message": "No external_tool_requirements.json in this package.",
        }


def tool_get_evidence_index(package_path: Path) -> dict[str, Any]:
    """Return results/evidence_index.json, or a structured not-found response if absent."""
    try:
        result = _read_json(package_path, _EVIDENCE_INDEX)
        if isinstance(result, dict):
            evidence_id = result.get("evidence_index_id")
            if isinstance(evidence_id, str) and evidence_id:
                return {**result, "ref": format_ref(_EVIDENCE_INDEX, evidence_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {
            "status": "not_found",
            "member": _EVIDENCE_INDEX,
            "message": "No evidence_index.json in this package.",
        }


def tool_get_tool_trace(package_path: Path) -> dict[str, Any]:
    """Return provenance/tool_trace.json, or a structured not-found response if absent."""
    try:
        result = _read_json(package_path, _TOOL_TRACE)
        if isinstance(result, dict):
            trace_id = result.get("tool_trace_id")
            if isinstance(trace_id, str) and trace_id:
                return {**result, "ref": format_ref(_TOOL_TRACE, trace_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {
            "status": "not_found",
            "member": _TOOL_TRACE,
            "message": "No tool_trace.json in this package.",
        }


def tool_get_completeness_report(package_path: Path) -> dict[str, Any]:
    """Return validation/completeness_report.json, or a structured not-found response if absent."""
    try:
        result = _read_json(package_path, _COMPLETENESS_REPORT)
        if isinstance(result, dict):
            report_id = result.get("report_id")
            if isinstance(report_id, str) and report_id:
                return {**result, "ref": format_ref(_COMPLETENESS_REPORT, report_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {
            "status": "not_found",
            "member": _COMPLETENESS_REPORT,
            "message": "No completeness_report.json in this package.",
        }


def tool_get_evidence_report(package_path: Path) -> dict[str, Any]:
    """Return validation/evidence_report.json, or a structured not-found response if absent."""
    try:
        result = _read_json(package_path, _EVIDENCE_REPORT)
        if isinstance(result, dict):
            report_id = result.get("report_id")
            if isinstance(report_id, str) and report_id:
                return {**result, "ref": format_ref(_EVIDENCE_REPORT, report_id)}
            return result
        return {"raw": result}
    except PackageNotReadable:
        return {
            "status": "not_found",
            "member": _EVIDENCE_REPORT,
            "message": "No evidence_report.json in this package.",
        }


def tool_get_summary(package_path: Path) -> str:
    """Return the ai/summary.md content."""
    return _read_text(package_path, _SUMMARY)


def tool_propose_patch(package_path: Path, intent: str) -> dict[str, Any]:
    """Run the rule-based patch proposer with the given intent and return the proposal."""
    _check_claim_policy(package_path, "propose_patch")
    from aieng.ai.patch_proposer import propose_patch_package
    propose_patch_package(package_path, intent)
    with zipfile.ZipFile(package_path) as zf:
        patches = sorted(
            [n for n in zf.namelist() if n.startswith(_PATCHES_DIR) and n.endswith(".json")],
            reverse=True,
        )
        if not patches:
            raise PackageNotReadable("no patch proposals found after proposer run")
        payload = json.loads(zf.read(patches[0]))
        patch_id = payload.get("patch_id") if isinstance(payload, dict) else None
        if isinstance(patch_id, str) and patch_id:
            payload = {**payload, "ref": format_ref(patches[0], patch_id)}
        return payload


def tool_resolve_ref(package_path: Path, ref: str) -> dict[str, Any]:
    """Resolve one @aieng[...] reference using the same shape as CLI ref-inspect --json."""
    return inspect_ref(package_path, ref)


# ---------------------------------------------------------------------------
# MCP server factory
# ---------------------------------------------------------------------------

def create_server(package_path: str | Path) -> Any:
    """Create a FastMCP server wrapping the given .aieng package.

    Requires: pip install 'mcp>=1.0'
    """
    pkg = Path(package_path)
    if not pkg.exists():
        raise FileNotFoundError(f"package does not exist: {pkg}")
    if pkg.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")

    # `mcp` 2.0 renamed FastMCP to MCPServer and moved the module; importing the
    # old path there raises a guidance stub, so try the new name first.
    try:
        try:
            from mcp.server.mcpserver import MCPServer as FastMCP  # mcp >= 2.0
        except ModuleNotFoundError:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x
    except ImportError as exc:
        raise ImportError(
            "The 'mcp' package is required for the MCP server. "
            "Install it with: pip install 'mcp>=1.0'"
        ) from exc

    server = FastMCP(f"aieng:{pkg.stem}")

    @server.tool()
    def get_manifest() -> dict:
        """Return manifest.json for the loaded .aieng package."""
        return tool_get_manifest(pkg)

    @server.tool()
    def get_feature(feature_id: str) -> dict:
        """Return a specific feature by ID from graph/feature_graph.json."""
        return tool_get_feature(pkg, feature_id)

    @server.tool()
    def get_topology(entity_type: str = None) -> dict:
        """Return topology entities, optionally filtered by type (face, edge, vertex)."""
        return tool_get_topology(pkg, entity_type)

    @server.tool()
    def get_interfaces(role: str = None) -> dict:
        """Return interface graph entries, optionally filtered by role."""
        return tool_get_interfaces(pkg, role)

    @server.tool()
    def get_validation_status() -> dict:
        """Return validation/status.yaml as a structured dict."""
        return tool_get_validation_status(pkg)

    @server.tool()
    def get_aag_neighbors(face_id: str) -> dict:
        """Return AAG arcs and neighbor nodes adjacent to the given topology face ID."""
        return tool_get_aag_neighbors(pkg, face_id)

    @server.tool()
    def get_task_spec() -> dict:
        """Return task/task_spec.yaml if present, or a not-found response."""
        return tool_get_task_spec(pkg)

    @server.tool()
    def get_external_tool_requirements() -> dict:
        """Return task/external_tool_requirements.json if present, or a not-found response."""
        return tool_get_external_tool_requirements(pkg)

    @server.tool()
    def get_evidence_index() -> dict:
        """Return results/evidence_index.json if present, or a not-found response."""
        return tool_get_evidence_index(pkg)

    @server.tool()
    def get_tool_trace() -> dict:
        """Return provenance/tool_trace.json if present, or a not-found response."""
        return tool_get_tool_trace(pkg)

    @server.tool()
    def get_completeness_report() -> dict:
        """Return validation/completeness_report.json if present, or a not-found response."""
        return tool_get_completeness_report(pkg)

    @server.tool()
    def get_evidence_report() -> dict:
        """Return validation/evidence_report.json if present, or a not-found response."""
        return tool_get_evidence_report(pkg)

    @server.tool()
    def propose_patch(intent: str) -> dict:
        """Run the rule-based patch proposer with the given intent and return the proposal."""
        return tool_propose_patch(pkg, intent)

    @server.tool()
    def get_summary() -> str:
        """Return the ai/summary.md content for this package."""
        return tool_get_summary(pkg)

    @server.tool()
    def resolve_ref(ref: str) -> dict:
        """Resolve a canonical @aieng[...] reference and return the target record."""
        return tool_resolve_ref(pkg, ref)

    return server


def serve(package_path: str | Path, *, port: int | None = None) -> None:
    """Start the MCP server for the given .aieng package.

    With no port: stdio transport (for Claude Desktop / Claude Code MCP integration).
    With --port N: SSE transport on 0.0.0.0:N (for HTTP clients and testing).
    """
    server = create_server(package_path)
    if port is not None:
        server.run(transport="sse", port=port)
    else:
        server.run()
