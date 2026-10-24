"""Screen capture and Windows game-window helpers."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np

from .config import CaptureConfig

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:  # pragma: no cover - this project targets Windows
    _user32 = None
    _EnumWindowsProc = None


def enable_dpi_awareness() -> None:
    """Make screen coordinates match physical pixels on scaled displays."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str
    x: int
    y: int
    width: int
    height: int

    @property
    def region(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


@dataclass
class Frame:
    image: np.ndarray
    window: GameWindow | None
    region: tuple[int, int, int, int]
    timestamp: float


def _client_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    assert _user32 is not None
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0, 0, 0
    point = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(point)):
        return 0, 0, 0, 0
    return point.x, point.y, int(rect.right), int(rect.bottom)


def _window_title(hwnd: int) -> str:
    assert _user32 is not None
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def find_game_window(title_contains: str) -> GameWindow | None:
    """Find the largest visible top-level window whose title contains the text."""
    if not IS_WINDOWS or _user32 is None:
        return None
    needle = title_contains.lower()
    matches: list[GameWindow] = []

    @_EnumWindowsProc
    def callback(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if needle not in title.lower():
            return True
        x, y, w, h = _client_screen_rect(hwnd)
        if w >= 320 and h >= 240:
            matches.append(GameWindow(hwnd=int(hwnd), title=title, x=x, y=y, width=w, height=h))
        return True

    _user32.EnumWindows(callback, 0)
    if not matches:
        return None
    return max(matches, key=lambda item: item.width * item.height)


def is_foreground(hwnd: int | None) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    assert _user32 is not None
    return int(_user32.GetForegroundWindow()) == int(hwnd)


def foreground_title() -> str:
    if not IS_WINDOWS or _user32 is None:
        return ""
    return _window_title(int(_user32.GetForegroundWindow()))


class ScreenCapture:
    """Grabs BGR frames from the Diablo IV client (or a monitor fallback)."""

    def __init__(self, config: CaptureConfig):
        enable_dpi_awareness()
        self.config = config
        # mss.mss() is deprecated in newer mss releases but still works.
        factory = getattr(mss, "MSS", None) or mss.mss
        self._sct = factory()
        self._fallback_warned = False
        self.last_region: tuple[int, int, int, int] = (0, 0, 0, 0)

    def grab(self) -> Frame:
        window = find_game_window(self.config.window_title) if self.config.mode == "window" else None
        if window is not None:
            region = window.region
        else:
            monitors = self._sct.monitors
            index = self.config.monitor
            if index >= len(monitors):
                index = 1 if len(monitors) > 1 else 0
            monitor = monitors[index]
            region = (monitor["left"], monitor["top"], monitor["width"], monitor["height"])
            if self.config.mode == "window" and not self._fallback_warned:
                log.warning(
                    "Game window %r not found; capturing monitor %d instead",
                    self.config.window_title,
                    index,
                )
                self._fallback_warned = True

        self.last_region = region
        # mss 4-tuples are (left, top, RIGHT, BOTTOM); passing width/height here
        # silently crops the frame. Use an explicit monitor dict instead.
        monitor = {
            "left": region[0],
            "top": region[1],
            "width": region[2],
            "height": region[3],
        }
        shot = self._sct.grab(monitor)
        image = np.asarray(shot, dtype=np.uint8)[:, :, :3].copy()
        return Frame(image=image, window=window, region=region, timestamp=time.perf_counter())

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


class FramePacer:
    """Simple fixed-rate loop timer that does not build up a backlog."""

    def __init__(self, fps: float):
        self.period = 1.0 / max(1.0, fps)
        self._next = time.perf_counter()

    def wait(self) -> None:
        now = time.perf_counter()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = max(self._next + self.period, time.perf_counter())
