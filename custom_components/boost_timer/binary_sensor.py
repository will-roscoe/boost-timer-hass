"""Binary sensors: whether the radiator is powered (by the model, and by the radiator sensor when
radiator sync is on), and whether the model may be wrong."""

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
    entities: list[BinarySensorEntity] = [
        HeatingBinarySensor(c, "heating"),
        UncertainBinarySensor(c, "uncertain"),
    ]
    if c.sync is not None:
        entities.append(VerifiedHeatingBinarySensor(c, "verified_heating"))
    async_add_entities(entities)


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


class VerifiedHeatingBinarySensor(BoostTimerEntity, BinarySensorEntity):
    """Whether the radiator sensor shows heating; unknown while blind or undecided."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    async def async_added_to_hass(self) -> None:
        """Also rewrite the state whenever radiator sync changes anything."""
        await super().async_added_to_hass()
        self.async_on_remove(self.controller.sync.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        return self.controller.sync.state
