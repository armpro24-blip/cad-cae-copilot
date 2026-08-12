"""Agent project importer — register a STEP file into the Workbench with one command.

When an agent does NOT have MCP tools, this script atomically:
1. Imports STEP into a .aieng package
2. Enriches topology / feature graph
3. Creates project directory + metadata.json
4. Copies optional preview (GLB/STL) into viewer/
5. Marks project as viewer-ready

Usage:
    conda run -n aieng311 python agent_import_project.py my_model.step \
        --name "My Part" \
        --preview my_model.stl \
        --project-id mypart_001

The project will appear in the UI after the next refresh.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.aieng_bridge import import_step_to_aieng, enrich_imported_package
from app.config import Settings


def default_project(name: str, project_id: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    pid = project_id or f"agent_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    return {
        "id": pid,
        "name": name,
        "status": "empty",
        "created_at": now,
        "updated_at": now,
        "source_step": None,
        "aieng_file": f"{pid}.aieng",
        "web_asset": None,
        "web_asset_format": None,
        "preview_info": None,
        "last_validation_ok": None,
        "last_error": None,
        "last_chat_audit": None,
    }


def register_project(
    step_path: Path,
    name: str,
    preview_path: Path | None = None,
    project_id: str | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    # `Settings(data_root=...)` used to be enough; the dataclass has since gained
    # four required fields, so the documented `--data-root` flag raised TypeError
    # instead of importing anything. Override the one field, keep the rest.
    settings = Settings.from_env()
    if data_root is not None:
        settings = replace(settings, data_root=Path(data_root))
    projects_root = settings.projects_root
    projects_root.mkdir(parents=True, exist_ok=True)

    pid = project_id or f"agent_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    project_dir = projects_root / pid
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "source").mkdir(exist_ok=True)
    (project_dir / "packages").mkdir(exist_ok=True)
    (project_dir / "viewer").mkdir(exist_ok=True)
    (project_dir / "logs").mkdir(exist_ok=True)

    aieng_path = project_dir / f"{pid}.aieng"

    # 1. Import STEP to .aieng
    import_result = import_step_to_aieng(
        step_path=step_path,
        out_path=aieng_path,
        aieng_root=settings.aieng_root,
        overwrite=True,
    )

    # 2. Enrich
    enrich_result = enrich_imported_package(
        package_path=aieng_path,
        aieng_root=settings.aieng_root,
        topology_backend="occ",
    )

    # 3. Handle preview
    web_asset: str | None = None
    web_asset_format: str | None = None
    status = "package_uploaded"

    if preview_path and preview_path.exists():
        ext = preview_path.suffix.lower()
        if ext == ".glb":
            dst_name = "preview.glb"
            web_asset_format = "glb"
        elif ext == ".stl":
            dst_name = "preview.stl"
            web_asset_format = "stl"
        else:
            dst_name = f"preview{ext}"
            web_asset_format = ext.lstrip(".")
        viewer_path = project_dir / "viewer" / dst_name
        shutil.copy2(preview_path, viewer_path)
        web_asset = f"viewer/{dst_name}"
        if web_asset_format in ("glb", "stl"):
            status = "viewer_ready_glb" if web_asset_format == "glb" else "viewer_ready_stl"

    # 4. Inject preview into .aieng package too (so cad-preview endpoint works)
    if preview_path and preview_path.exists():
        import zipfile
        preview_name = f"geometry/preview.{web_asset_format}"
        tmp_aieng = str(aieng_path) + ".tmp"
        with zipfile.ZipFile(aieng_path, "r") as zfr:
            with zipfile.ZipFile(tmp_aieng, "w", zipfile.ZIP_DEFLATED) as zfw:
                for item in zfr.namelist():
                    if item == preview_name:
                        continue
                    zfw.writestr(item, zfr.read(item))
                zfw.write(str(preview_path), preview_name)
        Path(tmp_aieng).replace(aieng_path)

    # 5. Write metadata
    project = default_project(name, pid)
    project["aieng_file"] = f"{pid}.aieng"
    project["web_asset"] = web_asset
    project["web_asset_format"] = web_asset_format
    project["status"] = status
    meta_path = project_dir / "metadata.json"
    meta_path.write_text(json.dumps(project, indent=2), encoding="utf-8")

    return {
        "project_id": pid,
        "project_dir": str(project_dir),
        "metadata": project,
        "import": import_result,
        "enrich": enrich_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import STEP into Workbench project")
    parser.add_argument("step", help="Path to .step file")
    parser.add_argument("--name", default="Agent import", help="Project display name")
    parser.add_argument("--preview", help="Optional preview file (.glb or .stl)")
    parser.add_argument("--project-id", help="Explicit project ID (auto-generated if omitted)")
    parser.add_argument("--data-root", help="Override platform data root")
    args = parser.parse_args()

    result = register_project(
        step_path=Path(args.step),
        name=args.name,
        preview_path=Path(args.preview) if args.preview else None,
        project_id=args.project_id,
        data_root=Path(args.data_root) if args.data_root else None,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
