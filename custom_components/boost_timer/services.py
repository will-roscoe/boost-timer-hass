"""Actions: ``boost_timer.heat_for`` (heat for a duration or until a time) and
``boost_timer.sync`` (tell the model what the LEDs show, without pressing)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import DOMAIN
from .controller import BoostTimerController
from .model import SettingObs
from .planner import Request

ATTR_ENTRY = "config_entry_id"

HEAT_FOR_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_ENTRY): cv.string,
            vol.Exclusive("duration", "end"): cv.positive_time_period,
            vol.Exclusive("until", "end"): cv.datetime,
        }
    ),
    cv.has_at_least_one_key("duration", "until"),
)

SYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required("setting"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("remaining"): cv.positive_time_period,
    }
)


def _controller(hass: HomeAssistant, call: ServiceCall) -> BoostTimerController:
    """Return the targeted timer; ``config_entry_id`` may be omitted when only one is loaded."""
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted is not None:
        entries = [e for e in entries if e.entry_id == wanted]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": wanted},
            )
    elif len(entries) != 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0].runtime_data


def _as_utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(t)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's actions (once, for all entries)."""

    async def heat_for(call: ServiceCall) -> None:
        controller = _controller(hass, call)
        now = dt_util.utcnow()
        until = (
            now + call.data["duration"] if "duration" in call.data else _as_utc(call.data["until"])
        )
        await controller.submit(Request.heat_until(until, now, source="service"))

    async def sync(call: ServiceCall) -> None:
        controller = _controller(hass, call)
        setting = call.data["setting"]
        if setting > controller.options.steps:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="setting_out_of_range",
                translation_placeholders={"steps": str(controller.options.steps)},
            )
        await controller.observe(SettingObs(setting, call.data.get("remaining")), source="sync")

    hass.services.async_register(DOMAIN, "heat_for", heat_for, schema=HEAT_FOR_SCHEMA)
    hass.services.async_register(DOMAIN, "sync", sync, schema=SYNC_SCHEMA)
