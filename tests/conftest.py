"""Fixtures."""
import pytest

from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.setup import async_setup_component

from custom_components.riego_inteligente import const

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_minutes(monkeypatch):
    monkeypatch.setattr(const, "SECONDS_PER_MINUTE", 0.05)
    monkeypatch.setattr(const, "ZONE_GAP_SECONDS", 0.02)


class Rig:
    """Válvulas, notificaciones y clima falsos."""

    def __init__(self, hass):
        self.hass = hass
        self.log = []          # (entity, on) en orden
        self.overlap = False
        self.notes = []
        self.forecast = [{"datetime": "2026-09-28T12:00:00-06:00", "precipitation_probability": 10, "condition": "sunny"}]

    async def setup(self):
        hass = self.hass
        await hass.config.async_set_time_zone("America/Mexico_City")
        hass.config.language = "es"
        assert await async_setup_component(
            hass, "input_boolean", {"input_boolean": {"valvula_1": {}, "valvula_2": {}}}
        )
        hass.states.async_set("binary_sensor.lluvia_shelly", "off")
        hass.states.async_set("binary_sensor.puerta_cocina", "off", {"friendly_name": "Puerta cocina"})
        hass.states.async_set("binary_sensor.ventana_sala", "off", {"friendly_name": "Ventana sala"})
        hass.states.async_set("weather.casa", "sunny")

        def track(event):
            new = event.data["new_state"]
            self.log.append((new.entity_id, new.state == "on"))
            if all(hass.states.get(e).state == "on" for e in ("input_boolean.valvula_1", "input_boolean.valvula_2")):
                self.overlap = True

        async_track_state_change_event(hass, ["input_boolean.valvula_1", "input_boolean.valvula_2"], track)

        async def notify(call: ServiceCall):
            self.notes.append((call.data["title"], call.data["message"]))

        hass.services.async_register("notify", "mobile_app_iphone", notify)

        async def fc(call: ServiceCall):
            return {"weather.casa": {"forecast": self.forecast}}

        hass.services.async_register("weather", "get_forecasts", fc, supports_response=SupportsResponse.ONLY)

    def titles(self):
        return [t for t, _ in self.notes]


@pytest.fixture
async def rig(hass):
    r = Rig(hass)
    await r.setup()
    return r


BASE = {
    "name": "Riego Jardín",
    "zone1_name": "Jardín frontal",
    "zone1_entity": "input_boolean.valvula_1",
    "zone2_name": "Jardín trasero",
    "zone2_entity": "input_boolean.valvula_2",
    "rain_sensor": "binary_sensor.lluvia_shelly",
    "weather_entity": "weather.casa",
    "use_forecast": True,
    "forecast_threshold": 70,
    "stop_on_rain": True,
    "rain_confirm_minutes": 0,
    "openings": ["binary_sensor.puerta_cocina", "binary_sensor.ventana_sala"],
    "max_wait": 60,
    "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "interval_days": 2,
    "time1": "06:00:00",
    "time2": None,
    "time3": None,
    "duration1": 2,
    "duration2": 2,
    "notify_services": ["notify.mobile_app_iphone"],
    "notify_skips": True,
    "notify_pauses": True,
}
