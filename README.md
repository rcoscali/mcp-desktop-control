# desktop-control — MCP server (screenshot + keyboard/mouse + accessibility + voice cmds/STT + voice synth/TTS) **[[JUST For Fun]]**

Lets an MCP client (e.g. Claude Code) drive a GUI: capture the screen, then
move/click/type and — optionally — target controls **by name** via the OS
accessibility layer.

**Cross-platform**: **Windows** (pyautogui + UI Automation) and **Linux**
(X11 via pyautogui; Wayland via `grim`/`gnome-screenshot` + `ydotool`; AT-SPI
accessibility). OS specifics are isolated in `backends.py`; `server.py` is the
shared tool surface. See `DESIGN.md` for the design, **`ARCHITECTURE.md`** for the
big picture (desktop-control + voice + bridge), and **`DEPLOY.md`** for a
step-by-step WSL2 + Windows install.

## 1. Install (on the target machine)

Python 3.10+ on the machine whose desktop you want to drive.

```bash
python -m pip install -r requirements.txt
```

Platform extras:

| Platform | Needed | How |
|---|---|---|
| Windows | UI Automation | `pywinauto` (in requirements) |
| Linux **X11** | input + capture | works out of the box (pyautogui + mss) |
| Linux **Wayland** | capture | `grim` *or* `gnome-screenshot` *or* `spectacle` |
| Linux **Wayland** | input | `ydotool` + running `ydotoold` (uinput access) |
| Linux | accessibility | distro pkg `python3-pyatspi` |

> **Wayland blocks synthetic input by design.** The Wayland path is best-effort
> (`drag`/`scroll` unsupported via ydotool; keys limited to a common keymap).
> **Prefer an X11 session**, or rely on the AT-SPI tools (`ui_tree`/`ui_click`).

## 2. Run modes

The server must run on the machine being driven. Two ways to wire it to Claude Code:

- **stdio** (client launches it). From **WSL** targeting Windows, point the
  command at `python.exe` so the *Windows* Python runs. On native Linux/Windows,
  use `python`/`python3`.
- **SSE** (you launch it): `python server.py --sse` → `http://127.0.0.1:8000/sse`,
  then connect the client to that URL.

> **WSL2 users**: WSLg can't see the Windows desktop, so to drive Windows run the
> server **on Windows** (via `python.exe` or SSE). See **`WSL2.md`** for ready
> configs and the voice-in-WSL2 path.

```bash
# stdio
claude mcp add desktop-control -- python3 /path/to/server.py            # Linux
claude mcp add desktop-control -- python.exe 'C:\path\to\server.py'     # Windows / from WSL
# sse
claude mcp add --transport sse desktop-control http://localhost:8000/sse
```
See `mcp.json.example` for project-scope `.mcp.json` blocks.

## 3. Tools

| Tool | Purpose |
|---|---|
| `screen_size` | platform, session, real & image size, scale, accessibility |
| `screenshot` | PNG of the screen, scaled to `MCP_DESKTOP_MAX_DIM` |
| `mouse_move` / `click` / `double_click` / `right_click` / `drag` | mouse |
| `scroll` | wheel scroll |
| `type_text` / `press_key` / `hotkey` | keyboard |
| `wait` | pause (≤30s) |
| `ui_tree` / `ui_click` | accessibility tree dump / click by name (UIA or AT-SPI) |
| `guide_start` / `guide_status` / `guide_step` / `guide_confirm` | resumable human-guided flows |
| `guide_wait` / `guide_capture_response` | pause for the user and capture their response |

### Coordinate model
Screenshots are scaled so the longest side ≤ `MCP_DESKTOP_MAX_DIM` (default
1280). **All click/move/drag coordinates are in the returned screenshot's pixel
space**; the server converts them to real pixels. Call `screen_size()` for the
mapping. For pixel-fragile UIs, prefer `ui_tree` + `ui_click`.

## 4. Configuration (env)

| Variable | Default | Effect |
|---|---|---|
| `MCP_DESKTOP_MAX_DIM` | `1280` | max screenshot side (px) |
| `MCP_DESKTOP_DRY_RUN` | `0` | if `1`, don't actually move/click/type |
| `MCP_DESKTOP_TRANSPORT` | `stdio` | `stdio` or `sse` |

## 5. Voice-driven loop (optional)

See **`voice/README.md`** for prerequisites, configuration, and the local/offline
voice loop:

- wake word / speech capture
- STT (Whisper)
- agent round-trip
- TTS playback

## 6. Limits

- Multi-monitor: primary/virtual screen.
- Wayland input is restricted (see §1); X11 or AT-SPI recommended.
- Latency: each step is a screenshot→decide→act round-trip.

## 7. Voice (optional) — `voice/`

Offline **voice command** (STT) and **spoken responses** (TTS): a `voice` MCP
server (`speak` / `listen` / `transcribe_file`) plus a hands-free loop
(wake word → record → agent → speak). Engines: faster-whisper + pyttsx3/Piper,
all local. See **`voice/README.md`**.

## 8. WSL2 → Windows delegation (optional) — `bridge/`

`ask_windows_agent` lets a **WSL2** client delegate a task to a **Windows**
agent (Claude/OpenAI/Mistral/Copilot; CLI or API), so the Windows side can drive
desktop/voice while WSL2 orchestrates. The legacy tool name `ask_windows_claude`
remains available for compatibility. See **`bridge/README.md`** and `WSL2.md`.
