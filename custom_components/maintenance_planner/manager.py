"""Durable, serialized access to the local maintenance inventory."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HORIZON_DAYS,
    DEFAULT_HORIZON_DAYS,
    EVENT_COMPLETED,
)
from .models import PlannerData, PlannerError
from .storage import PlannerStore

if TYPE_CHECKING:
    from .triggers import PlannerTriggers

_T = TypeVar("_T")


class PlannerManager:
    """Save a complete transaction before publishing it to entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.data = PlannerData()
        self.store = PlannerStore(hass, entry.entry_id)
        self._lock = asyncio.Lock()
        self._listeners: set[Callable[[], None]] = set()
        self.triggers: PlannerTriggers | None = None

    @property
    def horizon_days(self) -> int:
        """Return the purchasing lookahead in local calendar days."""
        return self.entry.options.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS)

    async def async_load(self) -> None:
        """Load and validate persisted data without replacing damaged data."""
        stored = await self.store.async_load()
        if stored is not None:
            self.data = PlannerData.from_dict(stored)

    @callback
    def async_subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to successful writes and date changes."""
        self._listeners.add(listener)

        @callback
        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    @callback
    def async_notify(self, *_: Any) -> None:
        """Refresh all entities, including when the local date rolls over."""
        for listener in tuple(self._listeners):
            listener()

    async def _async_mutate(self, operation: Callable[[PlannerData], _T]) -> _T:
        async with self._lock:
            snapshot = self.data.to_dict()
            candidate = PlannerData.from_dict(snapshot)
            result = operation(candidate)
            payload = candidate.to_dict()
            if payload == snapshot:
                return result

            async def save_and_publish() -> None:
                """Keep disk and published state aligned if a caller disconnects."""
                try:
                    await self.store.async_save(payload)
                except OSError as err:
                    raise HomeAssistantError("Could not save maintenance data") from err
                self.data = candidate
                self.async_notify()

            write = self.hass.async_create_task(save_and_publish())
            was_cancelled = False
            while True:
                try:
                    await asyncio.shield(write)
                    break
                except asyncio.CancelledError:
                    if write.cancelled():
                        raise
                    # Executor writes cannot be cancelled once started. Even
                    # repeated caller cancellation must not release the lock
                    # before disk and published state agree.
                    was_cancelled = True
            if was_cancelled:
                raise asyncio.CancelledError
            return result

    async def async_upsert_material(
        self, material_id: str | None = None, **fields: Any
    ) -> str:
        """Create or update a material."""
        return await self._async_mutate(
            lambda data: data.upsert_material(material_id, **fields)
        )

    async def async_remove_material(self, material_id: str) -> None:
        """Remove an unreferenced material."""
        await self._async_mutate(lambda data: data.remove_material(material_id))

    async def async_adjust_stock(self, material_id: str, quantity: float) -> None:
        """Add received stock or apply an inventory correction."""
        await self._async_mutate(lambda data: data.adjust_stock(material_id, quantity))

    async def async_upsert_task(self, task_id: str | None = None, **fields: Any) -> str:
        """Create or update a maintenance task."""
        return await self._async_mutate(
            lambda data: data.upsert_task(task_id, **fields)
        )

    async def async_remove_tasks(self, task_ids: list[str]) -> None:
        """Remove tasks in a single transaction."""
        await self._async_mutate(lambda data: data.remove_tasks(task_ids))

    async def async_set_trigger_state(
        self, task_id: str, active: bool, expected_trigger: dict[str, Any]
    ) -> bool:
        """Apply an observed source condition only if its rule is still current."""

        def update(data: PlannerData) -> bool:
            task = data.tasks.get(task_id)
            if task is None or task["trigger"] != expected_trigger:
                return False
            return data.set_trigger_state(task_id, active, dt_util.now().date())

        return await self._async_mutate(update)

    async def async_reopen_task(self, task_id: str) -> None:
        """Reopen a completed one-off without reversing consumed materials."""
        await self._async_mutate(lambda data: data.reopen_task(task_id))

    async def async_complete_task(
        self,
        task_id: str,
        expected_due: str | None = None,
        expected_generation: int | None = None,
    ) -> None:
        """Complete one occurrence, optionally rejecting a stale action."""
        await self.async_update_todo(
            task_id,
            complete=True,
            expected_generation=expected_generation,
            expected_due=expected_due,
        )

    async def async_update_todo(
        self,
        task_id: str,
        *,
        complete: bool,
        expected_generation: int | None = None,
        expected_due: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Apply an entire to-do update atomically, with duplicate protection."""
        completed_event: dict[str, Any] | None = None

        def update(data: PlannerData) -> None:
            nonlocal completed_event
            if task_id not in data.tasks:
                raise PlannerError("Unknown maintenance task")
            task = data.tasks[task_id]
            if (
                expected_generation is not None
                and task["generation"] != expected_generation
            ) or (expected_due is not None and task["due_date"] != expected_due):
                if complete:
                    return  # The same occurrence was already completed.
                raise PlannerError("The maintenance task has changed; refresh the list")
            if fields:
                data.upsert_task(task_id, **fields)
            if complete:
                old_generation = data.tasks[task_id]["generation"]
                data.complete_task(task_id, dt_util.now().date())
                if data.tasks[task_id]["generation"] != old_generation:
                    completed_event = {
                        "task_id": task_id,
                        "name": data.tasks[task_id]["name"],
                        "completed_on": data.tasks[task_id]["last_completed"],
                        "next_due": data.tasks[task_id]["due_date"],
                    }
            elif data.tasks[task_id]["completed"]:
                data.reopen_task(task_id)

        await self._async_mutate(update)
        if completed_event is not None:
            self.hass.bus.async_fire(EVENT_COMPLETED, completed_event)

    def shopping_needs(self) -> dict[str, float]:
        """Compute shortages for the configured purchasing horizon."""
        return self.data.shopping_needs(dt_util.now().date(), self.horizon_days)

    def due_tasks(self) -> list[dict[str, Any]]:
        """Return tasks due on or before today's local date."""
        return self.data.due_tasks(dt_util.now().date())

    def next_due_date(self) -> date | None:
        """Return the oldest open due date, including overdue maintenance."""
        return self.data.next_due_date()
