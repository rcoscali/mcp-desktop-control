#!/usr/bin/env python3
"""
windows-agent-bridge — MCP server to delegate a task to a Windows AI agent.

Runs in WSL2 (or Linux). The tool `ask_windows_agent` invokes either:
- a Windows CLI agent (Claude/OpenAI/Mistral/Copilot/custom), or
- an API endpoint (OpenAI/Mistral/Copilot/custom)
and returns the parsed result.

This lets a WSL2 agent delegate Windows-side work (e.g. driving the Windows
desktop through the
desktop-control / voice MCP servers configured on the Windows side).

Why: WSL2 cannot see the Windows desktop; a Windows-side agent can. The WSL2 agent
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
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("windows-agent-bridge")


def _as_list(value) -> list[str]:
    """Accept a list or a comma/space-separated string -> list of tokens."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [t for t in str(value).replace(",", " ").split() if t]


def _normalize_provider(provider: str | None) -> str:
    p = (provider or "claude").strip().lower()
    aliases = {"github-copilot": "copilot", "gh-copilot": "copilot"}
    return aliases.get(p, p)


def _env(provider: str, suffix: str, default: str | None = None) -> str | None:
    val = os.environ.get(f"ASK_WIN_{provider.upper()}_{suffix}")
    if val:
        return val
    return os.environ.get(f"ASK_WIN_AGENT_{suffix}", default)


def _coerce_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    s = str(value).strip()
    if not s:
        return {}
    return json.loads(s)


def _format_template(value, variables: dict[str, str]):
    if isinstance(value, str):
        try:
            return value.format(**variables)
        except Exception:
            return value
    if isinstance(value, list):
        return [_format_template(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _format_template(v, variables) for k, v in value.items()}
    return value


def _extract_api_text(data) -> str:
    if isinstance(data, dict):
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        if isinstance(data.get("result"), str):
            return data["result"]
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    return msg["content"]
                if isinstance(first.get("text"), str):
                    return first["text"]
        output = data.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict):
                        txt = content.get("text")
                        if isinstance(txt, str):
                            parts.append(txt)
            if parts:
                return "\n".join(parts)
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False)[:4000]


def _run_cli_agent(
    provider: str,
    prompt: str,
    *,
    allowed_tools=None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir=None,
    model: str | None = None,
    timeout: int = 600,
    cli_command: str | None = None,
    cli_args_template: str | list[str] | None = None,
) -> dict:
    if provider == "claude":
        return _run_windows_claude(
            prompt,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            resume=resume,
            cwd=cwd,
            add_dir=add_dir,
            model=model,
            timeout=timeout,
            binary=cli_command,
        )

    default_cmd = {"openai": "openai", "mistral": "mistral", "copilot": "copilot"}.get(provider)
    base_cmd = cli_command or _env(provider, "CLI_CMD", default_cmd)
    if not base_cmd:
        return {"is_error": True, "error": f"missing CLI command for provider '{provider}'"}
    argv = shlex.split(base_cmd)
    if not argv:
        return {"is_error": True, "error": f"invalid CLI command for provider '{provider}'"}

    variables = {
        "prompt": prompt,
        "model": model or _env(provider, "MODEL", "") or "",
        "resume": resume or "",
        "permission_mode": permission_mode or "",
        "allowed_tools": " ".join(_as_list(allowed_tools)),
        "add_dir": " ".join(_as_list(add_dir)),
        "cwd": cwd or "",
    }
    raw_template = cli_args_template or _env(provider, "CLI_ARGS")
    if raw_template:
        if isinstance(raw_template, list):
            templ = raw_template
        else:
            templ = shlex.split(str(raw_template))
        argv += [str(_format_template(tok, variables)) for tok in templ if str(tok).strip()]
    else:
        argv.append(prompt)

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"is_error": True, "error": f"timed out after {timeout}s"}
    except OSError as e:
        return {
            "is_error": True,
            "error": f"cannot launch '{argv[0]}' ({e.__class__.__name__}: {e}). "
            f"Set ASK_WIN_{provider.upper()}_CLI_CMD or ASK_WIN_AGENT_CLI_CMD.",
        }

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {
            "is_error": True,
            "error": f"exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[:2000],
            "stdout": out[:2000],
        }
    try:
        data = json.loads(out) if out else {}
        return {
            "is_error": bool(data.get("is_error", False)),
            "result": data.get("result") or _extract_api_text(data),
            "session_id": data.get("session_id"),
            "num_turns": data.get("num_turns"),
            "total_cost_usd": data.get("total_cost_usd"),
            "raw": data,
        }
    except Exception:
        return {"is_error": False, "result": out}


def _run_api_agent(
    provider: str,
    prompt: str,
    *,
    model: str | None = None,
    timeout: int = 600,
    api_url: str | None = None,
    api_key: str | None = None,
    api_headers: dict | str | None = None,
    api_body: dict | str | None = None,
) -> dict:
    url = api_url or _env(provider, "API_URL")
    if not url:
        return {
            "is_error": True,
            "error": f"missing API URL for provider '{provider}' (set ASK_WIN_{provider.upper()}_API_URL)",
        }

    vars_ = {"prompt": prompt, "model": model or _env(provider, "MODEL", "") or ""}
    headers = _coerce_dict(api_headers or _env(provider, "API_HEADERS"))
    token = api_key or _env(provider, "API_KEY")
    if token and "Authorization" not in headers:
        headers["Authorization"] = "Bearer " + token
    headers.setdefault("Content-Type", "application/json")

    default_body: dict | None = None
    if provider == "openai":
        default_body = {"model": "{model}", "input": "{prompt}"}
    elif provider in {"mistral", "copilot"}:
        default_body = {"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}]}
    body = _coerce_dict(api_body or _env(provider, "API_BODY") or default_body or {"prompt": "{prompt}"})
    body = _format_template(body, vars_)

    req = urllib.request.Request(
        url=url,
        data=json.dumps(body).encode("utf-8"),
        headers={k: str(v) for k, v in headers.items()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        details = ""
        try:
            details = e.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            pass
        return {"is_error": True, "error": f"http {e.code}", "details": details}
    except Exception as e:
        return {"is_error": True, "error": f"API call failed ({e.__class__.__name__}: {e})"}

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {"is_error": False, "result": raw}

    return {
        "is_error": bool(data.get("is_error", False)),
        "result": _extract_api_text(data),
        "raw": data,
        "session_id": data.get("session_id"),
        "num_turns": data.get("num_turns"),
        "total_cost_usd": data.get("total_cost_usd"),
    }


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
    binary: str | None = None,
) -> dict:
    if not prompt or not prompt.strip():
        return {"is_error": True, "error": "empty prompt"}

    binary = binary or os.environ.get("ASK_WIN_CLAUDE_BIN", "claude.exe")
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
    except subprocess.TimeoutExpired:
        return {"is_error": True, "error": f"timed out after {timeout}s"}
    except OSError as e:
        # FileNotFoundError, PermissionError, … — claude.exe not launchable.
        return {
            "is_error": True,
            "error": f"cannot launch '{binary}' ({e.__class__.__name__}: {e}). "
            "Install Claude Code on Windows and make sure claude.exe is on the WSL "
            "PATH (Windows interop) and executable, or set ASK_WIN_CLAUDE_BIN to "
            "its full path (e.g. /mnt/c/Users/<you>/.../claude.exe).",
        }

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
def ask_windows_agent(
    prompt: str,
    provider: str = "claude",
    interface: str = "cli",
    allowed_tools: list[str] | None = None,
    permission_mode: str | None = None,
    resume: str | None = None,
    cwd: str | None = None,
    add_dir: list[str] | None = None,
    model: str | None = None,
    timeout: int = 600,
    cli_command: str | None = None,
    cli_args_template: str | list[str] | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    api_headers: dict | str | None = None,
    api_body: dict | str | None = None,
) -> dict:
    """Delegate a task to a Windows agent (Claude/OpenAI/Mistral/Copilot/custom).

    - provider  : claude | openai | mistral | copilot | custom
    - interface : cli | api
    - cli       : use cli_command/cli_args_template or env defaults
    - api       : POST api_url with api_headers/api_body (templated with {prompt}/{model})
    """
    p = _normalize_provider(provider)
    itf = (interface or "cli").strip().lower()
    if itf == "api":
        return _run_api_agent(
            p,
            prompt,
            model=model,
            timeout=timeout,
            api_url=api_url,
            api_key=api_key,
            api_headers=api_headers,
            api_body=api_body,
        )
    return _run_cli_agent(
        p,
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model,
        timeout=timeout,
        cli_command=cli_command,
        cli_args_template=cli_args_template,
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
    return ask_windows_agent(
        prompt,
        provider="claude",
        interface="cli",
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
    print(f"[windows-agent-bridge] starting (transport={transport})",
          file=sys.stderr, flush=True)
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
