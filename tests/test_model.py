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


def test_on_setting_clamped_to_steps():
    assert Options.from_mapping({"on_setting": 5}).on_setting == 4
    assert Options.from_mapping({"on_setting": 4, "steps": 3}).on_setting == 3


# ---- v2: back-dated heating observations ----------------------------------------------------

M = timedelta(minutes=1)


def four_hours() -> TimerModel:
    """A model after a 4-press burst landing at T0 (presses 3 s apart)."""
    m = TimerModel()
    presses(m, 0, 3, 6, 9)
    return m


def off(m, at, lo=None, hi=None, now=None):
    return m.observe(HeatingObs(False, at, lo, hi), now or (hi or at), OPTS, source="verifier")


def test_run_started_at_is_last_press_of_burst():
    m = four_hours()
    assert m.run_started_at == T0 + 9 * S
    assert m.setting(T0 + 10 * S, OPTS) == 4


def test_off_press_keeps_run_started_at():
    m = four_hours()
    m.apply_press(T0 + 30 * S, OPTS)  # after the gap: off
    assert m.setting(T0 + 31 * S, OPTS) == 0
    assert m.run_started_at == T0 + 9 * S


def test_ended_early_rounds_to_whole_hours():
    """Will's example: set to 4h 140 min ago, stopped 20 min ago: it was 2h."""
    m = four_hours()
    rs = m.run_started_at
    assert off(m, T0 + 120 * M, T0 + 112 * M, T0 + 130 * M, now=T0 + 140 * M)
    assert m.expires_at == rs + 120 * M
    assert m.setting(T0 + 140 * M, OPTS) == 0
    assert m.uncertain is False


def test_natural_end_is_agreement():
    m = four_hours()
    expires = m.expires_at
    assert not off(m, T0 + 241 * M, T0 + 235 * M, T0 + 258 * M, now=T0 + 260 * M)
    assert m.expires_at == expires


def test_bracket_straddling_half_hour_marks_uncertain_off():
    m = four_hours()
    assert off(m, T0 + 90 * M, T0 + 80 * M, T0 + 100 * M, now=T0 + 101 * M)
    assert m.expires_at is None
    assert m.uncertain is True


def test_never_started_is_zero_hours():
    m = four_hours()
    rs = m.run_started_at
    assert off(m, rs, rs, rs, now=T0 + 50 * M)
    assert m.expires_at is None
    assert m.uncertain is False


def test_off_before_run_start_ignored():
    m = four_hours()
    expires = m.expires_at
    assert not off(m, T0 - 10 * M, T0 - 20 * M, T0 - 5 * M, now=T0 + 5 * M)
    assert m.expires_at == expires


def test_hand_press_adopted_as_one_hour():
    m = TimerModel()
    assert m.observe(HeatingObs(True, T0), T0 + 20 * M, OPTS, source="verifier")
    assert m.expires_at == T0 + 60 * M
    assert m.uncertain is True
    assert m.adopted is True
    assert m.run_started_at == T0
    assert m.last_press_at == T0


def test_adopted_run_extends_hourly_up_to_steps():
    m = TimerModel()
    m.observe(HeatingObs(True, T0), T0 + 20 * M, OPTS, source="verifier")
    seen = []
    for minutes in (60, 120, 180, 240, 300):
        m.tick(T0 + minutes * M, OPTS)
        seen.append(m.expires_at)
    assert seen == [T0 + 120 * M, T0 + 180 * M, T0 + 240 * M, T0 + 240 * M, T0 + 240 * M]


def test_adopted_run_then_off_rounds_from_hand_press():
    m = TimerModel()
    m.observe(HeatingObs(True, T0), T0 + 20 * M, OPTS, source="verifier")
    m.tick(T0 + 60 * M, OPTS)
    m.tick(T0 + 120 * M, OPTS)
    assert m.expires_at == T0 + 180 * M
    assert off(m, T0 + 125 * M, T0 + 118 * M, T0 + 140 * M, now=T0 + 141 * M)
    assert m.expires_at == T0 + 120 * M
    assert m.adopted is False


def test_press_clears_adopted():
    m = TimerModel()
    m.observe(HeatingObs(True, T0), T0 + 20 * M, OPTS, source="verifier")
    m.apply_press(T0 + 30 * M, OPTS)
    assert m.adopted is False


def test_round_trip_and_v1_data():
    m = TimerModel()
    m.observe(HeatingObs(True, T0), T0 + 20 * M, OPTS, source="verifier")
    back = TimerModel.from_dict(m.to_dict())
    assert back.run_started_at == T0
    assert back.adopted is True
    v1 = {"expires_at": (T0 + H).isoformat(), "last_press_at": T0.isoformat(), "uncertain": False}
    old = TimerModel.from_dict(v1)
    assert old.run_started_at == T0
    assert old.adopted is False
