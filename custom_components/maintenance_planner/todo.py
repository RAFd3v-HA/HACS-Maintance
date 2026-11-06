"""Native maintenance tasks and a calculated materials shopping list."""

from __future__ import annotations

from datetime import date

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PlannerConfigEntry
from .entity import PlannerEntity
from .manager import PlannerManager
from .models import PlannerError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlannerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose standard Home Assistant to-do lists."""
    async_add_entities(
        [MaintenanceTodo(entry.runtime_data), ShoppingTodo(entry.runtime_data)]
    )


def _parse_uid(uid: str | None) -> tuple[str, int]:
    try:
        task_id, generation = (uid or "").rsplit(":", 1)
        return task_id, int(generation)
    except (ValueError, TypeError) as err:
        raise HomeAssistantError("Invalid maintenance item ID") from err


class MaintenanceTodo(PlannerEntity, TodoListEntity):
    """Complete a maintenance occurrence and consume assigned materials."""

    _attr_translation_key = "tasks"
    _attr_icon = "mdi:tools"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, manager: PlannerManager) -> None:
        super().__init__(manager, "tasks")

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=f"{task['id']}:{task['generation']}",
                summary=task["name"],
                status=(
                    TodoItemStatus.COMPLETED
                    if task["completed"]
                    else TodoItemStatus.NEEDS_ACTION
                ),
                due=date.fromisoformat(task["due_date"]) if task["due_date"] else None,
                description=task["description"] or None,
            )
            for task in sorted(
                self.manager.data.tasks.values(),
                key=lambda task: (
                    task["completed"],
                    task["due_date"] or "9999-12-31",
                    task["name"],
                ),
            )
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        try:
            await self.manager.async_upsert_task(
                name=item.summary,
                due_date=item.due.isoformat() if item.due else None,
                description=item.description or "",
            )
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_update_todo_item(self, item: TodoItem) -> None:
        task_id, generation = _parse_uid(item.uid)
        try:
            await self.manager.async_update_todo(
                task_id,
                complete=item.status == TodoItemStatus.COMPLETED,
                expected_generation=generation,
                fields={
                    "name": item.summary,
                    "due_date": item.due.isoformat() if item.due else None,
                    "description": item.description or "",
                },
            )
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        task_ids = [_parse_uid(uid)[0] for uid in uids]
        try:
            await self.manager.async_remove_tasks(task_ids)
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err


class ShoppingTodo(PlannerEntity, TodoListEntity):
    """Read-only shortages; replenish stock using options or an action."""

    _attr_translation_key = "shopping"
    _attr_icon = "mdi:cart-outline"
    _attr_supported_features = TodoListEntityFeature(0)

    def __init__(self, manager: PlannerManager) -> None:
        super().__init__(manager, "shopping")

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=material_id,
                summary=f"{self.manager.data.materials[material_id]['name']}: {quantity:g} {self.manager.data.materials[material_id]['unit']}".strip(),
                status=TodoItemStatus.NEEDS_ACTION,
            )
            for material_id, quantity in sorted(
                self.manager.shopping_needs().items(),
                key=lambda item: self.manager.data.materials[item[0]]["name"],
            )
        ]
