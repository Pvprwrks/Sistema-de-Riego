"""Sensores de estado del riego."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import STATE_DISABLED, STATE_IDLE, STATE_PAUSED, STATE_WAITING, STATE_WATERING
from .engine import DIAS, MESES, fmt_dt
from .entity import RiegoEntity

DIAS_LARGOS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    e = entry.runtime_data
    async_add_entities([
        EstadoSensor(e), ProximoSensor(e), UltimoSensor(e), LluviaSensor(e), RestanteSensor(e),
        DuracionUltimoSensor(e),
    ])
    if e.meters:
        async_add_entities([AguaUltimoSensor(e), AguaTotalSensor(e)])


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

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    def _compute(self) -> None:
        self._next = self.engine.next_run()

    @property
    def native_value(self) -> datetime | None:
        self._compute()
        return self._next

    @property
    def extra_state_attributes(self) -> dict:
        e = self.engine
        if not hasattr(self, "_next"):
            self._compute()
        nxt = self._next
        if nxt is None:
            return {"texto": "Sin riego programado", "plan": e.plan_text()}
        loc = dt_util.as_local(nxt)
        hoy = dt_util.now().date()
        dias = (loc.date() - hoy).days
        cuando = "hoy" if dias == 0 else "mañana" if dias == 1 else DIAS_LARGOS[loc.weekday()]
        return {
            "fecha": loc.date().isoformat(),
            "hora": loc.strftime("%H:%M"),
            "dia": DIAS_LARGOS[loc.weekday()],
            "dia_corto": f"{DIAS[loc.weekday()]} {loc.day} {MESES[loc.month - 1]}",
            "dias_restantes": dias,
            "texto": f"{cuando.capitalize()} {loc.day} {MESES[loc.month - 1]} a las {loc:%H:%M}",
            "plan": e.plan_text(),
            "minutos_totales": e.total_minutes(),
            "aviso_previo": e.prewarn_for is not None,
        }


class UltimoSensor(RiegoEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, engine) -> None:
        super().__init__(engine, "last_run")

    @property
    def native_value(self) -> datetime | None:
        if self.engine.last_run and self.engine.last_run.get("inicio"):
            return dt_util.parse_datetime(self.engine.last_run["inicio"])
        return self.engine.last_run_start

    @property
    def extra_state_attributes(self) -> dict:
        lr = self.engine.last_run
        if not lr:
            return {}
        ini = dt_util.parse_datetime(lr["inicio"])
        fin = dt_util.parse_datetime(lr["fin"])
        return {
            **lr,
            "texto": f"{fmt_dt(ini)} – {dt_util.as_local(fin):%H:%M}",
        }


class DuracionUltimoSensor(RiegoEntity, SensorEntity):
    """Minutos que las válvulas estuvieron abiertas en el último riego."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:timer-sand-complete"

    def __init__(self, engine) -> None:
        super().__init__(engine, "last_duration")

    @property
    def native_value(self) -> float | None:
        lr = self.engine.last_run
        return lr.get("encendido_min") if lr else None

    @property
    def extra_state_attributes(self) -> dict:
        lr = self.engine.last_run or {}
        return {
            "duracion_total_min": lr.get("duracion_min"),
            "por_zona": {k: v["minutos"] for k, v in (lr.get("zonas") or {}).items()},
        }


class AguaUltimoSensor(RiegoEntity, SensorEntity):
    """Litros usados en el último riego."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 0

    def __init__(self, engine) -> None:
        super().__init__(engine, "last_water")

    @property
    def native_value(self) -> float | None:
        lr = self.engine.last_run
        return lr.get("agua_litros") if lr else None

    @property
    def extra_state_attributes(self) -> dict:
        lr = self.engine.last_run or {}
        return {"por_zona": {k: v["litros"] for k, v in (lr.get("zonas") or {}).items()}}


class AguaTotalSensor(RiegoEntity, SensorEntity):
    """Litros acumulados (sirve para el panel de Energía → Agua)."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 0

    def __init__(self, engine) -> None:
        super().__init__(engine, "total_water")

    @property
    def native_value(self) -> float:
        return round(self.engine.water_total, 1)


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
