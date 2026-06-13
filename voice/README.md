# voice — offline speech command + spoken responses

Adds **voice command** (speech-to-text) and **spoken responses** (text-to-speech)
to the project, fully **offline** (audio never leaves the machine). Two usages:

1. **MCP server** (`voice/server.py`) — exposes `speak`, `listen`,
   `transcribe_file` as tools, so the agent can hear and talk during a turn.
2. **Hands-free loop** (`voice/loop.py`) — wake word → record → transcribe →
   run the agent → speak the reply, no keyboard.

Engines: **faster-whisper** (STT), **pyttsx3** or **Piper** (TTS), microphone via
**sounddevice**, silence endpointing via **webrtcvad**, optional wake word via
**openWakeWord**. Shared engine code is in `voice_core.py`.

## Install

```bash
python -m pip install -r requirements.txt          # base (desktop-control)
python -m pip install -r voice/requirements.txt    # voice extras
```
System packages:
- **Linux**: `libportaudio2` (mic), `espeak-ng` (pyttsx3 TTS); optional `piper`.
- **Windows**: PortAudio ships in the `sounddevice` wheel; SAPI5 TTS is built in.

First run downloads the faster-whisper model (size via `MCP_VOICE_WHISPER_MODEL`,
default `base`).

## A. Voice MCP server

```bash
claude mcp add voice -- python3 /path/to/mcp-desktop-control/voice/server.py
# or SSE:  python voice/server.py --sse   →  http://127.0.0.1:8000/sse
```
Tools: `speak(text)`, `listen(max_seconds, language)`, `transcribe_file(path, language)`.
Combine with the desktop-control server for a voice-driven GUI agent.

## B. Hands-free loop

```bash
python voice/loop.py
```
- Trigger: set `MCP_VOICE_WAKE` to an openWakeWord model (e.g. `hey_jarvis`) for
  wake-word; leave it empty for **push-to-talk** (press Enter, then speak).
- First launch plays a one-time spoken onboarding presentation, then stores a
  marker in the user config directory (`~/.config/mcp-desktop-control/` on
  Linux, `%APPDATA%\mcp-desktop-control\` on Windows).
- The agent call is configurable: `MCP_VOICE_AGENT_CMD` (default `claude -p`);
  the transcribed text is appended and the agent's stdout is spoken back.
- Say a stop word (`MCP_VOICE_STOPWORDS`, default *stop/quitte/au revoir/…*) to end.

## Configuration (env)

| Variable | Default | Effect |
|---|---|---|
| `MCP_VOICE_WHISPER_MODEL` | `base` | STT model size (tiny/base/small/medium/large) |
| `MCP_VOICE_LANG` | auto | language hint, e.g. `fr`/`en` |
| `MCP_VOICE_SR` | `16000` | capture sample rate |
| `MCP_VOICE_TTS` | `pyttsx3` | `pyttsx3` \| `espeak` \| `piper` |
| `MCP_VOICE_MODEL_NAME` | `Assistant` | model/assistant display name used in onboarding and default ack |
| `MCP_VOICE_ACK` | uses `MCP_VOICE_MODEL_NAME` + ` ?` | spoken acknowledgement after trigger (set empty to disable) |
| `MCP_VOICE_RATE` | — | speech rate (pyttsx3 wpm / espeak `-s`) |
| `MCP_VOICE_ESPEAK_BIN` / `MCP_VOICE_ESPEAK_VOICE` | `espeak-ng` / — | espeak engine & voice (e.g. `fr`) |
| `MCP_VOICE_PIPER_BIN` / `MCP_VOICE_PIPER_MODEL` | `piper` / — | Piper voice |
| `MCP_VOICE_INPUT_DEVICE` / `MCP_VOICE_OUTPUT_DEVICE` | — | sounddevice in/out (index or name substring) |
| `MCP_VOICE_WAKE` | — | openWakeWord model name; empty = push-to-talk |
| `MCP_VOICE_AGENT_CMD` | `claude -p` | agent invoked by the loop |

## Validate the audio chain

```bash
python voice/smoke_test.py     # lists devices, TTS → record → STT → speak back
```

## WSL2 (WSLg)

Voice works inside WSL2 via the WSLg PulseAudio bridge — install
`libportaudio2 libasound2-plugins espeak-ng pulseaudio-utils`, prefer
`MCP_VOICE_TTS=espeak`, and set the input/output devices if needed. The
microphone can be flaky under WSLg; the more reliable option is to run this
server **on Windows** while the agent runs in WSL2. Full guide: **`../WSL2.md`**.

## Notes & caveats

- **Offline / privacy**: STT and TTS run locally; nothing is uploaded — suited to
  embedded/automotive contexts.
- **Latency**: local Whisper on CPU is a few seconds; use a smaller model or a
  GPU build for faster turns; `vosk` is an alternative for streaming.
- **Mis-hearing**: a voice command can be mis-transcribed — keep confirmation on
  risky actions (especially when paired with the desktop-control tools).
- If `webrtcvad` is absent, recording uses a fixed `max_seconds` window.
- If `openwakeword` is absent or `MCP_VOICE_WAKE` is empty, the loop uses
  push-to-talk.
- `MCP_VOICE_WAKE` is a wake **model id** (openWakeWord), not a free-form phrase;
  if the model cannot be loaded, the loop falls back to push-to-talk.
