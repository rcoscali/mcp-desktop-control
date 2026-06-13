#!/usr/bin/env python3
"""
bridge/ask.py — CLI to delegate a one-shot task to a Windows-side agent.

Same engine as the MCP tool, handy to validate the WSL2 -> Windows bridge before
wiring it into your MCP client.

    python bridge/ask.py "Open Notepad and type hello" \
        --allowed-tools "mcp__desktop-control__*" --permission-mode bypassPermissions
"""

from __future__ import annotations

import argparse
import json
import sys

from server import _ask_windows_agent


def main() -> int:
    ap = argparse.ArgumentParser(description="Delegate a task to a Windows-side agent.")
    ap.add_argument("prompt")
    ap.add_argument("--provider", default="claude", help="claude/openai/mistral/copilot/custom")
    ap.add_argument("--interface", default="cli", help="cli/api")
    ap.add_argument("--allowed-tools", nargs="*", default=None)
    ap.add_argument("--permission-mode", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--add-dir", nargs="*", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--cli-command", default=None, help="override CLI command (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)")
    ap.add_argument(
        "--cli-args-template",
        default=None,
        help="override CLI args template (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)",
    )
    ap.add_argument("--api-url", default=None, help="override API URL (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)")
    ap.add_argument("--api-key", default=None, help="override API key (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)")
    ap.add_argument("--api-headers", default=None, help="JSON object (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)")
    ap.add_argument("--api-body", default=None, help="JSON object (requires ASK_WIN_ALLOW_TOOL_PARAM_OVERRIDES)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true", help="print the full JSON result")
    args = ap.parse_args()

    res = _ask_windows_agent(
        args.prompt,
        provider=args.provider,
        interface=args.interface,
        allowed_tools=args.allowed_tools,
        permission_mode=args.permission_mode,
        resume=args.resume,
        cwd=args.cwd,
        add_dir=args.add_dir,
        model=args.model,
        cli_command=args.cli_command,
        cli_args_template=args.cli_args_template,
        api_url=args.api_url,
        api_key=args.api_key,
        api_headers=args.api_headers,
        api_body=args.api_body,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if res.get("is_error"):
            print(f"ERROR: {res.get('error') or res.get('result')}", file=sys.stderr)
        else:
            print(res.get("result", ""))
        if res.get("session_id"):
            print(f"\n[session_id: {res['session_id']}]", file=sys.stderr)
    return 1 if res.get("is_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
