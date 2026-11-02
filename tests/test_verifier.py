from datetime import UTC, datetime, timedelta

import pytest

from custom_components.boost_timer.verifier import (
    RadiatorDetector,
    Reading,
    cooling_minutes,
    fit_exponent,
    off_gap,
)

from .radiator_curves import series, switch_times

START = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
M = timedelta(minutes=1)
THREE = [(True, 120), (False, 90), (True, 60)]


def run(readings, detector=None):
    d = detector or RadiatorDetector()
    events = [e for r in readings if (e := d.update(r)) is not None]
    return d, events


@pytest.mark.parametrize("n", [1.0, 1.3])
def test_off_gap_and_cooling_minutes_are_inverse(n):
    assert cooling_minutes(30, off_gap(30, 25, 0.04, n), 0.04, n) == pytest.approx(25)


def test_fit_exponent_recovers_n():
    pairs = [(g, 0.01 * g**1.3) for g in range(4, 31)]
    c, n = fit_exponent(pairs)
    assert n == pytest.approx(1.3, abs=0.05)
    assert c == pytest.approx(0.01, rel=0.1)


def test_fit_exponent_rejects_unphysical():
    assert fit_exponent([(g, 0.001 * g**3) for g in range(4, 31)]) is None
    assert fit_exponent([(g, 0.01 * g) for g in range(4, 11)]) is None  # 7 pairs


def test_detects_heat_then_off_once_each():
    _, events = run(series(START, 20.0, THREE))
    assert [(e.heating, e.initial) for e in events] == [(True, True), (False, False), (True, False)]
    for e, true_switch in zip(events[1:], switch_times(START, THREE), strict=True):
        assert e.lo <= e.at <= e.hi
        assert e.lo <= true_switch <= e.hi
    assert events[2].at - switch_times(START, THREE)[1] <= 15 * M


def test_no_events_while_steady_on():
    _, events = run(series(START, 20.0, [(True, 300)]))
    assert [(e.heating, e.initial) for e in events] == [(True, True)]


@pytest.mark.parametrize("room", [5.0, 28.0])
def test_room_shift_does_not_change_events(room):
    _, base = run(series(START, 20.0, THREE))
    _, shifted = run(series(START, room, THREE))
    assert [e.heating for e in shifted] == [e.heating for e in base]
    for a, b in zip(base, shifted, strict=True):
        assert abs(a.at - b.at) <= M


def test_low_power_still_detected():
    _, events = run(series(START, 20.0, THREE, peak_gap=9.0))
    assert [(e.heating, e.initial) for e in events] == [(True, True), (False, False), (True, False)]


def test_cooling_exponent_learned_and_used():
    segments = [(True, 60), (False, 120)] * 3
    d, events = run(series(START, 20.0, segments, c=0.012, n=1.3))
    assert d.n == pytest.approx(1.3, abs=0.15)
    offs = [e for e in events if not e.heating and not e.initial]
    true_offs = switch_times(START, segments)[0::2]
    assert len(offs) == 3
    assert abs(offs[-1].at - true_offs[-1]) <= 5 * M


def test_stale_and_duplicate_readings_ignored():
    readings = series(START, 20.0, THREE)
    d, _ = run(readings)
    state = d.state
    assert d.update(readings[-1]) is None
    stale = Reading(readings[-1].at - M, 20.0, 20.0)
    assert d.update(stale) is None
    assert d.state == state
    assert d.last_reading_at == readings[-1].at


def test_round_trip_keeps_learning_drops_buffer():
    readings = series(START, 20.0, THREE)
    d, _ = run(readings)
    restored = RadiatorDetector.from_dict(d.to_dict())
    assert restored.state == d.state
    assert restored.n == d.n
    assert restored.to_dict() == d.to_dict()
    nxt = Reading(readings[-1].at + 10 * M, readings[-1].radiator, 20.0)
    assert restored.update(nxt) is None
