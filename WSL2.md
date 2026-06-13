# Running under WSL2

WSL2 ships **WSLg**: a *virtual* display (Weston/Wayland + Xwayland) and a
*virtual* audio stack (a **PulseAudio** server bridged to Windows), both
**separate from the Windows desktop**. That single fact decides what runs where.

| You want to drive… | Run the server… | Why |
|---|---|---|
| the **Windows desktop** | **on Windows** | WSL2 cannot see/capture the Windows desktop |
| **voice** (mic/speakers) reliably | **on Windows** | native audio + SAPI5, no WSLg quirks |
| **Linux GUI apps** inside WSLg | in WSL2 | only WSLg apps are visible there |
| **voice** while staying in WSL2 | in WSL2 | works via WSLg PulseAudio (with caveats) |

---

## Scheme B (recommended): agent in WSL2 + servers on Windows

Claude Code runs in WSL2; the MCP servers run as **Windows** processes (so they
see the Windows desktop and use native audio). Two transports:

### B.1 stdio via interop (simplest)
Claude Code (WSL2) spawns `python.exe`, which is the **Windows** Python — it runs
on Windows and drives the Windows desktop. `.mcp.json`:

```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "python.exe",
      "args": ["C:\\\\Users\\\\you\\\\mcp-desktop-control\\\\server.py"],
      "env": { "MCP_DESKTOP_MAX_DIM": "1280" }
    },
    "voice": {
      "command": "python.exe",
      "args": ["C:\\\\Users\\\\you\\\\mcp-desktop-control\\\\voice\\\\server.py"],
      "env": { "MCP_VOICE_LANG": "fr" }
    }
  }
}
```
Install the deps with the **Windows** Python first:
```powershell
py -m pip install -r requirements.txt
py -m pip install -r voice\requirements.txt
```

### B.2 SSE (decoupled)
Start the servers on Windows, connect Claude Code (WSL2) over HTTP/SSE:
```powershell
python server.py --sse                 # desktop-control → :8000
set MCP_DESKTOP_TRANSPORT=sse & python voice\server.py   # voice (use a 2nd port if needed)
```
```json
{ "mcpServers": {
    "desktop-control": { "type": "sse", "url": "http://localhost:8000/sse" }
} }
```
> WSL2 with **mirrored networking** reaches Windows on `localhost`; otherwise use
> the Windows host IP (`ip route | grep default`, or `$(hostname).local`).

> `desktop-control` on Windows uses the real Windows backend (pyautogui + UI
> Automation). No WSL2-specific change is needed — the design already supports
> being launched as a Windows process from WSL2.

### B.3 Delegate from WSL2 to a Windows Claude (`bridge/`)
Instead of (or alongside) running servers on Windows, a **WSL2 Claude can hand a
whole task to a Windows AI agent** via the `windows-agent-bridge` MCP server
(`ask_windows_agent`), using either CLI or API mode. The Windows agent — with
the desktop-control / voice servers configured — does the
GUI/voice work and returns the result. See `bridge/README.md`.

---

## Scheme A: voice inside WSL2 (WSLg audio)

Only worth it if you specifically want speech captured **inside** WSL2 (e.g. to
also drive Linux GUI apps). Uses the WSLg PulseAudio bridge.

### Prerequisites (Windows 11 WSLg)
```bash
ls /mnt/wslg            # WSLg present
echo "$PULSE_SERVER"    # e.g. unix:/mnt/wslg/PulseServer
sudo apt install -y libportaudio2 libasound2-plugins espeak-ng pulseaudio-utils
pactl info              # PulseAudio reachable?
parecord -d @DEFAULT_SOURCE@ /tmp/t.wav   # Ctrl-C; then: paplay /tmp/t.wav
python -m pip install -r voice/requirements.txt
```

### Recommended settings under WSLg
- **TTS**: `MCP_VOICE_TTS=espeak` (espeak-ng → wav → sounddevice) is more robust
  than pyttsx3's direct output under Weston; set `MCP_VOICE_ESPEAK_VOICE=fr`.
- **Devices**: if capture/playback pick the wrong endpoint, set
  `MCP_VOICE_INPUT_DEVICE` / `MCP_VOICE_OUTPUT_DEVICE` (index or name substring;
  `python voice/smoke_test.py` prints the table).

### Validate the chain
```bash
MCP_VOICE_TTS=espeak MCP_VOICE_ESPEAK_VOICE=fr python voice/smoke_test.py
```

### Caveats
- **Microphone** capture under WSLg depends on the Windows 11 build and the
  default source; it is sometimes unavailable or noisy. If so, prefer Scheme B
  (voice server on Windows).
- **Latency**: local Whisper on CPU adds a few seconds (use a smaller model).
- `desktop-control` in WSL2 only sees the **WSLg** display (Linux apps), and the
  Wayland input path (`grim`/`ydotool`) is poorly supported under Weston — not
  recommended for GUI control here.
