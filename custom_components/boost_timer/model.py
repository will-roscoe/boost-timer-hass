"""The boost timer's state and press rules (layer 1: pure Python, no Home Assistant).

The physical timer has one button and LEDs for 1..``steps`` steps. Presses less than
``selection_window`` apart step round the cycle off → 1 → … → steps → off; a press after a gap
turns a running timer off, or a stopped one on at 1 step. Each press starts a full run of the new
setting, which then counts down one step at a time.

:class:`TimerModel` mirrors that from two timestamps, so it survives restarts and can be corrected
by observations (:meth:`TimerModel.observe`) from a ``sync`` or, later, a temperature verifier.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any

from .const import (
    CONF_BURST_MARGIN,
    CONF_END_TOLERANCE,
    CONF_MAX_DURATION,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_ON_SETTING,
    CONF_PRESS_GAP,
    CONF_PRESS_RETRIES,
    CONF_REARM_EARLY,
    CONF_SELECTION_WINDOW,
    CONF_STEP,
    CONF_STEPS,
    DEFAULTS,
)

#: Extra wait after the selection window closes before a press counts as "after a gap".
IDLE_MARGIN = timedelta(seconds=1)
#: How early ``rearm_early`` re-arms before expiry.
REARM_LEAD = timedelta(seconds=60)
#: Wait before retrying after a failed press.
RETRY_BACKOFF = timedelta(minutes=5)
#: Wait before retrying after the Bot could not be connected to, so nothing was pressed.
UNREACHABLE_BACKOFF = timedelta(minutes=1)


@dataclass(frozen=True)
class Options:
    """Timing and behaviour settings, converted from the config entry's options to timedeltas."""

    step: timedelta
    steps: int
    selection_window: timedelta
    burst_margin: timedelta
    press_gap: timedelta
    press_retries: int
    on_setting: int
    rearm_early: bool
    rearm_lead: timedelta
    end_tolerance: timedelta
    max_duration: timedelta | None
    min_switch_interval: timedelta
    retry_backoff: timedelta

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> Options:
        """Build options from config-entry options, filling gaps from :data:`DEFAULTS`.

        :param m: options keyed by the ``CONF_*`` names; seconds for the selection window and
            burst margin, minutes for tolerances, intervals and the step; ``max_duration`` 0 = none.
        :returns: the options with every duration as a :class:`~datetime.timedelta`, and
            ``on_setting`` clamped to ``steps``.
        """
        v = {**DEFAULTS, **m}
        max_minutes = int(v[CONF_MAX_DURATION])
        steps = int(v[CONF_STEPS])
        return cls(
            step=timedelta(minutes=int(v[CONF_STEP])),
            steps=steps,
            selection_window=timedelta(seconds=float(v[CONF_SELECTION_WINDOW])),
            burst_margin=timedelta(seconds=float(v[CONF_BURST_MARGIN])),
            press_gap=timedelta(seconds=float(v[CONF_PRESS_GAP])),
            press_retries=int(v[CONF_PRESS_RETRIES]),
            # Above ``steps`` a burst would cycle back round to off and loop.
            on_setting=min(int(v[CONF_ON_SETTING]), steps),
            rearm_early=bool(v[CONF_REARM_EARLY]),
            rearm_lead=REARM_LEAD,
            end_tolerance=timedelta(minutes=int(v[CONF_END_TOLERANCE])),
            max_duration=timedelta(minutes=max_minutes) if max_minutes > 0 else None,
            min_switch_interval=timedelta(minutes=int(v[CONF_MIN_SWITCH_INTERVAL])),
            retry_backoff=RETRY_BACKOFF,
        )


@dataclass(frozen=True)
class SettingObs:
    """The timer is known to show ``setting`` steps (0 = off), with ``remaining`` left if known."""

    setting: int
    remaining: timedelta | None = None


@dataclass(frozen=True)
class HeatingObs:
    """The radiator is known to be heating (True) or not (False); the setting is unknown.

    :ivar at: when heating started or stopped, if known (back-dated, e.g. by a radiator sensor).
        ``None`` means "as of now" and keeps the original semantics.
    :ivar lo: earliest time the switch can have happened (default ``at``).
    :ivar hi: latest time the switch can have happened (default ``at``).
    """

    heating: bool
    at: datetime | None = None
    lo: datetime | None = None
    hi: datetime | None = None


@dataclass(frozen=True)
class UnknownObs:
    """Something happened that may have changed the timer, e.g. a press that may have failed."""


type Observation = SettingObs | HeatingObs | UnknownObs


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class TimerModel:
    """What the physical timer is doing, as far as Home Assistant knows.

    :ivar expires_at: when the timer reaches off; ``None`` while off.
    :ivar last_press_at: when the last press landed; decides whether the next press is quick.
    :ivar uncertain: the model may not match the timer (a failed press, an unknown observation).
    :ivar last_correction: ``(source, at)`` of the last observation that changed the model.
    :ivar run_started_at: when the press that started the current run landed (the last press of a
        burst that left the timer on), or a hand press adopted from an observation.
    :ivar adopted: the run is a hand press seen by an observation, setting unknown: it is assumed
        1 step and extended a step at a time while nothing says it stopped.
    """

    expires_at: datetime | None = None
    last_press_at: datetime | None = None
    uncertain: bool = False
    last_correction: tuple[str, datetime] | None = None
    run_started_at: datetime | None = None
    adopted: bool = False

    def setting(self, now: datetime, opts: Options) -> int:
        """Return the steps the LEDs show at ``now``: ``ceil(remaining / step)``, 0 when off."""
        if self.expires_at is None or now >= self.expires_at:
            return 0
        return min(opts.steps, math.ceil((self.expires_at - now) / opts.step))

    def is_idle(self, now: datetime, opts: Options) -> bool:
        """Return True if a press at ``now`` would count as "after a gap" (window closed)."""
        return self.last_press_at is None or now - self.last_press_at >= opts.selection_window

    def idle_at(self, opts: Options) -> datetime | None:
        """Return the safe time for a press after a gap: window close plus a 1 s margin."""
        if self.last_press_at is None:
            return None
        return self.last_press_at + opts.selection_window + IDLE_MARGIN

    def apply_press(self, now: datetime, opts: Options) -> None:
        """Record a press that landed at ``now`` and update the expiry by the button's rules."""
        current = self.setting(now, opts)
        if self.is_idle(now, opts):
            new = 0 if current > 0 else 1
        else:
            new = (current + 1) % (opts.steps + 1)
        self.expires_at = now + new * opts.step if new else None
        self.last_press_at = now
        self.adopted = False
        if new:
            self.run_started_at = now

    def observe(self, obs: Observation, now: datetime, opts: Options, source: str) -> bool:
        """Correct the model from an observation.

        A :class:`SettingObs` replaces the expiry (a full run when ``remaining`` is unknown) and
        clears ``uncertain``. A :class:`HeatingObs` without ``at`` that contradicts the model
        switches it off, or on at 1 step and uncertain (the setting is unknown). An
        :class:`UnknownObs` marks it uncertain.

        A :class:`HeatingObs` with ``at`` is back-dated:

        - Stopped: the run lasted whole steps, ``h = round((at − run_started_at) / step)``. If
          ``h`` is at least the run believed, this is agreement and nothing changes. Otherwise the
          run becomes ``h`` steps (``h = 0``: the presses never took). If ``lo`` and ``hi`` round
          to different ``h``, the model goes off and uncertain instead. Ignored before the run
          started.
        - Started while the model was off at ``at``: adopt it as a hand press, 1 step from ``at``,
          uncertain, extended by :meth:`tick` while nothing says it stopped.

        :param source: who observed it (``"sync"``, ``"verifier"``, ``"executor"``), recorded in
            :attr:`last_correction` when the model changes.
        :returns: True if the model changed.
        """
        before = (self.expires_at, self.uncertain)
        match obs:
            case SettingObs(setting=n, remaining=r):
                if n <= 0:
                    self.expires_at = None
                else:
                    self.expires_at = now + (r if r is not None else n * opts.step)
                self.uncertain = False
            case HeatingObs(at=at) if at is not None:
                self._observe_back_dated(obs, opts)
            case HeatingObs(heating=heating):
                on = self.setting(now, opts) > 0
                if heating and not on:
                    self.expires_at = now + opts.step
                    self.uncertain = True
                elif not heating and on:
                    self.expires_at = None
            case UnknownObs():
                self.uncertain = True
        changed = (self.expires_at, self.uncertain) != before
        if changed:
            at = obs.at if isinstance(obs, HeatingObs) and obs.at is not None else now
            self.last_correction = (source, at)
        return changed

    def _observe_back_dated(self, obs: HeatingObs, opts: Options) -> None:
        assert obs.at is not None
        at = obs.at
        if obs.heating:
            if self.setting(at, opts) == 0:
                self.run_started_at = self.last_press_at = at
                self.expires_at = at + opts.step
                self.uncertain = True
                self.adopted = True
            return
        rs, expires = self.run_started_at, self.expires_at
        if rs is None or expires is None or at < rs:
            return
        believed = round((expires - rs) / opts.step)
        h_lo = round(((obs.lo or at) - rs) / opts.step)
        h_hi = round(((obs.hi or at) - rs) / opts.step)
        if min(h_lo, h_hi) >= believed:
            return
        self.adopted = False
        if h_lo == h_hi:
            self.expires_at = rs + h_lo * opts.step if h_lo > 0 else None
            self.uncertain = False
        else:
            self.expires_at = None
            self.uncertain = True

    def _uncertain_until(self, opts: Options) -> datetime | None:
        corrected_at = self.last_correction[1] if self.last_correction else None
        marks = [t for t in (self.last_press_at, corrected_at) if t]
        return max(marks) + opts.steps * opts.step if marks else None

    def tick(self, now: datetime, opts: Options) -> None:
        """Clear ``uncertain`` once the timer must have run out (``steps`` steps after the last
        press or correction), and extend an adopted hand-press run by a step each time its assumed
        end passes, up to ``steps`` from when it started."""
        until = self._uncertain_until(opts)
        if self.uncertain and until is not None and now >= until:
            self.uncertain = False
        while (
            self.adopted
            and self.expires_at is not None
            and self.run_started_at is not None
            and self.expires_at <= now
            and self.expires_at - self.run_started_at < opts.steps * opts.step
        ):
            self.expires_at += opts.step

    def next_change(self, now: datetime, opts: Options) -> datetime | None:
        """Return the next time the displayed setting changes or uncertainty clears, if any."""
        times: list[datetime] = []
        if self.expires_at is not None and self.expires_at > now:
            remaining_steps = math.ceil((self.expires_at - now) / opts.step)
            times += [
                self.expires_at - k * opts.step
                for k in range(remaining_steps)
                if self.expires_at - k * opts.step > now
            ]
        if self.uncertain and (until := self._uncertain_until(opts)) and until > now:
            times.append(until)
        return min(times) if times else None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable copy for storage."""
        return {
            "expires_at": _iso(self.expires_at),
            "last_press_at": _iso(self.last_press_at),
            "uncertain": self.uncertain,
            "last_correction": (
                [self.last_correction[0], _iso(self.last_correction[1])]
                if self.last_correction
                else None
            ),
            "run_started_at": _iso(self.run_started_at),
            "adopted": self.adopted,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> TimerModel:
        """Rebuild a model saved by :meth:`to_dict`."""
        corr = d.get("last_correction")
        expires = _dt(d.get("expires_at"))
        last_press = _dt(d.get("last_press_at"))
        # Saved by v1: an on model's last press always started its run.
        run_started = _dt(d["run_started_at"]) if "run_started_at" in d else None
        if "run_started_at" not in d and expires is not None:
            run_started = last_press
        return cls(
            expires_at=expires,
            last_press_at=last_press,
            uncertain=bool(d.get("uncertain", False)),
            last_correction=(corr[0], _dt(corr[1])) if corr else None,
            run_started_at=run_started,
            adopted=bool(d.get("adopted", False)),
        )
