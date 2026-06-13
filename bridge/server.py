#!/usr/bin/env python3
"""
windows-agent-bridge — MCP server to delegate a task to a Windows-side agent.

Runs in WSL2 (or Linux). The generic tool `ask_windows_agent` (plus the legacy
`ask_windows_claude` alias) can invoke either:
- a Windows CLI agent (Claude/OpenAI/Mistral/Copilot/custom), or
- an API endpoint (OpenAI/Mistral/Copilot/custom),
and returns a normalized result.

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
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("windows-agent-bridge")
MAX_RESPONSE_TEXT_CHARS = 4000


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
    keys = [
        f"ASK_WIN_{provider.upper()}_{suffix}",
        f"ASK_WIN_AGENT_{suffix}",
    ]
    legacy = {"CLI_CMD": "ASK_WIN_AGENT_BIN", "CLI_ARGS": "ASK_WIN_AGENT_ARGS"}.get(suffix)
    if legacy:
        keys.append(legacy)
    for key in keys:
        val = os.environ.get(key)
        if val:
            return val
    return default


def _coerce_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    s = str(value).strip()
    if not s:
        return {}
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


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
    """Collect placeholder values used by CLI templates."""
    return {
        "prompt": prompt,
        "model": model,
        "resume": resume,
        "cwd": cwd,
        "permission_mode": permission_mode,
        "allowed_tools": _as_list(allowed_tools),
        "add_dir": _as_list(add_dir),
    }


def _render_template_tokens(
    tokens: list[str],
    values: dict[str, str | list[str] | None],
) -> list[str]:
    text_values = {
        key: "" if isinstance(value, list) or value is None else str(value)
        for key, value in values.items()
    }
    argv: list[str] = []
    for token in tokens:
        if token.startswith("{") and token.endswith("}"):
            value = values.get(token[1:-1])
            if isinstance(value, list):
                argv.extend(value)
                continue
            if value:
                argv.append(str(value))
            continue
        rendered = token.format_map(text_values)
        if rendered:
            argv.append(rendered)
    return argv


def _expand_arg_template(template: str, values: dict[str, str | list[str] | None]) -> list[str]:
    """Expand a shell-style argv template without splitting placeholder values."""
    return _render_template_tokens(shlex.split(template), values)


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
    return json.dumps(data, ensure_ascii=False)[:MAX_RESPONSE_TEXT_CHARS]


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
    binary: str | None = None,
    template: str | list[str] | None = None,
) -> list[str]:
    del timeout  # Signature compatibility with existing callers/tests.

    binary = binary or _env("claude", "CLI_CMD") or os.environ.get("ASK_WIN_CLAUDE_BIN") or "claude.exe"
    values = _template_values(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model or _env("claude", "MODEL"),
    )
    raw_template = template or _env("claude", "CLI_ARGS")
    if raw_template:
        if isinstance(raw_template, list):
            return [binary, *_render_template_tokens([str(token) for token in raw_template], values)]
        return [binary, *_expand_arg_template(str(raw_template), values)]

    argv: list[str] = [binary, "-p", prompt, "--output-format", "json"]

    tools = _as_list(allowed_tools) or _as_list(os.environ.get("ASK_WIN_CLAUDE_ALLOWED_TOOLS"))
    if tools:
        argv += ["--allowedTools", *tools]

    mode = permission_mode or os.environ.get("ASK_WIN_CLAUDE_PERMISSION_MODE")
    if mode:
        argv += ["--permission-mode", mode]

    mdl = model or _env("claude", "MODEL") or os.environ.get("ASK_WIN_CLAUDE_MODEL")
    if mdl:
        argv += ["--model", mdl]

    if resume:
        argv += ["--resume", resume]

    dirs = _as_list(add_dir)
    if dirs:
        argv += ["--add-dir", *dirs]
    return argv


def _normalize_agent_result(data) -> dict:
    """Map Claude-style or generic JSON payloads to a stable bridge result."""
    if isinstance(data, dict):
        result = data.get("result")
        if not isinstance(result, str) or not result.strip():
            result = None
        if result is None:
            for key in ("output_text", "text", "content", "response"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    result = value
                    break
        if result is None and isinstance(data.get("message"), dict):
            message = data["message"]
            msg_value = message.get("content") or message.get("text")
            if isinstance(msg_value, str) and msg_value.strip():
                result = msg_value
        if result is None and isinstance(data.get("choices"), list):
            for choice in data["choices"]:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if isinstance(message, dict):
                    msg_value = message.get("content") or message.get("text")
                    if isinstance(msg_value, str) and msg_value.strip():
                        result = msg_value
                if result:
                    break
                text_value = choice.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    result = text_value
                    break
        if result is None:
            result = _extract_api_text(data)

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
    binary: str | None = None,
    cli_args_template: str | list[str] | None = None,
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
        binary=binary,
        template=cli_args_template,
    )
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"is_error": True, "error": f"timed out after {timeout}s"}
    except OSError as e:
        agent_binary = argv[0]
        return {
            "is_error": True,
            "error": f"cannot launch '{agent_binary}' ({e.__class__.__name__}: {e}). "
            "Install your Windows-side agent CLI and make sure it is reachable from "
            "WSL, or set ASK_WIN_AGENT_BIN/ASK_WIN_AGENT_ARGS (or the provider-specific "
            "ASK_WIN_<PROVIDER>_CLI_CMD/CLI_ARGS vars) to the correct command.",
        }

    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except Exception:
        if proc.returncode != 0:
            return {
                "is_error": True,
                "error": f"exit {proc.returncode}",
                "stderr": (proc.stderr or "").strip()[:2000],
                "stdout": out[:2000],
            }
        return {"is_error": False, "result": out}

    result = _normalize_agent_result(data)
    if proc.returncode != 0 and not result.get("is_error"):
        result["is_error"] = True
        result["error"] = result.get("error") or f"exit {proc.returncode}"
        result["stderr"] = (proc.stderr or "").strip()[:2000]
    return result


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
        return _run_windows_agent(
            prompt,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            resume=resume,
            cwd=cwd,
            add_dir=add_dir,
            model=model,
            timeout=timeout,
            binary=cli_command,
            cli_args_template=cli_args_template,
        )

    if not prompt or not prompt.strip():
        return {"is_error": True, "error": "empty prompt"}

    default_cmd = {"openai": "openai", "mistral": "mistral", "copilot": "copilot"}.get(provider)
    base_cmd = cli_command or _env(provider, "CLI_CMD", default_cmd)
    if not base_cmd:
        return {"is_error": True, "error": f"missing CLI command for provider '{provider}'"}
    argv = shlex.split(base_cmd)
    if not argv:
        return {"is_error": True, "error": f"invalid CLI command for provider '{provider}'"}

    values = _template_values(
        prompt,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        resume=resume,
        cwd=cwd,
        add_dir=add_dir,
        model=model or _env(provider, "MODEL"),
    )
    raw_template = cli_args_template or _env(provider, "CLI_ARGS")
    if raw_template:
        if isinstance(raw_template, list):
            argv += _render_template_tokens([str(token) for token in raw_template], values)
        else:
            argv += _expand_arg_template(str(raw_template), values)
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
            f"Set ASK_WIN_{provider.upper()}_CLI_CMD, ASK_WIN_AGENT_CLI_CMD, or ASK_WIN_AGENT_BIN.",
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
    except Exception:
        return {"is_error": False, "result": out}

    result = _normalize_agent_result(data)
    result["raw"] = data
    return result


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
    if not prompt or not prompt.strip():
        return {"is_error": True, "error": "empty prompt"}

    url = api_url or _env(provider, "API_URL")
    if not url:
        return {
            "is_error": True,
            "error": f"missing API URL for provider '{provider}' (set ASK_WIN_{provider.upper()}_API_URL)",
        }

    vars_ = {"prompt": prompt, "model": model or _env(provider, "MODEL", "") or ""}
    try:
        headers = _coerce_dict(api_headers or _env(provider, "API_HEADERS"))
    except ValueError as exc:
        return {"is_error": True, "error": f"invalid API headers JSON: {exc}"}
    token = api_key or _env(provider, "API_KEY")
    if token and not any(str(key).lower() == "authorization" for key in headers):
        headers["Authorization"] = "Bearer " + token
    headers.setdefault("Content-Type", "application/json")

    default_body: dict | None = None
    if provider in {"openai", "mistral", "copilot"}:
        default_body = {"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}]}
    try:
        body = _coerce_dict(api_body or _env(provider, "API_BODY") or default_body or {"prompt": "{prompt}"})
    except ValueError as exc:
        return {"is_error": True, "error": f"invalid API body JSON: {exc}"}
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

    result = _normalize_agent_result(data)
    result["raw"] = data
    return result


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

    Returns {is_error, result, session_id, num_turns, total_cost_usd}.
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
    """Backward-compatible alias for ask_windows_agent()."""
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


_run_windows_claude = _run_windows_agent


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    print(f"[windows-agent-bridge] starting (transport={transport})", file=sys.stderr, flush=True)
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
