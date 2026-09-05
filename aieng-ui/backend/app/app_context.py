"""Create-app scoped services shared by route and runtime-tool registrars.

The callbacks remain closures over one app instance, preserving the historical
behavior while keeping ``app_factory`` focused on composition.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .legacy_app_symbols import sync_main_symbols
from .logging_utils import log_exception

LOGGER = logging.getLogger("app.app_factory")


def _sync_main_symbols() -> None:
    sync_main_symbols(globals())


@dataclass(frozen=True)
class AppContext:
    resolve_api_key: Any
    llm_config_from_payload: Any
    build_agent_response: Any
    autopilot_store: Any
    autopilot_run_response: Any
    publish_live_event: Any
    publish_agent_event: Any
    publish_chat_session_event: Any
    publish_chat_message_event: Any
    run_session_status: Any
    publish_autopilot_state: Any
    sync_autopilot_session: Any
    add_chat_message_and_publish: Any
    write_autopilot_audit: Any
    start_autopilot_worker: Any
    mark_autopilot_failed: Any
    cancel_session_autopilot_runs: Any
    delete_project_autopilot_runs: Any
    delete_project_everywhere: Any
    agent_context_with_session_summary: Any
    agent_context_with_session_summary_cached: Any
    project_package_reader: Any
    load_project_simulation_setup: Any
    load_project_feature_parameters: Any
    session_approval_mode: Any


def build_app_context(*, active_settings: Any, db_path: Any) -> AppContext:
    _sync_main_symbols()
    active_autopilot_workers: set[str] = set()
    active_autopilot_workers_lock = threading.Lock()

    def _resolve_api_key(data: dict[str, Any] | None = None) -> str | None:
        """Return API key from request payload, falling back to persisted settings."""
        payload = data or {}
        api_key = payload.get("api_key")
        if isinstance(api_key, str) and api_key:
            return api_key
        try:
            from . import db
            record = db.get_setting_record(db_path, "api_key")
            if record and isinstance(record.get("value"), str) and record["value"]:
                return record["value"]
        except Exception:
            log_exception(
                LOGGER,
                "Failed to load persisted API key from settings storage.",
                subsystem="app_factory.runtime.api_key_lookup",
                context={"db_path": db_path},
            )
        return None

    def _llm_config_from_payload(data: dict[str, Any] | None = None, *, include_api_key: bool = False) -> dict[str, Any]:
        llm_config = agent_engine.sanitize_llm_config((data or {}).get("llm_config"))
        if include_api_key:
            api_key = _resolve_api_key(data)
            if api_key:
                llm_config = {**llm_config, "api_key": api_key}
        return llm_config

    def _build_agent_response(data: dict[str, Any]) -> dict[str, Any]:
        message = str(data.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        project_id = data.get("project_id") or None
        project_summary: dict[str, Any] | None = None
        if project_id:
            from . import agent_context

            project_summary = agent_context.build_agent_context(active_settings, str(project_id))
        patch_json = data.get("patch_json") if isinstance(data.get("patch_json"), dict) else None
        selected_geometry = agent_engine.sanitize_selected_geometry(data.get("selected_geometry"))
        return agent_engine.build_agent_plan(
            settings=active_settings,
            message=message,
            project_id=str(project_id) if project_id else None,
            project_summary=project_summary,
            runtime_tools=_rt.registered_tools_info(),
            capabilities=agent_workbench.list_capabilities(active_settings),
            llm_config=_llm_config_from_payload(data, include_api_key=True),
            selected_geometry=selected_geometry,
            patch_json=patch_json,
            dry_run=bool(data.get("dry_run", False)),
        )

    def _autopilot_store():
        from .agent_autopilot.store import AutopilotStore

        return AutopilotStore(active_settings.data_root / "agent_autopilot" / "runs")

    def _live_autopilot_run_ids() -> set[str]:
        with active_autopilot_workers_lock:
            return set(active_autopilot_workers)

    def _autopilot_run_response(state: Any) -> dict[str, Any]:
        from .agent_autopilot.run_recovery import enrich_run_response

        return enrich_run_response(state, live_run_ids=_live_autopilot_run_ids())

    def _publish_live_event(event: dict[str, Any]) -> None:
        from .agent_autopilot.event_contract import apply_event_metadata

        event = apply_event_metadata(event)
        if event.get("type") in {
            "agent_message",
            "tool_started",
            "tool_completed",
            "tool_failed",
            "approval_requested",
            "approval_resolved",
            "artifact_ready",
            "viewer_asset_changed",
            "run_status_changed",
            "run_cancelled",
        } and event.get("project_id"):
            try:
                from . import db

                event_id = str(event.get("event_id") or f"{event.get('type')}-{event.get('call_id') or uuid.uuid4().hex[:12]}")
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {
                    k: v for k, v in event.items() if k not in {"payload"}
                }
                db.add_agent_event(
                    db_path,
                    event_id=event_id,
                    event_type=str(event.get("type")),
                    payload=payload,
                    run_id=str(event.get("run_id")) if event.get("run_id") else None,
                    project_id=str(event.get("project_id")) if event.get("project_id") else None,
                    session_id=str(event.get("session_id")) if event.get("session_id") else None,
                    status=str(event.get("status")) if event.get("status") else None,
                    content=str(event.get("content") or event.get("message")) if event.get("content") or event.get("message") else None,
                    created_at=str(event.get("created_at")) if event.get("created_at") else None,
                )
                event.setdefault("event_id", event_id)
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to persist live agent event before publish.",
                    subsystem="app_factory.live_event.persist",
                    context={
                        "event_type": event.get("type"),
                        "project_id": event.get("project_id"),
                        "run_id": event.get("run_id"),
                    },
                )
        try:
            from . import agent_activity

            agent_activity.publish(event)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to publish live agent event to activity stream.",
                subsystem="app_factory.live_event.publish",
                context={
                    "event_type": event.get("type"),
                    "project_id": event.get("project_id"),
                    "run_id": event.get("run_id"),
                },
            )

    def _publish_agent_event(event: dict[str, Any]) -> None:
        from . import db
        from .agent_autopilot.event_contract import apply_event_metadata

        event = apply_event_metadata(event)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {
            k: v for k, v in event.items() if k not in {"payload"}
        }
        event_id = str(event.get("event_id") or uuid.uuid4().hex[:16])
        event_type = str(event.get("type") or "agent_event")
        try:
            row = db.add_agent_event(
                db_path,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                run_id=str(event.get("run_id")) if event.get("run_id") else None,
                project_id=str(event.get("project_id")) if event.get("project_id") else None,
                session_id=str(event.get("session_id")) if event.get("session_id") else None,
                status=str(event.get("status")) if event.get("status") else None,
                content=str(event.get("content")) if event.get("content") else None,
                created_at=str(event.get("created_at")) if event.get("created_at") else None,
            )
            event = {**event, **{k: row[k] for k in ("event_id", "created_at") if k in row}}
        except Exception:
            log_exception(
                LOGGER,
                "Failed to persist agent event row.",
                subsystem="app_factory.agent_event.persist",
                context={
                    "event_type": event_type,
                    "project_id": event.get("project_id"),
                    "run_id": event.get("run_id"),
                },
            )
        _publish_live_event(event)

    def _publish_chat_session_event(session: dict[str, Any], action: str = "updated") -> None:
        _publish_live_event({
            "type": "chat_session_changed",
            "action": action,
            "project_id": session.get("project_id"),
            "session_id": session.get("id"),
            "session": session,
        })

    def _publish_chat_message_event(message: dict[str, Any], action: str = "created") -> None:
        _publish_live_event({
            "type": "chat_message",
            "action": action,
            "project_id": message.get("project_id"),
            "session_id": message.get("session_id"),
            "chat_message": message,
        })

    def _run_session_status(status: str) -> str:
        # "blocked" means the run is waiting on the user (ask_user / pause), not
        # finished — keep the session active so the UI restores the run + card.
        if status in {"running", "awaiting_approval", "chatting", "blocked"}:
            return "running"
        if status in {"completed", "failed", "cancelled"}:
            return status
        return "idle"

    def _publish_autopilot_state(state: Any) -> None:
        payload = state.model_dump() if hasattr(state, "model_dump") else dict(state)
        _publish_live_event({
            "type": "autopilot_update",
            "project_id": payload.get("project_id"),
            "session_id": payload.get("session_id"),
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "run": payload,
        })

    def _sync_autopilot_session(state: Any) -> None:
        session_id = getattr(state, "session_id", None)
        project_id = getattr(state, "project_id", None)
        run_id = getattr(state, "run_id", None)
        status = getattr(state, "status", None)
        if not session_id or not project_id or not run_id or not status:
            return
        try:
            from . import db

            session = db.update_chat_session(
                db_path,
                str(session_id),
                status=_run_session_status(str(status)),
                active_run_id=str(run_id),
            )
            if session and session.get("project_id") == project_id:
                _publish_chat_session_event(session)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to sync autopilot status back into chat session state.",
                subsystem="app_factory.autopilot.session_sync",
                context={"project_id": project_id, "run_id": run_id, "session_id": session_id},
            )

    def _add_chat_message_and_publish(
        *,
        project_id: str,
        role: str,
        content: str,
        session_id: str | None = None,
        mode: str | None = None,
        created_at: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from . import db

        row = db.add_chat_message(
            db_path,
            project_id=project_id,
            session_id=session_id,
            role=role,
            content=content,
            mode=mode,
            created_at=created_at,
            extra=extra,
        )
        _publish_chat_message_event(row)
        if session_id:
            session = db.get_chat_session(db_path, session_id)
            if session:
                _publish_chat_session_event(session)
        return row

    def _write_autopilot_audit(project_id: str | None, event: str, payload: dict[str, Any]) -> None:
        if not project_id:
            return
        try:
            write_audit_log(active_settings, project_id, "agent_autopilot", {
                "kind": event,
                **payload,
                "created_at": now_iso(),
            })
        except Exception:
            log_exception(
                LOGGER,
                "Failed to write autopilot audit log.",
                subsystem="app_factory.audit.autopilot",
                context={"project_id": project_id, "event": event},
            )

    def _start_autopilot_worker(target: Callable[[], None], *, run_id: str | None = None) -> None:
        if run_id:
            with active_autopilot_workers_lock:
                active_autopilot_workers.add(run_id)

        def _wrapped() -> None:
            try:
                target()
            finally:
                if run_id:
                    with active_autopilot_workers_lock:
                        active_autopilot_workers.discard(run_id)

        thread = threading.Thread(target=_wrapped, name="aieng-autopilot-worker", daemon=True)
        thread.start()

    def _mark_autopilot_failed(store: Any, run_id: str, exc: Exception) -> None:
        try:
            loaded = store.load(run_id)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to load autopilot run state while marking worker failure.",
                subsystem="app_factory.autopilot.mark_failed_load",
                context={"run_id": run_id},
            )
            return
        if getattr(loaded, "status", None) in {"completed", "failed", "cancelled"}:
            return
        loaded.status = "failed"
        loaded.errors.append(str(exc))
        store.save(loaded)
        _sync_autopilot_session(loaded)
        _publish_autopilot_state(loaded)
        _publish_agent_event({
            "event_id": f"{run_id}-worker-failed-{uuid.uuid4().hex[:8]}",
            "type": "tool_failed",
            "project_id": loaded.project_id,
            "session_id": loaded.session_id,
            "run_id": loaded.run_id,
            "status": "failed",
            "content": f"Local agent worker failed: {exc}",
            "payload": {"error": str(exc), "adapter_id": loaded.adapter_id},
            "created_at": now_iso(),
        })

    def _cancel_session_autopilot_runs(project_id: str, session_id: str) -> int:
        # De-engined (autopilot engine retired in the MCP-first cutover): mark any
        # in-flight persisted runs cancelled directly in the store. No new autopilot
        # runs are created anymore; this only tidies stale records on session cleanup.
        store = _autopilot_store()
        cancelled = 0
        for state in store.list_runs(project_id=project_id, session_id=session_id):
            if state.status in {"completed", "failed", "cancelled"}:
                continue
            try:
                state.status = "cancelled"
                state.updated_at = now_iso()
                store.save(state)
                _sync_autopilot_session(state)
                cancelled += 1
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to cancel in-flight autopilot run during session cleanup.",
                    subsystem="app_factory.autopilot.cancel_session_run",
                    context={"project_id": project_id, "session_id": session_id, "run_id": state.run_id},
                )
                continue
        return cancelled

    def _delete_project_autopilot_runs(project_id: str) -> int:
        try:
            return _autopilot_store().delete_runs(project_id=project_id)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to delete persisted autopilot runs for project cleanup.",
                subsystem="app_factory.autopilot.delete_project_runs",
                context={"project_id": project_id},
            )
            return 0

    def _remove_project_directory(target: Path) -> list[str]:
        """Delete a project's directory. Returns the files it could not remove.

        `metadata.json` goes LAST, and only once everything else is gone. The
        order is the difference between a retryable failure and an invisible
        one: `rmtree` used to take the metadata out first, so a single
        undeletable file (a `.aieng` package still open — routine on Windows)
        left the project's engineering data on disk while `get_project` began
        answering 404. The result was a directory no listing showed, no tool
        could address, and nothing would ever clean up — and the caller was told
        `deleted: true`.
        """
        import shutil

        if not target.exists():
            return []
        metadata = target / "metadata.json"
        for entry in sorted(target.iterdir()):
            if entry == metadata:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    entry.unlink()
                except OSError:
                    pass
        remaining = sorted(
            str(path.relative_to(target))
            for path in target.rglob("*")
            if path.is_file() and path != metadata
        )
        if remaining:
            # Leave the metadata in place: the project stays listed, and the
            # caller can close whatever holds the file and call this again.
            return remaining
        shutil.rmtree(target, ignore_errors=True)
        return [] if not target.exists() else ["metadata.json"]

    def _delete_project_everywhere(project_id: str) -> dict[str, Any]:
        import shutil
        from . import db

        get_project(active_settings, project_id)  # 404 if unknown
        runs_removed = _delete_project_autopilot_runs(project_id)
        chat_rows = 0
        try:
            chat_rows = db.delete_project_chat(db_path, project_id)
        except Exception:
            log_exception(
                LOGGER,
                "Failed to delete project chat rows during project removal.",
                subsystem="app_factory.project.delete_chat",
                context={"project_id": project_id, "db_path": db_path},
            )
        target = project_dir(active_settings, project_id)
        remaining = _remove_project_directory(target)
        _publish_live_event({"type": "project_deleted", "project_id": project_id})
        result: dict[str, Any] = {
            "deleted": not remaining,
            "project_id": project_id,
            "chat_rows_removed": chat_rows,
            "autopilot_runs_removed": runs_removed,
        }
        if remaining:
            result["remaining_files"] = remaining
            result["directory"] = str(target)
        return result


    def _agent_context_with_session_summary(project_id: str | None, session_id: str | None) -> dict[str, Any] | None:
        return _agent_context_with_session_summary_cached(project_id, session_id, package_reader=None)

    def _agent_context_with_session_summary_cached(
        project_id: str | None,
        session_id: str | None,
        *,
        package_reader: Any = None,
    ) -> dict[str, Any] | None:
        agent_context_snapshot: dict[str, Any] | None = None
        if project_id:
            try:
                from . import agent_context

                agent_context_snapshot = agent_context.build_agent_context(
                    active_settings,
                    project_id,
                    package_reader=package_reader,
                )
            except Exception as exc:
                agent_context_snapshot = {"error": str(exc)}
        if session_id:
            try:
                from . import db

                session = db.get_chat_session(db_path, session_id)
                if session and session.get("context_summary"):
                    agent_context_snapshot = dict(agent_context_snapshot or {})
                    agent_context_snapshot["context_summary"] = session["context_summary"]
            except Exception:
                log_exception(
                    LOGGER,
                    "Failed to attach session context summary to agent context snapshot.",
                    subsystem="app_factory.agent_context.session_summary",
                    context={"project_id": project_id, "session_id": session_id},
                )
        return agent_context_snapshot

    def _project_package_reader(project_id: str | None) -> Any:
        if not project_id:
            return None
        from . import package_inspection, project_io
        from pathlib import Path

        try:
            project = project_io.get_project(active_settings, project_id)
        except HTTPException:
            return None
        aieng_file = project.get("aieng_file")
        if not aieng_file:
            return None
        package_path = project_io.resolve_project_path(active_settings, project_id, str(Path(aieng_file)))
        if package_path is None or not package_path.exists():
            return None
        return package_inspection.PackageReadCache(package_path)

    def _load_project_simulation_setup(
        project_id: str | None,
        *,
        package_reader: Any = None,
    ) -> dict[str, Any] | None:
        """Read a project's direct CAE setup artifact for /simulate readiness.

        Opens the .aieng package and reads simulation/setup.* / cae/setup.* via the
        pure ``load_simulation_setup`` loader. Best-effort: any failure returns None
        so readiness falls back to the agent_context cae block.
        """
        if not project_id:
            return None
        try:
            from . import package_inspection, project_io
            from .agent_autopilot.simulation_readiness import load_simulation_setup

            owns_reader = package_reader is None
            reader = package_reader or _project_package_reader(project_id)
            if reader is None:
                return None
            try:
                return load_simulation_setup(
                    lambda name: package_inspection.read_package_text(reader, name)
                )
            finally:
                if owns_reader:
                    reader.close()
        except Exception:
            log_exception(
                LOGGER,
                "Failed to load direct simulation setup artifact; falling back to CAE context.",
                subsystem="app_factory.simulation.setup_load",
                context={"project_id": project_id},
            )
            return None

    def _load_project_feature_parameters(
        project_id: str | None,
        *,
        package_reader: Any = None,
    ) -> list[dict[str, Any]] | None:
        """Read a project's editable feature-graph parameter index for slot binding.

        Opens the .aieng package, reads graph/feature_graph.json, and flattens it via
        the pure ``build_parameter_index``. Best-effort: any failure / missing graph
        returns None, so parametric-slot binding degrades to ``known=None``
        (unverified) rather than a false negative.
        """
        if not project_id:
            return None
        try:
            from . import package_inspection, project_io
            from .agent_autopilot.parameter_binding import build_parameter_index

            owns_reader = package_reader is None
            reader = package_reader or _project_package_reader(project_id)
            if reader is None:
                return None
            try:
                feature_graph = package_inspection.read_package_json(
                    reader, "graph/feature_graph.json"
                )
                # The source lets a stored `local` scope be re-checked against
                # the live constant→part usage (a graph can predate the binder).
                source_code = package_inspection.read_package_text(
                    reader, "geometry/source.py"
                )
                return build_parameter_index(feature_graph, source_code)
            finally:
                if owns_reader:
                    reader.close()
        except Exception:
            log_exception(
                LOGGER,
                "Failed to load editable feature parameter index from package.",
                subsystem="app_factory.parameter_index.load",
                context={"project_id": project_id},
            )
            return None

    def _session_approval_mode(session_id: str | None) -> str:
        if not session_id:
            return "balanced"
        try:
            from . import db

            session = db.get_chat_session(db_path, session_id)
            mode = session.get("approval_mode") if session else None
            return mode if mode in {"balanced", "strict", "manual"} else "balanced"
        except Exception:
            log_exception(
                LOGGER,
                "Failed to resolve session approval mode; defaulting to balanced.",
                subsystem="app_factory.session.approval_mode",
                context={"session_id": session_id},
            )
            return "balanced"

    return AppContext(
        resolve_api_key=_resolve_api_key,
        llm_config_from_payload=_llm_config_from_payload,
        build_agent_response=_build_agent_response,
        autopilot_store=_autopilot_store,
        autopilot_run_response=_autopilot_run_response,
        publish_live_event=_publish_live_event,
        publish_agent_event=_publish_agent_event,
        publish_chat_session_event=_publish_chat_session_event,
        publish_chat_message_event=_publish_chat_message_event,
        run_session_status=_run_session_status,
        publish_autopilot_state=_publish_autopilot_state,
        sync_autopilot_session=_sync_autopilot_session,
        add_chat_message_and_publish=_add_chat_message_and_publish,
        write_autopilot_audit=_write_autopilot_audit,
        start_autopilot_worker=_start_autopilot_worker,
        mark_autopilot_failed=_mark_autopilot_failed,
        cancel_session_autopilot_runs=_cancel_session_autopilot_runs,
        delete_project_autopilot_runs=_delete_project_autopilot_runs,
        delete_project_everywhere=_delete_project_everywhere,
        agent_context_with_session_summary=_agent_context_with_session_summary,
        agent_context_with_session_summary_cached=_agent_context_with_session_summary_cached,
        project_package_reader=_project_package_reader,
        load_project_simulation_setup=_load_project_simulation_setup,
        load_project_feature_parameters=_load_project_feature_parameters,
        session_approval_mode=_session_approval_mode,
    )
