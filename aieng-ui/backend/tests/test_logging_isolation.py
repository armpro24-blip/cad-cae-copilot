"""Tests must not write into the operator's backend log (#483).

`aieng-ui/data/logs/backend.log` is what a human reads to find out what the
backend did. It had filled with tracebacks from deliberately-failing fixtures
("RuntimeError: segmentation exploded") interleaved with pytest tmp paths,
because any test building an app against the real data root attached the
production rotating file handler.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.logging_utils import _under_pytest, configure_backend_logging


def test_this_process_is_recognised_as_a_test_run() -> None:
    assert _under_pytest() is True


def test_no_log_file_is_created_or_written_under_pytest(tmp_path: Path) -> None:
    log_path = configure_backend_logging(tmp_path)

    logging.getLogger("app").warning("this must not reach a file")

    assert log_path == (tmp_path / "logs" / "backend.log").resolve()
    assert not log_path.exists(), "a test run must not create the backend log"


def test_no_managed_handler_is_attached_under_pytest(tmp_path: Path) -> None:
    configure_backend_logging(tmp_path)
    managed = [
        h for h in logging.getLogger("app").handlers
        if getattr(h, "_aieng_managed", False)
    ]
    assert managed == []


def test_opt_in_restores_file_logging(tmp_path: Path, monkeypatch) -> None:
    """A test that asserts on the log file itself can still ask for it."""
    monkeypatch.setenv("AIENG_LOG_TO_FILE", "1")
    log_path = configure_backend_logging(tmp_path)
    try:
        logging.getLogger("app").warning("recorded on purpose")
        assert log_path.exists()
        assert "recorded on purpose" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(logging.getLogger("app").handlers):
            if getattr(handler, "_aieng_managed", False):
                logging.getLogger("app").removeHandler(handler)
                handler.close()
