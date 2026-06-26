from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import runtime as _rt
from .app_context import build_app_context
from .config import Settings
from .logging_utils import configure_backend_logging
from .project_io import ensure_dirs
from .routers.agent import register_agent_routes
from .routers.cad_live import register_cad_live_routes
from .routers.catalog import register_catalog_routes
from .routers.discovery import register_discovery_routes
from .routers.evidence import register_evidence_routes
from .routers.intent_planner import register_intent_planner_routes
from .routers.project_analysis import register_project_analysis_routes
from .routers.project_chat import register_project_chat_routes
from .routers.project_workflows import register_project_workflow_routes
from .routers.runtime import register_runtime_routes
from .routers.settings import register_settings_routes
from .routers.system import register_system_routes
from .runtime_tool_registry import register_runtime_tools


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    ensure_dirs(active_settings)
    backend_log_path = configure_backend_logging(active_settings.data_root)
    from . import db

    db_path = active_settings.data_root / "aieng.db"
    db.init_db(db_path)
    server_started_at = datetime.now(timezone.utc).isoformat()
    app = FastAPI(title="aieng-platform")
    app.state.settings = active_settings
    app.state.db_path = db_path
    app.state.backend_log_path = str(backend_log_path)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/assets", StaticFiles(directory=str(active_settings.data_root)), name="assets")

    frontend_dist = active_settings.platform_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="workbench")

        from fastapi.responses import RedirectResponse

        @app.get("/")
        def _workbench_root_redirect() -> Any:
            return RedirectResponse(url="/app/")

    register_system_routes(app, active_settings=active_settings, server_started_at=server_started_at)
    register_discovery_routes(app, active_settings=active_settings)

    app_context = build_app_context(active_settings=active_settings, db_path=db_path)
    register_agent_routes(
        app,
        active_settings=active_settings,
        resolve_api_key=app_context.resolve_api_key,
        llm_config_from_payload=app_context.llm_config_from_payload,
        build_agent_response=app_context.build_agent_response,
        autopilot_store=app_context.autopilot_store,
        autopilot_run_response=app_context.autopilot_run_response,
        publish_agent_event=app_context.publish_agent_event,
    )
    register_intent_planner_routes(app, active_settings=active_settings)

    tool_handlers = register_runtime_tools(active_settings=active_settings, app_context=app_context)
    register_project_workflow_routes(
        app,
        active_settings=active_settings,
        db_path=db_path,
        app_context=app_context,
        tool_handlers=tool_handlers,
    )
    register_cad_live_routes(app, active_settings=active_settings, app_context=app_context)
    register_catalog_routes(app, active_settings=active_settings)
    register_project_analysis_routes(app, active_settings=active_settings)
    register_project_chat_routes(
        app,
        active_settings=active_settings,
        db_path=db_path,
        app_context=app_context,
    )
    register_settings_routes(app, db_path=db_path)
    register_evidence_routes(app, active_settings=active_settings)

    _rt.configure(
        Path(
            os.environ.get(
                "AIENG_RUNTIME_STATE_DIR",
                str(active_settings.data_root / "runtime" / "runs"),
            )
        )
    )
    register_runtime_routes(app, active_settings=active_settings)
    return app
