"""Config flow for Boost Timer."""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class BoostTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a boost timer driven by a SwitchBot Bot."""

    VERSION = 1
