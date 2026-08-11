from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from .schema import AdapterInvocationResult, AutopilotAgentAction, LocalAgentCapability


DEFAULT_PROBE_TIMEOUT_SECONDS = 3
DEFAULT_STEP_TIMEOUT_SECONDS = 1800
ProgressPhase = Literal[
    "started",
    "prompt_prepared",
    "request_sent",
    "waiting_for_model",
    "parsing_output",
    "completed",
    "timeout",
    "error",
]
COMMON_PROGRESS_PHASES: tuple[ProgressPhase, ...] = (
    "started",
    "prompt_prepared",
    "request_sent",
    "waiting_for_model",
    "parsing_output",
    "completed",
    "timeout",
    "error",
)


class LocalAgentAdapter(Protocol):
    adapter_id: str
    label: str

    def probe(self, timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS) -> LocalAgentCapability:
        ...

    def invoke(
        self,
        *,
        prompt: str,
        action_schema: dict[str, Any],
        timeout_seconds: int = DEFAULT_STEP_TIMEOUT_SECONDS,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        session_id: str | None = None,
        step_index: int = 0,
    ) -> AdapterInvocationResult:
        ...


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def progress_event(adapter_id: str, phase: ProgressPhase, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "phase": phase,
        "adapter_id": adapter_id,
        "message": message,
        **extra,
    }


def resolve_command(command: str) -> str | None:
    return shutil.which(command)


def run_probe_command(command_path: str, args: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [command_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:300]
    return ""


def capability_from_missing(adapter_id: str, label: str, command: str, duration_ms: int) -> LocalAgentCapability:
    return LocalAgentCapability(
        adapter_id=adapter_id,
        label=label,
        status="missing",
        command=command,
        diagnostic=f"Command not found on PATH: {command}",
        probe_duration_ms=duration_ms,
    )


class AdapterResultError(ValueError):
    """The CLI wrapper reported a failed/errored result (is_error / failure subtype)."""


class ProseResultError(ValueError):
    """The CLI returned prose (or otherwise non-structured) text instead of an action."""


PROSE_RESULT_MESSAGE = (
    "Claude Code returned prose instead of a structured action. No CAD tool was executed."
)


def _looks_like_cli_wrapper(payload: Any) -> bool:
    """Detect the Claude CLI result wrapper envelope.

    The CLI (``--output-format json``) wraps the model output in an envelope like
    ``{"type": "result", "subtype": "success", "is_error": false, "result": "..."}``
    where the actual model output lives in the ``result`` field (a string or, for
    ``--json-schema`` runs, a structured object).  We must not validate this
    envelope directly as an AutopilotAgentAction.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("type") == "result":
        return True
    if "is_error" in payload and ("result" in payload or "subtype" in payload):
        return True
    return False


def _strip_code_fence(text: str) -> str:
    inner = text.strip()
    if not inner.startswith("```"):
        return inner
    # Drop the opening fence line (``` or ```json) and the trailing fence.
    body = inner.split("\n", 1)[1] if "\n" in inner else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -3]
    return body.strip()


def _parse_result_string(result: str) -> dict[str, Any]:
    """Parse the wrapper's string ``result`` field into an action payload.

    Raises ProseResultError when the text is prose rather than structured
    action JSON.
    """
    inner = result.strip()
    if not inner:
        raise ProseResultError(PROSE_RESULT_MESSAGE)
    candidate: Any = None
    for text_candidate in (inner, _strip_code_fence(inner)):
        try:
            candidate = json.loads(text_candidate)
            break
        except json.JSONDecodeError:
            candidate = None
    if candidate is None:
        # Last resort: extract the outermost {...} substring from prose.
        start = inner.find("{")
        end = inner.rfind("}")
        if 0 <= start < end:
            try:
                candidate = json.loads(inner[start : end + 1])
            except json.JSONDecodeError:
                candidate = None
    if isinstance(candidate, dict) and (
        "action" in candidate or isinstance(candidate.get("structured_output"), dict)
    ):
        return candidate
    raise ProseResultError(PROSE_RESULT_MESSAGE)


def _unwrap_cli_wrapper(payload: Any) -> Any:
    """Unwrap a Claude CLI result envelope to the inner action payload.

    Raises AdapterResultError if the wrapper reports failure, and
    ProseResultError if the inner result is prose rather than action JSON.
    """
    if not _looks_like_cli_wrapper(payload):
        return payload
    is_error = bool(payload.get("is_error"))
    subtype = payload.get("subtype")
    if is_error or (subtype is not None and subtype != "success"):
        raise AdapterResultError(
            f"Claude Code reported a failed result (is_error={is_error}, subtype={subtype!r})."
        )
    if isinstance(payload.get("structured_output"), dict):
        return payload["structured_output"]
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        return _parse_result_string(result)
    raise ProseResultError(PROSE_RESULT_MESSAGE)


def parse_action_json(text: str) -> AutopilotAgentAction:
    stripped = text.strip()
    if not stripped:
        raise ValueError("adapter returned empty output")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    payload = _unwrap_cli_wrapper(payload)
    if isinstance(payload, dict) and isinstance(payload.get("structured_output"), dict):
        payload = payload["structured_output"]
    if isinstance(payload, dict) and isinstance(payload.get("action"), dict):
        action = dict(payload["action"])
        action_type = action.get("type")
        if action_type == "tool_call":
            action_input = action.get("input") if isinstance(action.get("input"), dict) else {}
            if not action_input and isinstance(action.get("input_json"), str):
                try:
                    parsed_input = json.loads(action["input_json"] or "{}")
                    action_input = parsed_input if isinstance(parsed_input, dict) else {}
                except json.JSONDecodeError:
                    action_input = {}
            payload["action"] = {
                "type": "tool_call",
                "tool_name": action.get("tool_name") or "",
                "input": action_input,
            }
        elif action_type == "ask_user":
            question = str(action.get("question") or "").strip()
            if not question:
                raise ValueError("terminal action ask_user requires a non-empty question")
            payload["action"] = {"type": "ask_user", "question": question}
        elif action_type == "final":
            message = str(action.get("message") or "").strip()
            if not message:
                raise ValueError("terminal action final requires a non-empty message")
            payload["action"] = {"type": "final", "message": message}
        elif action_type == "pause":
            reason = str(action.get("reason") or "").strip()
            if not reason:
                raise ValueError("terminal action pause requires a non-empty reason")
            payload["action"] = {"type": "pause", "reason": reason}
        elif action_type == "chat":
            message = str(action.get("message") or "").strip()
            if not message:
                raise ValueError("terminal action chat requires a non-empty message")
            payload["action"] = {"type": "chat", "message": message}
    return AutopilotAgentAction.model_validate(payload)


@dataclass
class FakeLocalAgentAdapter:
    actions: list[AutopilotAgentAction] = field(default_factory=list)

    adapter_id: str = "fake"
    label: str = "Fake local agent"

    def probe(self, timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS) -> LocalAgentCapability:
        return LocalAgentCapability(
            adapter_id=self.adapter_id,
            label=self.label,
            status="available",
            command="fake",
            supports_non_interactive=True,
            supports_json=True,
            supports_json_schema=True,
            supports_tool_disable=True,
            supports_session_continuation=False,
            diagnostic="Deterministic in-process adapter for dry-run tests.",
        )

    def invoke(
        self,
        *,
        prompt: str,
        action_schema: dict[str, Any],
        timeout_seconds: int = DEFAULT_STEP_TIMEOUT_SECONDS,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        session_id: str | None = None,
        step_index: int = 0,
    ) -> AdapterInvocationResult:
        start = time.perf_counter()
        if on_progress is not None:
            on_progress(progress_event(self.adapter_id, "started", "Fake adapter started."))
        if self.actions:
            action = self.actions.pop(0)
        else:
            action = AutopilotAgentAction.model_validate(
                {
                    "thought_summary": "No more fake actions; finish the dry run.",
                    "action": {
                        "type": "final",
                        "message": "Autopilot dry run completed.",
                    },
                    "done": True,
                    "user_message": "Autopilot dry run completed.",
                }
            )
        if on_progress is not None:
            on_progress(progress_event(self.adapter_id, "completed", "Fake adapter completed."))
        return AdapterInvocationResult(
            status="success",
            action=action,
            raw_output=action.model_dump_json(),
            duration_ms=_elapsed_ms(start),
        )


def adapter_registry(fake_actions: list[dict[str, Any]] | None = None) -> dict[str, LocalAgentAdapter]:
    from .claude_code_adapter import ClaudeCodeAdapter
    from .codex_cli_adapter import CodexCliAdapter

    parsed_fake_actions = [
        AutopilotAgentAction.model_validate(item)
        for item in (fake_actions or [])
    ]
    return {
        "fake": FakeLocalAgentAdapter(parsed_fake_actions),
        "claude-code": ClaudeCodeAdapter(),
        "codex-cli": CodexCliAdapter(),
    }


def probe_local_agent_capabilities() -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    for adapter_id, adapter in adapter_registry().items():
        if adapter_id == "fake":
            continue
        try:
            probes.append(adapter.probe().model_dump())
        except Exception as exc:
            probes.append(
                LocalAgentCapability(
                    adapter_id=adapter_id,
                    label=getattr(adapter, "label", adapter_id),
                    status="error",
                    command=os.environ.get("AIENG_LOCAL_AGENT_COMMAND", adapter_id),
                    diagnostic=f"Capability probe failed: {exc}",
                ).model_dump()
            )
    return probes


__all__ = [
    "DEFAULT_PROBE_TIMEOUT_SECONDS",
    "DEFAULT_STEP_TIMEOUT_SECONDS",
    "AdapterResultError",
    "ProseResultError",
    "PROSE_RESULT_MESSAGE",
    "FakeLocalAgentAdapter",
    "COMMON_PROGRESS_PHASES",
    "LocalAgentAdapter",
    "ProgressPhase",
    "adapter_registry",
    "capability_from_missing",
    "parse_action_json",
    "progress_event",
    "probe_local_agent_capabilities",
    "resolve_command",
    "run_probe_command",
    "_elapsed_ms",
    "_first_line",
]
