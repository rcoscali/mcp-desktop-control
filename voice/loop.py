#!/usr/bin/env python3
"""
voice/loop.py — hands-free voice loop around an agent (e.g. Claude Code).

Cycle:
    wake word / push-to-talk  →  record  →  transcribe  →  run agent  →  speak

The agent is invoked as a subprocess (default: `claude -p "<text>"`, Claude Code
headless print mode) and its stdout is read back aloud. Everything except the
agent call is local/offline.

Run:
    python voice/loop.py

Env (plus those of voice_core):
    MCP_VOICE_AGENT_CMD   agent command, the prompt is appended as last arg
                          (default: "claude -p")
    MCP_VOICE_ACK         spoken acknowledgement after the trigger (default: "Oui ?")
    MCP_VOICE_STOPWORDS   comma-separated phrases that end the loop
                          (default: "stop,quitte,au revoir,goodbye,exit")
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

import voice_core


def run_agent(prompt: str) -> str:
    cmd = shlex.split(os.environ.get("MCP_VOICE_AGENT_CMD", "claude -p"))
    try:
        r = subprocess.run(cmd + [prompt], capture_output=True, text=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        return f"Erreur lors de l'appel de l'agent : {e}"
    out = (r.stdout or "").strip()
    return out or (r.stderr or "").strip() or "(réponse vide)"


def main() -> int:
    ack = os.environ.get("MCP_VOICE_ACK", "Oui ?")
    stop = [s.strip().lower() for s in
            os.environ.get("MCP_VOICE_STOPWORDS",
                           "stop,quitte,au revoir,goodbye,exit").split(",")
            if s.strip()]

    print("[voice-loop] prêt. (Ctrl-C pour arrêter)", file=sys.stderr, flush=True)
    try:
        while True:
            if not voice_core.wait_for_trigger():
                break
            if ack:
                voice_core.speak(ack)

            text = voice_core.listen()
            if not text:
                voice_core.speak("Je n'ai rien entendu.")
                continue
            print(f"[voice-loop] commande: {text!r}", file=sys.stderr, flush=True)

            low = text.lower().strip(" .!?")
            if any(low == w or low.startswith(w) for w in stop):
                voice_core.speak("Au revoir.")
                break

            answer = run_agent(text)
            print(f"[voice-loop] réponse: {answer[:200]!r}…", file=sys.stderr, flush=True)
            voice_core.speak(answer)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
