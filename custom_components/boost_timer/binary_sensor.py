"""Binary sensors: whether the radiator is powered, and whether the model may be wrong."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoostTimerConfigEntry
from .entity import BoostTimerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoostTimerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the heating and uncertain sensors."""
    c = entry.runtime_data
    async_add_entities([HeatingBinarySensor(c, "heating"), UncertainBinarySensor(c, "uncertain")])


class HeatingBinarySensor(BoostTimerEntity, BinarySensorEntity):
    """On while the model says the timer is running."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool:
        return self.controller.is_heating(dt_util.utcnow())


class UncertainBinarySensor(BoostTimerEntity, BinarySensorEntity):
    """On when the model may not match the timer (a failed or slow press); fix with ``sync``."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        return self.controller.model.uncertain
