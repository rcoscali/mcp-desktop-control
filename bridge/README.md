# bridge — delegate from WSL2 to a Windows AI agent

`ask_windows_agent` lets an orchestrator in **WSL2** hand a task to an agent
running on **Windows** (CLI or API), and read back the result. This is
the clean way to act on the Windows side (e.g. drive the Windows desktop through
the desktop-control / voice MCP servers) from a WSL2 orchestrator — WSL2 cannot
see the Windows desktop itself.

```
WSL2 orchestrator  ──ask_windows_agent──▶  Windows agent CLI/API
                                             └─ its own tools / MCP servers
```

## Prerequisites
- A Windows-side agent/CLI/API configured (Claude/OpenAI/Mistral/Copilot/custom).
- The Windows-side agent has its **own auth** and (ideally) the desktop-control /
  voice MCP servers configured, plus pre-authorized tools for automation.
- `pip install mcp` in the WSL2 Python that runs this server.

## Use as an MCP server (in WSL2)
```bash
claude mcp add windows-agent-bridge -- python3 /path/to/mcp-desktop-control/bridge/server.py
```
Tool `ask_windows_agent(prompt, provider?, interface?, ... )`
→ returns `{is_error, result, session_id, num_turns, total_cost_usd}`.
Pass the returned `session_id` back as `resume` to continue the same Windows session.

## Quick test (CLI)
```bash
python bridge/ask.py "What is your working directory?" --json
python bridge/ask.py "Open Notepad and type hello" \
    --allowed-tools "mcp__desktop-control__*" --permission-mode bypassPermissions
python bridge/ask.py "Summarize this text" --provider openai --interface api \
    --api-url "https://api.openai.com/v1/responses" --api-key "$OPENAI_API_KEY" \
    --api-body '{"model":"gpt-4.1-mini","input":"{prompt}"}'
```

## Configuration (env)
| Variable | Effect |
|---|---|
| `ASK_WIN_CLAUDE_BIN` | Claude CLI binary (default `claude.exe`) |
| `ASK_WIN_CLAUDE_ALLOWED_TOOLS` | default `--allowedTools` for Claude |
| `ASK_WIN_CLAUDE_PERMISSION_MODE` | default `--permission-mode` for Claude |
| `ASK_WIN_CLAUDE_MODEL` | default Claude model |
| `ASK_WIN_<PROVIDER>_CLI_CMD` | CLI command for `provider` (`OPENAI`, `MISTRAL`, `COPILOT`, `CUSTOM`) |
| `ASK_WIN_<PROVIDER>_CLI_ARGS` | CLI args template (supports `{prompt}`, `{model}`…) |
| `ASK_WIN_<PROVIDER>_API_URL` | API URL for `provider` |
| `ASK_WIN_<PROVIDER>_API_KEY` | API key (****** if no Authorization header) |
| `ASK_WIN_<PROVIDER>_API_HEADERS` | JSON headers object |
| `ASK_WIN_<PROVIDER>_API_BODY` | JSON body template |
| `ASK_WIN_AGENT_*` | generic fallback for any provider |

## Safety
- The Windows agent's autonomy = your `permission_mode` / `allowed_tools` (CLI mode).
  **`bypassPermissions` grants full autonomy** — use only on a trusted/test
  machine, and keep risky GUI actions behind confirmation.
- Each call is a full agent run (cost + latency). Use `resume` for continuity.
- Paths are Windows-side: pass Windows paths to `add_dir` (e.g. `C:\work`), and
  a `/mnt/c/...` path for `cwd` when launching from WSL.

See `../WSL2.md` for the overall WSL2 deployment scheme.
