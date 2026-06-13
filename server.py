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
import sys

from mcp.server.fastmcp import FastMCP, Image

import backends

DRY_RUN = os.environ.get("MCP_DESKTOP_DRY_RUN", "0") not in ("0", "", "false", "False")
MAX_DIM = int(os.environ.get("MCP_DESKTOP_MAX_DIM", "1280"))

mcp = FastMCP("desktop-control")
_backend: backends.Backend | None = None


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


def _main() -> None:
    transport = os.environ.get("MCP_DESKTOP_TRANSPORT", "stdio").lower()
    if "--sse" in sys.argv:
        transport = "sse"
    _log(f"starting (transport={transport})")
    mcp.run(transport="sse") if transport == "sse" else mcp.run()


if __name__ == "__main__":
    _main()
