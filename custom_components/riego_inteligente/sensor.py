"""Sensores de estado del riego."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import STATE_DISABLED, STATE_IDLE, STATE_PAUSED, STATE_WAITING, STATE_WATERING
from .entity import RiegoEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    e = entry.runtime_data
    async_add_entities([
        EstadoSensor(e), ProximoSensor(e), UltimoSensor(e), LluviaSensor(e), RestanteSensor(e),
    ])


class EstadoSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_IDLE, STATE_WAITING, STATE_WATERING, STATE_PAUSED, STATE_DISABLED]

    def __init__(self, engine) -> None:
        super().__init__(engine, "status")

    @property
    def native_value(self) -> str:
        return self.engine.state

    @property
    def extra_state_attributes(self) -> dict:
        e = self.engine
        return {
            "zona_activa": e.zones[e.active_zone][0] if e.active_zone else None,
            "zona_activa_numero": e.active_zone,
            "motivo": e.run_reason if e.is_running else None,
            "pausado_por": e.pause_reason,
            "zona_termina": e.zone_ends_at.isoformat() if e.zone_ends_at else None,
            "ultimo_motivo_omision": e.last_skip_reason,
            "lluvia_ahora": e.is_raining_now(),
            "aberturas_abiertas": e.openings_open(),
            "intervalo_dias": e.interval,
            "horarios": [t.strftime("%H:%M") for t in e.times],
            "plan": e.plan_text(),
        }


class ProximoSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, engine) -> None:
        super().__init__(engine, "next_run")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # recalcular cada 5 min (cambios de día, pronóstico, eventos, etc.)
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(minutes=5)))

    @callback
    def _tick(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return self.engine.next_run()


class UltimoSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, engine) -> None:
        super().__init__(engine, "last_run")

    @property
    def native_value(self) -> datetime | None:
        return self.engine.last_run_end or self.engine.last_run_start


class LluviaSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, engine) -> None:
        super().__init__(engine, "last_rain")

    @property
    def native_value(self):
        return self.engine.last_rain


class RestanteSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(self, engine) -> None:
        super().__init__(engine, "remaining")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(seconds=30)))

    @callback
    def _tick(self, _now: datetime) -> None:
        if self.engine.is_running or self.state not in ("0", "0.0"):
            self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self.engine.remaining_minutes()
