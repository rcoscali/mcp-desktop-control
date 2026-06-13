#!/usr/bin/env python3
"""
bridge/ask.py — CLI to delegate a one-shot task to the Windows Claude Code.

Same engine as the MCP tool, handy to validate the WSL2 -> Windows bridge before
wiring it into Claude Code.

    python bridge/ask.py "Open Notepad and type hello" \
        --allowed-tools "mcp__desktop-control__*" --permission-mode bypassPermissions
"""

from __future__ import annotations

import argparse
import json
import sys

from server import _run_windows_claude


def main() -> int:
    ap = argparse.ArgumentParser(description="Delegate a task to the Windows Claude Code.")
    ap.add_argument("prompt")
    ap.add_argument("--allowed-tools", nargs="*", default=None)
    ap.add_argument("--permission-mode", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--add-dir", nargs="*", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--json", action="store_true", help="print the full JSON result")
    args = ap.parse_args()

    res = _run_windows_claude(
        args.prompt,
        allowed_tools=args.allowed_tools,
        permission_mode=args.permission_mode,
        resume=args.resume,
        cwd=args.cwd,
        add_dir=args.add_dir,
        model=args.model,
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
