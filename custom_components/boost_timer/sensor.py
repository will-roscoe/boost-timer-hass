"""Timestamp sensors: when the physical timer runs out, and when the active request ends."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoostTimerConfigEntry
from .entity import BoostTimerEntity
from .planner import Kind


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoostTimerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the expiry and request-end sensors."""
    c = entry.runtime_data
    async_add_entities([ExpiresAtSensor(c, "expires_at"), HeatingUntilSensor(c, "heating_until")])


class ExpiresAtSensor(BoostTimerEntity, SensorEntity):
    """When the timer reaches off; unknown while it is off."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        c = self.controller
        return c.model.expires_at if c.is_heating(dt_util.utcnow()) else None


class HeatingUntilSensor(BoostTimerEntity, SensorEntity):
    """When the active heat-until request ends; unknown for "on" without a limit, or no heat."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        r = self.controller.request
        return r.until if r is not None and r.kind is Kind.HEAT else None
