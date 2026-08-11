from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_VERSION_PROBE_SCRIPT = """\
import json
import os

result_path = os.environ["AIENG_FREECAD_PROBE_RESULT"]
result = {
    "status": "ok",
    "freecad_version": None,
    "part_available": False,
    "mesh_available": False,
    "meshpart_available": False,
}

try:
    import FreeCAD
    result["freecad_version"] = ".".join(str(v) for v in FreeCAD.Version()[:3])
except Exception as exc:
    result = {"status": "error", "error": f"FreeCAD import failed: {exc}"}
else:
    try:
        import Part  # noqa: F401
        result["part_available"] = True
    except Exception as exc:
        result["part_error"] = str(exc)
    try:
        import Mesh  # noqa: F401
        result["mesh_available"] = True
    except Exception as exc:
        result["mesh_error"] = str(exc)
    try:
        import MeshPart  # noqa: F401
        result["meshpart_available"] = True
    except Exception as exc:
        result["meshpart_error"] = str(exc)

with open(result_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh)
"""


_STEP_TO_STL_SCRIPT = """\
import json
import os

import FreeCAD
import Mesh
import MeshPart
import Part

input_path = os.environ["AIENG_PREVIEW_INPUT"]
output_path = os.environ["AIENG_PREVIEW_OUTPUT"]
result_path = os.environ["AIENG_PREVIEW_RESULT"]

doc = FreeCAD.newDocument("AiengPreview")
Part.insert(input_path, doc.Name)
doc.recompute()

objects = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
if not objects:
    raise ValueError("No exportable objects found after STEP import.")

meshes = []
face_count = 0
edge_count = 0
for obj in objects:
    shape = obj.Shape
    face_count += len(shape.Faces)
    edge_count += len(shape.Edges)
    meshes.append(MeshPart.meshFromShape(shape, LinearDeflection=0.1))

if len(meshes) == 1:
    final_mesh = meshes[0]
else:
    final_mesh = Mesh.Mesh()
    for mesh in meshes:
        final_mesh.addMesh(mesh)

final_mesh.write(output_path)

result = {
    "status": "ok",
    "freecad_version": ".".join(str(v) for v in FreeCAD.Version()[:3]),
    "object_count": len(objects),
    "face_count": face_count,
    "edge_count": edge_count,
    "output_path": output_path,
}

with open(result_path, "w", encoding="utf-8") as fh:
    json.dump(result, fh)
"""


class FreecadPreviewProvider:
    """Minimal real provider for STEP -> STL preview export through FreeCADCmd.

    This provider is intentionally narrow: it only claims readiness when a
    usable FreeCADCmd runtime is available, and it only implements preview
    export today. Import/summary/edit flows still degrade honestly to the core
    bridge or package fallback paths elsewhere in the stack.
    """

    provider = "freecad"

    def __init__(self, settings: Any, config: dict[str, str]) -> None:
        self._settings = settings
        self._config = config

    def probe_capabilities(self, *, whitelisted_tools: list[str]) -> dict[str, Any]:
        aieng_root = Path(str(self._config.get("aieng_root") or getattr(self._settings, "aieng_root", ""))).resolve()
        topology_requested = str(self._config.get("topology_backend") or "auto")
        topology_resolved = self._resolve_topology_backend(topology_requested)
        freecad_mcp_root = self._resolve_freecad_mcp_root()
        freecad_cmd = self._resolve_freecad_cmd()
        probe = self._run_version_probe(freecad_cmd) if freecad_cmd else None

        issues: list[str] = []
        bridge_error: str | None = None
        ready = False

        if freecad_cmd is None:
            bridge_error = (
                "FreeCADCmd was not found. Set Runtime freecad_home, FREECAD_HOME, "
                "or FREECAD_MCP_FREECAD_PATH to a FreeCAD install."
            )
            issues.append("FreeCADCmd not found.")
        elif probe is None:
            bridge_error = "FreeCADCmd probe did not return a result."
            issues.append("FreeCADCmd probe did not return a result.")
        elif probe.get("status") != "ok":
            bridge_error = str(probe.get("error") or "FreeCADCmd probe failed.")
            issues.append("FreeCADCmd exists but runtime probe failed.")
        else:
            if not probe.get("part_available"):
                issues.append("FreeCAD Part module is unavailable.")
            if not probe.get("mesh_available"):
                issues.append("FreeCAD Mesh module is unavailable.")
            if not probe.get("meshpart_available"):
                issues.append("FreeCAD MeshPart module is unavailable, so STL preview export cannot run.")
            ready = bool(
                probe.get("part_available")
                and probe.get("mesh_available")
                and probe.get("meshpart_available")
            )

        if ready and probe and probe.get("freecad_version"):
            issues.append(f"FreeCAD {probe['freecad_version']} ready for STEP preview export.")

        return {
            "provider": self.provider,
            "available": ready,
            "reason": None if ready else (bridge_error or "FreeCAD preview runtime not ready"),
            "topology_backend_requested": topology_requested,
            "topology_backend_resolved": topology_resolved,
            "aieng_root": str(aieng_root),
            "aieng_src_exists": (aieng_root / "src").exists(),
            "freecad_mcp_root": str(freecad_mcp_root) if freecad_mcp_root else str(self._config.get("freecad_mcp_root") or ""),
            "freecad_mcp_src_exists": bool(freecad_mcp_root and (freecad_mcp_root / "src").exists()),
            "freecad_home": str(self._config.get("freecad_home") or ""),
            "freecad_cmd": str(freecad_cmd) if freecad_cmd else "",
            "freecad_python": "",
            "freecad_cmd_exists": bool(freecad_cmd and freecad_cmd.exists()),
            "freecad_python_exists": False,
            "ready": ready,
            "issues": issues,
            "bridge_error": bridge_error,
            "tools": ["cad_export_stl"] if ready else [],
            "whitelisted_tools": whitelisted_tools,
            "freecad_version": probe.get("freecad_version") if isinstance(probe, dict) else None,
        }

    def import_step_to_package(self, *, step_path: Path, out_path: Path) -> dict[str, Any]:
        return self._unavailable("import_step_to_package")

    def enrich_package(self, *, package_path: Path, topology_backend: str) -> dict[str, Any]:
        return self._unavailable("enrich_package")

    def validate_package(self, *, package_path: Path) -> dict[str, Any]:
        return self._unavailable("validate_package")

    def package_summary_snapshot(self, *, package_path: Path) -> dict[str, Any]:
        return self._unavailable("package_summary_snapshot")

    def check_mcp_operation(
        self,
        *,
        package_path: str | None,
        payload: dict[str, Any],
        whitelisted_tools: list[str],
    ) -> dict[str, Any]:
        return self._unavailable("check_mcp_operation")

    def parse_patch_proposal(self, *, patch_json: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("parse_patch_proposal")

    def prepare_patch_preflight(self, *, package_path: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        return self._unavailable("prepare_patch_preflight")

    def export_step_preview_to_stl(self, *, step_path: Path, stl_path: Path) -> dict[str, Any]:
        if not step_path.exists():
            return {
                "status": "error",
                "code": "missing_source_step",
                "message": f"Source STEP not found: {step_path}",
            }
        freecad_cmd = self._resolve_freecad_cmd()
        if freecad_cmd is None:
            return {
                "status": "unavailable",
                "code": "freecad_cmd_missing",
                "message": (
                    "FreeCADCmd was not found. Configure freecad_home (or FREECAD_HOME / "
                    "FREECAD_MCP_FREECAD_PATH) before requesting a model preview."
                ),
            }

        probe = self._run_version_probe(freecad_cmd)
        if not probe or probe.get("status") != "ok":
            return {
                "status": "unavailable",
                "code": "freecad_probe_failed",
                "message": str((probe or {}).get("error") or "FreeCAD runtime probe failed."),
            }
        if not probe.get("part_available") or not probe.get("mesh_available") or not probe.get("meshpart_available"):
            missing = [
                name
                for name, ok in (
                    ("Part", probe.get("part_available")),
                    ("Mesh", probe.get("mesh_available")),
                    ("MeshPart", probe.get("meshpart_available")),
                )
                if not ok
            ]
            return {
                "status": "unavailable",
                "code": "freecad_preview_modules_missing",
                "message": f"FreeCAD preview export requires modules missing from this runtime: {', '.join(missing)}",
            }

        try:
            result = self._run_step_to_stl(step_path=step_path, stl_path=stl_path, freecad_cmd=freecad_cmd)
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "error",
                "code": "freecad_preview_timeout",
                "message": f"FreeCAD preview export timed out after {exc.timeout}s.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "code": "freecad_preview_failed",
                "message": f"{type(exc).__name__}: {exc}",
            }

        if result.get("status") != "ok" or not stl_path.exists():
            return {
                "status": "error",
                "code": "freecad_preview_failed",
                "message": str(result.get("error") or "FreeCAD preview export did not produce an STL asset."),
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
            }

        return {
            "status": "ok",
            "provider": self.provider,
            "freecad_version": result.get("freecad_version"),
            "object_count": result.get("object_count"),
            "face_count": result.get("face_count"),
            "edge_count": result.get("edge_count"),
            "stl_path": str(stl_path),
        }

    def _resolve_freecad_mcp_root(self) -> Path | None:
        configured = str(self._config.get("freecad_mcp_root") or "").strip()
        candidates = [Path(configured)] if configured else []
        workspace_candidate = Path(getattr(self._settings, "workspace_root", "")) / "legacy" / "aieng-freecad-mcp"
        candidates.append(workspace_candidate)
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate.resolve()
        return None

    def _resolve_freecad_cmd(self) -> Path | None:
        raw_home = str(self._config.get("freecad_home") or "").strip()
        env_home = os.environ.get("FREECAD_MCP_FREECAD_PATH", "").strip() or os.environ.get("FREECAD_HOME", "").strip()
        for raw in (raw_home, env_home):
            candidate = self._resolve_freecad_cmd_from_hint(raw)
            if candidate is not None:
                return candidate

        for raw in (
            r"C:\Program Files\FreeCAD 1.0",
            r"C:\Program Files\FreeCAD 0.22",
            r"C:\Program Files\FreeCAD 0.21",
        ):
            candidate = self._resolve_freecad_cmd_from_hint(raw)
            if candidate is not None:
                return candidate

        for name in ("FreeCADCmd", "freecadcmd", "FreeCADCmd.exe", "freecadcmd.exe"):
            found = shutil.which(name)
            if found:
                return Path(found).resolve()
        return None

    @staticmethod
    def _resolve_freecad_cmd_from_hint(raw: str) -> Path | None:
        if not raw:
            return None
        hint = Path(raw)
        if hint.is_file():
            return hint.resolve()
        candidates = (
            hint / "bin" / "FreeCADCmd.exe",
            hint / "bin" / "freecadcmd.exe",
            hint / "FreeCADCmd.exe",
            hint / "bin" / "FreeCADCmd",
            hint / "bin" / "freecadcmd",
            hint / "FreeCADCmd",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _resolve_topology_backend(requested: str | None) -> str:
        value = str(requested or "auto").strip().lower()
        if value != "auto":
            return value
        return "occ" if importlib.util.find_spec("OCP") is not None or importlib.util.find_spec("OCC") is not None else "mock"

    @staticmethod
    def _run_version_probe(freecad_cmd: Path) -> dict[str, Any] | None:
        with tempfile.TemporaryDirectory(prefix="aieng-freecad-probe-") as tmpdir:
            tmp_root = Path(tmpdir)
            script_path = tmp_root / "probe.py"
            result_path = tmp_root / "result.json"
            script_path.write_text(_VERSION_PROBE_SCRIPT, encoding="utf-8")
            completed = subprocess.run(
                [str(freecad_cmd), str(script_path)],
                env={**os.environ, "AIENG_FREECAD_PROBE_RESULT": str(result_path), "PYTHONIOENCODING": "utf-8"},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
            if not result_path.exists():
                detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
                return {"status": "error", "error": f"FreeCADCmd probe produced no result file: {detail}"}
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["stdout"] = completed.stdout
            result["stderr"] = completed.stderr
            result["return_code"] = completed.returncode
            return result

    @staticmethod
    def _run_step_to_stl(*, step_path: Path, stl_path: Path, freecad_cmd: Path) -> dict[str, Any]:
        stl_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aieng-freecad-preview-") as tmpdir:
            tmp_root = Path(tmpdir)
            script_path = tmp_root / "step_to_stl.py"
            result_path = tmp_root / "result.json"
            script_path.write_text(_STEP_TO_STL_SCRIPT, encoding="utf-8")
            completed = subprocess.run(
                [str(freecad_cmd), str(script_path)],
                env={
                    **os.environ,
                    "AIENG_PREVIEW_INPUT": str(step_path.resolve()),
                    "AIENG_PREVIEW_OUTPUT": str(stl_path.resolve()),
                    "AIENG_PREVIEW_RESULT": str(result_path),
                    "PYTHONIOENCODING": "utf-8",
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                stdin=subprocess.DEVNULL,
            )
            if not result_path.exists():
                detail = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
                return {
                    "status": "error",
                    "error": f"FreeCAD preview export produced no result file: {detail}",
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "return_code": completed.returncode,
                }
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["stdout"] = completed.stdout
            result["stderr"] = completed.stderr
            result["return_code"] = completed.returncode
            if completed.returncode != 0 and result.get("status") == "ok":
                result["status"] = "error"
                result["error"] = completed.stderr.strip() or completed.stdout.strip() or f"FreeCADCmd exited {completed.returncode}"
            return result

    @staticmethod
    def _unavailable(op: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "code": "freecad_preview_provider_minimal",
            "operation": op,
            "message": (
                f"{op} is not implemented in the minimal FreeCAD preview provider yet. "
                "Only STEP preview export is wired to a real CAD runtime today."
            ),
        }
