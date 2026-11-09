"""Deliver maintenance reminders for calendar windows and entity signals."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterable
from datetime import date, datetime
from math import isclose
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.const import ATTR_DOMAIN, ATTR_SERVICE, EVENT_SERVICE_REGISTERED
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .manager import PlannerManager
from .triggers import _condition

_LOGGER = logging.getLogger(__name__)
_NOTIFY_DOMAIN = "notify"
_MOBILE_PREFIX = "mobile_app_"
type _CalendarSchedule = tuple[str, int, int | None, bool, bool]


class PlannerReminders:
    """Watch reminder sources and persist delivery for each channel."""

    def __init__(self, hass: HomeAssistant, manager: PlannerManager) -> None:
        self.hass = hass
        self.manager = manager
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._ready = False
        self._pending: dict[tuple[str, bool], None] = {}
        self._worker: asyncio.Task[None] | None = None
        self._schedule_inputs: dict[
            str, tuple[_CalendarSchedule | None, str | None]
        ] = {}
        self._signal_rules: dict[str, dict[str, Any]] = {}
        self._unsub_states: CALLBACK_TYPE | None = None
        self._observations: deque[tuple[str, dict[str, Any], bool]] = deque()

    async def async_start(self) -> None:
        """Watch changes and wait for Home Assistant to finish startup."""
        if self._unsubscribers:
            return
        self._schedule_inputs = self._read_schedule()
        self._unsubscribers = [
            self.manager.async_subscribe(self._planner_updated),
            async_track_time_change(
                self.hass, self._daily_check, hour=9, minute=0, second=0
            ),
            self.hass.bus.async_listen(
                EVENT_SERVICE_REGISTERED, self._service_registered
            ),
        ]
        self._unsubscribers.append(async_at_started(self.hass, self._started))

    async def async_stop(self) -> None:
        """Detach listeners and finish recording any delivery already in progress."""
        self._ready = False
        self._pending.clear()
        self._observations.clear()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        self._signal_rules.clear()
        if self._worker is not None:
            await self._worker

    def _read_schedule(
        self,
    ) -> dict[str, tuple[_CalendarSchedule | None, str | None]]:
        schedule = {}
        for task_id, task in self.manager.data.tasks.items():
            calendar = None
            if (
                not task["completed"]
                and task["due_date"] is not None
                and (task["reminder_days"] is not None or task["due_reminder"])
            ):
                calendar = (
                    task["due_date"],
                    task["generation"],
                    task["reminder_days"],
                    task["due_reminder"],
                    task["due_reminder"] and task["triggered"],
                )
            if calendar is not None or task["reminder_trigger"] is not None:
                schedule[task_id] = (calendar, task["reminder_push"])
        return schedule

    @callback
    def _started(self, _hass: HomeAssistant) -> None:
        if self._unsubscribers:
            self._ready = True
            self._refresh_signal_rules()
            self._request_check()

    @callback
    def _daily_check(self, _now: datetime) -> None:
        self._request_check()

    @callback
    def _planner_updated(self) -> None:
        previous = self._schedule_inputs
        self._schedule_inputs = self._read_schedule()
        if self._ready:
            self._refresh_signal_rules()
            self._request_check(
                task_id
                for task_id, rule in self._schedule_inputs.items()
                if previous.get(task_id) != rule
            )

    @callback
    def _refresh_signal_rules(self) -> None:
        previous = self._signal_rules
        self._signal_rules = {
            task_id: dict(task["reminder_trigger"])
            for task_id, task in self.manager.data.tasks.items()
            if task["reminder_trigger"] is not None
        }
        entities = {rule["entity_id"] for rule in self._signal_rules.values()}
        if entities != {rule["entity_id"] for rule in previous.values()}:
            if self._unsub_states is not None:
                self._unsub_states()
                self._unsub_states = None
            if entities:
                self._unsub_states = async_track_state_change_event(
                    self.hass, entities, self._signal_state_changed
                )
        for task_id, rule in self._signal_rules.items():
            if previous.get(task_id) != rule:
                self._observe(task_id, rule, self.hass.states.get(rule["entity_id"]))

    @callback
    def _signal_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._ready:
            return
        for task_id, rule in self._signal_rules.items():
            if rule["entity_id"] == event.data["entity_id"]:
                self._observe(task_id, rule, event.data["new_state"])

    @callback
    def _observe(self, task_id: str, rule: dict[str, Any], state: State | None) -> None:
        if (active := _condition(rule, state)) is not None:
            self._observations.append((task_id, rule, active))
            self._ensure_worker()

    @callback
    def _service_registered(self, event: Event) -> None:
        if self._ready and event.data.get(ATTR_DOMAIN) == _NOTIFY_DOMAIN:
            service = event.data.get(ATTR_SERVICE)
            if service:
                self._request_check(
                    task_id
                    for task_id, (_, target) in self._schedule_inputs.items()
                    if target == service
                )

    @callback
    def _request_check(self, task_ids: Iterable[str] | None = None) -> None:
        if not self._ready:
            return
        for task_id in self._schedule_inputs if task_ids is None else task_ids:
            self._pending[task_id, False] = None
            self._pending[task_id, True] = None
        self._ensure_worker()

    @callback
    def _ensure_worker(self) -> None:
        if self._worker is None and (self._pending or self._observations):
            self._worker = self.manager.entry.async_create_task(
                self.hass,
                self._run_checks(),
                "Send maintenance reminders",
                eager_start=False,
            )

    async def _run_checks(self) -> None:
        """Apply ordered source edges and serialize all notification sends."""
        try:
            while self._ready:
                if self._observations:
                    task_id, rule, active = self._observations.popleft()
                    await self._async_apply_observation(task_id, rule, active)
                elif self._pending:
                    task_id, due = next(iter(self._pending))
                    del self._pending[task_id, due]
                    await self._async_remind(task_id, due=due)
                else:
                    return
        finally:
            self._worker = None

    async def _async_apply_observation(
        self, task_id: str, rule: dict[str, Any], active: bool
    ) -> None:
        try:
            changed = await self.manager.async_set_reminder_trigger_state(
                task_id, active, expected_trigger=rule
            )
        except Exception:
            _LOGGER.exception("Could not record a reminder signal for task %s", task_id)
            return
        if self._ready and active and changed:
            self._pending.pop((task_id, False), None)
            await self._async_remind(task_id, due=False)

    def _eligible_task(
        self, task_id: str, *, due: bool
    ) -> tuple[dict[str, Any], str] | None:
        task = self.manager.data.tasks.get(task_id)
        if task is None:
            return None
        if not due and task["reminder_trigger"] is not None:
            token = task["reminder_trigger_token"]
            return (task, token) if token else None
        due_date = task["due_date"]
        if task["completed"] or due_date is None:
            return None
        days_until_due = (date.fromisoformat(due_date) - dt_util.now().date()).days
        if due:
            return (
                (task, due_date)
                if task["due_reminder"] and days_until_due <= 0
                else None
            )
        if task["due_reminder"] and days_until_due <= 0:
            # Catch up with the due alert when both date notifications are enabled.
            return None
        days = task["reminder_days"]
        return (task, due_date) if days is not None and days_until_due <= days else None

    async def _async_remind(self, task_id: str, *, due: bool) -> None:
        if (eligible := self._eligible_task(task_id, due=due)) is None:
            return
        task, occurrence = eligible
        generation = task["generation"]
        signal = not due and task["reminder_trigger"] is not None
        local_marker = "due_reminded_for" if due else "reminded_for"
        push_marker = "due_pushed_for" if due else "pushed_for"
        if task[local_marker] != occurrence:
            await self._async_deliver(task_id, task, occurrence, due=due)

        # The task could have changed or been completed while recording local delivery.
        if (
            not self._ready
            or (eligible := self._eligible_task(task_id, due=due)) is None
        ):
            return
        task, current_occurrence = eligible
        if current_occurrence != occurrence or (
            not signal and task["generation"] != generation
        ):
            return
        service = task["reminder_push"]
        if (
            service
            and service.startswith(_MOBILE_PREFIX)
            and task[push_marker] != occurrence
            and self.hass.services.has_service(_NOTIFY_DOMAIN, service)
        ):
            await self._async_deliver(
                task_id, task, occurrence, due=due, service=service
            )

    async def _async_deliver(
        self,
        task_id: str,
        task: dict[str, Any],
        occurrence: str,
        *,
        due: bool,
        service: str | None = None,
    ) -> None:
        try:
            title, message = self._message(task, due=due)
            if service is None:
                category = "due" if due else "early"
                key = occurrence.replace(":", "_")
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title=title,
                    notification_id=f"{DOMAIN}_{task_id}_{category}_{key}",
                )
            else:
                await self.hass.services.async_call(
                    _NOTIFY_DOMAIN,
                    service,
                    {"title": title, "message": message},
                    blocking=True,
                )
            await self.manager.async_mark_reminder_sent(
                task_id,
                occurrence,
                task["generation"],
                push=service is not None,
                expected_push=service,
                due=due,
            )
        except Exception:
            _LOGGER.exception(
                "Could not send or record a maintenance reminder for task %s via %s",
                task_id,
                service or "Home Assistant notifications",
            )

    def _message(self, task: dict[str, Any], *, due: bool) -> tuple[str, str]:
        language = self.hass.config.language.lower().replace("_", "-")
        german = language.split("-", 1)[0] == "de"
        rule = task["reminder_trigger"] if not due else None
        lines = []
        if german:
            title = (
                f"Wartung fällig: {task['name']}"
                if due
                else f"Vorwarnung für {task['name']}"
            )
            labels = ("benötigt", "vorhanden", "fehlt")
        else:
            title = (
                f"Maintenance due: {task['name']}"
                if due
                else f"Early warning for {task['name']}"
            )
            labels = ("needed", "in stock", "missing")

        if rule:
            entity_id = rule["entity_id"]
            state = self.hass.states.get(entity_id)
            source = state.name if state is not None else entity_id
            lines.append(
                f"{source} hat die Vorwarnung ausgelöst."
                if german
                else f"{source} triggered an early warning."
            )
        if task["due_date"] and not (rule and task["completed"]):
            due_date = date.fromisoformat(task["due_date"])
            if german:
                date_label = "Fällig am" if due else "Geplanter Termin am"
                lines.append(f"{date_label} {due_date:%d.%m.%Y}.")
            else:
                date_label = "Due on" if due else "Scheduled for"
                lines.append(f"{date_label} {due_date.isoformat()}.")

        if task["requirements"]:
            lines.extend(("", "Material:" if german else "Materials:"))
            for material_id, quantity in task["requirements"].items():
                material = self.manager.data.materials[material_id]
                stock = material["stock"]
                missing = (
                    0
                    if isclose(stock, quantity, rel_tol=1e-12)
                    else max(0, quantity - stock)
                )
                unit = f" {material['unit']}" if material["unit"] else ""
                details = ", ".join(
                    f"{label} {value:g}{unit}"
                    for label, value in zip(
                        labels, (quantity, stock, missing), strict=True
                    )
                )
                lines.append(f"- {material['name']}: {details}")
        else:
            lines.extend(
                (
                    "",
                    "Kein Verbrauchsartikel hinterlegt."
                    if german
                    else "No materials assigned.",
                )
            )
        return title, "\n".join(lines)
