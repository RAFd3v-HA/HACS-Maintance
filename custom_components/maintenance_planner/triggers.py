"""Turn validated Home Assistant entity conditions into due maintenance."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from math import isfinite
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event

from .manager import PlannerManager

_LOGGER = logging.getLogger(__name__)
_UNAVAILABLE = {STATE_UNKNOWN, STATE_UNAVAILABLE}


def _condition(rule: dict[str, Any], state: State | None) -> bool | None:
    """Return None for unavailable observations so the saved latch stays armed."""
    if state is None or state.state in _UNAVAILABLE:
        return None
    if attribute := rule["attribute"]:
        if attribute not in state.attributes:
            return None
        observed = state.attributes[attribute]
    else:
        observed = state.state
    if observed is None or (isinstance(observed, str) and observed in _UNAVAILABLE):
        return None
    if rule["operator"] == "equals":
        text = str(observed).lower() if isinstance(observed, bool) else str(observed)
        return text == rule["value"]
    try:
        value = float(observed)
        expected = float(rule["value"])
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(value) or not isfinite(expected):
        return None
    if rule["operator"] == "above":
        return value > expected
    return value < expected


class PlannerTriggers:
    """Watch only configured sources; keep transition latches in persisted data."""

    def __init__(self, hass: HomeAssistant, manager: PlannerManager) -> None:
        self.hass = hass
        self.manager = manager
        self._running = False
        self._rules: dict[str, dict[str, Any]] = {}
        self._watched: frozenset[str] = frozenset()
        self._unsub_states = None
        self._unsub_manager = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def async_start(self) -> None:
        """Subscribe before checking existing sources, including startup alarms."""
        if self._running:
            return
        self._running = True
        self._unsub_manager = self.manager.async_subscribe(self._planner_updated)
        changed = self._refresh_rules()
        if changed:
            await self._schedule(self._evaluate_current(changed))

    async def async_stop(self) -> None:
        """Detach listeners and finish already-started inventory transactions."""
        self._running = False
        if self._unsub_manager is not None:
            self._unsub_manager()
            self._unsub_manager = None
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        self._rules = {}
        self._watched = frozenset()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    @callback
    def _schedule(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = self.manager.entry.async_create_task(
            self.hass, work, "Evaluate maintenance conditions"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _refresh_rules(self) -> list[tuple[str, dict[str, Any]]]:
        rules = {
            task_id: dict(task["trigger"])
            for task_id, task in self.manager.data.tasks.items()
            if task["trigger"] is not None
        }
        changed = [
            (task_id, rule)
            for task_id, rule in rules.items()
            if self._rules.get(task_id) != rule
        ]
        # Publish signatures before evaluation: saving a transition notifies us.
        self._rules = rules
        watched = frozenset(rule["entity_id"] for rule in rules.values())
        if watched != self._watched:
            if self._unsub_states is not None:
                self._unsub_states()
                self._unsub_states = None
            self._watched = watched
            if watched:
                self._unsub_states = async_track_state_change_event(
                    self.hass, watched, self._state_changed
                )
        return changed

    @callback
    def _planner_updated(self) -> None:
        if self._running and (changed := self._refresh_rules()):
            self._schedule(self._evaluate_current(changed))

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._running:
            return
        entity_id = event.data["entity_id"]
        rules = [
            (task_id, rule)
            for task_id, rule in self._rules.items()
            if rule["entity_id"] == entity_id
        ]
        if rules:
            # Evaluate this event's snapshot so queued off->on edges retain order.
            self._schedule(self._evaluate(rules, event.data["new_state"]))

    async def _evaluate_current(self, rules: list[tuple[str, dict[str, Any]]]) -> None:
        for task_id, rule in rules:
            await self._evaluate(
                [(task_id, rule)], self.hass.states.get(rule["entity_id"])
            )

    async def _evaluate(
        self, rules: list[tuple[str, dict[str, Any]]], state: State | None
    ) -> None:
        for task_id, rule in rules:
            if not self._running:
                return
            active = _condition(rule, state)
            if active is None:
                continue
            try:
                await self.manager.async_set_trigger_state(
                    task_id, active, expected_trigger=rule
                )
            except Exception:
                # A failed save must leave the persisted latch unchanged. A later
                # valid source update can retry it; never leave an unhandled task.
                _LOGGER.exception(
                    "Could not update maintenance condition for task %s", task_id
                )
