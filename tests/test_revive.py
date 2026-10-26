"""Death screen -> Revive at Checkpoint, against a fake game."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from d4bot.revive import DeathScreen, ReviveRunner

ROOT = Path(__file__).resolve().parents[1]
DEATH = ROOT / "data" / "death_20261026_140305.png"


def _death_frame() -> np.ndarray:
    banner = cv2.imread(str(ROOT / "templates" / "death_banner.png"))
    frame = np.full((1440, 2560, 3), (40, 45, 55), dtype=np.uint8)
    frame[225 : 225 + banner.shape[0], 1010 : 1010 + banner.shape[1]] = banner
    return frame


class FakeGame:
    """Death screen; the button only works after `greyed` clicks; then loading, then the HUD."""

    def __init__(self, greyed: int = 2):
        self.greyed = greyed
        self.clicks: list[tuple[int, int]] = []
        self.loading = -1
        self.alive = False

    def grab(self) -> np.ndarray:
        if self.alive:
            return np.full((1440, 2560, 3), (70, 90, 110), dtype=np.uint8)
        if self.loading >= 0:
            self.loading += 1
            if self.loading > 6:
                self.alive = True
            return np.zeros((1440, 2560, 3), dtype=np.uint8)
        return _death_frame()

    def send(self, actions) -> None:  # type: ignore[no-untyped-def]
        for a in actions:
            if a.kind == "click":
                self.clicks.append((a.x, a.y))
                if abs(a.x - 1280) < 140 and abs(a.y - 1273) < 22 and len(self.clicks) > self.greyed:
                    self.loading = 0


def _run(game: FakeGame, stopped=lambda: False):  # type: ignore[no-untyped-def]
    return ReviveRunner(
        DeathScreen(ROOT), hud_visible=lambda f: game.alive, grab=game.grab, send=game.send,
        stopped=stopped, sleep=lambda s: None,
    ).run()


def test_clicks_revive_until_back_in_game() -> None:
    game = FakeGame(greyed=2)
    result = _run(game)
    assert result.ok, result.reason
    assert result.clicks == 3                     # two on the greyed button, then it works
    assert all(abs(x - 1280) < 5 and abs(y - 1273) < 5 for x, y in game.clicks)


def test_no_death_screen_does_nothing() -> None:
    game = FakeGame()
    game.alive = True
    result = _run(game)
    assert not result.ok and game.clicks == []


def test_stops_on_f8() -> None:
    game = FakeGame(greyed=100)
    assert _run(game, stopped=lambda: True).reason == "stopped"


def test_banner_matches_only_the_death_screen() -> None:
    screen = DeathScreen(ROOT)
    assert screen.seen(_death_frame())
    assert not screen.seen(np.full((1440, 2560, 3), (70, 90, 110), dtype=np.uint8))
    if DEATH.is_file():
        assert screen.seen(cv2.imread(str(DEATH)))
