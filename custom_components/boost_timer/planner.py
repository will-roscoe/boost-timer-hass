"""Turn a request into presses (layer 2: pure Python, no Home Assistant).

:func:`next_action` looks at the :class:`~.model.TimerModel` and the active :class:`Request` and
says what to do now: press a :class:`Burst` of quick presses, :class:`Wait` until a time and ask
again, or nothing more (:class:`Done`). The controller executes the action and calls it again.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import math
from typing import Any

from .model import Options, TimerModel

PRIORITY_MANUAL = 100
PRIORITY_AUTOMATIC = 50


class Kind(StrEnum):
    """What a request asks for."""

    HEAT = "heat"
    OFF = "off"
    SET = "set"


@dataclass(frozen=True)
class Request:
    """One thing someone asked the timer to do.

    :ivar kind: heat (``until`` ``None`` = indefinitely), off, or set a setting once.
    :ivar requested_at: when it was asked for.
    :ivar until: end of a HEAT request; ``None`` for "on".
    :ivar setting: target steps of a SET request.
    :ivar source: who asked (``"switch"``, ``"select"``, ``"service"``, a thermostat, ...).
    :ivar priority: :data:`PRIORITY_MANUAL` outranks :data:`PRIORITY_AUTOMATIC`.
    """

    kind: Kind
    requested_at: datetime
    until: datetime | None = None
    setting: int | None = None
    source: str = "manual"
    priority: int = PRIORITY_MANUAL

    @property
    def label(self) -> str:
        """Return ``"on"``, ``"heat_until"``, ``"off"`` or ``"manual"`` (a one-off setting)."""
        if self.kind is Kind.HEAT:
            return "on" if self.until is None else "heat_until"
        return "off" if self.kind is Kind.OFF else "manual"

    @classmethod
    def on(cls, now: datetime, opts: Options, source: str = "manual", **kw: Any) -> Request:
        """Heat indefinitely, or for ``opts.max_duration`` when one is configured."""
        until = now + opts.max_duration if opts.max_duration else None
        return cls(Kind.HEAT, now, until=until, source=source, **kw)

    @classmethod
    def heat_until(
        cls, until: datetime, now: datetime, source: str = "manual", **kw: Any
    ) -> Request:
        """Heat until ``until``, within the end tolerance."""
        return cls(Kind.HEAT, now, until=until, source=source, **kw)

    @classmethod
    def off(cls, now: datetime, source: str = "manual", **kw: Any) -> Request:
        """Turn the timer off."""
        return cls(Kind.OFF, now, source=source, **kw)

    @classmethod
    def set(cls, n: int, now: datetime, source: str = "manual", **kw: Any) -> Request:
        """Press to setting ``n`` once (0 = off), with no further management."""
        return cls(Kind.SET, now, setting=n, source=source, **kw)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable copy for storage."""
        return {
            "kind": self.kind.value,
            "requested_at": self.requested_at.isoformat(),
            "until": self.until.isoformat() if self.until else None,
            "setting": self.setting,
            "source": self.source,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Request:
        """Rebuild a request saved by :meth:`to_dict`."""
        return cls(
            kind=Kind(d["kind"]),
            requested_at=datetime.fromisoformat(d["requested_at"]),
            until=datetime.fromisoformat(d["until"]) if d.get("until") else None,
            setting=d.get("setting"),
            source=d.get("source", "manual"),
            priority=d.get("priority", PRIORITY_MANUAL),
        )


@dataclass(frozen=True)
class Burst:
    """Press quickly until the timer shows ``target``; the first press comes after a gap.

    :ivar presses: how many presses that takes if the first one lands straight away.
    :ivar target: the setting to end on. A first press that lands late can change the count, so
        the executor counts towards this rather than ``presses``.
    """

    presses: int
    target: int


@dataclass(frozen=True)
class Wait:
    """Nothing to press until ``until``; ask again then."""

    until: datetime


@dataclass(frozen=True)
class Done:
    """The request needs no more presses."""


type Action = Burst | Wait | Done


def _burst(n: int, target: int, model: TimerModel, now: datetime, opts: Options) -> Burst | Wait:
    if model.is_idle(now, opts):
        return Burst(n, target)
    idle_at = model.idle_at(opts)
    assert idle_at is not None  # not idle implies a last press
    return Wait(idle_at)


def next_action(model: TimerModel, request: Request | None, now: datetime, opts: Options) -> Action:
    """Return what to do at ``now`` to carry out ``request``.

    A :class:`Burst` is only returned when a press now counts as "after a gap"; otherwise a
    :class:`Wait` until the selection window has closed.
    """
    if request is None:
        return Done()
    s = model.setting(now, opts)
    e = model.expires_at
    tol = opts.end_tolerance

    if request.kind is Kind.OFF:
        return _burst(1, 0, model, now, opts) if s > 0 else Done()

    if request.kind is Kind.SET:
        n = request.setting or 0
        if s == n:
            return Done()
        if s == 0:
            return _burst(n, n, model, now, opts)
        return _burst(1 + n, n, model, now, opts)

    until = request.until
    if until is not None and now >= until:
        if s > 0 and e is not None and e > until + tol:
            return _burst(1, 0, model, now, opts)
        return Done()

    if s == 0:
        if until is None:
            return _burst(opts.on_setting, opts.on_setting, model, now, opts)
        left = until - now - tol
        if left <= timedelta(0):
            return Done()
        n = max(1, min(opts.steps, math.ceil(left / opts.step)))
        return _burst(n, n, model, now, opts)

    assert e is not None  # s > 0 implies an expiry
    if until is None:
        if opts.rearm_early:
            if e - now <= opts.rearm_lead:
                return _burst(1 + opts.on_setting, opts.on_setting, model, now, opts)
            return Wait(e - opts.rearm_lead)
        return Wait(e)

    if abs(e - until) <= tol:
        return Done()
    return Wait(until) if e > until + tol else Wait(e)
