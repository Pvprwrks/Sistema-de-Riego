"""Riego Inteligente – sistema de riego de 2 zonas para Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, PLATFORMS
from .engine import RiegoEngine

ATTR_ENTRY = "config_entry_id"
ATTR_ZONES = "zones"
ATTR_DUR1 = "duration_zone_1"
ATTR_DUR2 = "duration_zone_2"
ATTR_IGNORE = "ignore_conditions"

type RiegoConfigEntry = ConfigEntry[RiegoEngine]


def device_info(engine: RiegoEngine) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, engine.entry.entry_id)},
        name=engine.name,
        manufacturer="Riego Inteligente",
        model="Controlador 2 zonas",
    )


async def async_setup_entry(hass: HomeAssistant, entry: RiegoConfigEntry) -> bool:
    engine = RiegoEngine(hass, entry)
    await engine.async_setup()
    entry.runtime_data = engine
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    _register_services(hass)
    return True


async def _reload(hass: HomeAssistant, entry: RiegoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RiegoConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_unload()
    return ok


def _engine(hass: HomeAssistant, call: ServiceCall) -> RiegoEngine:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if not entries:
        raise HomeAssistantError("No se encontró el sistema de riego")
    if len(entries) > 1 and not wanted:
        raise HomeAssistantError("Hay varios sistemas de riego: indica config_entry_id")
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "start"):
        return

    async def start(call: ServiceCall) -> None:
        engine = _engine(hass, call)
        zones = [int(z) for z in call.data.get(ATTR_ZONES, [1, 2])]
        durations = {}
        if ATTR_DUR1 in call.data:
            durations[1] = call.data[ATTR_DUR1]
        if ATTR_DUR2 in call.data:
            durations[2] = call.data[ATTR_DUR2]
        await engine.async_start(
            zones=zones,
            durations=durations,
            reason="servicio",
            scheduled=not call.data.get(ATTR_IGNORE, True),
        )

    async def stop(call: ServiceCall) -> None:
        await _engine(hass, call).async_stop()

    async def skip_next(call: ServiceCall) -> dict:
        when = await _engine(hass, call).async_skip_next()
        return {"skipped": when.isoformat() if when else None}

    base = {vol.Optional(ATTR_ENTRY): cv.string}
    hass.services.async_register(
        DOMAIN,
        "start",
        start,
        schema=vol.Schema({
            **base,
            vol.Optional(ATTR_ZONES): vol.All(cv.ensure_list, [vol.In(["1", "2", 1, 2])]),
            vol.Optional(ATTR_DUR1): vol.All(vol.Coerce(float), vol.Range(min=0, max=240)),
            vol.Optional(ATTR_DUR2): vol.All(vol.Coerce(float), vol.Range(min=0, max=240)),
            vol.Optional(ATTR_IGNORE, default=True): cv.boolean,
        }),
    )
    hass.services.async_register(DOMAIN, "stop", stop, schema=vol.Schema(base))
    hass.services.async_register(
        DOMAIN, "skip_next", skip_next, schema=vol.Schema(base),
        supports_response=SupportsResponse.OPTIONAL,
    )
