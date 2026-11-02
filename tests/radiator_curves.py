"""Synthetic radiator recordings shaped like the real sensor's: quantised, change-driven, with
silences after each switch."""

from __future__ import annotations

from datetime import datetime, timedelta
import math

from custom_components.boost_timer.verifier import SENSOR_STEP, Reading, off_gap


def _q(x: float) -> float:
    return round(round(x / SENSOR_STEP) * SENSOR_STEP, 2)


def series(
    start: datetime,
    room: float,
    segments: list[tuple[bool, int]],
    peak_gap: float = 29.0,
    c: float = 0.04,
    n: float = 1.0,
    heat_tau: float = 20.0,
    silence: int = 15,
    heartbeat: int = 60,
) -> list[Reading]:
    """Return 1-minute readings of a radiator switching per ``segments``, as the sensor reports them.

    :param segments: ``(heating, minutes)`` runs, starting from room temperature.
    :param peak_gap: °C above the room the radiator settles at while heating.
    :param c: cooling constant and ``n`` exponent of ``d(gap)/dt = −c·gap^n``.
    :param heat_tau: minutes for the heating approach to the peak (first order).
    :param silence: minutes with no readings after every switch except the first segment's start.
    :param heartbeat: a reading is sent after this many minutes even without a change.
    :returns: readings quantised to :data:`SENSOR_STEP`, sent only when the value changed by at
        least one step or ``heartbeat`` passed.
    """
    out: list[Reading] = []
    gap = 0.0
    minute = 0
    last_sent: tuple[int, float] | None = None
    for index, (heating, length) in enumerate(segments):
        quiet_until = minute + (silence if index > 0 else 0)
        for _ in range(length):
            if heating:
                gap += (peak_gap - gap) * (1 - math.exp(-1 / heat_tau))
            else:
                gap = off_gap(gap, 1, c, n) if gap > 0 else 0.0
            minute += 1
            value = _q(room + gap)
            if minute < quiet_until:
                continue
            due = last_sent is None or minute - last_sent[0] >= heartbeat
            if due or abs(value - last_sent[1]) >= SENSOR_STEP - 1e-9:
                out.append(Reading(start + timedelta(minutes=minute), value, room))
                last_sent = (minute, value)
    return out


def switch_times(start: datetime, segments: list[tuple[bool, int]]) -> list[datetime]:
    """Return when each segment after the first begins."""
    t, times = 0, []
    for _, length in segments[:-1]:
        t += length
        times.append(start + timedelta(minutes=t))
    return times
