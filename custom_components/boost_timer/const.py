"""Constants: the domain, config-entry keys and their defaults (with units)."""

from datetime import timedelta
from typing import Any

DOMAIN = "boost_timer"

CONF_NAME = "name"
CONF_BOT = "bot_entity"
#: Seconds after a press during which another press advances the cycle.
CONF_SELECTION_WINDOW = "selection_window"
#: Seconds of slack: a press call slower than ``selection_window - burst_margin`` ends a burst.
CONF_BURST_MARGIN = "burst_margin"
#: Seconds between presses in a burst. The Bot answers a press sent while its arm is still moving
#: with result code 3 and ignores it; at 2 s apart every second press was ignored.
CONF_PRESS_GAP = "press_gap"
CONF_PRESS_RETRIES = "press_retries"
#: Setting (in steps) that "On" arms the timer to.
CONF_ON_SETTING = "on_setting"
CONF_REARM_EARLY = "rearm_early"
#: Minutes a heat-for/until request may overshoot or undershoot its end.
CONF_END_TOLERANCE = "end_tolerance"
#: Minutes after which "On" stops by itself; 0 means never.
CONF_MAX_DURATION = "max_duration"
#: Minutes to wait before acting on a heating on/off reversal.
CONF_MIN_SWITCH_INTERVAL = "min_switch_interval"
#: Minutes per timer step (1 LED).
CONF_STEP = "step"
#: Number of timer steps (LEDs).
CONF_STEPS = "steps"
#: Service used to press a ``switch`` Bot: "turn_on", "turn_off" or "alternate".
CONF_PRESS_SERVICE = "press_service"

#: Temperature sensor stuck on the radiator; set to turn on radiator sync.
CONF_RADIATOR_SENSOR = "radiator_sensor"
#: Room temperature sensors the radiator is compared with (averaged).
CONF_ROOM_SENSORS = "room_sensors"
#: Whether radiator sync may correct the timer model (the detector runs either way).
CONF_SYNC_CORRECTIONS = "sync_corrections"

PRESS_SERVICES = ["turn_on", "turn_off", "alternate"]

#: Radiator sync ignores switches detected within this long after a press landed.
SUPPRESS_AFTER_PRESS = timedelta(minutes=3)
#: A run with no heating seen this long after its press is taken to have never started.
NEVER_STARTED_AFTER = timedelta(minutes=45)
#: Corrections per local day before radiator sync freezes until midnight.
CORRECTION_CAP = 6
#: No radiator reading for this long makes the detector blind.
BLIND_AFTER = timedelta(hours=1)
#: Continuous blindness this long raises a persistent notification.
BLIND_NOTIFY_AFTER = timedelta(hours=24)
#: Bus event fired for every correction radiator sync makes.
EVENT_CORRECTED = f"{DOMAIN}_corrected"

DEFAULTS: dict[str, Any] = {
    CONF_SELECTION_WINDOW: 5,
    CONF_BURST_MARGIN: 1,
    CONF_PRESS_GAP: 3,
    CONF_PRESS_RETRIES: 2,
    CONF_ON_SETTING: 4,
    CONF_REARM_EARLY: False,
    CONF_END_TOLERANCE: 10,
    CONF_MAX_DURATION: 0,
    CONF_MIN_SWITCH_INTERVAL: 5,
    CONF_STEP: 60,
    CONF_STEPS: 4,
    CONF_PRESS_SERVICE: "turn_on",
    CONF_SYNC_CORRECTIONS: True,
}
