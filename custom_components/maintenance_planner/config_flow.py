"""Configure a local maintenance planner through the Home Assistant UI."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    DeviceSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import DOMAIN
from .models import MaterialInUseError, PlannerError

_MAX_HORIZON_DAYS = 365
_MAX_INTERVAL_COUNT = 36500
_REMINDER_DAYS = {"off": None, "due": 0, "week": 7, "signal": None}


def _is_whole_number(value: float, maximum: int) -> bool:
    """Check submitted counts before converting them to integers."""
    return 0 <= value <= maximum and value % 1 == 0


def _number(
    minimum: float = 0, maximum: float = 1_000_000_000, *, integer: bool = False
) -> NumberSelector:
    """Expose a serializable selector for the numeric UI field."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1 if integer else "any",
            mode=NumberSelectorMode.BOX,
        )
    )


def _selection(
    records: dict[str, dict[str, Any]], *, show_units: bool = False
) -> SelectSelector:
    """Build a labelled selection while keeping stable IDs in the stored value."""
    labels = {
        record_id: f"{record['name']} ({record['unit']})"
        if show_units and record["unit"]
        else record["name"]
        for record_id, record in records.items()
    }
    names = list(labels.values())
    options = [
        {
            "value": record_id,
            "label": name if names.count(name) == 1 else f"{name} ({record_id[:6]})",
        }
        for record_id, name in labels.items()
    ]
    return SelectSelector(
        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
    )


def _consumable_requirements(
    rows: list[dict[str, Any]], materials: dict[str, dict[str, Any]]
) -> dict[str, float]:
    """Convert the form rows to the planner's material-to-quantity mapping."""
    requirements: dict[str, float] = {}
    for row in rows:
        material_id = row["material_id"]
        if material_id in requirements:
            raise PlannerError("duplicate_consumable")
        try:
            quantity = float(row["quantity"])
        except (ValueError, TypeError, OverflowError) as err:
            raise PlannerError("invalid_consumable") from err
        if (
            material_id not in materials
            or isinstance(row["quantity"], bool)
            or not 0 < quantity <= 1_000_000_000
        ):
            raise PlannerError("invalid_consumable")
        requirements[material_id] = quantity
    return requirements


class MaintenancePlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single local planner."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MaintenancePlannerOptionsFlow:
        """Offer planner management after setup."""
        return MaintenancePlannerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a local planner without cloud credentials."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input["name"].strip()
            horizon_days = user_input["horizon_days"]
            if not name:
                errors["name"] = "invalid_name"
            elif not _is_whole_number(horizon_days, _MAX_HORIZON_DAYS):
                errors["horizon_days"] = "invalid_horizon"
            else:
                return self.async_create_entry(
                    title=name,
                    data={"name": name},
                    options={"horizon_days": int(horizon_days)},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(
                            "name", default="Maintenance Planner"
                        ): TextSelector(),
                        vol.Required("horizon_days", default=30): _number(
                            0, _MAX_HORIZON_DAYS, integer=True
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )


class MaintenancePlannerOptionsFlow(OptionsFlow):
    """Manage tasks, materials, requirements and planner preferences."""

    def __init__(self) -> None:
        """Keep selected IDs for the multi-step editor."""
        self._task_id: str | None = None
        self._material_id: str | None = None
        self._delete_kind: str | None = None
        self._delete_id: str | None = None
        self._task_draft: dict[str, Any] | None = None
        self._signal_device_id: str | None = None
        self._use_due_signal = False
        self._use_reminder_signal = False

    @property
    def _manager(self) -> Any:
        return self.config_entry.runtime_data

    def _finish(self) -> ConfigFlowResult:
        """Keep saved preferences when a data edit finishes."""
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the planner's management menu."""
        if not hasattr(self.config_entry, "runtime_data"):
            return self.async_abort(reason="not_loaded")
        menu = ["settings", "add_task", "add_material"]
        if self._manager.data.tasks:
            menu.extend(["edit_task", "configure_trigger", "remove_task"])
        if self._manager.data.materials:
            menu.extend(["edit_material", "remove_material"])
        if self._manager.data.tasks and self._manager.data.materials:
            menu.append("assign_material")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Adjust the shopping planning horizon."""
        errors: dict[str, str] = {}
        if user_input is not None:
            horizon_days = user_input["horizon_days"]
            if _is_whole_number(horizon_days, _MAX_HORIZON_DAYS):
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        "horizon_days": int(horizon_days),
                    },
                )
            errors["horizon_days"] = "invalid_horizon"
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "horizon_days",
                        default=self.config_entry.options.get("horizon_days", 30),
                    ): _number(0, _MAX_HORIZON_DAYS, integer=True)
                }
            ),
            errors=errors,
        )

    async def async_step_add_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a task."""
        self._task_id = None
        return await self._task_form("add_task", user_input)

    async def async_step_edit_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a task to edit."""
        tasks = self._manager.data.tasks
        if not tasks:
            return self.async_abort(reason="no_tasks")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["task_id"] in tasks:
                self._task_id = user_input["task_id"]
                return await self.async_step_edit_task_details()
            errors["base"] = "not_found"
        return self.async_show_form(
            step_id="edit_task",
            data_schema=vol.Schema({vol.Required("task_id"): _selection(tasks)}),
            errors=errors,
        )

    async def async_step_edit_task_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the selected task."""
        return await self._task_form("edit_task_details", user_input)

    async def _task_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render and validate a task editor."""
        current = self._manager.data.tasks.get(self._task_id, {})
        if self._task_id is not None and not current:
            return self.async_abort(reason="not_found")
        adding = step_id == "add_task"
        materials = self._manager.data.materials
        errors: dict[str, str] = {}
        if user_input is not None:
            fields = dict(user_input)
            clear_due = fields.pop("clear_due_date", False)
            if clear_due:
                fields["due_date"] = None
            elif "due_date" not in fields and not current:
                fields["due_date"] = None
            interval_count = fields.pop("interval_count", 0)
            interval_unit = fields.pop("interval_unit", "days")
            reminder_mode = fields.pop("reminder_mode", None)
            reminder_push = fields.pop("reminder_push", None)
            if reminder_mode is not None:
                fields["reminder_days"] = _REMINDER_DAYS[reminder_mode]
                if reminder_mode != "signal":
                    fields["reminder_trigger"] = None
            if reminder_push is not None:
                fields["reminder_push"] = (
                    None if reminder_push == "local" else reminder_push
                )
            use_signal = fields.pop("use_signal", False)
            signal_device_id = fields.pop("signal_device_id", None)
            rows = fields.pop("consumables", [])
            if interval_unit not in ("days", "weeks", "months"):
                errors["interval_unit"] = "invalid_interval"
            elif not _is_whole_number(interval_count, _MAX_INTERVAL_COUNT):
                errors["interval_count"] = "invalid_interval_count"
            elif not fields["name"].strip():
                errors["name"] = "invalid_name"
            else:
                fields.update(interval_days=0, interval_weeks=0, interval_months=0)
                fields[f"interval_{interval_unit}"] = int(interval_count)
                if adding:
                    try:
                        fields["requirements"] = _consumable_requirements(
                            rows, materials
                        )
                    except PlannerError as err:
                        errors["consumables"] = str(err)
                if not errors and (
                    (adding and use_signal) or reminder_mode == "signal"
                ):
                    self._signal_device_id = signal_device_id
                    if self._signal_entities() == []:
                        errors["signal_device_id"] = "signal_device_has_no_entities"
                    else:
                        self._task_draft = fields
                        self._use_due_signal = adding and use_signal
                        self._use_reminder_signal = reminder_mode == "signal"
                        return await self.async_step_task_signals()
                if not errors:
                    errors = await self._save_task(fields)
                    if not errors:
                        return self._finish()
        interval_count = 0
        interval_unit = "days"
        for unit in ("days", "weeks", "months"):
            if count := current.get(f"interval_{unit}", 0):
                interval_count, interval_unit = count, unit
                break
        schema_fields = {
            vol.Required("name"): TextSelector(),
            vol.Optional("due_date"): DateSelector(),
            vol.Required("clear_due_date", default=False): BooleanSelector(),
            vol.Required("interval_count", default=interval_count): _number(
                0, _MAX_INTERVAL_COUNT, integer=True
            ),
            vol.Required("interval_unit", default=interval_unit): SelectSelector(
                SelectSelectorConfig(
                    options=["days", "weeks", "months"],
                    translation_key="interval_unit",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional("description", default=""): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
        }
        if adding:
            schema_fields[vol.Optional("use_signal", default=False)] = BooleanSelector()
            if materials:
                schema_fields[vol.Optional("consumables")] = ObjectSelector(
                    ObjectSelectorConfig(
                        multiple=True,
                        label_field="material_id",
                        description_field="quantity",
                        translation_key="task_consumables",
                        fields={
                            "material_id": {
                                "selector": _selection(materials, show_units=True),
                                "required": True,
                            },
                            "quantity": {"selector": _number(), "required": True},
                        },
                    )
                )
        schema_fields.update(self._reminder_fields(current))
        schema_fields[vol.Optional("signal_device_id")] = DeviceSelector()
        suggested = {key: value for key, value in current.items() if value is not None}
        suggested.update(interval_count=interval_count, interval_unit=interval_unit)
        suggested.update(user_input or {})
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema_fields), suggested
            ),
            errors=errors,
        )

    def _reminder_fields(self, task: dict[str, Any]) -> dict[Any, Any]:
        """Offer local reminders and the available Companion App push services."""
        mode = (
            "signal"
            if task.get("reminder_trigger")
            else next(
                key
                for key, days in _REMINDER_DAYS.items()
                if days == task.get("reminder_days")
            )
        )
        push_services = {
            name
            for name in self.hass.services.async_services().get("notify", {})
            if name.startswith("mobile_app_")
        }
        if push := task.get("reminder_push"):
            push_services.add(push)
        return {
            vol.Optional(
                "reminder_mode", description={"suggested_value": mode}
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(_REMINDER_DAYS), translation_key="task_reminder_mode"
                )
            ),
            vol.Optional(
                "reminder_push", description={"suggested_value": push or "local"}
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": "local", "label": "Home Assistant only"}]
                    + [
                        {
                            "value": name,
                            "label": name.removeprefix("mobile_app_").replace("_", " "),
                        }
                        for name in sorted(push_services)
                    ],
                    translation_key="task_reminder_push",
                )
            ),
        }

    async def _save_task(self, fields: dict[str, Any]) -> dict[str, str]:
        """Save a task and its assignments together."""
        try:
            await self._manager.async_upsert_task(self._task_id, **fields)
        except PlannerError:
            return {"base": "invalid_input"}
        except HomeAssistantError:
            return {"base": "storage_error"}
        return {}

    def _signal_entities(self) -> list[str] | None:
        """Return the enabled entities of the selected device, or allow all."""
        if not self._signal_device_id:
            return None
        return [
            entry.entity_id
            for entry in er.async_entries_for_device(
                er.async_get(self.hass), self._signal_device_id
            )
        ]

    async def async_step_task_signals(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose separate conditions for due maintenance and early reminders."""
        current = self._manager.data.tasks.get(self._task_id, {})
        if self._task_draft is None or (self._task_id is not None and not current):
            return self.async_abort(reason="not_found")
        entities = self._signal_entities()
        if entities == []:
            return self.async_abort(reason="signal_device_unavailable")
        selector = EntitySelector(
            EntitySelectorConfig(include_entities=entities)
            if entities is not None
            else None
        )
        signals = []
        if self._use_due_signal:
            signals.append(("trigger", "trigger_entity_id", "trigger_value"))
        if self._use_reminder_signal:
            signals.append(("reminder_trigger", "reminder_entity_id", "reminder_value"))

        schema_fields: dict[Any, Any] = {}
        suggested: dict[str, Any] = {}
        fields = dict(self._task_draft)
        errors: dict[str, str] = {}
        registry = er.async_get(self.hass)
        for model_key, entity_key, value_key in signals:
            rule = current.get(model_key) or {}
            schema_fields[vol.Required(entity_key)] = selector
            schema_fields[vol.Required(value_key, default=rule.get("value", "on"))] = (
                TextSelector()
            )
            if existing := rule.get("entity_id"):
                if entities is None or existing in entities:
                    suggested[entity_key] = existing
            if user_input is not None:
                entity_id = user_input[entity_key]
                if entry := registry.async_get(entity_id):
                    entity_id = entry.entity_id
                if entities is not None and entity_id not in entities:
                    errors[entity_key] = "signal_entity_unavailable"
                else:
                    fields[model_key] = {
                        "entity_id": entity_id,
                        "operator": "equals",
                        "value": user_input[value_key],
                    }
        if user_input is not None and not errors:
            errors = await self._save_task(fields)
            if not errors:
                return self._finish()
        suggested.update(user_input or {})
        return self.async_show_form(
            step_id="task_signals",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema_fields), suggested
            ),
            errors=errors,
        )

    async def async_step_configure_trigger(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which task should be made due by an entity condition."""
        tasks = self._manager.data.tasks
        if not tasks:
            return self.async_abort(reason="no_tasks")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["task_id"] in tasks:
                self._task_id = user_input["task_id"]
                return await self.async_step_trigger_details()
            errors["base"] = "not_found"
        return self.async_show_form(
            step_id="configure_trigger",
            data_schema=vol.Schema({vol.Required("task_id"): _selection(tasks)}),
            errors=errors,
        )

    async def async_step_trigger_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure an entity state/attribute rule, or disable it."""
        task = self._manager.data.tasks.get(self._task_id)
        if task is None:
            return self.async_abort(reason="not_found")
        current = task.get("trigger") or {}
        errors: dict[str, str] = {}
        if user_input is not None:
            trigger = None
            if user_input["trigger_enabled"]:
                entity_id = user_input.get("trigger_entity_id", "").strip()
                if not entity_id:
                    errors["trigger_entity_id"] = "entity_required"
                if "trigger_value" not in user_input:
                    errors["trigger_value"] = "value_required"
                trigger = {
                    "entity_id": entity_id,
                    "attribute": user_input.get("trigger_attribute", "").strip()
                    or None,
                    "operator": user_input.get("trigger_operator", "equals"),
                    "value": user_input.get("trigger_value", ""),
                }
            if not errors:
                try:
                    await self._manager.async_upsert_task(
                        self._task_id, trigger=trigger
                    )
                except PlannerError:
                    errors["base"] = "invalid_rule"
                except HomeAssistantError:
                    errors["base"] = "storage_error"
                else:
                    return self._finish()
        suggested = {
            "trigger_enabled": bool(current),
            "trigger_entity_id": current.get("entity_id"),
            "trigger_attribute": current.get("attribute") or "",
            "trigger_operator": current.get("operator", "equals"),
            "trigger_value": current.get("value", ""),
        }
        suggested = {
            key: value for key, value in suggested.items() if value is not None
        }
        suggested.update(user_input or {})
        return self.async_show_form(
            step_id="trigger_details",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(
                            "trigger_enabled", default=bool(current)
                        ): BooleanSelector(),
                        vol.Optional("trigger_entity_id"): EntitySelector(),
                        vol.Optional("trigger_attribute", default=""): TextSelector(),
                        vol.Required(
                            "trigger_operator",
                            default=current.get("operator", "equals"),
                        ): SelectSelector(
                            SelectSelectorConfig(
                                options=["equals", "above", "below"],
                                translation_key="trigger_operator",
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Optional(
                            "trigger_value", default=current.get("value", "")
                        ): TextSelector(),
                    }
                ),
                suggested,
            ),
            description_placeholders={"name": task["name"]},
            errors=errors,
        )

    async def async_step_add_material(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a material and its initial stock."""
        self._material_id = None
        return await self._material_form("add_material", user_input)

    async def async_step_edit_material(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a material to edit or replenish."""
        materials = self._manager.data.materials
        if not materials:
            return self.async_abort(reason="no_materials")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input["material_id"] in materials:
                self._material_id = user_input["material_id"]
                return await self.async_step_edit_material_details()
            errors["base"] = "not_found"
        return self.async_show_form(
            step_id="edit_material",
            data_schema=vol.Schema(
                {vol.Required("material_id"): _selection(materials)}
            ),
            errors=errors,
        )

    async def async_step_edit_material_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the selected material."""
        return await self._material_form("edit_material_details", user_input)

    async def _material_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render and validate a material editor."""
        current = self._manager.data.materials.get(self._material_id, {})
        if self._material_id is not None and not current:
            return self.async_abort(reason="not_found")
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["name"].strip():
                errors["name"] = "invalid_name"
            else:
                try:
                    await self._manager.async_upsert_material(
                        self._material_id, **user_input
                    )
                except PlannerError:
                    errors["base"] = "invalid_input"
                except HomeAssistantError:
                    errors["base"] = "storage_error"
                else:
                    return self._finish()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("name"): TextSelector(),
                        vol.Required("stock", default=0): _number(),
                        vol.Required("min_stock", default=0): _number(),
                        vol.Required("unit", default="pcs"): TextSelector(),
                        vol.Optional("notes", default=""): TextSelector(
                            TextSelectorConfig(multiline=True)
                        ),
                    }
                ),
                {**current, **(user_input or {})},
            ),
            errors=errors,
        )

    async def async_step_assign_material(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set one material requirement, or remove it with quantity zero."""
        tasks = self._manager.data.tasks
        materials = self._manager.data.materials
        if not tasks:
            return self.async_abort(reason="no_tasks")
        if not materials:
            return self.async_abort(reason="no_materials")
        errors: dict[str, str] = {}
        if user_input is not None:
            task_id = user_input["task_id"]
            material_id = user_input["material_id"]
            if task_id not in tasks or material_id not in materials:
                errors["base"] = "not_found"
            else:
                requirements = dict(tasks[task_id]["requirements"])
                if user_input["quantity"] == 0:
                    requirements.pop(material_id, None)
                else:
                    requirements[material_id] = user_input["quantity"]
                try:
                    await self._manager.async_upsert_task(
                        task_id, requirements=requirements
                    )
                except PlannerError:
                    errors["base"] = "invalid_input"
                except HomeAssistantError:
                    errors["base"] = "storage_error"
                else:
                    return self._finish()
        return self.async_show_form(
            step_id="assign_material",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("task_id"): _selection(tasks),
                        vol.Required("material_id"): _selection(materials),
                        vol.Required("quantity", default=1): _number(),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_remove_task(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a task for deletion."""
        return await self._remove_form("task", user_input)

    async def async_step_remove_material(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a material for deletion."""
        return await self._remove_form("material", user_input)

    async def _remove_form(
        self, kind: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Select a record before asking for confirmation."""
        records = (
            self._manager.data.tasks if kind == "task" else self._manager.data.materials
        )
        if not records:
            return self.async_abort(
                reason="no_tasks" if kind == "task" else "no_materials"
            )
        field = f"{kind}_id"
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[field] in records:
                self._delete_kind = kind
                self._delete_id = user_input[field]
                return await self.async_step_confirm_remove()
            errors["base"] = "not_found"
        return self.async_show_form(
            step_id=f"remove_{kind}",
            data_schema=vol.Schema({vol.Required(field): _selection(records)}),
            errors=errors,
        )

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete only after the user confirms the selected record."""
        records = (
            self._manager.data.tasks
            if self._delete_kind == "task"
            else self._manager.data.materials
        )
        if self._delete_id not in records:
            return self.async_abort(reason="not_found")
        name = records[self._delete_id]["name"]
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["confirm"]:
                return await self.async_step_init()
            try:
                if self._delete_kind == "task":
                    await self._manager.async_remove_tasks([self._delete_id])
                else:
                    await self._manager.async_remove_material(self._delete_id)
            except MaterialInUseError:
                errors["base"] = "material_in_use"
            except PlannerError:
                errors["base"] = "invalid_input"
            except HomeAssistantError:
                errors["base"] = "storage_error"
            else:
                return self._finish()
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): BooleanSelector()}
            ),
            description_placeholders={"name": name},
            errors=errors,
        )
