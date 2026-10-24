"""Warplan teleport against a fake game built from the user's map screenshots."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from d4bot.warplan import WarplanConfig, WarplanRunner, WarplanVision

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data" / "map"
TOWN = ROOT / "data" / "ichorfall" / "snap_01.png"

pytestmark = pytest.mark.skipif(
    not (MAP / "snap_10.png").is_file() or not TOWN.is_file(), reason="map screenshots not available"
)


class FakeGame:
    def __init__(self, confirm: bool = True, destination: str = "helltide"):
        self.screens = {
            "town": cv2.imread(str(TOWN)),
            "map": cv2.imread(str(MAP / "snap_06.png")),
            "confirm": cv2.imread(str(MAP / "snap_08.png")),
            "helltide": cv2.imread(str(MAP / "snap_10.png")),
            "elsewhere": cv2.imread(str(MAP / "snap_09.png")),
        }
        self.screen = "town"
        self.confirm = confirm
        self.destination = destination
        self.loading_ticks = -1
        self.clicks: list[tuple[int, int]] = []

    def grab(self) -> np.ndarray:
        if self.loading_ticks >= 0:
            self.loading_ticks += 1
            if self.loading_ticks <= 4:
                return np.zeros_like(self.screens["town"])
            self.loading_ticks = -1
            self.screen = self.destination
        return self.screens[self.screen].copy()

    def send(self, actions) -> None:  # type: ignore[no-untyped-def]
        for a in actions:
            if a.kind == "press" and a.key == "tab" and self.screen == "town":
                self.screen = "map"
            elif a.kind == "click":
                self.clicks.append((a.x, a.y))
                if self.screen == "map" and abs(a.x - 351) < 80 and abs(a.y - 1225) < 25:
                    if self.confirm:
                        self.screen = "confirm"
                    else:
                        self.loading_ticks = 0
                elif self.screen == "confirm" and abs(a.x - 1180) < 70 and abs(a.y - 870) < 25:
                    self.loading_ticks = 0


def _run(game: FakeGame):  # type: ignore[no-untyped-def]
    vision = WarplanVision(ROOT, WarplanConfig())
    return WarplanRunner(vision, WarplanConfig(), grab=game.grab, send=game.send, stopped=lambda: False, sleep=lambda s: None).run()


def test_teleports_with_confirmation_and_lands_in_helltide() -> None:
    game = FakeGame(confirm=True)
    result = _run(game)
    assert result.ok, result.reason
    assert len(game.clicks) == 2                  # Teleport, then Accept


def test_teleports_without_confirmation() -> None:
    game = FakeGame(confirm=False)
    assert _run(game).ok


def test_reports_when_warplan_goes_somewhere_else() -> None:
    game = FakeGame(destination="elsewhere")
    result = _run(game)
    assert not result.ok and "not in a Helltide" in result.reason


def test_already_in_helltide_does_nothing() -> None:
    game = FakeGame()
    game.screen = "helltide"
    assert _run(game).ok and game.clicks == []


LOADING = ROOT / "data" / "town_runs" / "20261023_235405" / "w1_no_button.jpg"


@pytest.mark.skipif(not LOADING.is_file(), reason="loading-screen capture not available")
def test_loading_art_counts_as_loading() -> None:
    from d4bot.warplan import is_loading

    assert is_loading(cv2.imread(str(LOADING)))
    assert not is_loading(cv2.imread(str(MAP / "snap_06.png")))
    assert not is_loading(cv2.imread(str(MAP / "snap_10.png")))
    assert not is_loading(cv2.imread(str(TOWN)))


@pytest.mark.skipif(not (MAP / "landing_kyovashad.jpg").is_file(), reason="Kyovashad landing capture not available")
def test_landing_in_the_helltide_waypoint_town_counts_as_arrived() -> None:
    """Live: the teleport lands in the town next to the Helltide (no timer shown yet)."""
    game = FakeGame(destination="kyovashad")
    game.screens["kyovashad"] = cv2.imread(str(MAP / "landing_kyovashad.jpg"))
    result = _run(game)
    assert result.ok, result.reason
