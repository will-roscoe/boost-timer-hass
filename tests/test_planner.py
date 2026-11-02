from datetime import UTC, datetime, timedelta

from custom_components.boost_timer.model import Options, SettingObs, TimerModel
from custom_components.boost_timer.planner import (
    PRIORITY_AUTOMATIC,
    Burst,
    Done,
    Kind,
    Request,
    Wait,
    next_action,
)

T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)
OPTS = Options.from_mapping({})
H = timedelta(hours=1)
M = timedelta(minutes=1)
S = timedelta(seconds=1)
GAP = 2 * S  # spacing between presses in a simulated burst


def run(model, request, start, end, opts=OPTS):
    """Simulate the planner from ``start`` to ``end``.

    :returns: ``(total_presses, model, bursts)`` where ``bursts`` is a list of ``(time, n)``.
    """
    t, total, bursts = start, 0, []
    for _ in range(1000):
        model.tick(t, opts)
        action = next_action(model, request, t, opts)
        if isinstance(action, Done):
            break
        if isinstance(action, Wait):
            if action.until > end:
                break
            t = max(t, action.until)
            continue
        bursts.append((t, action.presses))
        for _ in range(action.presses):
            model.apply_press(t, opts)
            total += 1
            t += GAP
    return total, model, bursts


def on_at(setting, start=T0, remaining=None):
    m = TimerModel()
    m.observe(SettingObs(setting, remaining), start - timedelta(minutes=10), OPTS, "sync")
    return m


def test_off_heat_30min():
    total, m, _ = run(TimerModel(), Request.heat_until(T0 + 30 * M, T0), T0, T0 + 5 * H)
    assert total == 2
    assert m.setting(T0 + 31 * M, OPTS) == 0


def test_off_heat_55min():
    total, _, _ = run(TimerModel(), Request.heat_until(T0 + 55 * M, T0), T0, T0 + 5 * H)
    assert total == 1


def test_off_heat_2h30():
    total, _, bursts = run(TimerModel(), Request.heat_until(T0 + 150 * M, T0), T0, T0 + 5 * H)
    assert total == 4
    assert [n for _, n in bursts] == [3, 1]
    assert bursts[1][0] == T0 + 150 * M


def test_on_95min_heat_90min_no_presses():
    m = TimerModel()
    m.observe(SettingObs(2, remaining=95 * M), T0, OPTS, "sync")
    assert next_action(m, Request.heat_until(T0 + 90 * M, T0), T0, OPTS) == Done()


def test_heat_shorter_than_tolerance_from_off_is_done():
    assert next_action(TimerModel(), Request.heat_until(T0 + 5 * M, T0), T0, OPTS) == Done()


def test_on_1h_set_3h_one_burst():
    assert next_action(on_at(1), Request.set(3, T0), T0, OPTS) == Burst(4, target=3)


def test_on_4h_set_1h():
    assert next_action(on_at(4), Request.set(1, T0), T0, OPTS) == Burst(2, target=1)


def test_set_off_from_on_and_set_current_is_done():
    assert next_action(on_at(2), Request.set(0, T0), T0, OPTS) == Burst(1, target=0)
    assert next_action(on_at(2), Request.set(2, T0), T0, OPTS) == Done()


def test_set_from_off():
    for n in range(1, 5):
        assert next_action(TimerModel(), Request.set(n, T0), T0, OPTS) == Burst(n, target=n)


def test_off_request():
    assert next_action(on_at(3), Request.off(T0), T0, OPTS) == Burst(1, target=0)
    assert next_action(TimerModel(), Request.off(T0), T0, OPTS) == Done()
    assert next_action(TimerModel(), None, T0, OPTS) == Done()


def test_on_request_rearm_cadence():
    total, _, bursts = run(TimerModel(), Request.on(T0, OPTS), T0, T0 + 12 * H)
    assert total == 12
    assert [n for _, n in bursts] == [4, 4, 4]
    # each re-arm happens when the previous run expires (4 h after its last press)
    assert bursts[1][0] == bursts[0][0] + 3 * GAP + 4 * H


def test_rearm_early():
    opts = Options.from_mapping({"rearm_early": True})
    total, m, bursts = run(TimerModel(), Request.on(T0, opts), T0, T0 + 5 * H, opts)
    first_expiry = T0 + 3 * GAP + 4 * H
    assert bursts[1] == (first_expiry - 60 * S, 5)
    assert total == 9
    assert m.setting(first_expiry, opts) == 4


def test_max_duration_stops():
    opts = Options.from_mapping({"max_duration": 90})
    request = Request.on(T0, opts)
    assert request.until == T0 + 90 * M
    total, m, bursts = run(TimerModel(), request, T0, T0 + 5 * H, opts)
    assert bursts[-1] == (T0 + 90 * M, 1)
    assert m.setting(T0 + 90 * M + 5 * S, opts) == 0
    assert next_action(m, request, T0 + 91 * M, opts) == Done()


def test_heat_until_over_4h_rearms():
    total, m, bursts = run(TimerModel(), Request.heat_until(T0 + 6 * H, T0), T0, T0 + 9 * H)
    assert [n for _, n in bursts] == [4, 2]
    assert abs(m.expires_at - (T0 + 6 * H)) <= OPTS.end_tolerance


def test_not_idle_returns_wait():
    m = TimerModel()
    m.apply_press(T0, OPTS)
    assert next_action(m, Request.set(3, T0 + 2 * S), T0 + 2 * S, OPTS) == Wait(T0 + 6 * S)


def test_request_labels_and_round_trip():
    assert Request.on(T0, OPTS).label == "on"
    assert Request.heat_until(T0 + H, T0).label == "heat_until"
    assert Request.off(T0).label == "off"
    assert Request.set(2, T0).label == "manual"
    r = Request.heat_until(T0 + H, T0, source="thermostat", priority=PRIORITY_AUTOMATIC)
    assert r.kind is Kind.HEAT
    assert Request.from_dict(r.to_dict()) == r
