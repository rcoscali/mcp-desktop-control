#!/usr/bin/env python3
"""
desktop-control — MCP server to drive a GUI by screenshot + mouse/keyboard.

Cross-platform: Windows (pyautogui + UI Automation) and Linux (X11 via
pyautogui; Wayland via grim/gnome-screenshot + ydotool; AT-SPI accessibility).
The OS specifics live in backends.py; this module is the platform-agnostic MCP
surface (tools, coordinate scaling, dry-run, transport).

Coordinate model
-----------------
Screenshots are returned scaled so the longest side is <= MCP_DESKTOP_MAX_DIM
(default 1280). ALL coordinate-taking tools expect (x, y) in the pixel space of
the returned screenshot; the server converts them back to real screen pixels.
Call `screen_size()` to learn the image space and scale.

Transport
---------
Default stdio (the client launches this process). Set MCP_DESKTOP_TRANSPORT=sse
(or pass --sse) to serve over HTTP/SSE instead.

Safety
------
- pyautogui FAILSAFE is ON (X11/Windows): slam the mouse into a corner to abort.
- MCP_DESKTOP_DRY_RUN=1 makes every *action* a logged no-op (perception still
  works) — rehearse a plan safely.
- Driving a real machine has side effects; the client should confirm risky
  actions (close/delete/submit) with the user.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP, Image

import backends

DRY_RUN = os.environ.get("MCP_DESKTOP_DRY_RUN", "0") not in ("0", "", "false", "False")
MAX_DIM = int(os.environ.get("MCP_DESKTOP_MAX_DIM", "1280"))

mcp = FastMCP("desktop-control")
_backend: backends.Backend | None = None
_guidance_sessions: dict[str, dict] = {}
_guide_tts_engine = None
GUIDE_MAX_WAIT_SECONDS = 120.0


def _token_set(env_name: str, default: str) -> set[str]:
    raw = os.environ.get(env_name, default)
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


GUIDE_CONFIRM_WORDS = _token_set(
    "MCP_DESKTOP_GUIDE_CONFIRM_WORDS",
    "yes,y,ok,okay,confirm,confirmed,proceed,continue,approved,approve,oui,confirmer,continuer",
)
GUIDE_REJECT_WORDS = _token_set(
    "MCP_DESKTOP_GUIDE_REJECT_WORDS",
    "no,n,cancel,stop,abort,deny,rejected,non,annuler,arreter,arrêter",
)


def _be() -> backends.Backend:
    global _backend
    if _backend is None:
        _backend = backends.get_backend()
        _log(f"backend={_backend.name} a11y={_backend.accessibility} dry_run={DRY_RUN}")
    return _backend


def _log(msg: str) -> None:
    print(f"[desktop-control] {msg}", file=sys.stderr, flush=True)


def _scale() -> float:
    w, h = _be().screen_size()
    longest = max(w, h)
    return min(1.0, MAX_DIM / longest) if longest else 1.0


def _to_real(x: float, y: float) -> tuple[int, int]:
    s = _scale()
    return int(round(x / s)), int(round(y / s))


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guidance_error(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _guidance_session(session_id: str) -> dict | None:
    return _guidance_sessions.get(session_id)


def _speak_optional(text: str) -> dict:
    global _guide_tts_engine
    if not text.strip():
        return {"spoken": False, "speech_error": "empty text"}
    try:
        import pyttsx3  # type: ignore

        if _guide_tts_engine is None:
            _guide_tts_engine = pyttsx3.init()
        _guide_tts_engine.say(text)
        _guide_tts_engine.runAndWait()
        return {"spoken": True, "speech_error": None}
    except Exception as e:  # noqa: BLE001
        return {"spoken": False, "speech_error": str(e)}


def _is_confirmed(response: str) -> bool:
    return response.strip().lower() in GUIDE_CONFIRM_WORDS


def _is_rejected(response: str) -> bool:
    return response.strip().lower() in GUIDE_REJECT_WORDS


# --- perception -------------------------------------------------------------
@mcp.tool()
def screen_size() -> dict:
    """Return real screen size, screenshot (image) size, scale and capabilities.

    Coordinates passed to click/move/drag are in the *image* space.
    """
    w, h = _be().screen_size()
    s = _scale()
    return {
        "platform": _be().name,
        "session": getattr(_be(), "session", "n/a"),
        "real_width": w,
        "real_height": h,
        "image_width": int(round(w * s)),
        "image_height": int(round(h * s)),
        "scale": s,
        "accessibility": _be().accessibility,
        "dry_run": DRY_RUN,
    }


@mcp.tool()
def screenshot() -> Image:
    """Capture the screen as a PNG (scaled to MCP_DESKTOP_MAX_DIM).

    Use the pixel coordinates of THIS image for click/move/drag.
    """
    img = _be().grab().convert("RGB")
    s = _scale()
    if s < 1.0:
        img = img.resize((int(img.width * s), int(img.height * s)))
    return Image(data=_png_bytes(img), format="png")


# --- mouse ------------------------------------------------------------------
@mcp.tool()
def mouse_move(x: float, y: float) -> str:
    """Move the cursor to (x, y) in screenshot space."""
    rx, ry = _to_real(x, y)
    if DRY_RUN:
        return f"[dry-run] move -> ({rx},{ry})"
    try:
        _be().move(rx, ry)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"moved to ({rx},{ry})"


@mcp.tool()
def click(x: float, y: float, button: str = "left", clicks: int = 1) -> str:
    """Click at (x, y) in screenshot space. button: left|right|middle."""
    if button not in ("left", "right", "middle"):
        return f"error: invalid button '{button}'"
    rx, ry = _to_real(x, y)
    if DRY_RUN:
        return f"[dry-run] {button} click x{clicks} -> ({rx},{ry})"
    try:
        _be().click(rx, ry, button=button, clicks=clicks)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"{button} click x{clicks} at ({rx},{ry})"


@mcp.tool()
def double_click(x: float, y: float) -> str:
    """Double left-click at (x, y) in screenshot space."""
    return click(x, y, button="left", clicks=2)


@mcp.tool()
def right_click(x: float, y: float) -> str:
    """Right-click at (x, y) in screenshot space."""
    return click(x, y, button="right", clicks=1)


@mcp.tool()
def drag(x1: float, y1: float, x2: float, y2: float, button: str = "left",
         duration: float = 0.3) -> str:
    """Drag from (x1,y1) to (x2,y2) in screenshot space."""
    rx1, ry1 = _to_real(x1, y1)
    rx2, ry2 = _to_real(x2, y2)
    if DRY_RUN:
        return f"[dry-run] drag ({rx1},{ry1}) -> ({rx2},{ry2})"
    try:
        _be().drag(rx1, ry1, rx2, ry2, button=button, duration=duration)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"dragged ({rx1},{ry1}) -> ({rx2},{ry2})"


@mcp.tool()
def scroll(amount: int, x: float | None = None, y: float | None = None) -> str:
    """Scroll vertically by `amount` clicks (positive = up). Optionally at (x,y)."""
    if DRY_RUN:
        return f"[dry-run] scroll {amount}"
    try:
        if x is not None and y is not None:
            rx, ry = _to_real(x, y)
            _be().scroll(amount, x=rx, y=ry)
        else:
            _be().scroll(amount)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"scrolled {amount}"


# --- keyboard ---------------------------------------------------------------
@mcp.tool()
def type_text(text: str, interval: float = 0.02) -> str:
    """Type a literal string at the current focus."""
    if DRY_RUN:
        return f"[dry-run] type {len(text)} chars"
    try:
        _be().type_text(text, interval=interval)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"typed {len(text)} chars"


@mcp.tool()
def press_key(key: str) -> str:
    """Press a single key (e.g. enter, esc, tab, f5, up, backspace)."""
    if DRY_RUN:
        return f"[dry-run] key {key}"
    try:
        _be().press(key)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"pressed {key}"


@mcp.tool()
def hotkey(keys: list[str]) -> str:
    """Press a key combination together, e.g. ["ctrl","c"] or ["win","r"]."""
    if not keys:
        return "error: empty hotkey"
    if DRY_RUN:
        return f"[dry-run] hotkey {'+'.join(keys)}"
    try:
        _be().hotkey(keys)
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    return f"hotkey {'+'.join(keys)}"


@mcp.tool()
def wait(seconds: float) -> str:
    """Sleep for `seconds` (e.g. to let a window open). Capped at 30s."""
    import time

    seconds = max(0.0, min(seconds, 30.0))
    time.sleep(seconds)
    return f"waited {seconds}s"


# --- accessibility (UI Automation on Windows / AT-SPI on Linux) -------------
@mcp.tool()
def ui_tree(window_title: str | None = None, max_chars: int = 4000) -> str:
    """Dump the accessibility control tree (optionally of one app/window).

    More reliable than vision for targeting. Returns a truncated text tree of
    control names/roles you can then act on with `ui_click`.
    """
    return _be().a11y_tree(window_title, max_chars)


@mcp.tool()
def ui_click(name: str, window_title: str | None = None,
             control_type: str | None = None) -> str:
    """Find a control by (partial) name via accessibility and click its center."""
    return _be().a11y_click(name, window_title, control_type)


# --- human operator guidance -------------------------------------------------
@mcp.tool()
def guide_start(goal: str, context: str | None = None, session_id: str | None = None) -> dict:
    """Start (or resume) a step-by-step human operator guidance session."""
    if not goal.strip():
        return _guidance_error("goal is required")

    sid = session_id or f"guide-{uuid.uuid4()}"
    existing = _guidance_session(sid)
    if existing:
        return {
            "ok": True,
            "resumed": True,
            "session_id": sid,
            "goal": existing["goal"],
            "steps_total": len(existing["steps"]),
            "events_total": len(existing["events"]),
            "updated_at": existing["updated_at"],
        }

    now = _now_iso()
    _guidance_sessions[sid] = {
        "session_id": sid,
        "goal": goal.strip(),
        "context": (context or "").strip(),
        "steps": [],
        "events": [],
        "created_at": now,
        "updated_at": now,
    }
    return {
        "ok": True,
        "resumed": False,
        "session_id": sid,
        "goal": goal.strip(),
        "context": (context or "").strip(),
        "next_hint": "Call guide_step(...) to present the next action to the operator.",
    }


@mcp.tool()
def guide_status(session_id: str) -> dict:
    """Return the current status of a guidance session for resumable workflows."""
    s = _guidance_session(session_id)
    if not s:
        return _guidance_error(f"unknown session_id '{session_id}'")
    last_step = s["steps"][-1] if s["steps"] else None
    return {
        "ok": True,
        "session_id": session_id,
        "goal": s["goal"],
        "context": s["context"],
        "steps_total": len(s["steps"]),
        "events_total": len(s["events"]),
        "last_step": last_step,
        "created_at": s["created_at"],
        "updated_at": s["updated_at"],
    }


@mcp.tool()
def guide_step(
    session_id: str,
    goal: str,
    operator_action: str,
    resume_signal: str,
    expected_outcome: str | None = None,
    risky: bool = False,
    speak: bool = False,
) -> dict:
    """Present the next human step with goal/action/resume signal."""
    s = _guidance_session(session_id)
    if not s:
        return _guidance_error(f"unknown session_id '{session_id}'")
    if not goal.strip() or not operator_action.strip() or not resume_signal.strip():
        return _guidance_error("goal, operator_action, and resume_signal are required")

    step_number = len(s["steps"]) + 1
    instruction = (
        f"Step {step_number}\n"
        f"- Goal: {goal.strip()}\n"
        f"- Operator action: {operator_action.strip()}\n"
        f"- Resume signal: {resume_signal.strip()}"
    )
    if expected_outcome and expected_outcome.strip():
        instruction += f"\n- Expected outcome: {expected_outcome.strip()}"
    if risky:
        instruction += (
            "\n- Safety: This is a risky action. Require explicit operator confirmation "
            "with guide_confirm(...) before executing any irreversible action."
        )

    speech = {"spoken": False, "speech_error": None}
    if speak:
        speech = _speak_optional(instruction)

    record = {
        "type": "step",
        "step_number": step_number,
        "goal": goal.strip(),
        "operator_action": operator_action.strip(),
        "resume_signal": resume_signal.strip(),
        "expected_outcome": (expected_outcome or "").strip(),
        "risky": risky,
        "instruction": instruction,
        "spoken": speech["spoken"],
        "speech_error": speech["speech_error"],
        "created_at": _now_iso(),
    }
    s["steps"].append(record)
    s["events"].append(record)
    s["updated_at"] = _now_iso()
    return {
        "ok": True,
        "session_id": session_id,
        "step_number": step_number,
        "instruction": instruction,
        "risky": risky,
        "spoken": speech["spoken"],
        "speech_error": speech["speech_error"],
    }


@mcp.tool()
def guide_confirm(
    session_id: str,
    action: str,
    operator_response: str,
    require_explicit_yes: bool = True,
) -> dict:
    """Capture operator confirmation for risky actions."""
    s = _guidance_session(session_id)
    if not s:
        return _guidance_error(f"unknown session_id '{session_id}'")
    if not action.strip():
        return _guidance_error("action is required")
    if not operator_response.strip():
        return _guidance_error("operator_response is required")

    if require_explicit_yes:
        confirmed = _is_confirmed(operator_response)
    else:
        confirmed = not _is_rejected(operator_response)
    event = {
        "type": "confirm",
        "action": action.strip(),
        "operator_response": operator_response.strip(),
        "require_explicit_yes": require_explicit_yes,
        "confirmed": confirmed,
        "created_at": _now_iso(),
    }
    s["events"].append(event)
    s["updated_at"] = _now_iso()
    return {
        "ok": True,
        "session_id": session_id,
        "action": action.strip(),
        "confirmed": confirmed,
        "next_hint": (
            "Safe to continue the action."
            if confirmed
            else "Do not execute the action yet; ask the operator again."
        ),
    }


@mcp.tool()
def guide_wait(session_id: str, reason: str, seconds: float = 2.0) -> dict:
    """Wait for the operator to complete a manual action (capped at 120s)."""
    s = _guidance_session(session_id)
    if not s:
        return _guidance_error(f"unknown session_id '{session_id}'")
    if not reason.strip():
        return _guidance_error("reason is required")
    seconds = max(0.0, min(seconds, GUIDE_MAX_WAIT_SECONDS))
    time.sleep(seconds)
    event = {
        "type": "wait",
        "reason": reason.strip(),
        "seconds": seconds,
        "created_at": _now_iso(),
    }
    s["events"].append(event)
    s["updated_at"] = _now_iso()
    return {
        "ok": True,
        "session_id": session_id,
        "waited_seconds": seconds,
        "reason": reason.strip(),
    }


@mcp.tool()
def guide_capture_response(
    session_id: str,
    question: str,
    response: str,
    expected_substring: str | None = None,
    match_mode: str = "substring",
) -> dict:
    """Collect a short operator response and optionally validate it."""
    s = _guidance_session(session_id)
    if not s:
        return _guidance_error(f"unknown session_id '{session_id}'")
    if not question.strip():
        return _guidance_error("question is required")
    if not response.strip():
        return _guidance_error("response is required")

    mode = match_mode.strip().lower()
    if mode not in {"substring", "word", "exact"}:
        return _guidance_error("match_mode must be one of: substring, word, exact")

    expected = (expected_substring or "").strip().lower()
    response_lc = response.strip().lower()
    if not expected:
        matches_expected = True
    elif mode == "exact":
        matches_expected = response_lc == expected
    elif mode == "word":
        matches_expected = expected in set(re.findall(r"\b\w+\b", response_lc))
    else:
        matches_expected = expected in response_lc
    event = {
        "type": "response",
        "question": question.strip(),
        "response": response.strip(),
        "expected_substring": expected_substring or "",
        "match_mode": mode,
        "matches_expected": matches_expected,
        "created_at": _now_iso(),
    }
    s["events"].append(event)
    s["updated_at"] = _now_iso()
    return {
        "ok": True,
        "session_id": session_id,
        "response": response.strip(),
        "matches_expected": matches_expected,
    }


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    _log(f"starting (transport={transport})")
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
