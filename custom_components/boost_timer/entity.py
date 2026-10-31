"""Shared base for Boost Timer entities: one device per timer, refreshed by the controller."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_NAME, DOMAIN
from .controller import BoostTimerController


class BoostTimerEntity(Entity):
    """An entity of one boost timer; its state is read from the controller on every change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: BoostTimerController, key: str) -> None:
        """
        :param key: the translation key, also the unique-id suffix (``<entry_id>_<key>``).
        """
        entry = controller.entry
        self.controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            model="Boost timer (SwitchBot)",
        )

    async def async_added_to_hass(self) -> None:
        """Rewrite the state whenever the controller changes anything."""
        self.async_on_remove(self.controller.add_listener(self.async_write_ha_state))
