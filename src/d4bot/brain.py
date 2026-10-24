"""Decision layer: combat assist and optional Helltide roam."""

from __future__ import annotations

import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import BotConfig, SkillConfig
from .minimap import MinimapReading, Pack
from .routes import RouteFollower
from .vision import Enemy, Observation

log = logging.getLogger(__name__)


def _wrap(angle: float) -> float:
    return (angle + math.pi) % math.tau - math.pi


@dataclass(frozen=True)
class Action:
    kind: str  # "press", "aim", "click"
    key: str | None = None
    x: int | None = None
    y: int | None = None


@dataclass
class Decision:
    actions: list[Action] = field(default_factory=list)
    state: str = "IDLE"
    note: str = ""


class CombatBrain:
    def __init__(self, config: BotConfig, rng: random.Random | None = None, live: bool = True):
        self.config = config
        # live=False (dry run): clicks are not sent, so the minimap motion is the
        # human player's, not ours. Do not learn rotation or call it stuck.
        self.live = live
        self._rng = rng or random.Random()
        self._last_press: dict[str, float] = {}
        self._last_potion = 0.0
        self._last_toggle = 0.0
        self._human_since: float | None = None
        self._was_demon = False
        self._toggle_state: str | None = None
        self._last_click = 0.0
        self._last_leg_start = 0.0
        self._last_click_target: tuple[int, int] | None = None
        self._heading = self._rng.uniform(0, math.tau)
        self._prev_gray: np.ndarray | None = None
        self._stuck_since: float | None = None
        self._lost_focus = False
        self._focus_back_at: float | None = None
        self._init_nav()
        self._init_buff_gate()
        self._init_pulse()
        self._init_hunt()

    def _init_nav(self) -> None:
        self._trail: deque[tuple[float, float, float]] = deque()
        self._blocked: deque[tuple[float, float]] = deque()
        self._clicks: deque[tuple[float, float]] = deque()
        self._moving_since: float | None = None
        self._rotation = math.radians(self.config.nav.rotation_deg)
        self._last_learn = 0.0
        self.stuck_count = 0

    def _init_hunt(self) -> None:
        self._hunt_goal: float | None = None
        self._hunt_last_near = -1e9
        self._hunt_target: Pack | None = None
        self._hunt_goal_pack: Pack | None = None
        self._hunt_goal_weight = 1.0
        self._hunt_fight_since: float | None = None
        self._follower = RouteFollower(
            lookahead=self.config.routes.lookahead, leash_px=self.config.routes.leash_px
        )
        self._follower_route: str | None = None
        self.route_mode = ""
        self._approach_since: float | None = None
        self._loot_since: float | None = None
        self._loot_goal = False
        self._loot_last_close = -1e9
        self._last_loot_click = 0.0
        self._ignored_loot: list[tuple[float, float, float]] = []
        self._ignored_screen: list[tuple[float, int, int]] = []  # screen-fixed UI text
        self.loot_clicks = 0
        self.loot_hold_until = 0.0
        self._ignored: list[tuple[float, float, float]] = []  # (expires at, world x, world y)
        self.packs_engaged = 0

    def _init_pulse(self) -> None:
        self._pulse_walk_since: float | None = None
        self._pulse_fight_since: float | None = None
        self._pulse_fight_until = 0.0
        self.kill_signals = 0

    def _init_buff_gate(self) -> None:
        self._pending_toggle: float | None = None
        self._failed_toggles = 0
        self._toggle_backoff_until = 0.0

    @property
    def heading(self) -> float:
        return self._heading

    @property
    def rotation(self) -> float:
        return self._rotation

    def reset(self) -> None:
        self._last_press.clear()
        self._last_potion = 0.0
        self._last_toggle = 0.0
        self._human_since = None
        self._was_demon = False
        self._toggle_state = None
        self._last_click = 0.0
        self._last_leg_start = 0.0
        self._last_click_target = None
        self._prev_gray = None
        self._stuck_since = None
        self._init_nav()
        self._init_buff_gate()
        self._init_pulse()
        self._init_hunt()

    def decide(self, obs: Observation, foreground: bool) -> Decision:
        now = time.monotonic()
        if not foreground:
            # Checked before death: a covered window captures whatever is on top
            # of it (live run 6 read a white app window as a death screen).
            self._lost_focus = True
            return Decision(state="PAUSED", note="game window is not foreground")
        if obs.dead:
            return Decision(state="DEAD", note="HUD gone (death screen, disconnect or menu); input stopped")
        if self._lost_focus:
            # Keys sent the instant focus returns can be dropped (live run 4).
            self._lost_focus = False
            self._focus_back_at = now
        if self._focus_back_at is not None and now - self._focus_back_at < 1.0:
            return Decision(state="PAUSED", note="focus regained; settling")
        if obs.black:
            self._moving_since = None
            return Decision(state="LOADING", note="black/loading screen")
        if obs.screen.no_hud:
            # Death screen, menu, map or cutscene: never click blind.
            self._moving_since = None
            return Decision(state="NOHUD", note=f"HUD not visible ({obs.screen.hud_score or 0.0:.2f})")

        actions: list[Action] = []
        notes: list[str] = []

        if self.config.input.potion_enabled:
            if self.config.input.potion_mode == "timer":
                in_demon = self._is_demon(obs)
                if (
                    (obs.enemies or self._pulse_fight_since is not None or self._hunt_target is not None)
                    and in_demon
                    and now - self._last_potion >= self.config.input.potion_timer_s
                ):
                    actions.append(Action(kind="press", key=self.config.input.potion_key))
                    self._last_potion = now
                    notes.append("potion(timer)")
            elif (
                obs.health.confidence >= 0.20
                and obs.health.level <= self.config.input.potion_threshold
                and now - self._last_potion >= self.config.input.potion_interval_s
            ):
                actions.append(Action(kind="press", key=self.config.input.potion_key))
                self._last_potion = now
                notes.append(f"potion@{obs.health.level:.2f}")

        if self.config.form.enabled:
            if self.config.form.mode == "visual":
                self._track_form(obs, now)
            form_decision = self._form_gate(obs, now, actions)
            if form_decision is not None:
                return form_decision

        actions.extend(self._utility_actions(obs, now))

        target = None if self.config.combat.mode == "hunt" else self._pick_target(obs)
        if target is not None:
            self._moving_since = None
            skill = self._choose_skill(obs, now)
            if skill is not None:
                aim_y = target.center[1] + int(obs.height * self.config.enemy.aim_offset_y)
                actions.append(Action(kind="aim", x=target.center[0], y=aim_y))
                actions.append(Action(kind="press", key=skill.key))
                self._last_press[skill.name] = now
                notes.append(f"{skill.name}->{target.center}")
                return Decision(actions=actions, state="FIGHT", note=", ".join(notes))
            notes.append("target; skills cooling")
            return Decision(actions=actions, state="TRACK", note=", ".join(notes))

        state = "IDLE"
        if self.config.mode == "roam":
            state = "ROAM"
            if self.config.combat.mode == "hunt" and obs.minimap.valid:
                hunt = self._hunt(obs, now, actions, notes)
                if hunt is not None:
                    return hunt
            elif self.config.combat.mode == "pulse":
                pulse = self._pulse(obs, now, actions, notes)
                if pulse is not None:
                    return pulse
            if self.config.nav.enabled and obs.minimap.valid:
                click = self._nav_click(obs, now)
                if click is not None:
                    actions.append(click)
                    notes.append(
                        f"nav {math.degrees(self._heading) % 360:.0f}deg"
                        f" rot={math.degrees(self._rotation):+.0f}"
                    )
            else:
                stuck = self._update_stuck(obs, now)
                click = self._roam_click(obs, now, force=stuck)
                if click is not None:
                    actions.append(click)
                    notes.append(f"roam->{click.x},{click.y}")

        if actions:
            return Decision(actions=actions, state=state, note=", ".join(notes))
        return Decision(state=state, note=", ".join(notes))

    def _track_form(self, obs: Observation, now: float) -> None:
        if obs.form.demon is True:
            self._was_demon = True
            self._human_since = None
        elif obs.form.demon is False and self._was_demon and self._human_since is None:
            # The 11s cooldown starts when demon form ends, not when it began.
            self._human_since = now

    def _is_demon(self, obs: Observation) -> bool:
        if not self.config.form.enabled:
            return True
        if self.config.form.mode in {"toggle_state", "buff", "glow"}:
            return self._toggle_state == "demon"
        return obs.form.demon is True

    def _form_gate(self, obs: Observation, now: float, actions: list[Action]) -> Decision | None:
        if self.config.form.mode in {"buff", "glow"}:
            return self._buff_gate(obs, now, actions)
        if self.config.form.mode == "toggle_state":
            return self._toggle_state_gate(obs, now, actions)
        form = obs.form
        if form.demon is True:
            return None
        if form.demon is None:
            return Decision(
                actions=actions,
                state="FORM?",
                note=f"waiting for form confidence ({form.metric:.2f}/{form.threshold:.2f})",
            )

        # Human form: only press the toggle when it is visually ready, and never
        # faster than the grace/retry windows. This prevents double-toggling.
        if now - self._last_toggle < max(
            self.config.form.post_toggle_grace_s,
            self.config.form.retry_interval_s,
        ):
            return Decision(
                actions=actions,
                state="HUMAN",
                note="toggle grace period",
            )
        if (
            self._human_since is not None
            and now - self._human_since < self.config.form.fallback_cooldown_s
        ):
            remaining = self.config.form.fallback_cooldown_s - (now - self._human_since)
            return Decision(
                actions=actions,
                state="HUMAN",
                note=f"demon cooldown {remaining:.1f}s",
            )
        reading = obs.skills.get(self.config.form.toggle_skill)
        if self.config.form.toggle_requires_ready and reading is not None and reading.ready is False:
            return Decision(
                actions=actions,
                state="HUMAN",
                note="demon toggle on cooldown",
            )
        # Without a skill_2 template we cannot see the 11s cooldown sweep, so
        # fall back to a timer. With a template, the visual ready state wins.
        if (
            (reading is None or reading.ready is None)
            and now - self._last_toggle < self.config.form.fallback_cooldown_s
        ):
            return Decision(
                actions=actions,
                state="HUMAN",
                note="toggle cooldown (fallback timer)",
            )

        actions.append(Action(kind="press", key=self.config.form.toggle_key))
        self._last_toggle = now
        return Decision(
            actions=actions,
            state="TOGGLE",
            note=f"entering demon form ({form.metric:.2f})",
        )

    def _toggle_ready(self, obs: Observation) -> bool:
        form = self.config.form
        ready = obs.form.metric >= form.ready_threshold
        reading = obs.skills.get(form.toggle_skill)
        if reading is not None and reading.ready is False:
            ready = False
        return ready

    def _buff_gate(self, obs: Observation, now: float, actions: list[Action]) -> Decision | None:
        """Form from a visual demon indicator (skill-frame glow or buff icon)."""
        form = self.config.form
        present = obs.buff.present
        if present is None:
            return Decision(actions=actions, state="FORM?", note=f"reading demon form ({obs.buff.score:.2f})")

        if present:
            if self._toggle_state != "demon":
                log.info("Form: DEMON (%.2f)", obs.buff.score)
            self._toggle_state = "demon"
            self._pending_toggle = None
            self._failed_toggles = 0
            return None

        if self._toggle_state != "human":
            log.info("Form: HUMAN (%.2f)", obs.buff.score)
            if self._toggle_state == "demon":
                # The 11s cooldown starts when demon form ends.
                self._human_since = now
        self._toggle_state = "human"

        if form.act_while_human:
            # Soak run 10: demon drops on its own in quiet areas and slot 2
            # comes back well before 11s. Pressing 2 on cooldown does nothing,
            # and the glow check (any burning frame = demon) has been right on
            # every captured frame, so just retry 2 every few seconds and keep
            # hunting/looting in human form meanwhile.
            if now - self._last_toggle >= form.retry_interval_s and (
                self._human_since is None or now - self._human_since >= form.fallback_cooldown_s
            ):
                actions.append(Action(kind="press", key=form.toggle_key))
                self._last_toggle = now
                log.debug("Form: human; pressing %s", form.toggle_key)
            return None

        self._moving_since = None
        if self._pending_toggle is not None:
            if now - self._pending_toggle < form.toggle_confirm_s:
                return Decision(actions=actions, state="TOGGLE", note="waiting for demon form")
            self._pending_toggle = None
            self._failed_toggles += 1
            log.warning(
                "Pressed %s but demon form did not appear (%d/%d)",
                form.toggle_key,
                self._failed_toggles,
                form.max_failed_toggles,
            )
            if self._failed_toggles >= form.max_failed_toggles:
                self._toggle_backoff_until = now + 30.0
                self._failed_toggles = form.max_failed_toggles - 1
                log.warning("Demon toggle keeps failing (town? wrong slot?); backing off 30s")

        if now < self._toggle_backoff_until:
            remaining = self._toggle_backoff_until - now
            return Decision(actions=actions, state="HUMAN", note=f"toggle failing; retry in {remaining:.0f}s")
        if self._human_since is not None and now - self._human_since < form.fallback_cooldown_s:
            remaining = form.fallback_cooldown_s - (now - self._human_since)
            return Decision(actions=actions, state="HUMAN", note=f"demon cooldown {remaining:.1f}s")
        if now - self._last_toggle < max(form.post_toggle_grace_s, form.retry_interval_s):
            return Decision(actions=actions, state="HUMAN", note="toggle grace period")
        if not self._toggle_ready(obs):
            return Decision(actions=actions, state="HUMAN", note="toggle icon not ready")

        actions.append(Action(kind="press", key=form.toggle_key))
        self._last_toggle = now
        self._pending_toggle = now
        return Decision(actions=actions, state="TOGGLE", note="entering demon form")

    def _toggle_state_gate(self, obs: Observation, now: float, actions: list[Action]) -> Decision | None:
        form = self.config.form
        ready = self._toggle_ready(obs)

        if self._toggle_state is None:
            self._toggle_state = "human" if form.assume_human_at_start else "demon"

        if self._toggle_state == "demon":
            if now - self._last_toggle < form.post_toggle_grace_s:
                return None
            if ready:
                return None
            # Slot 2 went dark/cooldown: the game returned us to human (damage or
            # manual exit). Start the 11s cooldown clock from the transition.
            self._toggle_state = "human"
            self._human_since = now
            return Decision(actions=actions, state="HUMAN", note="demon ended; cooldown")

        if (
            self._human_since is not None
            and now - self._human_since < form.fallback_cooldown_s
        ):
            remaining = form.fallback_cooldown_s - (now - self._human_since)
            return Decision(actions=actions, state="HUMAN", note=f"demon cooldown {remaining:.1f}s")
        if now - self._last_toggle < max(form.post_toggle_grace_s, form.retry_interval_s):
            return Decision(actions=actions, state="HUMAN", note="toggle grace period")
        if not ready:
            return Decision(actions=actions, state="HUMAN", note="toggle icon not ready")

        actions.append(Action(kind="press", key=form.toggle_key))
        self._last_toggle = now
        self._toggle_state = "demon"
        return Decision(actions=actions, state="TOGGLE", note="entering demon form")

    def _utility_actions(self, obs: Observation, now: float) -> list[Action]:
        if not self._is_demon(obs):
            return []
        actions: list[Action] = []
        for skill in self.config.skills:
            if not skill.enabled or not skill.utility:
                continue
            if now - self._last_press.get(skill.name, 0.0) < skill.utility_interval_s:
                continue
            actions.append(Action(kind="press", key=skill.key))
            self._last_press[skill.name] = now
        return actions

    def _pick_target(self, obs: Observation) -> Enemy | None:
        if not obs.enemies:
            return None
        cx, cy = self.config.regions["center"].center_pixels(obs.width, obs.height)
        return min(obs.enemies, key=lambda enemy: (enemy.center[0] - cx) ** 2 + (enemy.center[1] - cy) ** 2)

    def _hunt(self, obs: Observation, now: float, actions: list[Action], notes: list[str]) -> Decision | None:
        """Fight a minimap pack within range; otherwise steer roaming toward the nearest one."""
        combat = self.config.combat
        px, py = obs.minimap.position
        self._ignored = [entry for entry in self._ignored if entry[0] > now]
        packs = [
            p for p in obs.minimap.packs
            if all(math.hypot(px + p.dx - ix, py + p.dy - iy) > 12.0 for _, ix, iy in self._ignored)
        ]
        near = [
            p for p in packs
            if p.distance <= (combat.elite_fight_radius_px if p.elite else combat.fight_radius_px)
        ]
        if near:
            if self._hunt_target is None:
                self.packs_engaged += 1
                self._hunt_fight_since = now
            self._hunt_target = min(near, key=lambda p: p.distance)
            self._hunt_last_near = now
        elif self._hunt_target is not None and now - self._hunt_last_near > combat.linger_s:
            self._hunt_target = None
            self._hunt_fight_since = None
            self._approach_since = None

        if (
            self._hunt_target is not None
            and self._hunt_fight_since is not None
            and now - self._hunt_fight_since > combat.max_fight_s
        ):
            # Monsters die fast; a dot that will not go away is a map icon.
            target = self._hunt_target
            self._ignored.append((now + combat.ignore_s, px + target.dx, py + target.dy))
            log.info("Hunt: dot at %.0fpx %.0fdeg did not clear in %.0fs; ignoring it",
                     target.distance, math.degrees(target.angle) % 360, combat.max_fight_s)
            self._hunt_target = None
            self._hunt_fight_since = None
            packs = [p for p in packs if p is not target]

        self._loot_goal = False
        if self._hunt_target is None and combat.loot_enabled:
            loot = self._loot(obs, now, actions, notes)
            if loot is not None:
                return loot

        if self._hunt_target is None and self._loot_goal:
            return None  # walking to a loot star; its goal must not be replaced by a pack
        if self._hunt_target is None and self._route_goal(obs, notes):
            return None  # following a recorded route (or heading back on its leash)
        if self._hunt_target is None:
            # Elites count as closer; packs behind blocked ground count as much
            # further, so the bot takes open paths instead of hugging a wall.
            def cost(p: Pack) -> float:
                return p.distance / (2.0 if p.elite else 1.0) * (1.0 if p.clear else 3.0)

            goal = min(packs, key=cost) if packs else None
            self._hunt_goal = goal.angle if goal is not None else None
            self._hunt_goal_pack = goal
            self._hunt_goal_weight = 1.0 if goal is None or goal.clear else 0.35
            if goal is not None:
                notes.append(
                    f"{len(packs)} packs, goal {goal.distance:.0f}px{'' if goal.clear else ' (blocked)'}"
                )
            return None

        self._moving_since = None
        target = self._hunt_target
        scale = obs.height / 1440.0
        player_x = obs.width * self.config.nav.player_x
        player_y = obs.height * self.config.nav.player_y

        # Walk in before attacking (user: attacking from far away only works
        # for long-range builds). Give up approaching after approach_timeout_s
        # (a wall in the way) and attack from where we are.
        if target.distance > combat.attack_range_px:
            if self._approach_since is None:
                self._approach_since = now
            if now - self._approach_since < combat.approach_timeout_s:
                if now - self._last_click >= combat.approach_click_s:
                    # Click short of the pack, on the ground, not on a monster.
                    gap = max(target.distance - combat.attack_range_px * 0.6, 4.0)
                    distance = max(gap * combat.screen_per_minimap_px * scale, 110 * scale)
                    angle = target.angle + self._rotation
                    x = player_x + math.cos(angle) * distance
                    y = player_y + math.sin(angle) * distance * self.config.nav.y_scale
                    x = float(np.clip(x, obs.width * 0.05, obs.width * 0.95))
                    y = float(np.clip(y, obs.height * 0.10, obs.height * self.config.nav.click_y_max))
                    actions.append(Action(kind="click", x=int(round(x)), y=int(round(y))))
                    self._last_click = now
                notes.append(f"approach {target.distance:.0f}px -> {combat.attack_range_px:.0f}px")
                return Decision(actions=actions, state="APPROACH", note=", ".join(notes))
        else:
            self._approach_since = None

        skill = self._attack_skill(now)
        if skill is not None:
            angle = target.angle + self._rotation
            distance = float(np.clip(target.distance * combat.screen_per_minimap_px, combat.aim_min_px, combat.aim_max_px)) * scale
            x = obs.width * self.config.nav.player_x + math.cos(angle) * distance
            y = obs.height * self.config.nav.player_y + math.sin(angle) * distance * self.config.nav.y_scale
            x = float(np.clip(x, obs.width * 0.05, obs.width * 0.95))
            y = float(np.clip(y, obs.height * 0.10, obs.height * self.config.nav.click_y_max))
            actions.append(Action(kind="aim", x=int(round(x)), y=int(round(y))))
            actions.append(Action(kind="press", key=skill.key))
            self._last_press[skill.name] = now
        kind = "elite" if target.elite else "pack"
        notes.append(f"{kind} x{target.dots} @{target.distance:.0f}px {math.degrees(target.angle) % 360:.0f}deg")
        return Decision(actions=actions, state="FIGHT", note=", ".join(notes))

    def _route_goal(self, obs: Observation, notes: list[str]) -> bool:
        """Point the nav goal along a recorded route. False = no route / free to roam."""
        route, fix = obs.route_map, obs.route
        if route is None or not fix.valid:
            self.route_mode = ""
            return False
        if route.name != self._follower_route:
            self._follower.reset()
            self._follower_route = route.name
        goal = self._follower.goal(route, fix)
        if goal is None:
            self.route_mode = "roam"
            return False
        gx, gy, mode = goal
        self.route_mode = mode
        self._hunt_goal = math.atan2(gy, gx)
        self._hunt_goal_pack = None
        self._hunt_goal_weight = self.config.routes.weight / max(self.config.combat.hunt_weight, 1e-6)
        notes.append(f"{mode} {route.name} #{self._follower.index}/{len(route.path) - 1}")
        return True

    def _loot(self, obs: Observation, now: float, actions: list[Action], notes: list[str]) -> Decision | None:
        """Walk to a minimap loot star; when close, click the item label on screen."""
        if now < self.loot_hold_until:
            return None
        combat = self.config.combat
        px, py = obs.minimap.position
        self._ignored_loot = [entry for entry in self._ignored_loot if entry[0] > now]
        self._ignored_screen = [entry for entry in self._ignored_screen if entry[0] > now]
        stars = [
            (dx, dy) for dx, dy in obs.minimap.loot
            if math.hypot(dx, dy) <= combat.loot_range_px
            and all(math.hypot(px + dx - ix, py + dy - iy) > 15.0 for _, ix, iy in self._ignored_loot)
        ]
        star = min(stars, key=lambda s: math.hypot(*s)) if stars else None
        if star is not None and math.hypot(*star) <= combat.loot_near_px:
            self._loot_last_close = now
        # Any item label near the player is worth a click (the user's loot
        # filter decides what shows a label). Labels already given up on are
        # remembered in minimap (world) coordinates.
        scale = obs.height / 1440.0
        player_x = obs.width * self.config.nav.player_x
        player_y = obs.height * self.config.nav.player_y
        to_mm = combat.screen_per_minimap_px * scale

        def label_world(box: tuple[int, int, int, int]) -> tuple[float, float]:
            lx, ly = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
            return px + (lx - player_x) / to_mm, py + (ly - player_y) / (to_mm * self.config.nav.y_scale)

        labels = [
            box for box in obs.loot_labels
            if math.hypot(box[0] + box[2] / 2 - player_x, box[1] + box[3] / 2 - player_y)
            <= combat.loot_label_radius_px * scale
            and all(math.hypot(label_world(box)[0] - ix, label_world(box)[1] - iy) > 6.0 for _, ix, iy in self._ignored_loot)
            and all(math.hypot(box[0] + box[2] / 2 - sx, box[1] + box[3] / 2 - sy) > 30 * scale for _, sx, sy in self._ignored_screen)
        ]
        if labels:
            self._loot_last_close = now
        # Right next to the item the star can hide under the player arrow, so
        # stay in pickup mode for a moment after it was last seen close.
        picking = now - self._loot_last_close < 2.0
        if star is None and not picking:
            self._loot_since = None
            return None
        if self._loot_since is None:
            self._loot_since = now
        if now - self._loot_since > combat.loot_timeout_s:
            target = star or (0.0, 0.0)
            self._ignored_loot.append((now + combat.loot_ignore_s, px + target[0], py + target[1]))
            for box in labels:
                wx, wy = label_world(box)
                self._ignored_loot.append((now + combat.loot_ignore_s, wx, wy))
                # Also by screen position: banners/UI text stay put on screen.
                self._ignored_screen.append((now + combat.loot_ignore_s, box[0] + box[2] // 2, box[1] + box[3] // 2))
            log.info("Loot: not picked up in %.0fs (inventory full?); ignoring it", combat.loot_timeout_s)
            self._loot_since = None
            self._loot_last_close = -1e9
            return None

        if picking and labels:
            # Click the label nearest to where the star says the item is
            # (no star: the label nearest the player).
            sx, sy = star if star is not None and math.hypot(*star) <= combat.loot_near_px else (0.0, 0.0)
            angle = math.atan2(sy, sx) + self._rotation
            dist = math.hypot(sx, sy) * combat.screen_per_minimap_px * obs.height / 1440.0
            ex = obs.width * self.config.nav.player_x + math.cos(angle) * dist
            ey = obs.height * self.config.nav.player_y + math.sin(angle) * dist * self.config.nav.y_scale
            label = min(labels, key=lambda b: math.hypot(b[0] + b[2] / 2 - ex, b[1] + b[3] / 2 - ey))
            lx, ly = label[0] + label[2] // 2, label[1] + label[3] // 2
            if True:
                self._moving_since = None
                if now - self._last_loot_click >= combat.loot_click_interval_s:
                    actions.append(Action(kind="click", x=lx, y=ly))
                    self._last_loot_click = now
                    self.loot_clicks += 1
                notes.append(f"loot: click label at {lx},{ly}")
                return Decision(actions=actions, state="LOOT", note=", ".join(notes))

        if star is None:
            return None  # close but no label yet: let nav keep us here
        # Walk to the star: point the nav goal at it with a strong pull.
        self._loot_goal = True
        self._hunt_goal = math.atan2(star[1], star[0])
        self._hunt_goal_pack = None
        self._hunt_goal_weight = combat.loot_weight / max(combat.hunt_weight, 1e-6)
        notes.append(f"loot star {math.hypot(*star):.0f}px")
        return None

    def _pulse(self, obs: Observation, now: float, actions: list[Action], notes: list[str]) -> Decision | None:
        """Walk a bit, stop and fight, keep fighting while kills keep coming."""
        combat = self.config.combat
        if self._pulse_fight_since is None:
            if self._pulse_walk_since is None:
                self._pulse_walk_since = now
            # A kill while walking means a pack is right here: stop and fight now.
            if now - self._pulse_walk_since < combat.walk_s and not obs.kill_signal:
                return None
            self._pulse_fight_since = now
            self._pulse_fight_until = now + combat.fight_s
            self._moving_since = None

        if obs.kill_signal:
            self.kill_signals += 1
            self._pulse_fight_until = min(
                self._pulse_fight_since + combat.fight_max_s,
                max(self._pulse_fight_until, now + combat.kill_extend_s),
            )
        if now >= self._pulse_fight_until:
            self._pulse_fight_since = None
            self._pulse_walk_since = now
            return None

        skill = self._attack_skill(now)
        if skill is not None:
            elapsed = now - self._pulse_fight_since
            angle = self._heading + self._rotation + math.radians(combat.sweep_deg) * math.sin(elapsed * 3.0)
            distance = combat.aim_distance_px * obs.height / 1440.0
            x = obs.width * self.config.nav.player_x + math.cos(angle) * distance
            y = obs.height * self.config.nav.player_y + math.sin(angle) * distance * self.config.nav.y_scale
            actions.append(Action(kind="aim", x=int(round(x)), y=int(round(y))))
            actions.append(Action(kind="press", key=skill.key))
            self._last_press[skill.name] = now
        remaining = self._pulse_fight_until - now
        notes.append(f"pulse {remaining:.1f}s{' +kill' if obs.kill_signal else ''}")
        return Decision(actions=actions, state="PULSE", note=", ".join(notes))

    def _attack_skill(self, now: float) -> SkillConfig | None:
        """The main attack for blind fighting: enabled, non-utility, off its spam guard."""
        for skill in sorted(self.config.skills, key=lambda item: item.priority, reverse=True):
            if not skill.enabled or skill.utility:
                continue
            if now - self._last_press.get(skill.name, 0.0) < max(skill.min_interval_s, skill.fallback_cooldown_s):
                continue
            return skill
        return None

    def _choose_skill(self, obs: Observation, now: float) -> SkillConfig | None:
        for skill in sorted(self.config.skills, key=lambda item: item.priority, reverse=True):
            if not skill.enabled or skill.utility:
                continue
            if skill.requires_enemy and not obs.enemies:
                continue
            if now - self._last_press.get(skill.name, 0.0) < skill.min_interval_s:
                continue
            reading = obs.skills.get(skill.name)
            if reading is not None and reading.ready is False:
                continue
            if reading is not None and reading.ready is True:
                return skill
            if now - self._last_press.get(skill.name, 0.0) >= skill.fallback_cooldown_s:
                return skill
        return None

    def _roam_click(self, obs: Observation, now: float, force: bool = False) -> Action | None:
        roam = self.config.roam
        if not force and now - self._last_leg_start < roam.leg_interval_s:
            return None
        if now - self._last_click < roam.min_click_interval_s:
            return None

        if force:
            self._heading += math.pi + self._rng.uniform(-0.6, 0.6)
        else:
            jitter = math.radians(roam.turn_jitter_deg)
            self._heading += self._rng.uniform(-jitter, jitter)

        scale = max(0.5, obs.height / 1080.0)
        distance = roam.click_distance_px * scale
        cx = obs.width * 0.50
        cy = obs.height * 0.48
        margin_x = obs.width * roam.play_area_margin
        right = obs.width - margin_x
        top = obs.height * roam.click_y_min
        bottom = obs.height * roam.click_y_max

        def point() -> tuple[float, float]:
            px = cx + math.cos(self._heading) * distance
            # Always aim into the upper half; clicks near the bottom of an
            # isometric camera cover very little world distance.
            py = cy - abs(math.sin(self._heading)) * distance * 0.55
            return (
                float(np.clip(px, margin_x, right)),
                float(np.clip(py, top, bottom)),
            )

        x, y = point()
        # If a click hit a side clamp, bounce inward instead of pacing at the edge.
        if x >= right - 1 or x <= margin_x + 1:
            self._heading = (math.pi if x >= right - 1 else 0.0) + self._rng.uniform(-0.5, 0.5)
            x, y = point()
        # Do not immediately click almost the same spot again.
        if self._last_click_target is not None:
            lx, ly = self._last_click_target
            if abs(x - lx) < obs.width * 0.10 and abs(y - ly) < obs.height * 0.10:
                self._heading += math.pi + self._rng.uniform(-0.5, 0.5)
                x, y = point()

        self._last_click = now
        self._last_leg_start = now
        self._last_click_target = (int(round(x)), int(round(y)))
        self._stuck_since = None
        self._prev_gray = None
        return Action(kind="click", x=self._last_click_target[0], y=self._last_click_target[1])

    # ---- minimap navigation -------------------------------------------------

    def _nav_click(self, obs: Observation, now: float) -> Action | None:
        nav = self.config.nav
        minimap = obs.minimap
        self._record_trail(now, minimap.position)
        if self._moving_since is None:
            self._moving_since = now
            self._clicks.clear()
        if self.live:
            self._learn_rotation(now)

        stuck = self.live and self._nav_stuck(now)
        if not stuck and now - self._last_click < nav.click_interval_s:
            return None
        if stuck:
            self.stuck_count += 1
            self._blocked.append((now, self._heading))
            goal = self._hunt_goal_pack
            if goal is not None and abs(_wrap(goal.angle - self._heading)) < math.radians(60):
                # Stuck while walking at a pack: it is behind something; skip it a while.
                mx, my = minimap.position
                self._ignored.append((now + 20.0, mx + goal.dx, my + goal.dy))
                self._hunt_goal = None
                self._hunt_goal_pack = None
            self._moving_since = now
            self._clicks.clear()
            log.info("Nav: stuck heading %.0fdeg; turning", math.degrees(self._heading) % 360)

        self._heading = self._choose_heading(minimap, now, avoid=self._heading if stuck else None)
        x, y = self._project(obs, self._heading)
        self._last_click = now
        self._last_leg_start = now
        self._last_click_target = (x, y)
        self._clicks.append((now, self._heading))
        return Action(kind="click", x=x, y=y)

    def _record_trail(self, now: float, position: tuple[float, float]) -> None:
        if not self._trail or now - self._trail[-1][0] >= 0.25:
            self._trail.append((now, position[0], position[1]))
        while self._trail and now - self._trail[0][0] > self.config.nav.memory_s:
            self._trail.popleft()
        while self._blocked and now - self._blocked[0][0] > self.config.nav.blocked_memory_s:
            self._blocked.popleft()
        while self._clicks and now - self._clicks[0][0] > 5.0:
            self._clicks.popleft()

    def _position_at(self, when: float) -> tuple[float, float] | None:
        """Trail position recorded at or just before `when`."""
        found = None
        for t, x, y in self._trail:
            if t > when:
                break
            found = (x, y)
        return found

    def _nav_stuck(self, now: float) -> bool:
        nav = self.config.nav
        if self._moving_since is None or now - self._moving_since < nav.stuck_window_s:
            return False
        if not self._clicks or not self._trail:
            return False
        then = self._position_at(now - nav.stuck_window_s)
        if then is None:
            return False
        _, x, y = self._trail[-1]
        return math.hypot(x - then[0], y - then[1]) < nav.stuck_px

    def _learn_rotation(self, now: float) -> None:
        """Compare commanded heading with observed minimap motion; fix any rotation."""
        nav = self.config.nav
        if not nav.learn_rotation or now - self._last_learn < 0.5 or len(self._clicks) < 2:
            return
        window = 1.0
        recent = [h for t, h in self._clicks if now - t <= window + nav.click_interval_s]
        if len(recent) < 2 or any(abs(_wrap(h - recent[-1])) > math.radians(20) for h in recent):
            return
        then = self._position_at(now - window)
        if then is None or not self._trail:
            return
        _, x, y = self._trail[-1]
        dx, dy = x - then[0], y - then[1]
        if math.hypot(dx, dy) < 3.0 * nav.stuck_px:
            return
        error = _wrap(math.atan2(dy, dx) - recent[-1])
        if abs(error) > math.radians(100):
            return  # probably knocked back or pathing around something
        self._last_learn = now
        self._rotation = float(np.clip(self._rotation - 0.15 * error, -math.radians(60), math.radians(60)))
        log.debug("Nav: motion error %+.0fdeg -> rotation %+.1fdeg", math.degrees(error), math.degrees(self._rotation))

    def _choose_heading(self, minimap: MinimapReading, now: float, avoid: float | None = None) -> float:
        nav = self.config.nav
        angles = minimap.angles
        clearance = minimap.clearance.astype(np.float64)
        score = clearance.copy()
        score += nav.continuity_weight * (np.cos(angles - self._heading) + 1.0) / 2.0
        if self._hunt_goal is not None and self.config.combat.mode == "hunt":
            weight = self.config.combat.hunt_weight * self._hunt_goal_weight
            score += weight * (np.cos(angles - self._hunt_goal) + 1.0) / 2.0
        if avoid is not None:
            score -= 2.0 * (np.cos(angles - avoid) > math.cos(math.radians(50)))
        for _, blocked in self._blocked:
            score -= nav.blocked_weight * np.clip(np.cos(angles - blocked), 0.0, 1.0) ** 4

        old = np.array([(x, y) for t, x, y in self._trail if now - t > 4.0], dtype=np.float64)
        if len(old):
            pos = np.array(minimap.position, dtype=np.float64)
            ahead = pos[None, :] + nav.lookahead_px * np.stack([np.cos(angles), np.sin(angles)], axis=1)
            dist = np.linalg.norm(ahead[:, None, :] - old[None, :, :], axis=2)
            visits = (dist <= nav.visit_radius_px).sum(axis=1)
            score -= nav.revisit_weight * np.clip(visits / 8.0, 0.0, 1.0)

        score[clearance < nav.min_clearance] -= 1.0
        score += np.array([self._rng.uniform(0.0, nav.noise) for _ in range(len(angles))])
        return float(angles[int(np.argmax(score))])

    def _project(self, obs: Observation, heading: float) -> tuple[int, int]:
        """Screen click point for a minimap heading, kept inside the play area."""
        nav = self.config.nav
        roam = self.config.roam
        angle = heading + self._rotation
        distance = nav.click_distance_px * obs.height / 1440.0
        px, py = obs.width * nav.player_x, obs.height * nav.player_y
        vx = math.cos(angle) * distance
        vy = math.sin(angle) * distance * nav.y_scale
        left = obs.width * roam.play_area_margin
        right = obs.width - left
        top = obs.height * roam.click_y_min
        bottom = obs.height * nav.click_y_max
        t = 1.0
        if vx > 1e-6:
            t = min(t, (right - px) / vx)
        elif vx < -1e-6:
            t = min(t, (left - px) / vx)
        if vy > 1e-6:
            t = min(t, (bottom - py) / vy)
        elif vy < -1e-6:
            t = min(t, (top - py) / vy)
        t = max(t, 0.25)
        return int(round(px + vx * t)), int(round(py + vy * t))

    def _update_stuck(self, obs: Observation, now: float) -> bool:
        """Cheap stuck signal: the play-area view stops changing while roaming."""
        try:
            x, y, w, h = self.config.regions["center"].pixels(obs.width, obs.height)
            crop = obs.frame[y : y + h, x : x + w]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
            if self._prev_gray is None or self._prev_gray.shape != gray.shape:
                self._prev_gray = gray
                return False
            diff = float(np.mean(cv2.absdiff(gray, self._prev_gray)))
            self._prev_gray = gray
        except Exception:
            return False

        if diff >= self.config.roam.stuck_diff_threshold:
            self._stuck_since = None
            return False
        if self._stuck_since is None:
            self._stuck_since = now
            return False
        if now - self._stuck_since >= self.config.roam.stuck_confirm_s:
            self._stuck_since = None
            return True
        return False
