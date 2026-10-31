"""Run scoped, credential-free runtime probes, stopping at unsafe child launches.

This is not the CADGenBench reference agent or a benchmark solution. Run with
the aieng311 interpreter, -I -B, an explicit sanitized env and DEVNULL stdin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
from typing import Any
import zipfile


class ProbeSecurityBlocked(RuntimeError):
    """An inspected execution path needs authorization or security adaptation."""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("discovery", "probes", "input-check"), required=True)
    parser.add_argument("--authorized-local-probes", action="store_true")
    args = parser.parse_args()
    if not sys.flags.isolated or not args.authorized_local_probes:
        parser.error("Requires -I and explicit --authorized-local-probes")
    root = args.workspace.resolve()
    run_root = args.run_root.resolve()
    if Path.cwd().resolve() != run_root:
        parser.error("Launch with cwd=run-root to isolate relative caches")
    output = run_root / args.phase
    output.mkdir(exist_ok=False)
    security_events: list[dict[str, Any]] = []

    def audit(event: str, values: tuple[Any, ...]) -> None:
        if event in {"socket.connect", "urllib.Request"}:
            security_events.append({"event": event, "reason": "network forbidden"})
            raise ProbeSecurityBlocked("Network forbidden in local probes")
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(values[0])).name.lower()
            if name in {".env", "auth.json", ".netrc", "credentials.json", ".pypirc"}:
                raise ProbeSecurityBlocked("Credential-file access forbidden")
        if event == "subprocess.Popen":
            executable, argv, _cwd, env = values
            tokens = shlex.split(argv, posix=False) if isinstance(argv, str) else list(argv)
            isolated = "-I" in tokens
            python_child = Path(str(executable)).name.lower().startswith("python")
            evidence = {"executable": str(executable), "argv": argv,
                        "explicit_env": isinstance(env, dict), "python_child": python_child,
                        "isolated": isolated}
            security_events.append(evidence)
            if not isinstance(env, dict):
                raise ProbeSecurityBlocked("Child launch blocked: missing explicit env")
            if python_child and not isolated:
                raise ProbeSecurityBlocked("Python child launch blocked: missing -I")

    sys.addaudithook(audit)
    os.environ.update(
        AIENG_PLATFORM_DATA=str(output / "data"),
        AIENG_RUNTIME_STATE_DIR=str(output / "runtime"),
        AIENG_ROOT=str(root / "aieng"),
        AIENG_SAMPLE_STEP=str(root / ".bench/data/204/input.step"),
        AIENG_MCP_REQUIRE_GUIDES="1",
    )
    sys.path[:0] = [str(root / "aieng-ui/backend"), str(root / "aieng/src")]
    from app.app_factory import create_app
    from app.config import Settings
    from app import runtime

    settings = Settings(
        platform_root=output / "platform", workspace_root=output / "workspace",
        data_root=output / "data", aieng_root=root / "aieng",
        sample_step=root / ".bench/data/204/input.step",
    )
    create_app(settings)
    calls: list[dict[str, Any]] = []
    readme_read = False
    cad_read = False

    def invoke(name: str, payload: dict[str, Any]) -> Any:
        nonlocal readme_read, cad_read
        if name.startswith("cad.") and not (readme_read and cad_read):
            raise ProbeSecurityBlocked("guide_required: onboarding and CAD guide must be read first")
        start = time.monotonic()
        result = runtime.invoke_tool(name, payload)
        calls.append({"tool": name, "input": payload, "output": result,
                      "duration_s": time.monotonic() - start})
        if name == "aieng.agent_readme":
            readme_read = bool(result.get("content"))
        if name == "aieng.guide":
            cad_read = payload.get("topic") == "cad" and bool(result.get("content"))
        return result

    readme = invoke("aieng.agent_readme", {})
    guide = invoke("aieng.guide", {"topic": "cad"})
    assert readme_read and cad_read
    (output / "onboarding.md").write_text(readme["content"], encoding="utf-8")
    (output / "cad-guide.md").write_text(guide["content"], encoding="utf-8")
    if args.phase == "discovery":
        print(readme["content"])
        print(guide["content"])
    elif args.phase == "input-check":
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer

        path = root / ".bench/data/204/input.step"
        reader = STEPControl_Reader()
        loaded = reader.ReadFile(str(path)) == IFSelect_RetDone
        transferred = reader.TransferRoots() if loaded else 0
        shape = reader.OneShape()
        solid_count = 0
        if not shape.IsNull():
            explorer = TopExp_Explorer(shape, TopAbs_SOLID)
            while explorer.More():
                solid_count += 1
                explorer.Next()
        checked = {
            "method": "Direct OCP input BREP check; separate from blocked build123d import",
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "read_ok": loaded, "roots_transferred": transferred,
            "brep_valid": not shape.IsNull() and bool(BRepCheck_Analyzer(shape).IsValid()),
            "solid_count": solid_count, "candidate_checked": False,
            "mesh_gate_checked": False,
        }
        (output / "input-brep.json").write_text(json.dumps(checked, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(checked, indent=2))
    else:
        step_path = root / ".bench/data/204/input.step"
        input_check = {
            "path": str(step_path), "sha256": hashlib.sha256(step_path.read_bytes()).hexdigest(),
            "scope": "Input BREP check only, not candidate or complete benchmark mesh gate",
        }
        try:
            from build123d import import_step
            from OCP.BRepCheck import BRepCheck_Analyzer

            source = import_step(str(step_path))
            input_check.update(
                status="checked", brep_valid=bool(BRepCheck_Analyzer(source.wrapped).IsValid()),
                solid_count=len(source.solids()), volume=source.volume,
            )
        except ProbeSecurityBlocked as exc:
            input_check.update(status="blocked_dependency_subprocess", error=str(exc))
        probes = []
        for fixture, kind in [("108", "generation"), ("204", "editing")]:
            project = invoke("aieng.create_project", {"name": "cadgenbench-local-" + fixture})
            plan = {
                "authorization": "Assignment permits local probes; no native modeling-plan approval claimed",
                "fixture": fixture, "task_type": kind, "project_id": project["id"],
                "scope": "Inspect project source and input; stop before geometry mutation without real approval",
                "native_plan_approval_tested": False,
            }
            context = invoke("aieng.agent_context", {"project_id": project["id"]})
            source_state = invoke("cad.get_source", {"project_id": project["id"]})
            approval = runtime.registered_tool_metadata("cad.confirm_modeling_plan")
            assert approval is not None and approval["requires_approval"]
            step_packages = []
            for package in (settings.projects_root / project["id"] / "packages").glob("*.aieng"):
                with zipfile.ZipFile(package) as archive:
                    if "geometry/generated.step" in archive.namelist():
                        step_packages.append(str(package))
            probes.append({
                "plan": plan, "source_state": source_state,
                "context_returned": isinstance(context, dict),
                "status": "blocked_before_geometry_mutation",
                "blocker": "Native modeling-plan approval is unavailable through direct invoke_tool",
                "confirmation_requires_approval": approval["requires_approval"],
                "output_step_packages": step_packages,
            })
        (output / "probes.json").write_text(json.dumps({
            "python": sys.version, "isolated": bool(sys.flags.isolated),
            "input_step_check": input_check, "probes": probes,
            "note": "invoke_tool is in-process, not a transport or native-approval UI test",
        }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"input_step_check": input_check, "probes": probes}, ensure_ascii=False, indent=2, default=str))
    (output / "tool-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (output / "security-events.json").write_text(json.dumps(security_events, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
