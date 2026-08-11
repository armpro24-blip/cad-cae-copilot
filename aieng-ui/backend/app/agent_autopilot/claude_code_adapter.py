from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

from .adapters import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    DEFAULT_STEP_TIMEOUT_SECONDS,
    AdapterResultError,
    ProseResultError,
    _elapsed_ms,
    _first_line,
    capability_from_missing,
    parse_action_json,
    progress_event,
    resolve_command,
    run_probe_command,
)
from .schema import AdapterInvocationResult, LocalAgentCapability

# Interactive demo usage needs a tight ceiling: a non-interactive claude-code
# step that runs for many minutes is unusable.  Configurable via env.
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 180
DEFAULT_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS = 20
_PROMPT_PREVIEW_CHARS = 120


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _claude_timeout_default() -> int:
    raw = os.environ.get("AIENG_CLAUDE_CODE_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_CLAUDE_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CLAUDE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_CLAUDE_TIMEOUT_SECONDS


def _claude_preflight_timeout_default() -> int:
    raw = os.environ.get("AIENG_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_CLAUDE_PREFLIGHT_TIMEOUT_SECONDS


def _resolve_claude_exe(command: str) -> str | None:
    """On Windows, npm global CLI tools are often installed as .cmd wrappers.
    Those wrappers can trigger interactive 'Terminate batch job (Y/N)?'
    prompts when the underlying Node process exits.  Prefer the .exe if it
    exists alongside the .cmd."""
    path = resolve_command(command)
    if not path or os.name != "nt":
        return path
    lower = path.lower()
    if lower.endswith((".cmd", ".bat")):
        # Try replacing .cmd/.bat with .exe in the same directory
        base = path[:-4] if lower.endswith(".cmd") else path[:-4]
        exe_candidate = base + ".exe"
        if os.path.isfile(exe_candidate):
            return exe_candidate
    return path


def _build_claude_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "NO_COLOR": "1",
    })
    return env


def _windows_subprocess_kwargs() -> dict[str, Any]:
    # On Windows use CREATE_NO_WINDOW to prevent the child process from
    # creating a console window (and possibly interactive prompts like the
    # infamous "Terminate batch job (Y/N)?").
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


def _env_summary(env: dict[str, str], command_path: str | None = None) -> dict[str, Any]:
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    command_dir = os.path.dirname(os.path.abspath(command_path)) if command_path else ""
    return {
        "USERPROFILE": env.get("USERPROFILE"),
        "APPDATA": env.get("APPDATA"),
        "LOCALAPPDATA": env.get("LOCALAPPDATA"),
        "HOME": env.get("HOME"),
        "PATH_first_entries": path_entries[:6],
        "claude_dir_in_PATH": bool(command_dir) and any(
            os.path.normcase(os.path.abspath(entry)) == os.path.normcase(command_dir)
            for entry in path_entries
        ),
        "ANTHROPIC_env_names": sorted(name for name in env if name.startswith("ANTHROPIC_")),
        "CLAUDE_env_names": sorted(name for name in env if name.startswith("CLAUDE_")),
    }


def _sanitize_argv(cmd: list[str], prompt: str) -> dict[str, Any]:
    sanitized: list[str] = []
    skip_next = False
    value_redacting_flags = {"--json-schema", "--settings", "--system-prompt", "--append-system-prompt"}
    for index, arg in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        sanitized.append(arg)
        if arg in value_redacting_flags and index + 1 < len(cmd):
            value = cmd[index + 1]
            label = "json_schema" if arg == "--json-schema" else "value"
            sanitized.append(f"<{label} length={len(value)}>")
            skip_next = True
    return {
        "args": sanitized,
        "prompt_input": "stdin",
        "prompt_length": len(prompt),
        "prompt_preview": prompt[:_PROMPT_PREVIEW_CHARS],
    }


def _has_flag(cmd: list[str], *flags: str) -> bool:
    return any(flag in cmd for flag in flags)


def _has_permission_flag(cmd: list[str]) -> bool:
    return any(
        arg.startswith("--permission")
        or arg in {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}
        for arg in cmd
    )


def _looks_like_not_logged_in(*parts: str) -> bool:
    text = "\n".join(part or "" for part in parts)
    return "not logged in" in text.lower()


def _looks_like_session_not_found(*parts: str) -> bool:
    text = "\n".join(part or "" for part in parts).lower()
    return (
        "no conversation found with session id" in text
        or "conversation not found" in text
        or "session not found" in text
    )


def _claude_version(command_path: str, env: dict[str, str], timeout_seconds: int = 5) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [command_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=env,
            **_windows_subprocess_kwargs(),
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"ok": False, "error": str(exc)}
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return {"ok": result.returncode == 0, "rc": result.returncode, "output": output}


def run_claude_preflight(
    *,
    command: str | None = None,
    timeout_seconds: int | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Run the minimal command that users are asked to verify locally.

    This intentionally matches the successful manual shape:
    ``claude -p "Say hello" --output-format json`` while using the same inherited
    environment normalization as the adapter.
    """
    selected_command = command or os.environ.get("AIENG_CLAUDE_CODE_COMMAND", "claude")
    command_path = _resolve_claude_exe(selected_command)
    env = _build_claude_env()
    effective_cwd = cwd or os.getcwd()
    effective_timeout = timeout_seconds if timeout_seconds is not None else _claude_preflight_timeout_default()
    base: dict[str, Any] = {
        "ok": False,
        "resolved_path": command_path,
        "version": _claude_version(command_path, env) if command_path else {"ok": False, "error": "command not found"},
        "cwd": effective_cwd,
        "platform": platform.platform(),
        "env_summary": _env_summary(env, command_path),
        "stdout_parsed_result": None,
        "stderr": "",
        "rc": None,
    }
    if not command_path:
        return base
    cmd = [command_path, "-p", "Say hello", "--output-format", "json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=effective_timeout,
            check=False,
            env=env,
            cwd=effective_cwd,
            **_windows_subprocess_kwargs(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        base.update({
            "stderr": exc.stderr or "",
            "stdout": exc.stdout or "",
            "rc": None,
            "error": f"timeout after {effective_timeout}s",
        })
        return base
    except OSError as exc:
        base.update({"stderr": str(exc), "error": str(exc)})
        return base
    parsed: Any = None
    parse_error: str | None = None
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    is_error = bool(parsed.get("is_error")) if isinstance(parsed, dict) else False
    base.update({
        "ok": result.returncode == 0 and not is_error,
        "stdout": result.stdout,
        "stdout_parsed_result": parsed,
        "stdout_parse_error": parse_error,
        "stderr": result.stderr,
        "rc": result.returncode,
    })
    return base


def _invocation_diagnostic(
    *,
    headline: str,
    command_path: str,
    cmd: list[str],
    prompt: str,
    result: subprocess.CompletedProcess[str],
    step_index: int,
    adapter_id: str,
    effective_session_id: str,
    include_preflight: bool = False,
    command: str | None = None,
) -> str:
    env = _build_claude_env()
    preflight = (
        run_claude_preflight(command=command)
        if include_preflight
        else None
    )
    mismatch = bool(
        preflight
        and preflight.get("ok")
        and _looks_like_not_logged_in(result.stdout, result.stderr)
    )
    if mismatch:
        headline = (
            'Claude Code is authenticated for plain CLI calls, but the workbench adapter invocation '
            'failed with "Not logged in". This likely indicates an incompatible adapter flag or '
            "environment mismatch. See adapter diagnostics."
        )
    diagnostic = {
        "message": headline,
        "resolved_claude_executable_path": command_path,
        "claude_version": _claude_version(command_path, env),
        "cwd": os.getcwd(),
        "platform": platform.platform(),
        "argv_sanitized": _sanitize_argv(cmd, prompt),
        "env_summary": _env_summary(env, command_path),
        "passed_session_id": _has_flag(cmd, "--session-id"),
        "passed_resume": _has_flag(cmd, "--resume"),
        "passed_json_schema": _has_flag(cmd, "--json-schema"),
        "passed_permission_flags": _has_permission_flag(cmd),
        "passed_bare": _has_flag(cmd, "--bare"),
        "shell": False,
        "step_index": step_index,
        "adapter_id": adapter_id,
        "effective_claude_session_id": effective_session_id,
        "returncode": result.returncode,
        "stderr_preview": (result.stderr or "")[:800],
        "stdout_preview": (result.stdout or "")[:800],
    }
    if preflight is not None:
        diagnostic["preflight"] = preflight
    return f"{headline}\n{json.dumps(diagnostic, ensure_ascii=False, indent=2)}"


def _run_claude_step(cmd: list[str], prompt: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = _build_claude_env()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **_windows_subprocess_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        else:
            proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout_seconds,
            output=stdout or exc.output,
            stderr=stderr or exc.stderr,
        ) from exc
    logger.info(
        "claude step finished: rc=%s stdout=%d chars stderr=%d chars cmd=%s",
        proc.returncode,
        len(stdout),
        len(stderr),
        cmd[:6],
    )
    if proc.returncode != 0:
        logger.warning(
            "claude step nonzero exit: rc=%s stderr=%r stdout_preview=%r",
            proc.returncode,
            stderr[:500],
            stdout[:500],
        )
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=stderr)


class ClaudeCodeAdapter:
    adapter_id = "claude-code"
    label = "Claude Code CLI"

    def __init__(self, command: str | None = None, workspace: str | None = None) -> None:
        self.command = command or os.environ.get("AIENG_CLAUDE_CODE_COMMAND", "claude")
        self.workspace = workspace or os.environ.get("AIENG_LOCAL_AGENT_WORKSPACE", "")
        # Allowlist for Claude's built-in tools. Empty string disables all built-in
        # tools; a comma-separated list (e.g. "Read,Edit,Grep") enables only those.
        # Default allows most common tools except Bash to avoid arbitrary shell execution.
        self.tools = os.environ.get("AIENG_CLAUDE_CODE_TOOLS", "Read,Edit,Grep,Glob,LS,Search")
        # Do not use --bare by default. Claude Code 2.1.x documents --bare as
        # skipping keychain/OAuth reads, which can make a normally-authenticated
        # Windows CLI look "Not logged in" inside the adapter. Keep it as an
        # explicit diagnostic/advanced opt-in only.
        self.use_bare = _env_flag("AIENG_CLAUDE_CODE_BARE")
        # Hard ceiling for a single non-interactive step (interactive demo usage).
        self.timeout_seconds = _claude_timeout_default()

    @staticmethod
    def _claude_session_id(session_id: str | None) -> str:
        """Return a valid UUID for --session-id / --resume.

        The frontend chat session ID is an arbitrary string (e.g. 12-char hex).
        Claude CLI --session-id and --resume require a strict UUID format, so we
        map non-UUID values deterministically via uuid5.  This is stateless and
        survives adapter instance rebuilds (e.g. per-request engine creation).
        """
        if session_id is None:
            return str(uuid.uuid4())
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_OID, session_id))

    def probe(self, timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS) -> LocalAgentCapability:
        start = time.perf_counter()
        if os.environ.get("AIENG_DISABLE_CLAUDE_CODE_ADAPTER") == "1":
            return LocalAgentCapability(
                adapter_id=self.adapter_id,
                label=self.label,
                status="blocked",
                command=self.command,
                diagnostic="Claude Code adapter disabled by AIENG_DISABLE_CLAUDE_CODE_ADAPTER=1.",
                probe_duration_ms=_elapsed_ms(start),
            )
        command_path = resolve_command(self.command)
        if not command_path:
            return capability_from_missing(self.adapter_id, self.label, self.command, _elapsed_ms(start))
        try:
            help_result = run_probe_command(command_path, ["--help"], timeout_seconds)
        except subprocess.TimeoutExpired:
            return LocalAgentCapability(
                adapter_id=self.adapter_id,
                label=self.label,
                status="blocked",
                command=self.command,
                command_path=command_path,
                diagnostic="claude --help timed out; refusing to start an interactive session.",
                probe_duration_ms=_elapsed_ms(start),
            )
        except OSError as exc:
            return LocalAgentCapability(
                adapter_id=self.adapter_id,
                label=self.label,
                status="error",
                command=self.command,
                command_path=command_path,
                diagnostic=str(exc),
                probe_duration_ms=_elapsed_ms(start),
            )
        text = f"{help_result.stdout}\n{help_result.stderr}"
        supports_non_interactive = "-p" in text or "--print" in text
        supports_json = "--output-format" in text and "json" in text.lower()
        supports_json_schema = "--json-schema" in text
        supports_tool_disable = "--tools" in text
        status = "available" if supports_non_interactive and supports_json else "blocked"
        diagnostic = "Safe non-interactive JSON mode detected." if status == "available" else (
            "Claude Code CLI found, but required non-interactive JSON flags were not detected."
        )
        return LocalAgentCapability(
            adapter_id=self.adapter_id,
            label=self.label,
            status=status,
            command=self.command,
            command_path=command_path,
            version=_first_line(text),
            supports_non_interactive=supports_non_interactive,
            supports_json=supports_json,
            supports_json_schema=supports_json_schema,
            supports_tool_disable=supports_tool_disable,
            supports_session_continuation=True,
            diagnostic=diagnostic,
            probe_duration_ms=_elapsed_ms(start),
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
        # Clamp to the adapter ceiling so a caller's large global step timeout
        # (e.g. the engine's 1800s default) cannot leave a demo run hanging for
        # many minutes.  An explicitly-smaller caller value still wins.
        timeout_seconds = min(timeout_seconds, self.timeout_seconds)
        if on_progress is not None:
            on_progress(progress_event(self.adapter_id, "started", "Starting Claude Code CLI in non-interactive JSON mode."))
        command_path = _resolve_claude_exe(self.command)
        if not command_path:
            return AdapterInvocationResult(
                status="error",
                diagnostic=f"Command not found on PATH: {self.command}",
                duration_ms=_elapsed_ms(start),
            )
        # Session-aware invocation:
        #   step_index==0 (engine-level, cross-run) → --session-id creates a new session.
        #   step_index>0  → --resume reconnects to the same session.
        #   Using --resume with an explicit session_id avoids --continue which would
        #   resume the *most recent* session and could leak into a user's interactive
        #   CLI session.  If --session-id fails with "already in use" (e.g. after a
        #   backend restart) we fall back to --resume automatically.
        effective_session_id = self._claude_session_id(session_id)
        use_resume = step_index > 0
        if on_progress is not None:
            on_progress(progress_event(
                self.adapter_id,
                "prompt_prepared",
                f"Claude Code command resolved; session={effective_session_id} mode={'--resume' if use_resume else '--session-id'}.",
                command_path=command_path,
                session_id=effective_session_id,
                step_index=step_index,
            ))

        def _build_cmd(session_flag: str) -> list[str]:
            cmd = [
                command_path,
                "-p",
                session_flag,
                effective_session_id,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(action_schema),
                "--permission-mode",
                "auto",
                "--tools",
                self.tools,
            ]
            if self.use_bare:
                cmd.insert(2, "--bare")
            if self.workspace:
                cmd.extend(["--add-dir", self.workspace])
            return cmd

        cmd = _build_cmd("--resume" if use_resume else "--session-id")
        try:
            if on_progress is not None:
                on_progress(progress_event(
                    self.adapter_id,
                    "request_sent",
                    "Claude Code request prepared; starting subprocess.",
                    session_id=effective_session_id,
                    step_index=step_index,
                ))
                on_progress(progress_event(
                    self.adapter_id,
                    "waiting_for_model",
                    "Claude Code process is running; waiting for structured action JSON.",
                    session_id=effective_session_id,
                    step_index=step_index,
                ))
            result = _run_claude_step(cmd, prompt, timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            msg = f"Claude Code timed out before producing a structured action (after {timeout_seconds}s)."
            if on_progress is not None:
                on_progress(progress_event(self.adapter_id, "timeout", msg, step_index=step_index))
            return AdapterInvocationResult(
                status="timeout",
                raw_output=exc.stdout or "",
                stderr=exc.stderr or "",
                diagnostic=msg,
                duration_ms=_elapsed_ms(start),
            )
        except OSError as exc:
            if on_progress is not None:
                on_progress(progress_event(self.adapter_id, "error", str(exc), step_index=step_index))
            return AdapterInvocationResult(
                status="error",
                diagnostic=str(exc),
                duration_ms=_elapsed_ms(start),
            )
        # Fallback: if the session already exists (e.g. backend restart), retry with --resume.
        if result.returncode != 0 and "already in use" in (result.stderr or ""):
            logger.warning(
                "claude session '%s' already in use; retrying with --resume.",
                effective_session_id,
            )
            cmd = _build_cmd("--resume")
            try:
                result = _run_claude_step(cmd, prompt, timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                msg = f"Claude Code timed out before producing a structured action (after {timeout_seconds}s)."
                if on_progress is not None:
                    on_progress(progress_event(self.adapter_id, "timeout", msg, step_index=step_index))
                return AdapterInvocationResult(
                    status="timeout",
                    raw_output=exc.stdout or "",
                    stderr=exc.stderr or "",
                    diagnostic=msg,
                    duration_ms=_elapsed_ms(start),
                )
            except OSError as exc:
                if on_progress is not None:
                    on_progress(progress_event(self.adapter_id, "error", str(exc), step_index=step_index))
                return AdapterInvocationResult(
                    status="error",
                    diagnostic=str(exc),
                    duration_ms=_elapsed_ms(start),
                )
        if result.returncode != 0:
            headline = f"Claude Code exited with code {result.returncode}."
            if use_resume and _looks_like_session_not_found(result.stdout, result.stderr):
                headline = (
                    "Claude Code could not resume the requested session. "
                    "This indicates a Claude CLI conversation/session mismatch; see adapter diagnostics."
                )
            diag = _invocation_diagnostic(
                headline=headline,
                command_path=command_path,
                cmd=cmd,
                prompt=prompt,
                result=result,
                step_index=step_index,
                adapter_id=self.adapter_id,
                effective_session_id=effective_session_id,
                include_preflight=_looks_like_not_logged_in(result.stdout, result.stderr),
                command=self.command,
            )
            logger.error("claude invoke failed: %s", diag)
            if on_progress is not None:
                on_progress(progress_event(self.adapter_id, "error", "Claude Code exited with an error.", step_index=step_index))
            return AdapterInvocationResult(
                status="error",
                raw_output=result.stdout,
                stderr=result.stderr,
                diagnostic=diag,
                duration_ms=_elapsed_ms(start),
            )
        try:
            if on_progress is not None:
                on_progress(progress_event(
                    self.adapter_id,
                    "parsing_output",
                    (
                        "Claude Code returned output; validating the structured action "
                        f"({len(result.stdout)} stdout chars, {len(result.stderr)} stderr chars)."
                    ),
                    stdout_chars=len(result.stdout),
                    stderr_chars=len(result.stderr),
                    step_index=step_index,
                ))
            action = parse_action_json(result.stdout)
        except (ProseResultError, AdapterResultError) as exc:
            # Claude returned prose (or a failed/errored wrapper) instead of a
            # structured action.  Fail fast and clearly with a non-empty message
            # — do NOT fabricate a success/final action.
            headline = str(exc)
            diag = _invocation_diagnostic(
                headline=headline,
                command_path=command_path,
                cmd=cmd,
                prompt=prompt,
                result=result,
                step_index=step_index,
                adapter_id=self.adapter_id,
                effective_session_id=effective_session_id,
                include_preflight=_looks_like_not_logged_in(str(exc), result.stdout, result.stderr),
                command=self.command,
            )
            logger.warning("claude returned non-structured output: %s", diag)
            if on_progress is not None:
                on_progress(progress_event(self.adapter_id, "error", headline, step_index=step_index))
            return AdapterInvocationResult(
                status="error",
                raw_output=result.stdout,
                stderr=result.stderr,
                diagnostic=diag,
                duration_ms=_elapsed_ms(start),
            )
        except Exception as exc:
            diag = (
                f"Claude Code returned invalid action JSON: {exc}\n"
                f"session_id={effective_session_id} step={step_index}\n"
                f"stderr: {result.stderr[:800]!r}\n"
                f"stdout_preview: {result.stdout[:800]!r}"
            )
            logger.error("claude parse failed: %s", diag)
            if on_progress is not None:
                on_progress(progress_event(self.adapter_id, "error", "Claude Code returned invalid action JSON.", step_index=step_index))
            return AdapterInvocationResult(
                status="error",
                raw_output=result.stdout,
                stderr=result.stderr,
                diagnostic=diag,
                duration_ms=_elapsed_ms(start),
            )
        if on_progress is not None:
            on_progress(progress_event(
                self.adapter_id,
                "completed",
                f"Claude Code selected action {action.action.type}.",
                action_type=action.action.type,
                duration_ms=_elapsed_ms(start),
                step_index=step_index,
            ))
        return AdapterInvocationResult(
            status="success",
            action=action,
            raw_output=result.stdout,
            stderr=result.stderr,
            duration_ms=_elapsed_ms(start),
        )
