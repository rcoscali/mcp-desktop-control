#!/usr/bin/env python3
"""
voice — MCP server exposing offline speech I/O (STT + TTS).

Tools:
  - speak(text)              : synthesize and play text on the local speakers
  - listen(max_seconds, lang): record a spoken command and return its text
  - transcribe_file(path)    : transcribe an existing audio file

All processing is local (faster-whisper + pyttsx3/Piper); audio never leaves the
machine. See voice_core.py for engines/config and README.md for setup.

Transport: stdio by default; MCP_DESKTOP_TRANSPORT=sse (or --sse) for HTTP/SSE.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

import voice_core

mcp = FastMCP("voice")


@mcp.tool()
def speak(text: str) -> str:
    """Speak `text` aloud on the local speakers (blocking until done)."""
    try:
        voice_core.speak(text)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"spoke {len(text)} chars"


@mcp.tool()
def listen(max_seconds: float = 15.0, language: str | None = None) -> str:
    """Record a spoken command from the mic and return its transcription.

    Stops on ~0.8s of silence (when VAD is available) or after max_seconds.
    `language` is an optional hint like "fr" or "en" (default: auto-detect).
    """
    try:
        text = voice_core.listen(max_seconds=max_seconds, language=language)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return text or "(no speech detected)"


@mcp.tool()
def transcribe_file(path: str, language: str | None = None) -> str:
    """Transcribe an existing audio file (wav/mp3/…) to text."""
    if not os.path.exists(path):
        return f"error: file not found: {path}"
    try:
        return voice_core.transcribe(path, language=language) or "(empty)"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    print(f"[voice] starting (transport={transport})", file=sys.stderr, flush=True)
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
