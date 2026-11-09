"""Maintenance summaries and dynamically added material stock sensors."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PlannerConfigEntry
from .entity import PlannerEntity
from .manager import PlannerManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlannerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create summary sensors and discover newly registered materials."""
    manager = entry.runtime_data
    async_add_entities(
        [
            PlannerSummary(manager, "due_tasks", "mdi:clipboard-alert-outline"),
            PlannerSummary(manager, "next_due", "mdi:calendar-wrench"),
            PlannerSummary(manager, "missing_materials", "mdi:package-variant-closed"),
        ]
    )
    known: set[str] = set()

    @callback
    def add_materials() -> None:
        new_ids = manager.data.materials.keys() - known
        if new_ids:
            known.update(new_ids)
            async_add_entities([MaterialStock(manager, key) for key in sorted(new_ids)])

    add_materials()
    entry.async_on_unload(manager.async_subscribe(add_materials))


class PlannerSummary(PlannerEntity, SensorEntity):
    """A compact automation target for due tasks or stock shortages."""

    def __init__(self, manager: PlannerManager, key: str, icon: str) -> None:
        super().__init__(manager, key)
        self._key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        if key == "next_due":
            self._attr_device_class = SensorDeviceClass.DATE

    @property
    def native_value(self) -> int | date | None:
        if self._key == "due_tasks":
            return len(self.manager.due_tasks())
        if self._key == "next_due":
            return self.manager.next_due_date()
        return len(self.manager.shopping_needs())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._key == "due_tasks":
            return {
                "tasks": [
                    {
                        "id": task["id"],
                        "name": task["name"],
                        "device_id": task["device_id"],
                        "device": self.manager.device_name(task["device_id"])
                        if task["device_id"]
                        else None,
                        "due_date": task["due_date"],
                        "triggered": task["triggered"],
                        "source_entity": task["trigger"]["entity_id"]
                        if task["trigger"]
                        else None,
                    }
                    for task in self.manager.due_tasks()
                ]
            }
        if self._key == "missing_materials":
            return {
                "horizon_days": self.manager.horizon_days,
                "materials": [
                    {
                        "id": key,
                        "name": self.manager.data.materials[key]["name"],
                        "quantity": quantity,
                        "unit": self.manager.data.materials[key]["unit"],
                    }
                    for key, quantity in self.manager.shopping_needs().items()
                ],
            }
        return {}


class MaterialStock(PlannerEntity, SensorEntity):
    """The current quantity of a material, in its chosen unit."""

    _attr_translation_key = "stock"
    _attr_icon = "mdi:package-variant"

    def __init__(self, manager: PlannerManager, material_id: str) -> None:
        super().__init__(manager, f"stock_{material_id}")
        self._material_id = material_id
        self._last_name = manager.data.materials[material_id]["name"]

    @property
    def available(self) -> bool:
        return self._material_id in self.manager.data.materials

    @property
    def name(self) -> str:
        material = self.manager.data.materials.get(self._material_id)
        if material is not None:
            self._last_name = material["name"]
        return self._last_name

    @property
    def native_value(self) -> float | None:
        material = self.manager.data.materials.get(self._material_id)
        return material["stock"] if material else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        material = self.manager.data.materials.get(self._material_id)
        return (material["unit"] or None) if material else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        material = self.manager.data.materials.get(self._material_id)
        if material is None:
            return {}
        device_id = material["device_id"]
        return {
            "material_id": self._material_id,
            "device_id": device_id,
            "device": self.manager.device_name(device_id) if device_id else None,
            "purchase_url": material["purchase_url"],
            "minimum_stock": material["min_stock"],
            "purchase_quantity": self.manager.shopping_needs().get(
                self._material_id, 0
            ),
            "notes": material["notes"],
        }
