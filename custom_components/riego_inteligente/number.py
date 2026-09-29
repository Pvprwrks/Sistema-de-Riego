"""Duración por zona e intervalo de días, editables desde el dashboard."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant

from .entity import RiegoEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    e = entry.runtime_data
    async_add_entities([DuracionNumber(e, 1), DuracionNumber(e, 2), IntervaloNumber(e)])


class DuracionNumber(RiegoEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-outline"

    def __init__(self, engine, zone: int) -> None:
        super().__init__(engine, f"duration_{zone}")
        self.zone = zone
        self._attr_translation_placeholders = {"zone": engine.zones[zone][0]}

    @property
    def native_value(self) -> float:
        return self.engine.durations[self.zone]

    async def async_set_native_value(self, value: float) -> None:
        self.engine.set_duration(self.zone, value)


class IntervaloNumber(RiegoEntity, NumberEntity):
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-refresh"

    def __init__(self, engine) -> None:
        super().__init__(engine, "interval")

    @property
    def native_value(self) -> int:
        return self.engine.interval

    async def async_set_native_value(self, value: float) -> None:
        self.engine.set_interval(int(value))
