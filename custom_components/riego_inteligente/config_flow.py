"""Config flow y opciones de Riego Inteligente."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector as sel

from .const import (
    CONF_DURATION1,
    CONF_DURATION2,
    CONF_FORECAST_THRESHOLD,
    CONF_INTERVAL,
    CONF_MAX_WAIT,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_PAUSES,
    CONF_NOTIFY_SKIPS,
    CONF_OPENINGS,
    CONF_RAIN_CONFIRM,
    CONF_RAIN_SENSOR,
    CONF_STOP_ON_RAIN,
    CONF_TIME1,
    CONF_TIME2,
    CONF_TIME3,
    CONF_USE_FORECAST,
    CONF_WEATHER,
    CONF_WEEKDAYS,
    CONF_ZONE1_ENTITY,
    CONF_ZONE1_NAME,
    CONF_ZONE2_ENTITY,
    CONF_ZONE2_NAME,
    DEFAULTS,
    DOMAIN,
    WEEKDAYS,
)

VALVE_DOMAINS = ["switch", "valve", "input_boolean", "light"]
OPTIONAL_KEYS = {
    "conditions": [CONF_RAIN_SENSOR, CONF_WEATHER],
    "schedule": [CONF_TIME2, CONF_TIME3],
}


def _opt(key: str, current: dict[str, Any]) -> vol.Optional:
    """Campo opcional con valor sugerido (se puede borrar)."""
    val = current.get(key)
    return vol.Optional(key, description={"suggested_value": val} if val not in (None, "") else None)


def _minutes(max_value: int = 240) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(min=0, max=max_value, step=1, unit_of_measurement="min",
                                 mode=sel.NumberSelectorMode.BOX)
    )


def zones_schema(c: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_NAME, default=c.get(CONF_NAME, DEFAULTS[CONF_NAME])): sel.TextSelector(),
        vol.Required(CONF_ZONE1_NAME, default=c.get(CONF_ZONE1_NAME, DEFAULTS[CONF_ZONE1_NAME])): sel.TextSelector(),
        vol.Required(CONF_ZONE1_ENTITY, default=c.get(CONF_ZONE1_ENTITY, vol.UNDEFINED)): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=VALVE_DOMAINS)),
        vol.Required(CONF_ZONE2_NAME, default=c.get(CONF_ZONE2_NAME, DEFAULTS[CONF_ZONE2_NAME])): sel.TextSelector(),
        vol.Required(CONF_ZONE2_ENTITY, default=c.get(CONF_ZONE2_ENTITY, vol.UNDEFINED)): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=VALVE_DOMAINS)),
    })


def conditions_schema(c: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        _opt(CONF_RAIN_SENSOR, c): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=["binary_sensor", "sensor", "input_boolean"])),
        vol.Required(CONF_RAIN_CONFIRM, default=c.get(CONF_RAIN_CONFIRM, 10)): _minutes(180),
        _opt(CONF_WEATHER, c): sel.EntitySelector(sel.EntitySelectorConfig(domain="weather")),
        vol.Required(CONF_USE_FORECAST, default=c.get(CONF_USE_FORECAST, True)): sel.BooleanSelector(),
        vol.Required(CONF_FORECAST_THRESHOLD, default=c.get(CONF_FORECAST_THRESHOLD, 70)): sel.NumberSelector(
            sel.NumberSelectorConfig(min=0, max=100, step=5, unit_of_measurement="%",
                                     mode=sel.NumberSelectorMode.SLIDER)),
        vol.Required(CONF_STOP_ON_RAIN, default=c.get(CONF_STOP_ON_RAIN, True)): sel.BooleanSelector(),
        vol.Optional(CONF_OPENINGS, default=c.get(CONF_OPENINGS, [])): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=["binary_sensor", "cover", "input_boolean"], multiple=True)),
        vol.Required(CONF_MAX_WAIT, default=c.get(CONF_MAX_WAIT, 120)): _minutes(1440),
    })


def schedule_schema(c: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_WEEKDAYS, default=c.get(CONF_WEEKDAYS, WEEKDAYS)): sel.SelectSelector(
            sel.SelectSelectorConfig(options=WEEKDAYS, multiple=True, translation_key="weekdays",
                                     mode=sel.SelectSelectorMode.LIST)),
        vol.Required(CONF_INTERVAL, default=c.get(CONF_INTERVAL, 2)): sel.NumberSelector(
            sel.NumberSelectorConfig(min=1, max=30, step=1, unit_of_measurement="días",
                                     mode=sel.NumberSelectorMode.BOX)),
        vol.Required(CONF_TIME1, default=c.get(CONF_TIME1, DEFAULTS[CONF_TIME1])): sel.TimeSelector(),
        _opt(CONF_TIME2, c): sel.TimeSelector(),
        _opt(CONF_TIME3, c): sel.TimeSelector(),
        vol.Required(CONF_DURATION1, default=c.get(CONF_DURATION1, 15)): _minutes(),
        vol.Required(CONF_DURATION2, default=c.get(CONF_DURATION2, 15)): _minutes(),
    })


def notify_schema(hass: HomeAssistant, c: dict[str, Any]) -> vol.Schema:
    services = sorted(f"notify.{s}" for s in hass.services.async_services_for_domain("notify")
                      if s not in ("send_message", "persistent_notification"))
    current = [s for s in c.get(CONF_NOTIFY, []) if s]
    options = sorted(set(services) | set(current))
    return vol.Schema({
        vol.Optional(CONF_NOTIFY, default=current): sel.SelectSelector(
            sel.SelectSelectorConfig(options=options, multiple=True, custom_value=True,
                                     mode=sel.SelectSelectorMode.DROPDOWN)),
        vol.Required(CONF_NOTIFY_SKIPS, default=c.get(CONF_NOTIFY_SKIPS, True)): sel.BooleanSelector(),
        vol.Required(CONF_NOTIFY_PAUSES, default=c.get(CONF_NOTIFY_PAUSES, True)): sel.BooleanSelector(),
    })


def _clean(step: str, user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in OPTIONAL_KEYS.get(step, []):
        data.setdefault(key, None)
    for key in (CONF_INTERVAL, CONF_MAX_WAIT, CONF_FORECAST_THRESHOLD, CONF_RAIN_CONFIRM):
        if key in data:
            data[key] = int(data[key])
    return data


def _validate_zones(data: dict[str, Any]) -> dict[str, str]:
    if data[CONF_ZONE1_ENTITY] == data[CONF_ZONE2_ENTITY]:
        return {"base": "same_entity"}
    return {}


def _validate_schedule(data: dict[str, Any]) -> dict[str, str]:
    if not data.get(CONF_WEEKDAYS):
        return {"base": "no_weekdays"}
    if float(data[CONF_DURATION1]) + float(data[CONF_DURATION2]) <= 0:
        return {"base": "no_duration"}
    return {}


class RiegoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Asistente de configuración."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_zones(user_input)
            if not errors:
                self._data.update(_clean("zones", user_input))
                return await self.async_step_conditions()
        return self.async_show_form(step_id="user", data_schema=zones_schema(user_input or {}), errors=errors)

    async def async_step_conditions(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean("conditions", user_input))
            return await self.async_step_schedule()
        return self.async_show_form(step_id="conditions", data_schema=conditions_schema(DEFAULTS))

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_schedule(user_input)
            if not errors:
                self._data.update(_clean("schedule", user_input))
                return await self.async_step_notify()
        return self.async_show_form(step_id="schedule", data_schema=schedule_schema(user_input or DEFAULTS),
                                    errors=errors)

    async def async_step_notify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean("notify", user_input))
            await self.async_set_unique_id(f"{self._data[CONF_ZONE1_ENTITY]}|{self._data[CONF_ZONE2_ENTITY]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
        return self.async_show_form(step_id="notify", data_schema=notify_schema(self.hass, DEFAULTS))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RiegoOptionsFlow()


class RiegoOptionsFlow(OptionsFlow):
    """Modificar todo desde Configuración → Dispositivos → Riego Inteligente → Configurar."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**DEFAULTS, **self.config_entry.data, **self.config_entry.options, **self._data}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["zones", "conditions", "schedule", "notify"])

    async def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **self._data})

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_zones(user_input)
            if not errors:
                self._data.update(_clean("zones", user_input))
                return await self._finish()
        return self.async_show_form(step_id="zones", data_schema=zones_schema(self._current), errors=errors)

    async def async_step_conditions(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean("conditions", user_input))
            return await self._finish()
        return self.async_show_form(step_id="conditions", data_schema=conditions_schema(self._current))

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_schedule(user_input)
            if not errors:
                self._data.update(_clean("schedule", user_input))
                return await self._finish()
        return self.async_show_form(step_id="schedule", data_schema=schedule_schema(self._current), errors=errors)

    async def async_step_notify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(_clean("notify", user_input))
            return await self._finish()
        return self.async_show_form(step_id="notify", data_schema=notify_schema(self.hass, self._current))
