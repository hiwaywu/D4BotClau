"""Input execution with foreground and emergency-stop gates."""

from __future__ import annotations

import ctypes
import logging
import random
import sys
import time

from .brain import Action, Decision
from .capture import GameWindow, is_foreground
from .config import InputConfig

log = logging.getLogger(__name__)

try:
    import pydirectinput
except Exception as exc:  # pragma: no cover - import error is environment-specific
    pydirectinput = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None

IS_WINDOWS = sys.platform == "win32"


def _vk_for_key(key: str) -> int | None:
    key = key.lower()
    if len(key) >= 2 and key[0] == "f" and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x6F + number
    return None


def emergency_stop_pressed(key: str = "f8") -> bool:
    if not IS_WINDOWS:
        return False
    vk = _vk_for_key(key)
    if vk is None:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class InputController:
    def __init__(self, config: InputConfig, enabled: bool):
        self.config = config
        self.enabled = enabled
        self.stopped = False
        self._jitter = random.Random()
        if pydirectinput is not None:
            try:
                pydirectinput.PAUSE = 0.0
            except Exception:
                pass
        if enabled and pydirectinput is None:
            raise RuntimeError(
                "pydirectinput is not installed; run .\\setup.cmd or pip install -r requirements.txt"
            ) from _IMPORT_ERROR

    def execute(
        self,
        decision: Decision,
        window: GameWindow | None,
        region: tuple[int, int, int, int],
    ) -> int:
        """Execute actions. Returns the number of actions actually sent."""
        if not self.enabled or self.stopped or not decision.actions:
            return 0
        sent = 0
        for action in decision.actions:
            if emergency_stop_pressed(self.config.emergency_stop_key):
                self.stopped = True
                log.warning("Emergency stop pressed; input disabled.")
                break
            if self.config.require_foreground and not is_foreground(window.hwnd if window else None):
                return sent
            try:
                if action.kind == "aim" and action.x is not None and action.y is not None:
                    self._aim(region, action.x, action.y)
                    sent += 1
                elif action.kind == "click" and action.x is not None and action.y is not None:
                    self._click(region, action.x, action.y)
                    sent += 1
                elif action.kind == "rclick" and action.x is not None and action.y is not None:
                    self._click(region, action.x, action.y, button="right")
                    sent += 1
                elif action.kind == "press" and action.key:
                    self._press(action)
                    sent += 1
            except Exception:
                log.exception("Input action failed: %s", action)
                return sent
            if self.config.action_gap_s > 0:
                time.sleep(self.config.action_gap_s)
        return sent

    def _aim(self, region: tuple[int, int, int, int], x: int, y: int) -> None:
        assert pydirectinput is not None
        jitter = self.config.aim_jitter_px
        sx = region[0] + x + (self._jitter.randint(-jitter, jitter) if jitter else 0)
        sy = region[1] + y + (self._jitter.randint(-jitter, jitter) if jitter else 0)
        pydirectinput.moveTo(int(sx), int(sy), duration=0.02)

    def _click(self, region: tuple[int, int, int, int], x: int, y: int, button: str = "left") -> None:
        assert pydirectinput is not None
        pydirectinput.click(region[0] + int(x), region[1] + int(y), button=button)

    def _press(self, action: Action) -> None:
        assert pydirectinput is not None and action.key is not None
        key = action.key.lower()
        if key in {"mouse_left", "lmb", "left_click"}:
            pydirectinput.click(button="left")
        elif key in {"mouse_right", "rmb", "right_click"}:
            pydirectinput.click(button="right")
        else:
            pydirectinput.press(action.key)

    def stop(self) -> None:
        self.stopped = True
        if pydirectinput is not None:
            try:
                pydirectinput.mouseUp(button="left")
                pydirectinput.mouseUp(button="right")
            except Exception:
                pass
