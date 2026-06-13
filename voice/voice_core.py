"""
voice_core — offline speech I/O shared by the MCP server and the hands-free loop.

- TTS  : pyttsx3 (SAPI5 on Windows, espeak on Linux) by default; optional Piper.
- STT  : faster-whisper (local), microphone via sounddevice, endpointed with
         webrtcvad when available (else a fixed recording window).
- Wake : openWakeWord if configured, otherwise push-to-talk (press Enter).

Everything runs locally: audio never leaves the machine. All heavy imports are
lazy so this module imports on a host without the audio stack installed.

Env configuration
-----------------
  MCP_VOICE_WHISPER_MODEL  faster-whisper model size (default "base")
  MCP_VOICE_LANG           language hint, e.g. "fr"/"en" (default: auto)
  MCP_VOICE_SR             sample rate (default 16000)
  MCP_VOICE_TTS            "pyttsx3" (default) | "piper"
  MCP_VOICE_RATE           pyttsx3 speech rate (words/min)
  MCP_VOICE_PIPER_BIN      piper binary (default "piper")
  MCP_VOICE_PIPER_MODEL    piper .onnx voice model (required if TTS=piper)
  MCP_VOICE_WAKE           openWakeWord model name; empty = push-to-talk
"""

from __future__ import annotations

import os

SR = int(os.environ.get("MCP_VOICE_SR", "16000"))

# --- lazy singletons --------------------------------------------------------
_whisper = None


def _model():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        size = os.environ.get("MCP_VOICE_WHISPER_MODEL", "base")
        _whisper = WhisperModel(size, device="cpu", compute_type="int8")
    return _whisper


# --- text to speech ---------------------------------------------------------
def speak(text: str) -> None:
    """Synthesize and play `text` on the local speakers (blocking)."""
    if not text:
        return
    if os.environ.get("MCP_VOICE_TTS", "pyttsx3").lower() == "piper":
        _speak_piper(text)
        return
    import pyttsx3

    engine = pyttsx3.init()
    rate = os.environ.get("MCP_VOICE_RATE")
    if rate:
        engine.setProperty("rate", int(rate))
    engine.say(text)
    engine.runAndWait()


def _speak_piper(text: str) -> None:
    import subprocess
    import tempfile

    import soundfile as sf
    import sounddevice as sd

    model = os.environ["MCP_VOICE_PIPER_MODEL"]
    binary = os.environ.get("MCP_VOICE_PIPER_BIN", "piper")
    wav = os.path.join(tempfile.gettempdir(), "mcp_voice_tts.wav")
    subprocess.run([binary, "-m", model, "-f", wav], input=text.encode(),
                   check=True, capture_output=True)
    data, sr = sf.read(wav, dtype="float32")
    sd.play(data, sr)
    sd.wait()


# --- microphone capture (endpointed) ---------------------------------------
def record_command(max_seconds: float = 15.0, silence_ms: int = 800):
    """Record from the mic until ~silence_ms of silence (VAD) or max_seconds.

    Returns a mono float32 numpy array at SR Hz.
    """
    import numpy as np
    import sounddevice as sd
    import time

    try:
        import webrtcvad

        vad = webrtcvad.Vad(2)
    except Exception:
        vad = None

    frame_ms = 30
    frame = int(SR * frame_ms / 1000)
    chunks = []
    voiced = False
    silence = 0
    start = time.time()

    with sd.InputStream(samplerate=SR, channels=1, dtype="int16") as stream:
        while True:
            data, _ = stream.read(frame)
            mono = data[:, 0]
            chunks.append(mono.copy())
            if vad is not None:
                try:
                    is_speech = vad.is_speech(mono.tobytes(), SR)
                except Exception:
                    is_speech = True
                if is_speech:
                    voiced = True
                    silence = 0
                elif voiced:
                    silence += frame_ms
                if voiced and silence >= silence_ms:
                    break
            if time.time() - start > max_seconds:
                break

    if not chunks:
        return np.zeros(0, dtype="float32")
    audio = np.concatenate(chunks).astype("float32") / 32768.0
    return audio


# --- speech to text ---------------------------------------------------------
def transcribe(audio, language: str | None = None) -> str:
    """Transcribe a numpy float32 array (at SR) or an audio file path to text."""
    lang = language or os.environ.get("MCP_VOICE_LANG") or None
    segments, _info = _model().transcribe(audio, language=lang)
    return " ".join(s.text.strip() for s in segments).strip()


def listen(max_seconds: float = 15.0, language: str | None = None) -> str:
    """Record a spoken command and return its transcription."""
    audio = record_command(max_seconds=max_seconds)
    if audio is None or len(audio) == 0:
        return ""
    return transcribe(audio, language=language)


# --- wake word / trigger ----------------------------------------------------
def wait_for_trigger() -> bool:
    """Block until the user wants to speak.

    Uses openWakeWord if MCP_VOICE_WAKE is set, otherwise push-to-talk (Enter).
    Returns True when triggered.
    """
    wake = os.environ.get("MCP_VOICE_WAKE", "").strip()
    if not wake:
        try:
            input("[push-to-talk] Entrée puis parlez (Ctrl-C pour quitter)… ")
        except EOFError:
            return False
        return True

    import numpy as np  # noqa: F401
    import sounddevice as sd
    from openwakeword.model import Model

    oww = Model() if wake == "*" else Model(wakeword_models=[wake])
    frame = int(SR * 0.08)
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16") as stream:
        while True:
            data, _ = stream.read(frame)
            preds = oww.predict(data[:, 0])
            if any(score > 0.5 for score in preds.values()):
                return True
