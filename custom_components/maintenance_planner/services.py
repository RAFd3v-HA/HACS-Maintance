"""Automation actions for the singleton planner."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .manager import PlannerManager
from .models import InsufficientStockError, PlannerError

_MATERIAL_FIELDS = {
    vol.Optional("material_id"): cv.string,
    vol.Required("name"): cv.string,
    vol.Optional("stock"): vol.Coerce(float),
    vol.Optional("min_stock"): vol.Coerce(float),
    vol.Optional("unit"): cv.string,
    vol.Optional("notes"): cv.string,
}
_TASK_FIELDS = {
    vol.Optional("task_id"): cv.string,
    vol.Required("name"): cv.string,
    vol.Optional("due_date"): vol.Any(None, cv.string),
    vol.Optional("interval_days"): cv.positive_int,
    vol.Optional("interval_weeks"): cv.positive_int,
    vol.Optional("interval_months"): cv.positive_int,
    vol.Optional("trigger"): vol.Any(
        None,
        vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Optional("attribute"): vol.Any(None, cv.string),
                vol.Required("operator"): vol.In(["equals", "above", "below"]),
                vol.Required("value"): cv.string,
            }
        ),
    ),
    vol.Optional("requirements"): {cv.string: vol.Coerce(float)},
    vol.Optional("description"): cv.string,
}
SCHEMAS = {
    "upsert_material": vol.Schema(_MATERIAL_FIELDS),
    "remove_material": vol.Schema({vol.Required("material_id"): cv.string}),
    "adjust_stock": vol.Schema(
        {
            vol.Required("material_id"): cv.string,
            vol.Required("quantity"): vol.Coerce(float),
        }
    ),
    "upsert_task": vol.Schema(_TASK_FIELDS),
    "remove_task": vol.Schema({vol.Required("task_id"): cv.string}),
    "complete_task": vol.Schema(
        {
            vol.Required("task_id"): cv.string,
            vol.Optional("expected_generation"): cv.positive_int,
        }
    ),
    "reopen_task": vol.Schema({vol.Required("task_id"): cv.string}),
    "get_plan": vol.Schema({}),
}


def _manager(hass: HomeAssistant) -> PlannerManager:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries or entries[0].state != ConfigEntryState.LOADED:
        raise HomeAssistantError("Maintenance Planner is not loaded")
    return entries[0].runtime_data


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register actions once, independently of the config entry lifecycle."""

    async def handle(call: ServiceCall) -> dict[str, Any] | None:
        manager = _manager(hass)
        data = dict(call.data)
        result: dict[str, Any] = {}
        try:
            if call.service == "upsert_material":
                result = {"material_id": await manager.async_upsert_material(**data)}
            elif call.service == "remove_material":
                await manager.async_remove_material(data["material_id"])
            elif call.service == "adjust_stock":
                await manager.async_adjust_stock(**data)
            elif call.service == "upsert_task":
                result = {"task_id": await manager.async_upsert_task(**data)}
            elif call.service == "remove_task":
                await manager.async_remove_tasks([data["task_id"]])
            elif call.service == "complete_task":
                await manager.async_complete_task(**data)
            elif call.service == "reopen_task":
                await manager.async_reopen_task(data["task_id"])
            elif call.service == "get_plan":
                result = {
                    **manager.data.to_dict(),
                    "shopping_needs": manager.shopping_needs(),
                    "horizon_days": manager.horizon_days,
                }
        except InsufficientStockError as err:
            missing = ", ".join(
                f"{manager.data.materials[key]['name']}: {quantity:g} {manager.data.materials[key]['unit']}".strip()
                for key, quantity in err.missing.items()
            )
            raise HomeAssistantError(f"Insufficient material stock: {missing}") from err
        except PlannerError as err:
            raise HomeAssistantError(str(err)) from err
        return result if call.return_response else None

    for name, schema in SCHEMAS.items():
        response = SupportsResponse.NONE
        if name in {"upsert_material", "upsert_task"}:
            response = SupportsResponse.OPTIONAL
        elif name == "get_plan":
            response = SupportsResponse.ONLY
        hass.services.async_register(
            DOMAIN, name, handle, schema=schema, supports_response=response
        )
