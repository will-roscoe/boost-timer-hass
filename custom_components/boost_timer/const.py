"""Constants: the domain, config-entry keys and their defaults (with units)."""

from typing import Any

DOMAIN = "boost_timer"

CONF_NAME = "name"
CONF_BOT = "bot_entity"
#: Seconds after a press during which another press advances the cycle.
CONF_SELECTION_WINDOW = "selection_window"
#: Seconds of slack: a press call slower than ``selection_window - burst_margin`` ends a burst.
CONF_BURST_MARGIN = "burst_margin"
#: Seconds to wait between presses in a burst: the Bot acknowledges at once, but its arm takes time.
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

PRESS_SERVICES = ["turn_on", "turn_off", "alternate"]

DEFAULTS: dict[str, Any] = {
    CONF_SELECTION_WINDOW: 5,
    CONF_BURST_MARGIN: 1,
    CONF_PRESS_GAP: 2,
    CONF_PRESS_RETRIES: 2,
    CONF_ON_SETTING: 4,
    CONF_REARM_EARLY: False,
    CONF_END_TOLERANCE: 10,
    CONF_MAX_DURATION: 0,
    CONF_MIN_SWITCH_INTERVAL: 5,
    CONF_STEP: 60,
    CONF_STEPS: 4,
    CONF_PRESS_SERVICE: "turn_on",
}
