#!/usr/bin/env python3
"""
voice/smoke_test.py — validate the local audio chain (mic → STT → TTS).

Run on the target machine after installing voice/requirements.txt (and, on WSL2,
the WSLg audio prerequisites). It lists audio devices, speaks a prompt, records a
few seconds, transcribes them and reads the result back. Useful to confirm the
input/output devices (e.g. MCP_VOICE_INPUT_DEVICE / MCP_VOICE_OUTPUT_DEVICE) and
the TTS engine (MCP_VOICE_TTS) before wiring the MCP server / loop.

    python voice/smoke_test.py
"""

from __future__ import annotations

import sys

import voice_core


def main() -> int:
    try:
        table, default = voice_core.list_devices()
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] cannot query audio devices: {e}", file=sys.stderr)
        print("Hint: install PortAudio (libportaudio2) and, on WSL2, check that "
              "WSLg/PulseAudio is up (echo $PULSE_SERVER ; pactl info).",
              file=sys.stderr)
        return 2

    print("=== audio devices ===")
    print(table)
    print(f"default (input, output) = {default}\n")

    print("[smoke] TTS test…")
    try:
        voice_core.speak("Test audio. Parlez après le signal.")
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] TTS failed: {e}", file=sys.stderr)
        return 3

    print("[smoke] recording ~5s — speak now…")
    try:
        audio = voice_core.record_command(max_seconds=5.0)
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] mic capture failed: {e}", file=sys.stderr)
        return 4
    print(f"[smoke] captured {len(audio)} samples")

    print("[smoke] transcribing (first run downloads the Whisper model)…")
    try:
        text = voice_core.transcribe(audio)
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] STT failed: {e}", file=sys.stderr)
        return 5
    print(f"[smoke] transcription: {text!r}")

    voice_core.speak(f"Vous avez dit : {text}" if text else "Je n'ai rien entendu.")
    print("[smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
