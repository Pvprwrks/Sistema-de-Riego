"""Botones: regar ahora, por zona, detener y omitir el próximo."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant

from .entity import RiegoEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    e = entry.runtime_data
    async_add_entities([
        RiegoButton(e, "run_all", "mdi:sprinkler", [1, 2]),
        RiegoButton(e, "run_zone_1", "mdi:numeric-1-circle", [1]),
        RiegoButton(e, "run_zone_2", "mdi:numeric-2-circle", [2]),
        RiegoButton(e, "stop", "mdi:stop-circle", None),
        RiegoButton(e, "skip_next", "mdi:skip-next-circle", None),
    ])


class RiegoButton(RiegoEntity, ButtonEntity):
    def __init__(self, engine, key: str, icon: str, zones: list[int] | None) -> None:
        super().__init__(engine, key)
        self._key = key
        self._zones = zones
        self._attr_icon = icon
        if key in ("run_zone_1", "run_zone_2"):
            self._attr_translation_placeholders = {"zone": engine.zones[zones[0]][0]}

    async def async_press(self) -> None:
        if self._key == "stop":
            await self.engine.async_stop()
        elif self._key == "skip_next":
            await self.engine.async_skip_next()
        else:
            await self.engine.async_start(zones=self._zones, reason="botón en Home Assistant")
