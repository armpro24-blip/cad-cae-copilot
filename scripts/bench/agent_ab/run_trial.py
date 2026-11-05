"""Run one headless Claude Code trial against an aieng checkout and score it.

The trial measures what an agent pays to use the workbench and whether it
finishes the promised bracket task (build -> analyse -> edit -> re-analyse ->
compare). Each trial gets its own data directory and MCP working directory, so
trials can run in parallel and never touch the real workbench data.

    python run_trial.py --checkout PATH --arm baseline --index 1 --out DIR

Isolation limits (stated, not hidden):
- The agent may use only the aieng MCP tools (plus ToolSearch); file, shell and
  web tools are disabled, so it cannot read AGENTS.md off disk.
- `--setting-sources project` drops user settings and hooks, but the user's
  global CLAUDE.md still loads (`--bare` would drop it but needs an API key).
  It is identical across arms.
- The prompt pre-approves the modeling plan and solver runs, and
  AskUserQuestion is disabled: a headless run has nobody to answer.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISALLOWED = [
    "Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit",
    "WebFetch", "WebSearch", "Agent", "Task", "AskUserQuestion", "Skill",
]
# Responses whose size is the onboarding tax this experiment is about.
TRACKED_RESULT_TOOLS = ("aieng_agent_readme", "aieng_guide", "aieng_list_projects", "aieng_agent_context")


def _mcp_config(checkout: Path, data_dir: Path, mcp_cwd: Path) -> dict:
    return {"mcpServers": {"aieng-workbench": {
        "command": "conda",
        "args": ["run", "-n", "aieng311", "--no-capture-output", "python", "-m",
                 "app.mcp_server", "--approval-mode", "client"],
        "cwd": str(mcp_cwd),
        "env": {
            "PYTHONPATH": str(checkout / "aieng-ui" / "backend"),
            "AIENG_PLATFORM_DATA": str(data_dir),
        },
    }}}


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def summarize_transcript(lines: list[str]) -> dict:
    """Token, tool-call and refusal accounting from a stream-json transcript."""
    calls: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    tool_names: dict[str, str] = {}
    result_chars: Counter[str] = Counter()
    final: dict = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "result":
            final = event
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name", "").removeprefix("mcp__aieng-workbench__")
                calls[name] += 1
                tool_names[block.get("id", "")] = name
            elif block.get("type") == "tool_result":
                name = tool_names.get(block.get("tool_use_id", ""), "?")
                text = _result_text(block)
                if name in TRACKED_RESULT_TOOLS:
                    result_chars[name] += len(text)
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("status") == "error":
                    codes[f"{name}:{payload.get('code')}"] += 1
    usage = final.get("usage") or {}
    return {
        "is_error": final.get("is_error"),
        "terminal_reason": final.get("terminal_reason") or final.get("subtype"),
        "num_turns": final.get("num_turns"),
        "duration_s": round((final.get("duration_ms") or 0) / 1000, 1),
        "cost_usd": final.get("total_cost_usd"),
        "input_tokens_total": sum(usage.get(k, 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
        "output_tokens": usage.get("output_tokens"),
        "tool_calls_total": sum(calls.values()),
        "tool_calls": dict(calls.most_common()),
        "error_codes": dict(codes.most_common()),
        "onboarding_result_chars": dict(result_chars),
        "final_text": final.get("result"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--max-budget-usd", default="8")
    parser.add_argument("--timeout-s", type=int, default=3600)
    args = parser.parse_args()

    checkout = args.checkout.resolve()
    run_dir = (args.out / f"{args.arm}-{args.index}").resolve()
    if run_dir.exists():
        sys.exit(f"{run_dir} exists; pick another --out or --index")
    data_dir, mcp_cwd, agent_cwd = run_dir / "data", run_dir / "mcpcwd", run_dir / "agentcwd"
    for path in (data_dir, mcp_cwd, agent_cwd):
        path.mkdir(parents=True)
    config_path = run_dir / "mcp.json"
    config_path.write_text(json.dumps(_mcp_config(checkout, data_dir, mcp_cwd), indent=2), encoding="utf-8")

    project_name = f"ab-{args.arm}-{args.index}"
    prompt = (HERE / "prompt.md").read_text(encoding="utf-8").format(project_name=project_name)
    claude = shutil.which("claude")
    if claude is None:
        sys.exit("claude CLI not found on PATH")
    command = [
        claude, "-p", prompt, "--model", args.model,
        "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config", "--mcp-config", str(config_path),
        "--setting-sources", "project",
        "--allowedTools", "mcp__aieng-workbench",
        "--disallowedTools", *DISALLOWED,
        "--max-budget-usd", str(args.max_budget_usd),
        "--no-session-persistence",
    ]
    started = time.time()
    transcript = run_dir / "transcript.jsonl"
    with transcript.open("w", encoding="utf-8") as out, (run_dir / "stderr.txt").open("w", encoding="utf-8") as err:
        try:
            proc = subprocess.run(command, cwd=agent_cwd, stdout=out, stderr=err,
                                  stdin=subprocess.DEVNULL, timeout=args.timeout_s,
                                  env=os.environ.copy())
            exit_code: int | str = proc.returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
    summary = summarize_transcript(transcript.read_text(encoding="utf-8").splitlines())
    summary.update(arm=args.arm, index=args.index, model=args.model, exit_code=exit_code,
                   wall_s=round(time.time() - started, 1))

    score_env = os.environ.copy()
    score_env.update(PYTHONPATH=str(checkout / "aieng-ui" / "backend"), AIENG_PLATFORM_DATA=str(data_dir))
    scored = subprocess.run(
        ["conda", "run", "-n", "aieng311", "python", str(HERE / "score.py"), "--project-name", project_name],
        cwd=mcp_cwd, env=score_env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    try:
        summary["score"] = json.loads(scored.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        summary["score"] = {"error": (scored.stderr or scored.stdout)[-2000:]}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("arm", "index", "cost_usd", "input_tokens_total",
                                              "tool_calls_total", "error_codes")}, default=str))
    print(json.dumps(summary["score"], default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
