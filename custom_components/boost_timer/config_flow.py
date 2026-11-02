"""Config flow (name and SwitchBot entity) and options flow (every timing setting)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
import voluptuous as vol

from .const import (
    CONF_BOT,
    CONF_BURST_MARGIN,
    CONF_END_TOLERANCE,
    CONF_MAX_DURATION,
    CONF_MIN_SWITCH_INTERVAL,
    CONF_NAME,
    CONF_ON_SETTING,
    CONF_PRESS_GAP,
    CONF_PRESS_RETRIES,
    CONF_PRESS_SERVICE,
    CONF_REARM_EARLY,
    CONF_SELECTION_WINDOW,
    CONF_STEP,
    CONF_STEPS,
    DEFAULTS,
    DOMAIN,
    PRESS_SERVICES,
)


def _number(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


#: Options-flow fields: key → selector. Units match :data:`.const.DEFAULTS`.
OPTION_FIELDS = {
    CONF_ON_SETTING: _number(1, 12),
    CONF_MAX_DURATION: _number(0, 1440, unit="min"),
    CONF_END_TOLERANCE: _number(0, 60, unit="min"),
    CONF_MIN_SWITCH_INTERVAL: _number(0, 60, unit="min"),
    CONF_REARM_EARLY: BooleanSelector(),
    CONF_SELECTION_WINDOW: _number(1, 30, 0.5, "s"),
    CONF_BURST_MARGIN: _number(0, 5, 0.5, "s"),
    CONF_PRESS_GAP: _number(0, 10, 0.5, "s"),
    CONF_PRESS_RETRIES: _number(1, 10),
    CONF_PRESS_SERVICE: SelectSelector(
        SelectSelectorConfig(options=PRESS_SERVICES, translation_key=CONF_PRESS_SERVICE)
    ),
    CONF_STEP: _number(1, 240, unit="min"),
    CONF_STEPS: _number(1, 12),
}


class BoostTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a boost timer driven by a SwitchBot Bot (``switch`` in press mode, or ``button``)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_BOT])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Radiator"): TextSelector(),
                vol.Required(CONF_BOT): EntitySelector(
                    EntitySelectorConfig(domain=["switch", "button"])
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BoostTimerOptionsFlow:
        return BoostTimerOptionsFlow()


class BoostTimerOptionsFlow(OptionsFlowWithReload):
    """Edit every timing setting; saving reloads the timer."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_ON_SETTING] > user_input[CONF_STEPS]:
                errors[CONF_ON_SETTING] = "on_setting_above_steps"
            elif user_input[CONF_PRESS_GAP] >= (
                user_input[CONF_SELECTION_WINDOW] - user_input[CONF_BURST_MARGIN]
            ):
                errors[CONF_PRESS_GAP] = "press_gap_too_long"
            else:
                return self.async_create_entry(data=user_input)
        current = {**DEFAULTS, **self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Optional(key, default=current[key]): selector
                for key, selector in OPTION_FIELDS.items()
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
