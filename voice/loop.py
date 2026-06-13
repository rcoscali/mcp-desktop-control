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
    MCP_VOICE_MODEL_NAME  assistant/model display name used for onboarding and
                          default acknowledgement (default: "Assistant")
    MCP_VOICE_ACK         spoken acknowledgement after the trigger
                          (default: "<MCP_VOICE_MODEL_NAME> ?")
    MCP_VOICE_STOPWORDS   comma-separated phrases that end the loop
                          (default: "stop,quitte,au revoir,goodbye,exit")
"""

from __future__ import annotations

import os
from pathlib import Path
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


def _config_home() -> Path:
    if os.name == "nt":
        fallback = str(Path.home() / "AppData" / "Roaming")
        return Path(os.environ.get("APPDATA", fallback))
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _first_run_marker() -> Path:
    return _config_home() / "mcp-desktop-control" / "voice_onboarding_done.marker"


def _onboarding_message(model_name: str) -> str:
    is_fr = voice_core.is_french()
    wake = os.environ.get("MCP_VOICE_WAKE", "").strip()
    if is_fr:
        trigger = ("Déclenchez-moi avec le modèle de mot-clé configuré."
                   if wake else
                   "Mode push-to-talk actif : appuyez sur Entrée puis parlez.")
        return (f"Bonjour, je suis {model_name}. "
                "Je peux écouter votre commande, appeler l'agent puis lire la réponse. "
                f"{trigger}")
    trigger = ("Wake-word mode is active with your configured wake model."
               if wake else
               "Push-to-talk mode is active: press Enter, then speak.")
    return (f"Hello, I am {model_name}. "
            "I can listen to your command, call the agent, and read the response aloud. "
            f"{trigger}")


def _maybe_run_first_launch_onboarding(model_name: str) -> None:
    marker = _first_run_marker()
    if marker.exists():
        return
    try:
        voice_core.speak(_onboarding_message(model_name))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[voice-loop] onboarding skipped: {e}", file=sys.stderr, flush=True)


def main() -> int:
    model_name = os.environ.get("MCP_VOICE_MODEL_NAME", "").strip() or "Assistant"
    ack = os.environ.get("MCP_VOICE_ACK", f"{model_name} ?")
    stop = [s.strip().lower() for s in
            os.environ.get("MCP_VOICE_STOPWORDS",
                           "stop,quitte,au revoir,goodbye,exit").split(",")
            if s.strip()]

    print("[voice-loop] prêt. (Ctrl-C pour arrêter)", file=sys.stderr, flush=True)
    _maybe_run_first_launch_onboarding(model_name)
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
