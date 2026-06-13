# WSL2 notes — drive Linux GUI from WSL2, or drive Windows by running the server on Windows

## Key point
- **WSLg apps ≠ Windows desktop apps**. WSLg exposes the **Linux** GUI into Windows,
  but it does **not** let a Linux process control the native Windows desktop.
- Therefore:
  - To drive **Linux GUI apps** from WSL2 → run `desktop-control` **inside WSL2**.
  - To drive the **Windows desktop/apps** from a WSL2-based agent → either
    1) run `desktop-control` **on Windows** (best), or
    2) delegate the whole task to a **Windows-side agent** via `bridge/`.

---

## Scheme B (recommended): control the Windows desktop from WSL2 by running the server on Windows

Because Windows Python can be launched from WSL via `python.exe`, you can keep
your agent in WSL2 but run the MCP server as a **Windows process**, which then
uses the real Windows backend (`pyautogui`, UI Automation, etc.).

### B.1 stdio transport (recommended)
In your MCP client config inside WSL2:
```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "python.exe",
      "args": ["C:\\Users\\you\\mcp-desktop-control\\server.py"],
      "env": { "MCP_DESKTOP_MAX_DIM": "1280" }
    }
  }
}
```

### B.2 SSE transport (also works)
Start the Windows server:
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

### B.3 Delegate from WSL2 to a Windows agent (`bridge/`)
Instead of (or alongside) running servers on Windows, a WSL2 client can hand a
whole task to a Windows AI agent via the `windows-agent-bridge` MCP server
(`ask_windows_agent`), using either CLI or API mode. The Windows agent — with
the desktop-control / voice servers configured — does the GUI/voice work and
returns the result. See `bridge/README.md`.

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
