"""Sensores binarios: lluvia combinada, aberturas y regando."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant

from .const import CONF_RAIN_SENSOR, CONF_WEATHER, STATE_WATERING
from .entity import RiegoEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    e = entry.runtime_data
    ents = [RegandoBinary(e)]
    if e.config.get(CONF_RAIN_SENSOR) or e.config.get(CONF_WEATHER):
        ents.append(LluviaBinary(e))
    if e.openings:
        ents.append(AberturasBinary(e))
    async_add_entities(ents)


class RegandoBinary(RiegoEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, engine) -> None:
        super().__init__(engine, "watering")

    @property
    def is_on(self) -> bool:
        return self.engine.state == STATE_WATERING


class LluviaBinary(RiegoEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, engine) -> None:
        super().__init__(engine, "rain")

    @property
    def is_on(self) -> bool:
        return self.engine.is_raining_now()

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "sensor_lluvia": self.engine.rain_sensor_confirmed(),
            "sensor_lluvia_sin_confirmar": self.engine.rain_sensor_pending(),
            "clima_lluvia": self.engine.weather_rain_now(),
        }


class AberturasBinary(RiegoEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, engine) -> None:
        super().__init__(engine, "openings")

    @property
    def is_on(self) -> bool:
        return bool(self.engine.openings_open())

    @property
    def extra_state_attributes(self) -> dict:
        return {"abiertas": self.engine.openings_open(), "vigiladas": self.engine.openings}
