#!/usr/bin/env python3
"""
windows-claude-bridge — MCP server to delegate a task to a Windows Claude Code.

Runs in WSL2 (or Linux). The single tool `ask_windows_claude` invokes the
**Windows** `claude.exe` in headless mode (`-p --output-format json`) via the
WSL interop, parses the JSON result and returns it. This lets a WSL2 Claude
delegate Windows-side work (e.g. driving the Windows desktop through the
desktop-control / voice MCP servers configured on the Windows side).

Why: WSL2 cannot see the Windows desktop; a Windows Claude can. The WSL2 agent
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


def _run_windows_claude(
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

    binary = os.environ.get("ASK_WIN_CLAUDE_BIN", "claude.exe")
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

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except FileNotFoundError:
        return {
            "is_error": True,
            "error": f"'{binary}' not found. Install Claude Code on Windows and "
            "ensure claude.exe is on the WSL PATH (Windows interop), or set "
            "ASK_WIN_CLAUDE_BIN.",
        }
    except subprocess.TimeoutExpired:
        return {"is_error": True, "error": f"timed out after {timeout}s"}

    out = (proc.stdout or "").strip()
    # `--output-format json` prints a single result object.
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

    return {
        "is_error": bool(data.get("is_error", False)),
        "result": data.get("result", ""),
        "session_id": data.get("session_id"),
        "num_turns": data.get("num_turns"),
        "total_cost_usd": data.get("total_cost_usd"),
    }


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
    """Delegate a task to the Windows Claude Code and return its result.

    - prompt          : the instruction for the Windows agent.
    - allowed_tools   : tools the Windows agent may use without prompting, e.g.
                        ["mcp__desktop-control__*","Bash"]. Without it, the
                        Windows agent uses its own permission settings.
    - permission_mode : "default" | "acceptEdits" | "bypassPermissions"
                        (bypass = full autonomy; use only on a trusted machine).
    - resume          : a session_id returned by a previous call, to continue it.
    - cwd / add_dir   : working directory / extra allowed dirs (Windows paths,
                        e.g. C:\\work — pass /mnt/c/... for cwd from WSL).
    - model / timeout : optional model override / max seconds.

    Returns {is_error, result, session_id, num_turns, total_cost_usd}.
    """
    return _run_windows_claude(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
        timeout=timeout,
    )


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    print(f"[windows-claude-bridge] starting (transport={transport})",
          file=sys.stderr, flush=True)
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
