"""Plan maintenance and manage materials locally in Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .manager import PlannerManager
from .models import PlannerError
from .reminders import PlannerReminders
from .services import async_register_services
from .triggers import PlannerTriggers

PLATFORMS = [Platform.SENSOR, Platform.TODO]
type PlannerConfigEntry = ConfigEntry[PlannerManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Keep actions available even when the entry is currently unloaded."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PlannerConfigEntry) -> bool:
    """Load local storage and forward entities."""
    manager = PlannerManager(hass, entry)
    try:
        await manager.async_load()
    except PlannerError as err:
        raise ConfigEntryError(f"Invalid maintenance data: {err}") from err
    except OSError as err:
        raise ConfigEntryNotReady("Cannot load maintenance data") from err
    except HomeAssistantError as err:
        raise ConfigEntryError(f"Cannot read maintenance data: {err}") from err
    entry.runtime_data = manager
    entry.async_on_unload(
        async_track_time_change(hass, manager.async_notify, hour=0, minute=0, second=1)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.triggers = PlannerTriggers(hass, manager)
    await manager.triggers.async_start()
    manager.reminders = PlannerReminders(hass, manager)
    await manager.reminders.async_start()
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: PlannerConfigEntry
) -> None:
    """Immediately recalculate shopping needs after a horizon change."""
    entry.runtime_data.async_notify()


async def async_unload_entry(hass: HomeAssistant, entry: PlannerConfigEntry) -> bool:
    """Unload all entities and listeners."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager = entry.runtime_data
        if manager.reminders is not None:
            await manager.reminders.async_stop()
        if manager.triggers is not None:
            await manager.triggers.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: PlannerConfigEntry) -> None:
    """Remove local planner data when the user deletes the integration."""
    await PlannerManager(hass, entry).store.async_remove()
