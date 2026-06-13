#!/usr/bin/env python3
"""
windows-claude-bridge — MCP server to delegate a task to a Windows-side agent.

Runs in WSL2 (or Linux). The generic tool `ask_windows_agent` (plus the legacy
`ask_windows_claude` alias) launches a Windows headless agent CLI via WSL
interop, parses the result and returns it. By default it targets
`claude.exe -p --output-format json`, but the binary/argv can be overridden to
use another agent.

Why: WSL2 cannot see the Windows desktop; the Windows agent can. The WSL2 agent
orchestrates, the Windows agent executes. See ../WSL2.md.

Safety
------
- Tools/permissions of the Windows agent are controlled by `allowed_tools` /
  `permission_mode` (default: the Windows agent's own settings). `bypassPermissions`
  is **opt-in** — reserve it for a trusted/test machine.
- Each call is a full agent run; pass `resume` (a returned session_id) to keep
  context across calls.

Transport: stdio by default; MCP_DESKTOP_TRANSPORT=sse (or --sse) for HTTP/SSE.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("windows-claude-bridge")


def _as_list(value) -> list[str]:
    """Accept a list or a comma/space-separated string -> list of tokens."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [t for t in str(value).replace(",", " ").split() if t]


def _template_values(
    prompt: str,
    *,
    allowed_tools=None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir=None,
    model: str | None = None,
) -> dict[str, str | list[str] | None]:
    return {
        "prompt": prompt,
        "model": model,
        "resume": resume,
        "cwd": cwd,
        "permission_mode": permission_mode,
        "allowed_tools": _as_list(allowed_tools),
        "add_dir": _as_list(add_dir),
    }


def _expand_arg_template(template: str, values: dict[str, str | list[str] | None]) -> list[str]:
    argv: list[str] = []
    for token in shlex.split(template):
        if token.startswith("{") and token.endswith("}"):
            value = values.get(token[1:-1])
            if isinstance(value, list):
                argv.extend(value)
                continue
            if value:
                argv.append(str(value))
            continue
        rendered = token.format_map({
            key: "" if isinstance(value, list) or value is None else str(value)
            for key, value in values.items()
        })
        if rendered:
            argv.append(rendered)
    return argv


def _build_windows_agent_argv(
    prompt: str,
    *,
    allowed_tools=None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir=None,
    model: str | None = None,
    timeout: int = 600,
) -> list[str]:
    binary = (
        os.environ.get("ASK_WIN_AGENT_BIN")
        or os.environ.get("ASK_WIN_CLAUDE_BIN")
        or "claude.exe"
    )
    values = _template_values(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
    )
    template = os.environ.get("ASK_WIN_AGENT_ARGS")
    if template:
        return [binary, *_expand_arg_template(template, values)]

    argv: list[str] = [binary, "-p", prompt, "--output-format", "json"]

    tools = _as_list(allowed_tools) or _as_list(os.environ.get("ASK_WIN_CLAUDE_ALLOWED_TOOLS"))
    if tools:
        argv += ["--allowedTools", *tools]

    mode = permission_mode or os.environ.get("ASK_WIN_CLAUDE_PERMISSION_MODE")
    if mode:
        argv += ["--permission-mode", mode]

    mdl = model or os.environ.get("ASK_WIN_CLAUDE_MODEL")
    if mdl:
        argv += ["--model", mdl]

    if resume:
        argv += ["--resume", resume]

    dirs = _as_list(add_dir)
    if dirs:
        argv += ["--add-dir", *dirs]
    return argv


def _normalize_agent_result(data) -> dict:
    if isinstance(data, dict):
        result = data.get("result")
        if result is None:
            for key in ("text", "content", "output", "response"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    result = value
                    break
            if result is None and isinstance(data.get("message"), dict):
                message = data["message"]
                result = message.get("content") or message.get("text")
            if result is None and isinstance(data.get("choices"), list):
                for choice in data["choices"]:
                    if isinstance(choice, dict):
                        message = choice.get("message")
                        if isinstance(message, dict):
                            result = message.get("content") or message.get("text")
                        result = result or choice.get("text")
                    if result:
                        break
        if result is None:
            result = json.dumps(data, ensure_ascii=False)
        error = data.get("error")
        is_error = bool(data.get("is_error", False))
        if not is_error and error:
            is_error = True
        return {
            "is_error": is_error,
            "error": error,
            "result": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
            "session_id": data.get("session_id") or data.get("sessionId"),
            "num_turns": data.get("num_turns") or data.get("turns"),
            "total_cost_usd": data.get("total_cost_usd") or data.get("cost_usd"),
        }
    return {"is_error": False, "result": json.dumps(data, ensure_ascii=False)}


def _run_windows_agent(
    prompt: str,
    *,
    allowed_tools=None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir=None,
    model: str | None = None,
    timeout: int = 600,
) -> dict:
    if not prompt or not prompt.strip():
        return {"is_error": True, "error": "empty prompt"}

    argv = _build_windows_agent_argv(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
        timeout=timeout,
    )
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except subprocess.TimeoutExpired:
        return {"is_error": True, "error": f"timed out after {timeout}s"}
    except OSError as e:
        # FileNotFoundError, PermissionError, … — agent binary not launchable.
        binary = argv[0]
        return {
            "is_error": True,
            "error": f"cannot launch '{binary}' ({e.__class__.__name__}: {e}). "
            "Install your Windows-side agent CLI and make sure it is reachable from "
            "WSL, or set ASK_WIN_AGENT_BIN (or ASK_WIN_CLAUDE_BIN for Claude Code) "
            "to its full path.",
        }

    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except Exception:
        if proc.returncode != 0:
            return {
                "is_error": True,
                "error": f"exit {proc.returncode}",
                "stderr": (proc.stderr or "").strip()[:2000],
                "stdout": out[:2000],
            }
        return {"is_error": False, "result": out}
    return _normalize_agent_result(data)


@mcp.tool()
def ask_windows_agent(
    prompt: str,
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir: list[str] | None = None,
    model: str | None = None,
    timeout: int = 600,
) -> dict:
    """Delegate a task to a Windows-side headless agent and return its result.

    By default this targets Claude Code (`claude.exe -p --output-format json`).
    To use another CLI (Copilot, Gemini, Codex/OpenAI, open-model wrappers, ...),
    set `ASK_WIN_AGENT_BIN` and `ASK_WIN_AGENT_ARGS` in the bridge environment.

    - prompt          : the instruction for the Windows agent.
    - allowed_tools   : tools the Windows agent may use without prompting, e.g.
                        ["mcp__desktop-control__*","Bash"]. Without it, the
                        Windows agent uses its own permission settings. This is
                        passed automatically only with the default Claude setup.
    - permission_mode : "default" | "acceptEdits" | "bypassPermissions"
                        (bypass = full autonomy; use only on a trusted machine).
    - resume          : a session_id returned by a previous call, to continue it.
    - cwd / add_dir   : working directory / extra allowed dirs (Windows paths,
                        e.g. C:\\work — pass /mnt/c/... for cwd from WSL).
    - model / timeout : optional model override / max seconds. Custom agent
                        templates may ignore unsupported fields.

    Returns {is_error, result, session_id, num_turns, total_cost_usd}.
    """
    return _run_windows_agent(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
        timeout=timeout,
    )


@mcp.tool()
def ask_windows_claude(
    prompt: str,
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir: list[str] | None = None,
    model: str | None = None,
    timeout: int = 600,
) -> dict:
    """Backward-compatible alias for ask_windows_agent()."""
    return ask_windows_agent(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
        timeout=timeout,
    )


_run_windows_claude = _run_windows_agent


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    print(f"[windows-claude-bridge] starting (transport={transport})",
          file=sys.stderr, flush=True)
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
