"""
Platform backends for the desktop-control MCP server.

A backend performs raw OS actions in **real screen pixels**. All MCP concerns
(coordinate scaling, dry-run, image encoding, transport) stay in server.py, so
the backends are thin and interchangeable.

Backends:
  - WindowsBackend : pyautogui + (optional) pywinauto UI Automation.
  - MacOSBackend   : pyautogui (accessibility tools unavailable for now).
  - LinuxBackend   : X11 via pyautogui (+mss); Wayland via grim/gnome-screenshot
                     for capture and ydotool for input (best-effort);
                     (optional) AT-SPI via pyatspi for accessibility.

Imports that need a live display/session are done lazily so this module can be
imported on a headless host.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile


def _which(name: str) -> str | None:
    return shutil.which(name)


class Backend:
    """Abstract backend; methods work in REAL screen pixels."""

    name = "base"
    accessibility = "none"

    # --- perception
    def screen_size(self) -> tuple[int, int]:
        raise NotImplementedError

    def grab(self):  # -> PIL.Image.Image
        raise NotImplementedError

    # --- mouse
    def move(self, x: int, y: int) -> None:
        raise NotImplementedError

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        raise NotImplementedError

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left",
             duration: float = 0.3) -> None:
        raise NotImplementedError

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        raise NotImplementedError

    # --- keyboard
    def type_text(self, text: str, interval: float = 0.02) -> None:
        raise NotImplementedError

    def press(self, key: str) -> None:
        raise NotImplementedError

    def hotkey(self, keys: list[str]) -> None:
        raise NotImplementedError

    # --- accessibility (optional)
    def a11y_available(self) -> bool:
        return False

    def a11y_tree(self, window: str | None, max_chars: int) -> str:
        return "error: no accessibility backend on this host"

    def a11y_click(self, name: str, window: str | None, control_type: str | None) -> str:
        return "error: no accessibility backend on this host"


# ===========================================================================
# Windows
# ===========================================================================
class WindowsBackend(Backend):
    name = "windows"

    def __init__(self) -> None:
        # DPI awareness BEFORE any size query, else clicks are offset on HiDPI.
        try:
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = float(os.environ.get("MCP_DESKTOP_PAUSE", "0.05"))
        self._pg = pyautogui

        try:
            from pywinauto import Desktop

            self._uia = Desktop(backend="uia")
            self.accessibility = "uia"
        except Exception:
            self._uia = None

    def screen_size(self):
        s = self._pg.size()
        return int(s.width), int(s.height)

    def grab(self):
        return self._pg.screenshot()

    def move(self, x, y):
        self._pg.moveTo(x, y)

    def click(self, x, y, button="left", clicks=1):
        self._pg.click(x, y, clicks=clicks, button=button)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.3):
        self._pg.moveTo(x1, y1)
        self._pg.dragTo(x2, y2, duration=duration, button=button)

    def scroll(self, amount, x=None, y=None):
        if x is not None and y is not None:
            self._pg.scroll(amount, x=x, y=y)
        else:
            self._pg.scroll(amount)

    def type_text(self, text, interval=0.02):
        self._pg.write(text, interval=interval)

    def press(self, key):
        self._pg.press(key)

    def hotkey(self, keys):
        self._pg.hotkey(*keys)

    # --- UI Automation
    def a11y_available(self):
        return self._uia is not None

    def a11y_tree(self, window, max_chars):
        if not self._uia:
            return "error: pywinauto/UIA not available"
        try:
            lines: list[str] = []
            if window:
                root = self._uia.window(title_re=f".*{window}.*")

                def walk(ctrl, depth=0):
                    if len("\n".join(lines)) > max_chars:
                        return
                    try:
                        info = ctrl.element_info
                        lines.append(f"{'  ' * depth}- {info.control_type!r} name={info.name!r}")
                    except Exception:
                        return
                    for child in ctrl.children():
                        walk(child, depth + 1)

                walk(root)
            else:
                for w in self._uia.windows():
                    try:
                        lines.append(f"# window: {w.window_text()!r}")
                    except Exception:
                        pass
            out = "\n".join(lines)
            return out[:max_chars] + ("\n…(truncated)" if len(out) > max_chars else "")
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    def a11y_click(self, name, window, control_type):
        if not self._uia:
            return "error: pywinauto/UIA not available"
        try:
            scope = self._uia.window(title_re=f".*{window}.*") if window else self._uia
            kwargs = {"title_re": f".*{name}.*"}
            if control_type:
                kwargs["control_type"] = control_type
            ctrl = scope.child_window(**kwargs)
            cx, cy = ctrl.rectangle().mid_point()
            ctrl.click_input()
            return f"ui_click '{name}' at ({cx},{cy})"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"


# ===========================================================================
# Linux (X11 + Wayland)
# ===========================================================================
class LinuxBackend(Backend):
    name = "linux"

    def __init__(self) -> None:
        self.wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )
        self.session = "wayland" if self.wayland else "x11"
        self._pg = None  # lazy: pyautogui (X11 input/screenshot)
        self._mss = None  # lazy: mss (X11 screenshot)

        # Accessibility via AT-SPI (optional, both X11 and Wayland).
        try:
            import pyatspi  # type: ignore

            self._atspi = pyatspi
            self.accessibility = "atspi"
        except Exception:
            self._atspi = None

        if self.wayland and not _which("ydotool"):
            # Not fatal: capture may still work; input will report clearly.
            pass

    # --- lazy loaders
    def _pyautogui(self):
        if self._pg is None:
            import pyautogui

            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = float(os.environ.get("MCP_DESKTOP_PAUSE", "0.05"))
            self._pg = pyautogui
        return self._pg

    def _run(self, argv: list[str]) -> None:
        subprocess.run(argv, check=True, capture_output=True)

    # --- perception
    def screen_size(self):
        # X11: pyautogui knows it. Wayland: derive from a capture.
        if not self.wayland:
            s = self._pyautogui().size()
            return int(s.width), int(s.height)
        img = self.grab()
        return img.width, img.height

    def grab(self):
        from PIL import Image as PILImage

        # X11: mss (no scrot dependency), then pyautogui as fallback.
        if not self.wayland:
            try:
                if self._mss is None:
                    import mss

                    self._mss = mss.mss()
                shot = self._mss.grab(self._mss.monitors[1])
                return PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            except Exception:
                return self._pyautogui().screenshot()

        # Wayland: external screenshot tools to a temp PNG.
        tmp = os.path.join(tempfile.gettempdir(), "mcp_desktop_shot.png")
        for argv in (
            ["grim", tmp],                       # wlroots / Sway / Hyprland
            ["gnome-screenshot", "-f", tmp],     # GNOME
            ["spectacle", "-b", "-n", "-o", tmp],  # KDE
        ):
            if _which(argv[0]):
                try:
                    self._run(argv)
                    return PILImage.open(tmp).convert("RGB")
                except Exception:
                    continue
        raise RuntimeError(
            "Wayland: no working screenshot tool (install grim, gnome-screenshot "
            "or spectacle)"
        )

    # --- ydotool helpers (Wayland input)
    _YDO_BUTTON = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
    # Minimal name -> Linux input keycode map for common keys (Wayland/ydotool).
    _YDO_KEY = {
        "enter": 28, "return": 28, "esc": 1, "escape": 1, "tab": 15,
        "space": 57, "backspace": 14, "delete": 111, "up": 103, "down": 108,
        "left": 105, "right": 106, "home": 102, "end": 107, "pageup": 104,
        "pagedown": 109, "ctrl": 29, "alt": 56, "shift": 42, "win": 125,
        "super": 125, "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63,
        "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
    }

    def _ydo(self, *args: str) -> None:
        if not _which("ydotool"):
            raise RuntimeError(
                "Wayland input needs ydotool (and a running ydotoold + uinput "
                "access). Prefer an X11 session or use the AT-SPI tools."
            )
        self._run(["ydotool", *args])

    # --- mouse
    def move(self, x, y):
        if self.wayland:
            self._ydo("mousemove", "--absolute", "-x", str(x), "-y", str(y))
        else:
            self._pyautogui().moveTo(x, y)

    def click(self, x, y, button="left", clicks=1):
        if self.wayland:
            self.move(x, y)
            for _ in range(clicks):
                self._ydo("click", self._YDO_BUTTON.get(button, "0xC0"))
        else:
            self._pyautogui().click(x, y, clicks=clicks, button=button)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.3):
        if self.wayland:
            # ydotool has no smooth drag; emulate with down/move/up via click is
            # not reliable, so report rather than do something wrong.
            raise RuntimeError("drag is not supported on Wayland (use X11)")
        pg = self._pyautogui()
        pg.moveTo(x1, y1)
        pg.dragTo(x2, y2, duration=duration, button=button)

    def scroll(self, amount, x=None, y=None):
        if self.wayland:
            raise RuntimeError("scroll is not supported on Wayland via ydotool (use X11)")
        pg = self._pyautogui()
        if x is not None and y is not None:
            pg.scroll(amount, x=x, y=y)
        else:
            pg.scroll(amount)

    # --- keyboard
    def type_text(self, text, interval=0.02):
        if self.wayland:
            self._ydo("type", text)
        else:
            self._pyautogui().write(text, interval=interval)

    def _ydo_key(self, key: str) -> None:
        code = self._YDO_KEY.get(key.lower())
        if code is None:
            raise RuntimeError(f"Wayland: key '{key}' not in the ydotool keymap")
        self._ydo("key", f"{code}:1", f"{code}:0")

    def press(self, key):
        if self.wayland:
            self._ydo_key(key)
        else:
            self._pyautogui().press(key)

    def hotkey(self, keys):
        if self.wayland:
            codes = [self._YDO_KEY.get(k.lower()) for k in keys]
            if any(c is None for c in codes):
                raise RuntimeError(f"Wayland: unknown key in {keys}")
            seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
            self._ydo("key", *seq)
        else:
            self._pyautogui().hotkey(*keys)

    # --- AT-SPI accessibility
    def a11y_available(self):
        return self._atspi is not None

    def a11y_tree(self, window, max_chars):
        if not self._atspi:
            return "error: AT-SPI/pyatspi not available (install python3-pyatspi)"
        try:
            lines: list[str] = []
            desktop = self._atspi.Registry.getDesktop(0)
            for app in desktop:
                if not app:
                    continue
                if window and window.lower() not in (app.name or "").lower():
                    continue
                lines.append(f"# app: {app.name!r}")

                def walk(node, depth=1):
                    if len("\n".join(lines)) > max_chars:
                        return
                    try:
                        role = node.getRoleName()
                        nm = node.name
                        lines.append(f"{'  ' * depth}- {role} name={nm!r}")
                        for i in range(node.childCount):
                            walk(node.getChildAtIndex(i), depth + 1)
                    except Exception:
                        return

                walk(app)
            out = "\n".join(lines)
            return out[:max_chars] + ("\n…(truncated)" if len(out) > max_chars else "")
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    def a11y_click(self, name, window, control_type):
        if not self._atspi:
            return "error: AT-SPI/pyatspi not available"
        try:
            pyatspi = self._atspi
            desktop = pyatspi.Registry.getDesktop(0)
            target = None
            for app in desktop:
                if not app:
                    continue
                if window and window.lower() not in (app.name or "").lower():
                    continue

                def find(node):
                    try:
                        if name.lower() in (node.name or "").lower() and (
                            not control_type or control_type.lower() in node.getRoleName().lower()
                        ):
                            return node
                        for i in range(node.childCount):
                            r = find(node.getChildAtIndex(i))
                            if r:
                                return r
                    except Exception:
                        return None
                    return None

                target = find(app)
                if target:
                    break
            if not target:
                return f"error: no accessible named ~'{name}'"
            comp = target.queryComponent()
            x, y, w, h = comp.getExtents(pyatspi.DESKTOP_COORDS)
            cx, cy = x + w // 2, y + h // 2
            self.click(cx, cy)
            return f"ui_click '{name}' at ({cx},{cy})"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"


class MacOSBackend(Backend):
    name = "macos"
    session = "macos"

    def __init__(self) -> None:
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = float(os.environ.get("MCP_DESKTOP_PAUSE", "0.05"))
        self._pg = pyautogui

    def screen_size(self):
        s = self._pg.size()
        return int(s.width), int(s.height)

    def grab(self):
        return self._pg.screenshot()

    def move(self, x, y):
        self._pg.moveTo(x, y)

    def click(self, x, y, button="left", clicks=1):
        self._pg.click(x, y, clicks=clicks, button=button)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.3):
        self._pg.moveTo(x1, y1)
        self._pg.dragTo(x2, y2, duration=duration, button=button)

    def scroll(self, amount, x=None, y=None):
        if x is not None and y is not None:
            self._pg.scroll(amount, x=x, y=y)
        else:
            self._pg.scroll(amount)

    def type_text(self, text, interval=0.02):
        self._pg.write(text, interval=interval)

    def press(self, key):
        self._pg.press(key)

    def hotkey(self, keys):
        self._pg.hotkey(*keys)

    def a11y_tree(self, window, max_chars):
        return "error: macOS accessibility backend not implemented yet"

    def a11y_click(self, name, window, control_type):
        return "error: macOS accessibility backend not implemented yet"


def get_backend() -> Backend:
    system = platform.system()
    if system == "Windows":
        return WindowsBackend()
    if system == "Linux":
        return LinuxBackend()
    if system == "Darwin":
        return MacOSBackend()
    raise RuntimeError(f"unsupported platform: {system}")
