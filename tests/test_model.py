from datetime import UTC, datetime, timedelta

from custom_components.boost_timer.model import (
    HeatingObs,
    Options,
    SettingObs,
    TimerModel,
    UnknownObs,
)

T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)
OPTS = Options.from_mapping({})
H = timedelta(hours=1)
S = timedelta(seconds=1)


def presses(m: TimerModel, *offsets_s: float) -> None:
    for s in offsets_s:
        m.apply_press(T0 + s * S, OPTS)


def test_options_defaults_and_units():
    assert OPTS.step == H
    assert OPTS.steps == 4
    assert OPTS.selection_window == 5 * S
    assert OPTS.burst_margin == S
    assert OPTS.end_tolerance == timedelta(minutes=10)
    assert OPTS.max_duration is None
    assert OPTS.min_switch_interval == timedelta(minutes=5)
    assert OPTS.rearm_lead == timedelta(seconds=60)
    assert OPTS.retry_backoff == timedelta(minutes=5)
    assert Options.from_mapping({"max_duration": 90}).max_duration == timedelta(minutes=90)


def test_idle_press_from_off_is_1h():
    m = TimerModel()
    m.apply_press(T0, OPTS)
    assert m.setting(T0, OPTS) == 1
    assert m.expires_at == T0 + H


def test_quick_presses_cycle():
    m = TimerModel()
    presses(m, 0, 2, 4, 6)
    assert m.setting(T0 + 6 * S, OPTS) == 4
    presses(m, 8)
    assert m.setting(T0 + 8 * S, OPTS) == 0
    assert m.expires_at is None
    presses(m, 10)
    assert m.setting(T0 + 10 * S, OPTS) == 1


def test_idle_press_while_on_turns_off():
    m = TimerModel()
    presses(m, 0, 2, 4)
    assert m.setting(T0 + 4 * S, OPTS) == 3
    presses(m, 14)
    assert m.setting(T0 + 14 * S, OPTS) == 0
    assert m.expires_at is None


def test_burst_from_on_continues_from_off():
    m = TimerModel()
    presses(m, 0)
    presses(m, 10)
    assert m.setting(T0 + 10 * S, OPTS) == 0
    presses(m, 12)
    assert m.setting(T0 + 12 * S, OPTS) == 1
    presses(m, 14)
    assert m.setting(T0 + 14 * S, OPTS) == 2


def test_countdown_display():
    m = TimerModel()
    m.observe(SettingObs(2), T0, OPTS, source="sync")
    assert m.setting(T0 + timedelta(minutes=59), OPTS) == 2
    assert m.setting(T0 + timedelta(minutes=61), OPTS) == 1
    assert m.setting(T0 + 2 * H, OPTS) == 0


def test_idle_tracking():
    m = TimerModel()
    assert m.is_idle(T0, OPTS)
    assert m.idle_at(OPTS) is None
    m.apply_press(T0, OPTS)
    assert not m.is_idle(T0 + 4 * S, OPTS)
    assert m.is_idle(T0 + 5 * S, OPTS)
    assert m.idle_at(OPTS) == T0 + 6 * S


def test_observe_setting_without_remaining_assumes_full_run():
    m = TimerModel()
    assert m.observe(SettingObs(3), T0, OPTS, source="sync")
    assert m.expires_at == T0 + 3 * H


def test_observe_setting_with_remaining_and_clears_uncertain():
    m = TimerModel(uncertain=True)
    m.observe(SettingObs(2, remaining=timedelta(minutes=70)), T0, OPTS, source="sync")
    assert m.expires_at == T0 + timedelta(minutes=70)
    assert m.uncertain is False
    assert m.last_correction == ("sync", T0)


def test_observe_heating_false_turns_model_off():
    m = TimerModel()
    presses(m, 0, 2)
    assert m.observe(HeatingObs(False), T0 + H / 2, OPTS, source="verifier")
    assert m.expires_at is None
    assert m.last_correction[0] == "verifier"


def test_observe_heating_matching_model_changes_nothing():
    m = TimerModel()
    presses(m, 0)
    assert not m.observe(HeatingObs(True), T0 + 10 * S, OPTS, source="verifier")
    assert m.last_correction is None


def test_observe_heating_true_while_off_is_uncertain():
    m = TimerModel()
    assert m.observe(HeatingObs(True), T0, OPTS, source="verifier")
    assert m.expires_at == T0 + H
    assert m.uncertain is True


def test_observe_unknown_marks_uncertain():
    m = TimerModel()
    assert m.observe(UnknownObs(), T0, OPTS, source="executor")
    assert m.uncertain is True


def test_uncertain_clears_after_4h():
    m = TimerModel()
    presses(m, 0)
    m.observe(UnknownObs(), T0 + 10 * S, OPTS, source="executor")
    m.tick(T0 + 4 * H - S, OPTS)
    assert m.uncertain is True
    m.tick(T0 + 4 * H + 10 * S, OPTS)
    assert m.uncertain is False


def test_next_change_is_step_boundary_then_expiry():
    m = TimerModel()
    m.observe(SettingObs(2), T0, OPTS, source="sync")
    assert m.next_change(T0, OPTS) == T0 + H
    assert m.next_change(T0 + H, OPTS) == T0 + 2 * H
    assert m.next_change(T0 + 2 * H, OPTS) is None


def test_next_change_includes_uncertainty_clearing():
    m = TimerModel()
    m.observe(UnknownObs(), T0, OPTS, source="executor")
    assert m.next_change(T0, OPTS) == T0 + 4 * H


def test_round_trip_dict():
    m = TimerModel()
    presses(m, 0, 2)
    m.observe(UnknownObs(), T0 + 3 * S, OPTS, source="executor")
    assert TimerModel.from_dict(m.to_dict()) == m
    assert TimerModel.from_dict(TimerModel().to_dict()) == TimerModel()
