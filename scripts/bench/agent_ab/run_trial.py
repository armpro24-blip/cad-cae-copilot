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
# The native arm: the same libraries, reached through a shell instead of the
# workbench. No web, no subagents, nobody to ask — same as the MCP arm.
NATIVE_TOOLS = ["Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep"]
NATIVE_DISALLOWED = ["NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task", "AskUserQuestion", "Skill"]
TOOLS_SENTENCE = {
    "mcp": ('You are connected to the aieng-workbench MCP server. Use its tools to do the task, '
            'in a new project named "{project_name}".'),
    "native": ("You have a shell in the current directory. A Python environment with build123d and "
               "gmsh (Python API) runs as `conda run -n aieng311 python`, and the CalculiX solver as "
               "`conda run -n calculix-env ccx`. Use them to do the task."),
}
# Responses whose size is the onboarding tax this experiment is about.
TRACKED_RESULT_TOOLS = ("aieng_agent_readme", "aieng_guide", "aieng_list_projects", "aieng_agent_context")


def _mcp_config(checkout: Path, data_dir: Path, mcp_cwd: Path) -> dict:
    """MCP config running the checkout's server against this trial's own data directory."""
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
    """Text of a tool_result block, whether its content is a string or a list of parts."""
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
        message = event.get("message")
        if not isinstance(message, dict):  # some event types carry a plain string
            message = {}
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
    """Run one headless trial, write transcript.jsonl and summary.json, and score it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--max-budget-usd", default="8")
    parser.add_argument("--timeout-s", type=int, default=3600)
    parser.add_argument("--task", choices=("bracket", "cantilever", "sizing", "bracket_answer"), default="bracket")
    parser.add_argument("--native", action="store_true",
                        help="no workbench: the agent gets a shell and the same libraries")
    args = parser.parse_args()

    checkout = args.checkout.resolve()
    run_dir = (args.out / f"{args.arm}-{args.index}").resolve()
    if run_dir.exists():
        sys.exit(f"{run_dir} exists; pick another --out or --index")
    data_dir, mcp_cwd, agent_cwd = run_dir / "data", run_dir / "mcpcwd", run_dir / "agentcwd"
    for path in (data_dir, mcp_cwd, agent_cwd):
        path.mkdir(parents=True)
    config_path = run_dir / "mcp.json"
    config = {"mcpServers": {}} if args.native else _mcp_config(checkout, data_dir, mcp_cwd)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    project_name = f"ab-{args.arm}-{args.index}"
    if args.task == "bracket":
        if args.native:
            sys.exit("the bracket task is scored from the workbench package; use an answer-scored task")
        prompt = (HERE / "prompt.md").read_text(encoding="utf-8").format(project_name=project_name)
    else:
        tools = TOOLS_SENTENCE["native" if args.native else "mcp"].format(project_name=project_name)
        prompt = (HERE / f"task_{args.task}.md").read_text(encoding="utf-8").format(tools=tools)
    claude = shutil.which("claude")
    if claude is None:
        sys.exit("claude CLI not found on PATH")
    command = [
        claude, "-p", prompt, "--model", args.model,
        "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config", "--mcp-config", str(config_path),
        "--setting-sources", "project",
        "--allowedTools", *(NATIVE_TOOLS if args.native else ["mcp__aieng-workbench"]),
        "--disallowedTools", *(NATIVE_DISALLOWED if args.native else DISALLOWED),
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

    summary["task"] = args.task
    summary["native"] = args.native
    if args.task != "bracket":
        import score_answer

        scorer = getattr(score_answer, f"score_{args.task}")
        summary["score"] = scorer(summary.get("final_text"))
        summary["trial_status"] = "scored"
        return _finish(run_dir, summary)

    score_env = os.environ.copy()
    score_env.update(PYTHONPATH=str(checkout / "aieng-ui" / "backend"), AIENG_PLATFORM_DATA=str(data_dir))
    scored = subprocess.run(
        ["conda", "run", "-n", "aieng311", "python", str(HERE / "score.py"), "--project-name", project_name],
        cwd=mcp_cwd, env=score_env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    # A trial the scorer could not read is invalid, not a task failure.
    try:
        if scored.returncode != 0:
            raise ValueError(f"score.py exited {scored.returncode}")
        summary["score"] = json.loads(scored.stdout.strip().splitlines()[-1])
        summary["trial_status"] = "scored"
    except (IndexError, ValueError):
        summary["score"] = {"error": (scored.stderr or scored.stdout)[-2000:]}
        summary["trial_status"] = "scoring_failed"
    return _finish(run_dir, summary)


def _finish(run_dir: Path, summary: dict) -> int:
    """Write summary.json and print the headline numbers."""
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("arm", "index", "trial_status", "cost_usd", "input_tokens_total",
                                              "tool_calls_total", "error_codes")}, default=str))
    print(json.dumps(summary["score"], default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
