# bridge — delegate from a WSL2 Claude to a Windows Claude

`ask_windows_claude` lets a Claude Code running in **WSL2** hand a task to a
Claude Code running on **Windows** (headless), and read back the result. This is
the clean way to act on the Windows side (e.g. drive the Windows desktop through
the desktop-control / voice MCP servers) from a WSL2 orchestrator — WSL2 cannot
see the Windows desktop itself.

```
Claude (WSL2)  ──ask_windows_claude──▶  claude.exe -p … --output-format json (Windows)
                                          └─ its own tools / MCP servers
```

## Prerequisites
- **Claude Code installed on Windows**, with `claude.exe` reachable from WSL
  (default Windows↔WSL interop puts it on PATH; else set `ASK_WIN_CLAUDE_BIN`).
- The Windows Claude has its **own auth** and (ideally) the desktop-control /
  voice MCP servers configured, plus pre-authorized tools for automation.
- `pip install mcp` in the WSL2 Python that runs this server.

## Use as an MCP server (in WSL2)
```bash
claude mcp add windows-claude-bridge -- python3 /path/to/mcp-desktop-control/bridge/server.py
```
Tool `ask_windows_claude(prompt, allowed_tools?, permission_mode?, resume?, cwd?, add_dir?, model?, timeout?)`
→ returns `{is_error, result, session_id, num_turns, total_cost_usd}`.
Pass the returned `session_id` back as `resume` to continue the same Windows session.

## Quick test (CLI)
```bash
python bridge/ask.py "What is your working directory?" --json
python bridge/ask.py "Open Notepad and type hello" \
    --allowed-tools "mcp__desktop-control__*" --permission-mode bypassPermissions
```

## Configuration (env)
| Variable | Effect |
|---|---|
| `ASK_WIN_CLAUDE_BIN` | path to the Windows Claude (default `claude.exe`) |
| `ASK_WIN_CLAUDE_ALLOWED_TOOLS` | default `--allowedTools` (space/comma list) |
| `ASK_WIN_CLAUDE_PERMISSION_MODE` | default `--permission-mode` |
| `ASK_WIN_CLAUDE_MODEL` | default `--model` |

## Safety
- The Windows agent's autonomy = your `permission_mode` / `allowed_tools`.
  **`bypassPermissions` grants full autonomy** — use only on a trusted/test
  machine, and keep risky GUI actions behind confirmation.
- Each call is a full agent run (cost + latency). Use `resume` for continuity.
- Paths are Windows-side: pass Windows paths to `add_dir` (e.g. `C:\work`), and
  a `/mnt/c/...` path for `cwd` when launching from WSL.

See `../WSL2.md` for the overall WSL2 deployment scheme.
