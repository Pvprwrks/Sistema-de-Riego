"""Entidad base."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import device_info
from .const import SIGNAL_UPDATE
from .engine import RiegoEngine


class RiegoEntity(Entity):
    """Entidad que se refresca cuando el motor cambia."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: RiegoEngine, key: str) -> None:
        self.engine = engine
        self._attr_unique_id = f"{engine.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(engine)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self.engine.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
