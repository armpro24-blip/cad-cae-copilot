from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOGGING_LOCK = threading.Lock()
_ERROR_METRICS: Counter[str] = Counter()
_DEFAULT_LOGGER_NAME = "app"
_DEFAULT_LOG_FILENAME = "backend.log"
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 5
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|bearer)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-[redacted]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)\S+"), r"\1[redacted]"),
)


def _under_pytest() -> bool:
    """True when this process is a test run.

    `PYTEST_CURRENT_TEST` only exists while a test is executing, so import-time
    and fixture-time configuration would slip past it; the imported module is
    the reliable marker.
    """
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def configure_backend_logging(
    data_root: Path,
    *,
    logger_name: str = _DEFAULT_LOGGER_NAME,
    log_filename: str = _DEFAULT_LOG_FILENAME,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> Path:
    """Configure a single managed rotating backend log file for the app logger.

    Under pytest the file handler is NOT attached: a test that builds an app
    against the real data root would otherwise write into the operator's
    `aieng-ui/data/logs/backend.log`. That log is what a human reads to find out
    what the backend did, and it had filled with tracebacks from deliberately
    failing fixtures ("RuntimeError: segmentation exploded") interleaved with
    pytest tmp paths — noise that makes a real incident harder to see, and
    changes to a shared file that tests should not be making at all.

    Set ``AIENG_LOG_TO_FILE=1`` to force the handler on anyway (e.g. a test that
    asserts on the log file itself); records still reach stderr regardless, so
    pytest's own capture keeps showing them.
    """
    if _under_pytest() and os.environ.get("AIENG_LOG_TO_FILE", "").strip() not in {"1", "true", "yes"}:
        return (Path(data_root) / "logs" / log_filename).resolve()

    logs_root = Path(data_root) / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    log_path = (logs_root / log_filename).resolve()
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with _LOGGING_LOCK:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in list(logger.handlers):
            if getattr(handler, "_aieng_managed", False):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    continue
        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        handler._aieng_managed = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return log_path


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    subsystem: str,
    level: int = logging.WARNING,
    context: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> None:
    """Record a recoverable exception with structured context and a metric bucket."""
    _increment_error_metric(subsystem)
    serialized_context = _serialize_context(context)
    if serialized_context:
        message = f"{message} | subsystem={subsystem} | context={serialized_context}"
    else:
        message = f"{message} | subsystem={subsystem}"
    if exc is None:
        logger.log(level, message, exc_info=True)
        return
    logger.log(level, message, exc_info=(type(exc), exc, exc.__traceback__))


def error_metrics_snapshot(*, limit: int = 100) -> dict[str, Any]:
    with _LOGGING_LOCK:
        buckets = sorted(_ERROR_METRICS.items(), key=lambda item: (-item[1], item[0]))
    return {
        "total_errors": sum(count for _, count in buckets),
        "distinct_buckets": len(buckets),
        "buckets": [
            {"bucket": bucket, "count": count}
            for bucket, count in buckets[: max(0, int(limit))]
        ],
    }


def reset_error_metrics() -> None:
    with _LOGGING_LOCK:
        _ERROR_METRICS.clear()


def _increment_error_metric(bucket: str) -> None:
    with _LOGGING_LOCK:
        _ERROR_METRICS[str(bucket)] += 1


def _serialize_context(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    safe_context = {str(key): _safe_value(value, key=str(key)) for key, value in context.items()}
    return json.dumps(safe_context, ensure_ascii=True, sort_keys=True)


def _safe_value(value: Any, *, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY_RE.search(key):
        return "[redacted]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return _redact_string(value) if isinstance(value, str) else value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(child_key): _safe_value(item, key=str(child_key)) for child_key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    return _redact_string(str(value))


def _redact_string(value: str) -> str:
    redacted = value
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
