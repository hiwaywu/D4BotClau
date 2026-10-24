"""Town routine against a fake game built from the user's real screenshots.

Needs data/town/snap_*.png and one Helltide frame (gitignored captures);
skipped when they are not present.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from d4bot.town import (
    INV_TABS,
    INV_XS,
    INV_YS,
    STASH_TABS,
    TownConfig,
    TownRunner,
    TownVision,
    active_tab,
    occupied_cells,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPS = ROOT / "data" / "town"
HELLTIDE = sorted((ROOT / "data").glob("soak14/raw_*.jpg"))

pytestmark = pytest.mark.skipif(
    not (SNAPS / "snap_04.png").is_file() or not HELLTIDE, reason="town screenshots not available"
)


def _load(name: str) -> np.ndarray:
    image = cv2.imread(str(SNAPS / f"{name}.png"))
    assert image is not None
    return image


def _box(cx: int, cy: int, r: int = 28) -> tuple[slice, slice]:
    return slice(cy - r, cy + r), slice(cx - r, cx + r)


class FakeGame:
    """Reacts to the bot's actions like the game would, and renders frames."""

    def __init__(self, items: dict[int, int], stash_capacity: list[int], unmovable: int = 0):
        self.where = "helltide"
        self.stash_open = False
        self.inv_tab = 0
        self.stash_tab = 0
        self.inventory = {t: [True] * n + [False] * (33 - n) for t, n in items.items()}
        self.unmovable = unmovable          # first N items of tab 0 cannot be stashed
        self.capacity = list(stash_capacity)
        self.portal_ticks = -1
        self.t_presses = 0
        self.used_while_closed = 0          # right-clicks with the stash closed (would equip!)
        self.close_after_clicks: int | None = None
        self.rclicks = 0
        self.helltide = cv2.imread(str(HELLTIDE[0]))
        self.landing = _load("snap_01")
        self.near_chest = _load("snap_03")
        base = _load("snap_04")
        self.open_base = base
        empty_src, item_src = _load("snap_06"), _load("snap_06")
        self.empty_cell = empty_src[INV_YS[1] + 3 : INV_YS[2] - 3, INV_XS[0] + 3 : INV_XS[1] - 3].copy()
        self.item_cell = item_src[INV_YS[0] + 3 : INV_YS[1] - 3, INV_XS[0] + 3 : INV_XS[1] - 3].copy()
        self.inv_on = base[_box(*INV_TABS[0])].copy()
        self.inv_off = base[_box(*INV_TABS[1])].copy()
        self.stash_on = base[_box(*STASH_TABS[0])].copy()
        self.stash_off = base[_box(*STASH_TABS[1])].copy()

    # ---- the bot's view
    def grab(self) -> np.ndarray:
        if self.portal_ticks >= 0:
            self.portal_ticks += 1
            if self.portal_ticks <= 2:
                return self.helltide.copy() if self.where == "helltide" else self.landing.copy()
            if self.portal_ticks <= 5:
                return np.zeros_like(self.landing)              # loading screen
            self.where, self.portal_ticks = "landing", -1
        if self.where == "helltide":
            return self.helltide.copy()
        if not self.stash_open:
            return (self.landing if self.where == "landing" else self.near_chest).copy()
        frame = self.open_base.copy()
        for i, xy in enumerate(INV_TABS):
            frame[_box(*xy)] = self.inv_on if i == self.inv_tab else self.inv_off
        for i, xy in enumerate(STASH_TABS):
            frame[_box(*xy)] = self.stash_on if i == self.stash_tab else self.stash_off
        cells = self.inventory.get(self.inv_tab, [False] * 33)
        for k, full in enumerate(cells):
            r, c = divmod(k, 11)
            y0, y1, x0, x1 = INV_YS[r] + 3, INV_YS[r + 1] - 3, INV_XS[c] + 3, INV_XS[c + 1] - 3
            patch = self.item_cell if full else self.empty_cell
            frame[y0:y1, x0:x1] = cv2.resize(patch, (x1 - x0, y1 - y0))
        return frame

    # ---- the game's reaction to input
    def send(self, actions) -> None:  # type: ignore[no-untyped-def]
        for a in actions:
            if a.kind == "press" and a.key == "t":
                self.portal_ticks = 0                              # works in town too
                self.stash_open = False
                self.t_presses += 1
            elif a.kind == "press" and a.key == "esc":
                self.stash_open = False
            elif a.kind == "click" and self.where == "landing" and not self.stash_open:
                self.where = "near_chest"          # walking toward the stash
            elif a.kind == "click" and self.where == "near_chest" and not self.stash_open:
                if abs(a.x - 1392) < 120 and abs(a.y - 495) < 120:
                    self.stash_open = True
            elif a.kind == "click" and self.stash_open:
                for i, (x, y) in enumerate(INV_TABS):
                    if abs(a.x - x) < 25 and abs(a.y - y) < 25:
                        self.inv_tab = i
                for i, (x, y) in enumerate(STASH_TABS):
                    if abs(a.x - x) < 30 and abs(a.y - y) < 30:
                        self.stash_tab = i
            elif a.kind == "rclick":
                self.rclicks += 1
                if not self.stash_open:
                    self.used_while_closed += 1
                    continue
                if self.close_after_clicks is not None and self.rclicks >= self.close_after_clicks:
                    self.stash_open = False
                self._stash_cell(a.x, a.y)

    def _stash_cell(self, x: int, y: int) -> None:
        cells = self.inventory.get(self.inv_tab)
        if cells is None:
            return
        for k in range(33):
            r, c = divmod(k, 11)
            if INV_XS[c] < x < INV_XS[c + 1] and INV_YS[r] < y < INV_YS[r + 1] and cells[k]:
                if self.inv_tab == 0 and k < self.unmovable:
                    return
                if self.capacity[self.stash_tab] > 0:
                    self.capacity[self.stash_tab] -= 1
                    cells[k] = False


def _runner(game: FakeGame) -> TownRunner:
    vision = TownVision(ROOT, TownConfig(town="temis"))
    return TownRunner(
        vision,
        TownConfig(town="temis", settle_s=0.0, click_gap_s=0.0, after_batch_s=0.0),
        grab=game.grab,
        send=game.send,
        stopped=lambda: False,
        sleep=lambda s: None,
    )


def test_occupancy_and_tabs_on_real_screenshots() -> None:
    assert len(occupied_cells(_load("snap_04"))) == 3       # 3 equipment items
    assert len(occupied_cells(_load("snap_06"))) == 11      # one full row of socketables
    assert active_tab(_load("snap_07"), INV_TABS) == 3
    assert active_tab(_load("snap_24"), STASH_TABS) == 6
    vision = TownVision(ROOT, TownConfig(town="temis"))
    assert vision.in_temis(_load("snap_01")) and not vision.in_temis(cv2.imread(str(HELLTIDE[0])))
    assert vision.stash_open(_load("snap_04")) and not vision.stash_open(_load("snap_03"))


def test_full_town_run_empties_every_tab() -> None:
    game = FakeGame(items={0: 3, 1: 5, 2: 11, 3: 19, 4: 27}, stash_capacity=[100] * 7)
    result = _runner(game).run()
    assert result.ok, result.reason
    assert result.moved == {"equipment": 3, "talismans": 5, "socketables": 11, "consumables": 19, "keys": 27}
    assert not any(any(cells) for cells in game.inventory.values())
    assert game.used_while_closed == 0
    assert not game.stash_open                               # closed with Esc at the end


def test_full_stash_tab_moves_on_to_the_next_one() -> None:
    game = FakeGame(items={0: 3, 2: 11}, stash_capacity=[5, 0, 100, 100, 100, 100, 100])
    result = _runner(game).run()
    assert result.ok, result.reason
    assert result.moved["equipment"] == 3 and result.moved["socketables"] == 11
    assert result.stash_tab == 2                             # tab 0 filled, tab 1 full, tab 2 used


def test_unmovable_items_are_left_without_looping_forever() -> None:
    game = FakeGame(items={0: 4}, stash_capacity=[100] * 7, unmovable=2)
    result = _runner(game).run()
    assert result.ok, result.reason
    assert result.moved["equipment"] == 2 and result.left["equipment"] == 2


def test_never_right_clicks_with_the_stash_closed() -> None:
    game = FakeGame(items={0: 3, 3: 19}, stash_capacity=[100] * 7)
    game.close_after_clicks = 5                              # stash closes mid-run (e.g. got hit)
    result = _runner(game).run()
    assert not result.ok and "stash closed" in result.reason
    assert game.used_while_closed <= 1                       # only the click that closed it, none after


def test_always_presses_t_even_when_already_in_town() -> None:
    game = FakeGame(items={0: 2}, stash_capacity=[100] * 7)
    game.where = "landing"                                     # already in Temis
    result = _runner(game).run()
    assert result.ok, result.reason
    assert game.t_presses == 1


def test_lookalike_zone_name_without_loading_screen_does_not_count() -> None:
    game = FakeGame(items={0: 2}, stash_capacity=[100] * 7)
    game.send = lambda actions: None                           # T does nothing (e.g. portal blocked)
    result = _runner(game).run()
    assert not result.ok and "town portal" in result.reason


RUN2 = ROOT / "data" / "town_runs" / "20261023_212735"


@pytest.mark.skipif(not (RUN2 / "03_walk_640.jpg").is_file(), reason="live run 2 frames not available")
def test_finds_stash_by_hover_label_when_lighting_changed() -> None:
    """Live run 2: new lighting, chest template 0.61. Only the hover label works."""

    class NewLighting(FakeGame):
        cursor = (0, 0)

        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            super().__init__(*args, **kwargs)
            self.landing = cv2.imread(str(RUN2 / "02_in_temis.jpg"))
            self.near_chest = cv2.imread(str(RUN2 / "03_walk_640.jpg"))
            self.tooltip = _load("snap_03")[300:380, 1240:1450].copy()

        def grab(self):  # type: ignore[no-untyped-def]
            frame = super().grab()
            near = self.where == "near_chest" and not self.stash_open and self.portal_ticks < 0
            if near and abs(self.cursor[0] - 2183) < 120 and abs(self.cursor[1] - 800) < 70:
                frame[560:640, 2080:2290] = self.tooltip
            return frame

        def send(self, actions):  # type: ignore[no-untyped-def]
            for a in actions:
                if a.kind == "aim":
                    self.cursor = (a.x, a.y)
            plain = [a for a in actions if a.kind != "click" or self.where != "near_chest" or self.stash_open]
            super().send(plain)
            for a in actions:
                if a.kind == "click" and self.where == "near_chest" and not self.stash_open:
                    if abs(a.x - 2183) < 120 and abs(a.y - 800) < 70:
                        self.stash_open = True

    game = NewLighting(items={0: 3, 3: 5}, stash_capacity=[100] * 7)
    result = _runner(game).run()
    assert result.ok, result.reason
    assert result.moved["equipment"] == 3 and result.moved["consumables"] == 5


ICHORFALL = ROOT / "data" / "ichorfall"


@pytest.mark.skipif(not (ICHORFALL / "snap_01.png").is_file(), reason="Ichorfall screenshots not available")
def test_ichorfall_chest_is_clicked_straight_from_the_landing_spot() -> None:
    from d4bot.town import TownConfig as Cfg

    class Ichorfall(FakeGame):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            super().__init__(*args, **kwargs)
            self.landing = cv2.imread(str(ICHORFALL / "snap_01.png"))
            self.walk_clicks = 0

        def send(self, actions):  # type: ignore[no-untyped-def]
            for a in actions:
                if a.kind == "click" and self.where == "landing" and not self.stash_open:
                    if abs(a.x - 930) < 100 and abs(a.y - 555) < 90:
                        self.stash_open = True
                    else:
                        self.walk_clicks += 1
                    continue
                super().send([a])

    game = Ichorfall(items={0: 3, 2: 4}, stash_capacity=[100] * 7)
    vision = TownVision(ROOT, Cfg())
    runner = TownRunner(
        vision, Cfg(settle_s=0.0, click_gap_s=0.0, after_batch_s=0.0),
        grab=game.grab, send=game.send, stopped=lambda: False, sleep=lambda s: None,
    )
    result = runner.run()
    assert result.ok, result.reason
    assert game.walk_clicks == 0                             # clicked the chest, no walking
    assert result.moved["equipment"] == 3 and result.moved["socketables"] == 4
