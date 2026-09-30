"""Medición de agua a partir del sensor del Sonoff (caudal o total acumulado)."""

from __future__ import annotations

import logging

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Unidades de caudal -> litros por minuto
FLOW_TO_LPM = {
    "l/min": 1.0, "lpm": 1.0, "l/h": 1 / 60, "l/s": 60.0,
    "m³/h": 1000 / 60, "m3/h": 1000 / 60, "m³/min": 1000.0, "m³/s": 60000.0,
    "ml/s": 0.06, "ml/min": 0.001,
    "gal/min": 3.78541, "gpm": 3.78541, "gal/h": 3.78541 / 60,
    "ft³/min": 28.3168, "ft3/min": 28.3168, "cfm": 28.3168,
}
# Unidades de volumen -> litros
VOLUME_TO_L = {
    "l": 1.0, "ml": 0.001, "m³": 1000.0, "m3": 1000.0,
    "gal": 3.78541, "ft³": 28.3168, "ft3": 28.3168, "ccf": 2831.68,
}


def _num(hass: HomeAssistant, entity_id: str) -> tuple[float | None, str, str | None]:
    st = hass.states.get(entity_id)
    if st is None:
        return None, "", None
    unit = str(st.attributes.get("unit_of_measurement") or "").strip().lower()
    dclass = st.attributes.get("device_class")
    try:
        return float(st.state), unit, dclass
    except (TypeError, ValueError):
        return None, unit, dclass


class WaterMeter:
    """Mide litros entre start() y stop(), sea sensor de caudal o de total."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self._mode: str | None = None
        self._factor = 1.0
        self._start_value: float | None = None
        self._rate_lpm = 0.0
        self._last = None
        self._liters = 0.0
        self._unsub: CALLBACK_TYPE | None = None

    def _detect(self) -> None:
        value, unit, dclass = _num(self.hass, self.entity_id)
        if unit in FLOW_TO_LPM or dclass == "volume_flow_rate":
            self._mode, self._factor = "flow", FLOW_TO_LPM.get(unit, 1.0)
        elif unit in VOLUME_TO_L or dclass in ("water", "volume", "volume_storage"):
            self._mode, self._factor = "total", VOLUME_TO_L.get(unit, 1.0)
        elif "/" in unit:
            _LOGGER.warning("Unidad de caudal no reconocida en %s: %s (se asume L/min)", self.entity_id, unit)
            self._mode, self._factor = "flow", 1.0
        else:
            _LOGGER.warning("Unidad no reconocida en %s: '%s' (se asume litros acumulados)", self.entity_id, unit)
            self._mode, self._factor = "total", 1.0

    def start(self) -> None:
        self._detect()
        value, _, _ = _num(self.hass, self.entity_id)
        if self._mode == "total":
            self._start_value = value
        else:
            self._rate_lpm = (value or 0.0) * self._factor
            self._last = dt_util.utcnow()
            self._unsub = async_track_state_change_event(self.hass, [self.entity_id], self._flow_changed)

    def _accumulate(self) -> None:
        now = dt_util.utcnow()
        if self._last is not None:
            self._liters += max(0.0, self._rate_lpm) * (now - self._last).total_seconds() / 60
        self._last = now

    @callback
    def _flow_changed(self, event: Event) -> None:
        self._accumulate()
        value, _, _ = _num(self.hass, self.entity_id)
        self._rate_lpm = (value or 0.0) * self._factor

    def stop(self) -> float:
        """Termina el segmento y devuelve los litros medidos en él."""
        liters = 0.0
        if self._mode == "total":
            value, _, _ = _num(self.hass, self.entity_id)
            if value is not None and self._start_value is not None:
                liters = max(0.0, (value - self._start_value) * self._factor)
        elif self._mode == "flow":
            self._accumulate()
            liters = self._liters
            if self._unsub:
                self._unsub()
        self._unsub = None
        self._liters = 0.0
        self._last = None
        self._start_value = None
        return liters
