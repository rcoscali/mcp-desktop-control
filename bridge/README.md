# bridge — delegate from WSL2 to a Windows-side agent

`ask_windows_agent` lets a client running in **WSL2** hand a task to a
headless agent running on **Windows**, and read back the result. This is
the clean way to act on the Windows side (e.g. drive the Windows desktop through
the desktop-control / voice MCP servers) from a WSL2 orchestrator — WSL2 cannot
see the Windows desktop itself.

```
Agent (WSL2)  ──ask_windows_agent──▶  Windows agent CLI (Claude / Gemini / Codex / Copilot / local wrapper)
                                        └─ its own tools / MCP servers
```

## Prerequisites
- A **Windows-side headless agent CLI** installed on Windows and reachable from
  WSL. By default the bridge targets `claude.exe`; set `ASK_WIN_AGENT_BIN`
  and `ASK_WIN_AGENT_ARGS` to use another agent.
- The Windows agent has its **own auth** and (ideally) the desktop-control /
  voice MCP servers configured, plus pre-authorized tools for automation.
- `pip install mcp` in the WSL2 Python that runs this server.

## Use as an MCP server (in WSL2)
```bash
claude mcp add windows-agent-bridge -- python3 /path/to/mcp-desktop-control/bridge/server.py
```
Tools `ask_windows_agent(...)` and `ask_windows_claude(...)`
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
| `ASK_WIN_AGENT_BIN` | path to the Windows agent CLI (defaults to `claude.exe`; `ASK_WIN_CLAUDE_BIN` still works) |
| `ASK_WIN_AGENT_ARGS` | optional argv template, e.g. `exec --json {prompt}` or `-p {prompt} --output-format json` |
| `ASK_WIN_CLAUDE_ALLOWED_TOOLS` | default `--allowedTools` (space/comma list) |
| `ASK_WIN_CLAUDE_PERMISSION_MODE` | default `--permission-mode` |
| `ASK_WIN_CLAUDE_MODEL` | default `--model` |

`ASK_WIN_AGENT_ARGS` placeholders: `{prompt}`, `{model}`, `{resume}`, `{cwd}`,
`{permission_mode}`, `{allowed_tools}`, `{add_dir}`. List placeholders should be
their own argv token.

## Safety
- The Windows agent's autonomy = your `permission_mode` / `allowed_tools`.
  **`bypassPermissions` grants full autonomy** — use only on a trusted/test
  machine, and keep risky GUI actions behind confirmation.
- Each call is a full agent run (cost + latency). Use `resume` for continuity.
- Paths are Windows-side: pass Windows paths to `add_dir` (e.g. `C:\work`), and
  a `/mnt/c/...` path for `cwd` when launching from WSL.

See `../WSL2.md` for the overall WSL2 deployment scheme.
