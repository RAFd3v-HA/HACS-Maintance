"""Shared push entity behavior."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .manager import PlannerManager


class PlannerEntity(Entity):
    """An entity backed by the local planner store."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: PlannerManager, key: str) -> None:
        self.manager = manager
        self._attr_unique_id = f"{manager.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name=manager.entry.title,
            manufacturer="Maintenance Planner",
            model="Local maintenance planner",
        )

    async def async_added_to_hass(self) -> None:
        """Update the entity after changes; remove the listener on unload."""
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.async_subscribe(self.async_write_ha_state))
