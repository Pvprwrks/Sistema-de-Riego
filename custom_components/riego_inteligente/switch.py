"""Interruptor principal del programa."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant

from .entity import RiegoEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([ProgramaSwitch(entry.runtime_data)])


class ProgramaSwitch(RiegoEntity, SwitchEntity):
    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, engine) -> None:
        super().__init__(engine, "enabled")

    @property
    def is_on(self) -> bool:
        return self.engine.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.engine.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.engine.async_set_enabled(False)
