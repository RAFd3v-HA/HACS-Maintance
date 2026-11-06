"""Atomic inventory persistence that reports unsuccessful writes to callers.

Home Assistant's Store intentionally logs and suppresses some write errors.
Inventory transactions need the error to reach the caller, so this adapter uses
the public JSON helpers directly and retains the usual .storage envelope.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.json import save_json
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError, load_json

from .const import DOMAIN, STORAGE_VERSION


class PlannerStore:
    """Read and atomically replace a complete planner transaction."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.key = f"{DOMAIN}.{entry_id}"
        self.path = hass.config.path(".storage", self.key)
        self._lock = asyncio.Lock()

    async def async_load(self) -> dict[str, Any] | None:
        """Read from disk, preserving damaged or unsupported files unchanged."""
        async with self._lock:
            envelope = await self.hass.async_add_executor_job(self._load)
        if envelope is None:
            return None
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("version")) is not int
            or envelope["version"] != STORAGE_VERSION
            or envelope.get("minor_version", 1) != 1
            or envelope.get("key") != self.key
            or not isinstance(envelope.get("data"), dict)
        ):
            raise HomeAssistantError(
                "The maintenance data file is invalid or uses an unsupported "
                "storage version. Restore a backup before continuing."
            )
        return envelope["data"]

    def _load(self) -> Any:
        """Distinguish a missing file from an existing file containing JSON null."""
        try:
            Path(self.path).stat()
        except FileNotFoundError:
            return None
        envelope = load_json(self.path, default=None)
        if envelope is None:
            raise HomeAssistantError(
                "The maintenance data file has no storage envelope"
            )
        return envelope

    async def async_save(self, data: dict[str, Any]) -> None:
        """Finish the atomic write before acknowledging a transaction."""
        envelope = {
            "version": STORAGE_VERSION,
            "minor_version": 1,
            "key": self.key,
            "data": deepcopy(data),
        }
        async with self._lock:
            try:
                await self.hass.async_add_executor_job(self._save, envelope)
            except (OSError, SerializationError, WriteError) as err:
                raise HomeAssistantError("Could not save maintenance data") from err

    def _save(self, envelope: dict[str, Any]) -> None:
        """Run serialization and filesystem operations in the executor."""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        save_json(self.path, envelope, private=True, atomic_writes=True)

    async def async_remove(self) -> None:
        """Remove this planner's data after the config entry is removed."""
        async with self._lock:
            await self.hass.async_add_executor_job(
                partial(Path(self.path).unlink, missing_ok=True)
            )
