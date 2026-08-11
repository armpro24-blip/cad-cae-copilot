from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.logging_utils import (
    configure_backend_logging,
    error_metrics_snapshot,
    log_exception,
    reset_error_metrics,
)
from app.main import Settings, create_app, default_project, save_project


@pytest.fixture(autouse=True)
def _enable_file_logging(monkeypatch: pytest.MonkeyPatch):
    """This module asserts on the log FILE, so it must opt in (#483).

    Test runs no longer attach the rotating file handler by default — a test
    building an app against the real data root was writing fixture tracebacks
    into the operator's `aieng-ui/data/logs/backend.log`. Tests about the log
    itself ask for it explicitly; every log path here is under tmp_path.
    """
    monkeypatch.setenv("AIENG_LOG_TO_FILE", "1")
    yield
    for handler in list(logging.getLogger("app").handlers):
        if getattr(handler, "_aieng_managed", False):
            logging.getLogger("app").removeHandler(handler)
            handler.close()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        sample_step=tmp_path / "workspace" / "sample.step",
    )


def _flush_app_handlers() -> None:
    for handler in logging.getLogger("app").handlers:
        try:
            handler.flush()
        except Exception:
            continue


def test_configure_backend_logging_uses_rotating_file_handler(tmp_path: Path) -> None:
    reset_error_metrics()
    log_path = configure_backend_logging(tmp_path / "data", max_bytes=1234, backup_count=7)

    handlers = [handler for handler in logging.getLogger("app").handlers if getattr(handler, "_aieng_managed", False)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 1234
    assert handler.backupCount == 7
    assert Path(handler.baseFilename) == log_path


def test_log_exception_writes_file_and_records_metric(tmp_path: Path) -> None:
    reset_error_metrics()
    log_path = configure_backend_logging(tmp_path / "data")
    logger = logging.getLogger("app.tests.backend_logging")

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log_exception(
            logger,
            "Recoverable backend failure.",
            subsystem="tests.backend_logging",
            context={"project_id": "p1"},
        )

    _flush_app_handlers()
    text = log_path.read_text(encoding="utf-8")
    assert "Recoverable backend failure." in text
    assert '"project_id": "p1"' in text

    snapshot = error_metrics_snapshot()
    assert snapshot["total_errors"] == 1
    assert snapshot["buckets"][0] == {"bucket": "tests.backend_logging", "count": 1}


def test_log_exception_redacts_secrets_from_context(tmp_path: Path) -> None:
    reset_error_metrics()
    log_path = configure_backend_logging(tmp_path / "data")
    logger = logging.getLogger("app.tests.backend_logging")

    try:
        raise RuntimeError("provider failed")
    except RuntimeError:
        log_exception(
            logger,
            "Recoverable provider failure.",
            subsystem="tests.secret_redaction",
            context={
                "api_key": "sk-live123456789",
                "headers": {"Authorization": "Bearer abcdef123456789"},
                "notes": "retry with token=plain-secret and sk-anothersecret123",
                "project_id": "visible-project",
            },
        )

    _flush_app_handlers()
    text = log_path.read_text(encoding="utf-8")
    assert "visible-project" in text
    for secret in (
        "sk-live123456789",
        "abcdef123456789",
        "plain-secret",
        "sk-anothersecret123",
    ):
        assert secret not in text
    assert "[redacted]" in text


def test_error_metrics_endpoint_reports_log_path_and_buckets(tmp_path: Path) -> None:
    reset_error_metrics()
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))

    try:
        raise ValueError("diagnostic")
    except ValueError:
        log_exception(
            logging.getLogger("app.tests.endpoint"),
            "Diagnostic logging sample.",
            subsystem="tests.diagnostics.endpoint",
        )

    response = client.get("/api/diagnostics/error-metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["backend_log_path"].endswith("backend.log")
    assert data["total_errors"] == 1
    assert {"bucket": "tests.diagnostics.endpoint", "count": 1} in data["buckets"]


def test_critique_endpoint_failure_is_logged_and_nonfatal(monkeypatch, tmp_path: Path) -> None:
    reset_error_metrics()
    settings = _settings(tmp_path)
    project = save_project(settings, default_project("logging-critique"))
    client = TestClient(create_app(settings))

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("critique exploded")

    monkeypatch.setattr("app.cad_generation.critique", _boom)

    response = client.get(f"/api/projects/{project['id']}/critique")
    assert response.status_code == 200
    assert response.json()["findings"] == []

    snapshot = error_metrics_snapshot()
    critique_bucket = next(item for item in snapshot["buckets"] if item["bucket"] == "app_factory.project_critique")
    assert critique_bucket["count"] >= 1
