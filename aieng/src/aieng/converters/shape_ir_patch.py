"""Shape IR patch format + apply.

Lets an agent edit a Shape IR *surgically* instead of rewriting the whole JSON or
the generated source. A patch is a list of typed operations; applying it is
**atomic and validated**: operations run against a working copy, every operation's
outcome is recorded, and if any operation fails or the result is not a valid Shape
IR the original is left untouched (never silently overwrite invalid Shape IR).

Patch document::

    {
      "format_version": "0.1",
      "patch_id": "...",            # optional
      "author": "...", "tool": "...",  # optional provenance metadata
      "operations": [ {op...}, ... ]
    }

Operations (each may carry ``reason``):
  - set_parameter            target, parameter, value
  - move_control_point       target, path=[i,j], (value=[x,y,z] | delta=[dx,dy,dz])
  - add_node                 node={...}
  - remove_node              target
  - replace_node             target, node={...}
  - connect                  connection={source,target,type?}
  - disconnect               connection={source,target}
  - change_representation_backend  value=<representation>

This module runs no CAD kernel; it only transforms the Shape IR dict.
"""
from __future__ import annotations

import copy
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aieng import FORMAT_VERSION

from .shape_ir import _node_id, _shape_nodes, resolve_representation

PATCH_REPORT_PATH = "diagnostics/shape_ir_patch_report.json"
SHAPE_IR_MEMBER = "geometry/shape_ir.json"

_OPS = {
    "set_parameter", "move_control_point", "add_node", "remove_node",
    "replace_node", "connect", "disconnect", "change_representation_backend",
}


#: Fields that identify a node rather than dimension it. Setting one as a
#: "parameter" would be inert at best and would break the target lookup at
#: worst, so it is refused with a pointer at the operation that can. `name` is
#: in here because `_node_id` falls back to it when `id` is absent — rewriting
#: it would move the target out from under the very patch doing the rewriting.
_IDENTITY_FIELDS = frozenset({"id", "name", "type", "label"})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class PatchOpError(Exception):
    """A single patch operation could not be applied."""


def validate_shape_ir(payload: Any) -> tuple[bool, list[str]]:
    """Minimal structural validation of a Shape IR document."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return False, ["Shape IR must be a JSON object"]
    nodes_key = "parts" if isinstance(payload.get("parts"), list) else (
        "components" if isinstance(payload.get("components"), list) else None)
    if nodes_key is None:
        return False, ["Shape IR must contain a 'parts' or 'components' array"]
    nodes = payload[nodes_key]
    if not nodes:
        errors.append(f"'{nodes_key}' is empty")
    seen: set[str] = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"{nodes_key}[{i}] is not an object")
            continue
        nid = _node_id(node, i + 1)
        if nid in seen:
            errors.append(f"duplicate node id '{nid}'")
        seen.add(nid)
    return (not errors), errors


def _nodes_key(payload: dict[str, Any]) -> str:
    return "parts" if isinstance(payload.get("parts"), list) else (
        "components" if isinstance(payload.get("components"), list) else "parts")


def _find_index(payload: dict[str, Any], target: str) -> int:
    nodes = _shape_nodes(payload)
    for i, node in enumerate(nodes):
        if _node_id(node, i + 1) == target:
            # map back to the raw list index (parts may contain non-dicts, rare)
            raw = payload.get(_nodes_key(payload), [])
            count = -1
            for j, raw_node in enumerate(raw):
                if isinstance(raw_node, dict):
                    count += 1
                    if count == i:
                        return j
    raise PatchOpError(f"node not found: '{target}'")


def _control_net(node: dict[str, Any]) -> list[Any]:
    # `polygons` last: it is the control net of an `extruded_region`, which is
    # what the topology-optimization writeback and the 2D contour path actually
    # produce. Without it, `move_control_point` refused ("node has no
    # control_net") on the dominant node kind the product writes — a documented
    # operation that could not touch the geometry it ships.
    for key in ("control_net", "control_points", "points", "net", "polygons"):
        cn = node.get(key)
        if isinstance(cn, list):
            return cn
    raise PatchOpError("node has no control net (control_net/control_points/points/net/polygons)")


def _apply_op(payload: dict[str, Any], op: dict[str, Any]) -> str:
    """Mutate ``payload`` for one operation. Returns a short description.
    Raises PatchOpError on any problem."""
    kind = str(op.get("op") or "").strip()
    if kind not in _OPS:
        raise PatchOpError(f"unknown op: '{kind}'")
    nodes_key = _nodes_key(payload)
    raw_nodes = payload.setdefault(nodes_key, [])

    if kind == "set_parameter":
        target = str(op.get("target") or "")
        param = op.get("parameter")
        if not param:
            raise PatchOpError("set_parameter requires 'parameter'")
        if "value" not in op:
            raise PatchOpError("set_parameter requires 'value'")
        node = raw_nodes[_find_index(payload, target)]
        name = str(param)
        # Write where the compilers READ. A node's own field wins over its
        # `parameters` map — `_compile_node` merges them as `{**params, **node}`,
        # and an `extruded_region` reads `thickness` / `polygons` off the node
        # directly and never looks at `parameters` at all. Always writing to
        # `parameters` meant `set_parameter thickness=30` on a real IR part
        # returned ok, reported "set X.parameters.thickness = 30.0", and
        # recompiled the SAME geometry: an approval-gated edit that changed
        # nothing (#513-adjacent dogfood).
        if name in _IDENTITY_FIELDS:
            raise PatchOpError(
                f"'{name}' identifies the node; use replace_node to change it "
                "(writing it as a parameter would be inert)"
            )
        if name in node:
            current = node[name]
            value = op["value"]
            # Type-preserving, not schema validation: a dimension that is a
            # number must stay one. Writing `thickness: "thick"` would be
            # accepted here and fail much later, inside the compiler, with an
            # error that no longer mentions the patch.
            if _is_number(current) and not _is_number(value):
                raise PatchOpError(
                    f"'{name}' on node '{target}' is numeric ({current!r}); "
                    f"refusing to set it to {value!r}"
                )
            node[name] = value
            return f"set {target}.{name} = {value}"
        node.setdefault("parameters", {})
        if not isinstance(node["parameters"], dict):
            raise PatchOpError(f"node '{target}' parameters is not an object")
        node["parameters"][name] = op["value"]
        return f"set {target}.parameters.{name} = {op['value']}"

    if kind == "move_control_point":
        target = str(op.get("target") or "")
        path = op.get("path")
        if not (isinstance(path, list) and len(path) == 2):
            raise PatchOpError("move_control_point requires 'path' = [i, j]")
        node = raw_nodes[_find_index(payload, target)]
        cn = _control_net(node)
        i, j = int(path[0]), int(path[1])
        try:
            current = cn[i][j]
        except (IndexError, TypeError) as exc:
            raise PatchOpError(f"control point [{i}][{j}] out of range") from exc
        # Move the point in ITS OWN dimension. Hard-coding 3 rejected every
        # planar control point: an extruded region's polygon vertices are [x, y]
        # in the local plane.
        try:
            dims = len(current)
        except TypeError as exc:
            raise PatchOpError(f"control point [{i}][{j}] is not a coordinate") from exc
        if "value" in op:
            new = [float(v) for v in op["value"]]
        elif "delta" in op:
            delta = op["delta"]
            if not (isinstance(delta, (list, tuple)) and len(delta) == dims):
                got = len(delta) if isinstance(delta, (list, tuple)) else "none"
                raise PatchOpError(
                    f"delta must have exactly {dims} component(s) for this control "
                    f"point; got {got}"
                )
            new = [float(current[k]) + float(delta[k]) for k in range(dims)]
        else:
            raise PatchOpError("move_control_point requires 'value' or 'delta'")
        if len(new) != dims:
            raise PatchOpError(
                f"control point [{i}][{j}] has {dims} component(s); got {len(new)}"
            )
        cn[i][j] = new
        return f"moved {target} control point [{i}][{j}] -> {new}"

    if kind == "add_node":
        node = op.get("node")
        if not isinstance(node, dict):
            raise PatchOpError("add_node requires a 'node' object")
        new_id = _node_id(node, len(_shape_nodes(payload)) + 1)
        existing = {_node_id(n, k + 1) for k, n in enumerate(_shape_nodes(payload))}
        if new_id in existing:
            raise PatchOpError(f"add_node id '{new_id}' already exists")
        raw_nodes.append(copy.deepcopy(node))
        return f"added node '{new_id}'"

    if kind == "remove_node":
        target = str(op.get("target") or "")
        idx = _find_index(payload, target)
        raw_nodes.pop(idx)
        _drop_adjacency(payload, target)
        return f"removed node '{target}'"

    if kind == "replace_node":
        target = str(op.get("target") or "")
        node = op.get("node")
        if not isinstance(node, dict):
            raise PatchOpError("replace_node requires a 'node' object")
        idx = _find_index(payload, target)
        raw_nodes[idx] = copy.deepcopy(node)
        return f"replaced node '{target}'"

    if kind == "connect":
        conn = op.get("connection")
        if not isinstance(conn, dict) or not conn.get("source") or not conn.get("target"):
            raise PatchOpError("connect requires connection={source, target}")
        ids = {_node_id(n, k + 1) for k, n in enumerate(_shape_nodes(payload))}
        for endpoint in ("source", "target"):
            if str(conn[endpoint]) not in ids:
                raise PatchOpError(f"connect endpoint not found: '{conn[endpoint]}'")
        adjacency = payload.setdefault("adjacency", [])
        entry = {"source": str(conn["source"]), "target": str(conn["target"]),
                 "type": str(conn.get("type") or "adjacent_to")}
        if entry not in adjacency:
            adjacency.append(entry)
        return f"connected {entry['source']} -> {entry['target']}"

    if kind == "disconnect":
        conn = op.get("connection")
        if not isinstance(conn, dict) or not conn.get("source") or not conn.get("target"):
            raise PatchOpError("disconnect requires connection={source, target}")
        adjacency = payload.get("adjacency", [])
        before = len(adjacency)
        s, t = str(conn["source"]), str(conn["target"])
        payload["adjacency"] = [
            a for a in adjacency
            if not (isinstance(a, dict) and str(a.get("source")) == s and str(a.get("target")) == t)
        ]
        if len(payload["adjacency"]) == before:
            raise PatchOpError(f"no connection {s} -> {t} to disconnect")
        return f"disconnected {s} -> {t}"

    if kind == "change_representation_backend":
        value = str(op.get("value") or "").strip()
        if not value:
            raise PatchOpError("change_representation_backend requires 'value'")
        resolved = resolve_representation(value)
        if resolved["fallback"]:
            raise PatchOpError(f"unknown representation: '{value}'")
        payload["representation"] = resolved["representation"]
        return f"representation -> {resolved['representation']}"

    raise PatchOpError(f"unhandled op: '{kind}'")  # pragma: no cover


def apply_shape_ir_patch(
    payload: dict[str, Any], patch: dict[str, Any], *, dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a patch to a Shape IR payload (atomic).

    Returns ``{ok, dry_run, operations, applied, failed, validation, new_payload,
    error}``. ``new_payload`` is the patched copy; the caller commits it only when
    ``ok`` and not ``dry_run``. The input ``payload`` is never mutated.
    """
    operations_in = patch.get("operations") if isinstance(patch, dict) else None
    op_results: list[dict[str, Any]] = []
    if not isinstance(operations_in, list) or not operations_in:
        return {
            "ok": False, "dry_run": dry_run, "operations": [], "applied": [], "failed": [],
            "validation": {"ok": False, "errors": ["patch has no 'operations'"]},
            "new_payload": payload, "error": "patch has no 'operations'",
        }

    working = copy.deepcopy(payload)
    ok = True
    error: str | None = None
    for index, op in enumerate(operations_in):
        entry: dict[str, Any] = {
            "index": index,
            "op": (op.get("op") if isinstance(op, dict) else None),
            "target": (op.get("target") if isinstance(op, dict) else None),
            "reason": (op.get("reason") if isinstance(op, dict) else None),
        }
        if not ok:
            entry["status"] = "skipped"
            op_results.append(entry)
            continue
        try:
            if not isinstance(op, dict):
                raise PatchOpError("operation is not an object")
            entry["detail"] = _apply_op(working, op)
            entry["status"] = "applied"
        except PatchOpError as exc:
            entry["status"] = "failed"
            entry["message"] = str(exc)
            ok = False
            error = f"operation {index} ({entry['op']}) failed: {exc}"
        op_results.append(entry)

    valid, verrors = validate_shape_ir(working) if ok else (False, ["aborted: an operation failed"])
    if ok and not valid:
        ok = False
        error = "patched Shape IR failed validation: " + "; ".join(verrors)

    return {
        "ok": ok,
        "dry_run": dry_run,
        "operations": op_results,
        "applied": [r for r in op_results if r.get("status") == "applied"],
        "failed": [r for r in op_results if r.get("status") == "failed"],
        "validation": {"ok": valid, "errors": verrors},
        "new_payload": working,
        "error": error,
    }


def build_patch_report(patch: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Assemble diagnostics/shape_ir_patch_report.json from an apply result."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "format_version": FORMAT_VERSION,
        "generated_at_utc": now,
        "patch_id": patch.get("patch_id"),
        "dry_run": result["dry_run"],
        "ok": result["ok"],
        "operation_count": len(result["operations"]),
        "applied_count": len(result["applied"]),
        "failed_count": len(result["failed"]),
        "operations": result["operations"],
        "validation": result["validation"],
        "error": result.get("error"),
        "provenance": {
            "applied_at_utc": now,
            "author": patch.get("author"),
            "tool": patch.get("tool"),
            "committed": bool(result["ok"] and not result["dry_run"]),
        },
    }


def write_patch_report(package_path: str | Path, report: dict[str, Any]) -> None:
    """Write the patch report to diagnostics/shape_ir_patch_report.json."""
    package_path = Path(package_path)
    if not package_path.exists():
        return
    data = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    tmp = package_path.with_suffix(".tmp.aieng")
    try:
        with (
            zipfile.ZipFile(package_path, "r") as src,
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst,
        ):
            for item in src.infolist():
                if item.filename != PATCH_REPORT_PATH:
                    dst.writestr(item, src.read(item.filename))
            dst.writestr(PATCH_REPORT_PATH, data)
        tmp.replace(package_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _drop_adjacency(payload: dict[str, Any], node_id: str) -> None:
    adj = payload.get("adjacency")
    if isinstance(adj, list):
        payload["adjacency"] = [
            a for a in adj
            if not (isinstance(a, dict) and node_id in (str(a.get("source")), str(a.get("target"))))
        ]
