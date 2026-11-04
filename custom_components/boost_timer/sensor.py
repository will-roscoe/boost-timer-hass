"""Sensors: when the physical timer runs out, when the active request ends, and (with radiator
sync) how many corrections the radiator sensor made today."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
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
    entities: list[SensorEntity] = [
        ExpiresAtSensor(c, "expires_at"),
        HeatingUntilSensor(c, "heating_until"),
    ]
    if c.sync is not None:
        entities.append(SyncCorrectionsSensor(c, "sync_corrections"))
    async_add_entities(entities)


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


class SyncCorrectionsSensor(BoostTimerEntity, SensorEntity):
    """Corrections radiator sync made today; the last one, and the sync's health, as attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        """Also rewrite the state whenever radiator sync changes anything."""
        await super().async_added_to_hass()
        self.async_on_remove(self.controller.sync.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        return self.controller.sync.corrections_today

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sync = self.controller.sync
        return {
            "last_correction": sync.last_correction,
            "corrections_total": sync.corrections_total,
            "frozen": sync.frozen,
            "blind": sync.blind(dt_util.utcnow()),
        }
