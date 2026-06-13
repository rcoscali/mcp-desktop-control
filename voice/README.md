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
- The agent call is configurable: `MCP_VOICE_AGENT_CMD` (default `claude -p`);
  the transcribed text is appended and the agent's stdout is spoken back.
- Say a stop word (`MCP_VOICE_STOPWORDS`, default *stop/quitte/au revoir/…*) to end.

## Configuration (env)

| Variable | Default | Effect |
|---|---|---|
| `MCP_VOICE_WHISPER_MODEL` | `base` | STT model size (tiny/base/small/medium/large) |
| `MCP_VOICE_LANG` | auto | language hint, e.g. `fr`/`en` |
| `MCP_VOICE_SR` | `16000` | capture sample rate |
| `MCP_VOICE_TTS` | `pyttsx3` | `pyttsx3` or `piper` |
| `MCP_VOICE_RATE` | — | pyttsx3 speech rate (wpm) |
| `MCP_VOICE_PIPER_BIN` / `MCP_VOICE_PIPER_MODEL` | `piper` / — | Piper voice |
| `MCP_VOICE_WAKE` | — | openWakeWord model; empty = push-to-talk |
| `MCP_VOICE_AGENT_CMD` | `claude -p` | agent invoked by the loop |

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
