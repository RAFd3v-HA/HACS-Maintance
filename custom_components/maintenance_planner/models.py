"""Validated maintenance and inventory data without Home Assistant dependencies.

Dates are calendar dates in the user's Home Assistant time zone. Recurrences run
from the actual completion date. Shopping planning excludes undated tasks and
uses the larger of minimum stock and the total demand in the planning horizon.
"""

from __future__ import annotations

import re
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from math import isclose, isfinite
from typing import Any
from uuid import uuid4

MAX_HISTORY = 1000


class PlannerError(ValueError):
    """A planner operation or saved value is invalid."""


class MaterialInUseError(PlannerError):
    """A material cannot be deleted while a task references it."""


class InsufficientStockError(PlannerError):
    """A completion requires more material than is available."""

    def __init__(self, missing: dict[str, float]) -> None:
        self.missing = dict(missing)
        super().__init__("Insufficient material stock")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlannerError(f"{label} must be a nonempty string")
    return value


def _text(value: Any, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise PlannerError(f"{label} must be text")
    value = value.strip()
    if required and not value:
        raise PlannerError(f"{label} must not be empty")
    return value


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannerError(f"{label} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as err:
        raise PlannerError(f"{label} must be finite") from err
    if not isfinite(result) or result < 0 or (positive and result == 0):
        raise PlannerError(
            f"{label} must be finite and {'positive' if positive else 'nonnegative'}"
        )
    return result


def _integer(value: Any, label: str) -> int:
    number = _number(value, label)
    if not number.is_integer():
        raise PlannerError(f"{label} must be a whole number")
    return int(number)


def _iso_date(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        raise PlannerError(f"{label} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as err:
        raise PlannerError(f"{label} must be an ISO calendar date") from err
    if value != parsed.isoformat():
        raise PlannerError(f"{label} must be an ISO calendar date")
    return value


def _calendar_date(value: Any, label: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PlannerError(f"{label} must be a calendar date")
    return value


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _trigger(value: Any) -> dict[str, Any] | None:
    """Validate an entity condition and return its canonical saved shape."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() - {
        "entity_id",
        "attribute",
        "operator",
        "value",
    }:
        raise PlannerError(
            "Trigger must contain only entity_id, attribute, operator, and value"
        )
    entity_id = _text(value.get("entity_id"), "Trigger entity ID", required=True)
    if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id):
        raise PlannerError(
            "Trigger entity ID must use lowercase domain.object_id syntax"
        )
    attribute = value.get("attribute")
    if attribute is not None:
        attribute = _text(attribute, "Trigger attribute") or None
    operator = value.get("operator")
    if not isinstance(operator, str) or operator not in {"equals", "above", "below"}:
        raise PlannerError("Trigger operator must be equals, above, or below")
    expected = value.get("value")
    if not isinstance(expected, str):
        raise PlannerError("Trigger value must be text")
    if operator in {"above", "below"}:
        try:
            numeric = float(expected)
        except ValueError as err:
            raise PlannerError("Numeric trigger value must be a finite number") from err
        if not isfinite(numeric):
            raise PlannerError("Numeric trigger value must be a finite number")
    return {
        "entity_id": entity_id,
        "attribute": attribute,
        "operator": operator,
        "value": expected,
    }


class PlannerData:
    """In-memory planner with atomic validated operations and JSON snapshots."""

    def __init__(self) -> None:
        self.materials: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlannerData:
        """Restore a snapshot, rejecting corrupt or inconsistent saved data."""
        if not isinstance(data, dict):
            raise PlannerError("Planner data must be an object")
        materials = data.get("materials", {})
        tasks = data.get("tasks", {})
        history = data.get("history", [])
        if not isinstance(materials, dict) or not isinstance(tasks, dict):
            raise PlannerError("Materials and tasks must be objects keyed by ID")
        if not isinstance(history, list):
            raise PlannerError("History must be a list")
        result = cls()
        for material_id, material in materials.items():
            _identifier(material_id, "Material ID")
            if not isinstance(material, dict) or material.get("id") != material_id:
                raise PlannerError("Saved material ID does not match its key")
            fields = {key: value for key, value in material.items() if key != "id"}
            result.upsert_material(material_id, **fields)
        for task_id, task in tasks.items():
            _identifier(task_id, "Task ID")
            if not isinstance(task, dict) or task.get("id") != task_id:
                raise PlannerError("Saved task ID does not match its key")
            generation = _integer(task.get("generation", 0), "Task generation")
            trigger_state = {
                key: task.get(key, False) for key in ("trigger_active", "triggered")
            }
            if any(not isinstance(value, bool) for value in trigger_state.values()):
                raise PlannerError("Saved trigger state must use boolean values")
            fields = {
                key: value
                for key, value in task.items()
                if key not in {"id", "generation", "trigger_active", "triggered"}
            }
            result.upsert_task(task_id, **fields)
            result.tasks[task_id]["generation"] = generation
            result.tasks[task_id].update(trigger_state)
        for record in history:
            result._validate_history(record)
        result.history = deepcopy(history[-MAX_HISTORY:])
        return result

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible snapshot."""
        return deepcopy(
            {"materials": self.materials, "tasks": self.tasks, "history": self.history}
        )

    def upsert_material(self, material_id: str | None = None, **fields: Any) -> str:
        """Create or update material, committing only after complete validation."""
        material_id = (
            uuid4().hex
            if material_id is None
            else _identifier(material_id, "Material ID")
        )
        allowed = {"name", "stock", "min_stock", "unit", "notes"}
        if fields.keys() - allowed:
            raise PlannerError("Unknown material fields")
        material = {
            "id": material_id,
            "name": "",
            "stock": 0.0,
            "min_stock": 0.0,
            "unit": "",
            "notes": "",
            **self.materials.get(material_id, {}),
            **deepcopy(fields),
        }
        material["name"] = _text(material["name"], "Material name", required=True)
        material["stock"] = _number(material["stock"], "Stock")
        material["min_stock"] = _number(material["min_stock"], "Minimum stock")
        material["unit"] = _text(material["unit"], "Unit")
        material["notes"] = _text(material["notes"], "Notes")
        self.materials[material_id] = material
        return material_id

    def remove_material(self, material_id: str) -> None:
        """Delete an unreferenced material; completed-task references also count."""
        self._material(material_id)
        if any(material_id in task["requirements"] for task in self.tasks.values()):
            raise MaterialInUseError("Material is still assigned to a maintenance task")
        del self.materials[material_id]

    def adjust_stock(self, material_id: str, quantity: float) -> None:
        """Apply a signed stock adjustment without allowing a negative result."""
        material = self._material(material_id)
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise PlannerError("Stock adjustment must be a finite number")
        try:
            adjustment = float(quantity)
        except (ValueError, OverflowError) as err:
            raise PlannerError("Stock adjustment must be finite") from err
        if not isfinite(adjustment):
            raise PlannerError("Stock adjustment must be finite")
        new_stock = material["stock"] + adjustment
        if new_stock < 0 and not isclose(material["stock"], -adjustment, rel_tol=1e-12):
            raise PlannerError("Stock adjustment would make stock negative")
        self.upsert_material(material_id, stock=max(0.0, new_stock))

    def upsert_task(self, task_id: str | None = None, **fields: Any) -> str:
        """Create or edit a task with validated recurrence and material links."""
        task_id = uuid4().hex if task_id is None else _identifier(task_id, "Task ID")
        allowed = {
            "name",
            "due_date",
            "interval_days",
            "interval_weeks",
            "interval_months",
            "trigger",
            "requirements",
            "description",
            "completed",
            "last_completed",
        }
        if fields.keys() - allowed:
            raise PlannerError("Unknown maintenance fields")
        task = {
            "id": task_id,
            "name": "",
            "due_date": None,
            "interval_days": 0,
            "interval_weeks": 0,
            "interval_months": 0,
            "trigger": None,
            "trigger_active": False,
            "triggered": False,
            "requirements": {},
            "description": "",
            "completed": False,
            "last_completed": None,
            "generation": 0,
            **self.tasks.get(task_id, {}),
            **deepcopy(fields),
        }
        task["name"] = _text(task["name"], "Maintenance name", required=True)
        task["description"] = _text(task["description"], "Description")
        task["due_date"] = _iso_date(task["due_date"], "Due date")
        task["last_completed"] = _iso_date(task["last_completed"], "Last completion")
        task["interval_days"] = _integer(task["interval_days"], "Interval days")
        task["interval_weeks"] = _integer(task["interval_weeks"], "Interval weeks")
        task["interval_months"] = _integer(task["interval_months"], "Interval months")
        if (
            sum(
                bool(task[key])
                for key in ("interval_days", "interval_weeks", "interval_months")
            )
            > 1
        ):
            raise PlannerError(
                "Choose only one recurrence interval: days, weeks, or months"
            )
        task["trigger"] = _trigger(task["trigger"])
        previous = self.tasks.get(task_id)
        if previous is not None and previous["trigger"] != task["trigger"]:
            task["trigger_active"] = False
            task["triggered"] = False
        if not isinstance(task["completed"], bool):
            raise PlannerError("Completed must be a boolean")
        if not isinstance(task["requirements"], dict):
            raise PlannerError("Material requirements must be an object")
        requirements = {}
        for material_id, quantity in task["requirements"].items():
            self._material(material_id)
            requirements[material_id] = _number(
                quantity, "Material quantity", positive=True
            )
        task["requirements"] = requirements
        self.tasks[task_id] = task
        return task_id

    def remove_tasks(self, task_ids: list[str]) -> None:
        """Delete all selected tasks atomically, preserving completion history."""
        if not isinstance(task_ids, list):
            raise PlannerError("Task IDs must be a list")
        for task_id in task_ids:
            self._task(task_id)
        for task_id in set(task_ids):
            del self.tasks[task_id]

    def complete_task(self, task_id: str, completed_on: date) -> None:
        """Consume all required materials and record completion atomically.

        Repeated completion of a completed one-off task is a no-op. Recurring
        tasks stay pending for their next occurrence; callers must prevent
        duplicate completion requests for the same occurrence.
        """
        task = self._task(task_id)
        completed_on = _calendar_date(completed_on, "Completion date")
        if task["completed"]:
            return
        missing = {}
        for material_id, quantity in task["requirements"].items():
            stock = self._material(material_id)["stock"]
            if stock < quantity and not isclose(stock, quantity, rel_tol=1e-12):
                missing[material_id] = quantity - stock
        if missing:
            raise InsufficientStockError(missing)
        next_due = None
        try:
            if task["interval_days"] or task["interval_weeks"]:
                next_due = completed_on + timedelta(
                    days=task["interval_days"] + 7 * task["interval_weeks"]
                )
            elif task["interval_months"]:
                next_due = _add_months(completed_on, task["interval_months"])
        except (OverflowError, ValueError) as err:
            raise PlannerError(
                "Next maintenance date is outside the supported calendar"
            ) from err
        consumed = {
            material_id: {
                "name": self.materials[material_id]["name"],
                "quantity": quantity,
                "unit": self.materials[material_id]["unit"],
            }
            for material_id, quantity in task["requirements"].items()
        }
        for material_id, quantity in task["requirements"].items():
            self.materials[material_id]["stock"] = max(
                0.0, self.materials[material_id]["stock"] - quantity
            )
        task["last_completed"] = completed_on.isoformat()
        task["generation"] += 1
        task["completed"] = next_due is None
        task["triggered"] = False
        if next_due is not None:
            task["due_date"] = next_due.isoformat()
        self.history.append(
            {
                "task_id": task_id,
                "name": task["name"],
                "completed_on": completed_on.isoformat(),
                "consumed": consumed,
                "next_due_date": next_due.isoformat() if next_due is not None else None,
            }
        )
        self.history = self.history[-MAX_HISTORY:]

    def reopen_task(self, task_id: str) -> None:
        """Reopen a one-off task without undoing consumption or its history."""
        task = self._task(task_id)
        if task["interval_days"] or task["interval_weeks"] or task["interval_months"]:
            raise PlannerError(
                "Recurring tasks already represent their next occurrence"
            )
        if task["completed"]:
            task["completed"] = False
            task["generation"] += 1

    def set_trigger_state(self, task_id: str, active: bool, observed_on: date) -> bool:
        """Latch condition transitions and make a rising condition due today.

        A falling condition only rearms the trigger. Its due task remains open
        until explicitly completed. Completion acknowledges an active condition
        without rearming it, so a source that stays active cannot retrigger.
        """
        task = self._task(task_id)
        if not isinstance(active, bool):
            raise PlannerError("Trigger activity must be a boolean")
        observed_on = _calendar_date(observed_on, "Trigger observation date")
        if task["trigger"] is None:
            raise PlannerError("Maintenance task has no trigger condition")
        if task["trigger_active"] == active:
            return False
        task["trigger_active"] = active
        if active:
            task["triggered"] = True
            observed_date = observed_on.isoformat()
            task["due_date"] = (
                min(task["due_date"], observed_date)
                if task["due_date"]
                else observed_date
            )
            if task["completed"]:
                task["completed"] = False
                task["generation"] += 1
        return True

    def due_tasks(self, today: date) -> list[dict[str, Any]]:
        """Return detached pending tasks due today or earlier, ordered by date."""
        today = _calendar_date(today, "Today")
        tasks = [
            task
            for task in self.tasks.values()
            if not task["completed"]
            and task["due_date"] is not None
            and task["due_date"] <= today.isoformat()
        ]
        return deepcopy(
            sorted(tasks, key=lambda task: (task["due_date"], task["name"], task["id"]))
        )

    def shopping_needs(self, today: date, horizon_days: int = 30) -> dict[str, float]:
        """Return stock shortages for upcoming/overdue tasks and minimum stock.

        Shared material demand is summed once per pending task. Undated and
        completed tasks do not reserve stock. Minimum stock is a target rather
        than an additional reserve on top of the upcoming consumption.
        """
        today = _calendar_date(today, "Today")
        horizon_days = _integer(horizon_days, "Planning horizon")
        try:
            end = (today + timedelta(days=horizon_days)).isoformat()
        except (OverflowError, ValueError) as err:
            raise PlannerError(
                "Planning horizon is outside the supported calendar"
            ) from err
        demand: dict[str, float] = {}
        for task in self.tasks.values():
            if task["completed"] or task["due_date"] is None or task["due_date"] > end:
                continue
            for material_id, quantity in task["requirements"].items():
                demand[material_id] = demand.get(material_id, 0.0) + quantity
                if not isfinite(demand[material_id]):
                    raise PlannerError(
                        "Combined material demand exceeds the supported quantity"
                    )
        needs = {}
        for material_id, material in self.materials.items():
            target = max(material["min_stock"], demand.get(material_id, 0.0))
            shortfall = target - material["stock"]
            if shortfall > 0 and not isclose(target, material["stock"], rel_tol=1e-12):
                needs[material_id] = shortfall
        return needs

    def next_due_date(self) -> date | None:
        """Return the earliest scheduled pending task, including overdue tasks."""
        dates = [
            task["due_date"]
            for task in self.tasks.values()
            if not task["completed"] and task["due_date"] is not None
        ]
        return date.fromisoformat(min(dates)) if dates else None

    def _material(self, material_id: str) -> dict[str, Any]:
        _identifier(material_id, "Material ID")
        if material_id not in self.materials:
            raise PlannerError("Unknown material ID")
        return self.materials[material_id]

    def _task(self, task_id: str) -> dict[str, Any]:
        _identifier(task_id, "Task ID")
        if task_id not in self.tasks:
            raise PlannerError("Unknown maintenance ID")
        return self.tasks[task_id]

    @staticmethod
    def _validate_history(record: Any) -> None:
        """Validate snapshots independently of live task and material existence."""
        if not isinstance(record, dict):
            raise PlannerError("History entry must be an object")
        _identifier(record.get("task_id"), "History task ID")
        _text(record.get("name"), "History maintenance name", required=True)
        if _iso_date(record.get("completed_on"), "History completion date") is None:
            raise PlannerError("History entry is missing its completion date")
        _iso_date(record.get("next_due_date"), "History next due date")
        if not isinstance(record.get("consumed"), dict):
            raise PlannerError("History consumption must be an object")
        for material_id, snapshot in record["consumed"].items():
            _identifier(material_id, "History material ID")
            if not isinstance(snapshot, dict):
                raise PlannerError("History material snapshot must be an object")
            _text(snapshot.get("name"), "History material name", required=True)
            _number(snapshot.get("quantity"), "History quantity", positive=True)
            _text(snapshot.get("unit"), "History unit")
