"""Calendario editable del riego."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import RiegoEntity
from .engine import Occurrence


def _to_event(o: Occurrence) -> CalendarEvent:
    desc = o.description
    if o.skipped:
        desc = f"(omitido) {desc or ''}"
    return CalendarEvent(
        start=o.start,
        end=o.end,
        summary=o.summary,
        description=desc,
        uid=o.uid,
        recurrence_id=o.recurrence_id,
        rrule=o.rrule,
    )


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([RiegoCalendar(entry.runtime_data)])


class RiegoCalendar(RiegoEntity, CalendarEntity):
    """Muestra riegos programados, historial y lluvia; permite crear/editar/borrar."""

    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, engine) -> None:
        super().__init__(engine, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        occ = [
            o for o in self.engine.occurrences(now - timedelta(hours=3), now + timedelta(days=60))
            if o.kind in ("auto", "manual") and isinstance(o.start, datetime)
            and dt_util.as_local(o.end) > now
        ]
        return _to_event(occ[0]) if occ else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        return [_to_event(o) for o in self.engine.occurrences(start_date, end_date)]

    async def async_create_event(self, **kwargs: Any) -> None:
        self.engine.create_event(kwargs)

    async def async_delete_event(self, uid: str, recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        self.engine.delete_event(uid, recurrence_id, recurrence_range)

    async def async_update_event(self, uid: str, event: dict[str, Any], recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        self.engine.update_event(uid, event, recurrence_id, recurrence_range)
