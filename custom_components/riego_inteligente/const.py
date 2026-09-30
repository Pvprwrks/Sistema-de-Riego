"""Constantes de Riego Inteligente."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "riego_inteligente"
PLATFORMS: Final = ["binary_sensor", "button", "calendar", "number", "sensor", "switch"]

STORAGE_VERSION: Final = 1

# --- Claves de configuración ---
CONF_NAME: Final = "name"
CONF_ZONE1_NAME: Final = "zone1_name"
CONF_ZONE1_ENTITY: Final = "zone1_entity"
CONF_ZONE2_NAME: Final = "zone2_name"
CONF_ZONE2_ENTITY: Final = "zone2_entity"

CONF_RAIN_SENSOR: Final = "rain_sensor"
CONF_WEATHER: Final = "weather_entity"
CONF_USE_FORECAST: Final = "use_forecast"
CONF_FORECAST_THRESHOLD: Final = "forecast_threshold"
CONF_STOP_ON_RAIN: Final = "stop_on_rain"
CONF_RAIN_CONFIRM: Final = "rain_confirm_minutes"

CONF_OPENINGS: Final = "openings"
CONF_MAX_WAIT: Final = "max_wait"

CONF_WEEKDAYS: Final = "weekdays"
CONF_INTERVAL: Final = "interval_days"
CONF_TIME1: Final = "time1"
CONF_TIME2: Final = "time2"
CONF_TIME3: Final = "time3"
CONF_DURATION1: Final = "duration1"
CONF_DURATION2: Final = "duration2"

CONF_NOTIFY: Final = "notify_services"
CONF_NOTIFY_SKIPS: Final = "notify_skips"
CONF_NOTIFY_PAUSES: Final = "notify_pauses"

WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_ES: Final = {
    "mon": "Lunes",
    "tue": "Martes",
    "wed": "Miércoles",
    "thu": "Jueves",
    "fri": "Viernes",
    "sat": "Sábado",
    "sun": "Domingo",
}

DEFAULTS: Final = {
    CONF_NAME: "Riego Jardín",
    CONF_ZONE1_NAME: "Zona 1",
    CONF_ZONE2_NAME: "Zona 2",
    CONF_USE_FORECAST: True,
    CONF_FORECAST_THRESHOLD: 70,
    CONF_STOP_ON_RAIN: True,
    CONF_RAIN_CONFIRM: 10,
    CONF_OPENINGS: [],
    CONF_MAX_WAIT: 120,
    CONF_WEEKDAYS: list(WEEKDAYS),
    CONF_INTERVAL: 2,
    CONF_TIME1: "06:00:00",
    CONF_DURATION1: 15,
    CONF_DURATION2: 15,
    CONF_NOTIFY: [],
    CONF_NOTIFY_SKIPS: True,
    CONF_NOTIFY_PAUSES: True,
}

# Condiciones de clima de HA que cuentan como lluvia
RAIN_CONDITIONS: Final = {
    "rainy",
    "pouring",
    "lightning-rainy",
    "snowy-rainy",
    "hail",
}

# Estados que se interpretan como "lloviendo" en un sensor
RAIN_STATES: Final = {"on", "wet", "rain", "raining", "lluvia", "true", "detected"}
# Estados que se interpretan como "abierto" en puertas / ventanas / cubiertas
OPEN_STATES: Final = {"on", "open", "opening", "unlocked", "true"}

# Estados del sistema
STATE_IDLE: Final = "inactivo"
STATE_WAITING: Final = "esperando_cierre"
STATE_WATERING: Final = "regando"
STATE_PAUSED: Final = "pausado"
STATE_DISABLED: Final = "deshabilitado"

# Segundos que dura un "minuto" (se reduce en pruebas)
SECONDS_PER_MINUTE: float = 60.0
# Pausa de seguridad entre zonas (segundos) para garantizar que nunca rieguen juntas
ZONE_GAP_SECONDS: float = 5.0

SIGNAL_UPDATE: Final = f"{DOMAIN}_update_{{}}"

SKIP_PREFIXES: Final = ("no regar", "sin riego", "omitir", "skip", "no riego")
