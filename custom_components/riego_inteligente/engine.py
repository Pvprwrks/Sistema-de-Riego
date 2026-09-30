"""Motor de Riego Inteligente: programa, condiciones y ejecución secuencial."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import const
from .const import (
    CONF_DURATION1,
    CONF_DURATION2,
    CONF_FORECAST_THRESHOLD,
    CONF_INTERVAL,
    CONF_MAX_WAIT,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_NOTIFY_PAUSES,
    CONF_NOTIFY_SKIPS,
    CONF_RAIN_CONFIRM,
    CONF_OPENINGS,
    CONF_RAIN_SENSOR,
    CONF_STOP_ON_RAIN,
    CONF_TIME1,
    CONF_TIME2,
    CONF_TIME3,
    CONF_USE_FORECAST,
    CONF_WEATHER,
    CONF_WEEKDAYS,
    CONF_ZONE1_ENTITY,
    CONF_ZONE1_NAME,
    CONF_ZONE2_ENTITY,
    CONF_ZONE2_NAME,
    DEFAULTS,
    DOMAIN,
    OPEN_STATES,
    RAIN_CONDITIONS,
    RAIN_STATES,
    SIGNAL_UPDATE,
    SKIP_PREFIXES,
    STATE_DISABLED,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_WAITING,
    STATE_WATERING,
    STORAGE_VERSION,
    WEEKDAYS,
)

try:  # python-dateutil viene incluido con Home Assistant
    from dateutil.rrule import rrulestr
except ImportError:  # pragma: no cover
    rrulestr = None

_LOGGER = logging.getLogger(__name__)

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fmt_dt(value: datetime | None) -> str:
    """Formatea una fecha en español corto: 'mié 30 sep 06:00'."""
    if value is None:
        return "sin programar"
    value = dt_util.as_local(value)
    return f"{DIAS[value.weekday()]} {value.day} {MESES[value.month - 1]} {value:%H:%M}"


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        parts = [int(p) for p in str(value).split(":")]
        return time(parts[0], parts[1] if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


def _to_local(value: date | datetime) -> datetime:
    """Convierte date/datetime (naive o aware) a datetime local aware."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt_util.get_default_time_zone())
        return dt_util.as_local(value)
    return datetime.combine(value, time(0), tzinfo=dt_util.get_default_time_zone())


def _parse_iso(value: str) -> date | datetime:
    if "T" in value:
        return _to_local(datetime.fromisoformat(value))
    return date.fromisoformat(value)


def _rid(start: date | datetime) -> str:
    """recurrence_id en formato RFC5545."""
    if isinstance(start, datetime):
        return start.strftime("%Y%m%dT%H%M%S")
    return start.strftime("%Y%m%d")


@dataclass
class Occurrence:
    """Un riego (o evento informativo) calculado para el calendario."""

    start: date | datetime
    end: date | datetime
    summary: str
    uid: str
    kind: str  # auto | manual | skip_day | history | rain
    description: str | None = None
    key: str | None = None
    skipped: bool = False
    recurrence_id: str | None = None
    rrule: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class RiegoEngine:
    """Lógica completa de un sistema de riego de 2 zonas."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.config: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

        # --- Datos persistentes ---
        self.enabled: bool = True
        self.durations: dict[int, float] = {
            1: float(self.config[CONF_DURATION1]),
            2: float(self.config[CONF_DURATION2]),
        }
        self.interval: int = int(self.config[CONF_INTERVAL])
        self.anchor: date = dt_util.now().date()
        self.last_scheduled: date | None = None
        self.rain_dates: list[str] = []
        self.skips: set[str] = set()
        self.manual_events: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.last_run_start: datetime | None = None
        self.last_run_end: datetime | None = None

        # --- Estado en tiempo de ejecución ---
        self.state: str = STATE_IDLE
        self.active_zone: int | None = None
        self.zone_ends_at: datetime | None = None
        self.run_reason: str | None = None
        self.pause_reason: str | None = None
        self.last_skip_reason: str | None = None
        self._remaining: dict[int, float] = {}
        self._task: asyncio.Task | None = None
        self._stop_reason: str | None = None
        self._changed = asyncio.Event()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._fired: set[str] = set()
        self._rain_timer: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ setup
    @property
    def name(self) -> str:
        return self.config[CONF_NAME]

    @property
    def zones(self) -> dict[int, tuple[str, str]]:
        """{n: (nombre, entity_id)}"""
        return {
            1: (self.config[CONF_ZONE1_NAME], self.config[CONF_ZONE1_ENTITY]),
            2: (self.config[CONF_ZONE2_NAME], self.config[CONF_ZONE2_ENTITY]),
        }

    @property
    def times(self) -> list[time]:
        slots = {
            t
            for t in (_parse_time(self.config.get(k)) for k in (CONF_TIME1, CONF_TIME2, CONF_TIME3))
            if t is not None
        }
        return sorted(slots)

    @property
    def openings(self) -> list[str]:
        return list(self.config.get(CONF_OPENINGS) or [])

    async def async_setup(self) -> None:
        """Carga el estado guardado y registra los listeners."""
        data = await self._store.async_load() or {}
        self.enabled = data.get("enabled", True)
        # Los valores de duración/intervalo se pueden cambiar desde las entidades
        # "number"; si el usuario los cambió en Configurar, mandan los nuevos.
        if data.get("cfg_seen") == self._cfg_signature():
            if "durations" in data:
                self.durations = {int(k): float(v) for k, v in data["durations"].items()}
            self.interval = int(data.get("interval", self.interval))
        if data.get("anchor"):
            self.anchor = date.fromisoformat(data["anchor"])
        if data.get("last_scheduled"):
            self.last_scheduled = date.fromisoformat(data["last_scheduled"])
        self.rain_dates = list(data.get("rain_dates", []))
        self.skips = set(data.get("skips", []))
        self.manual_events = list(data.get("manual_events", []))
        self.history = list(data.get("history", []))
        if data.get("last_run_start"):
            self.last_run_start = dt_util.parse_datetime(data["last_run_start"])
        if data.get("last_run_end"):
            self.last_run_end = dt_util.parse_datetime(data["last_run_end"])
        if not data:
            self._save()

        # Seguridad: si HA se reinició a media sesión, cerrar válvulas.
        await self._close_all()

        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        if self.openings:
            self._unsubs.append(
                async_track_state_change_event(self.hass, self.openings, self._openings_changed)
            )
        rain_entities = [e for e in (self.config.get(CONF_RAIN_SENSOR), self.config.get(CONF_WEATHER)) if e]
        if rain_entities:
            self._unsubs.append(
                async_track_state_change_event(self.hass, rain_entities, self._rain_changed)
            )
        valves = [z[1] for z in self.zones.values()]
        self._unsubs.append(async_track_state_change_event(self.hass, valves, self._valve_changed))

        if self.state == STATE_IDLE and not self.enabled:
            self.state = STATE_DISABLED
        if self.is_raining_now():
            await self._record_rain(self._rain_source(), notify=False)
        elif self.rain_sensor_active():
            self._schedule_rain_confirm()

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_rain_timer()
        if self._task and not self._task.done():
            self._stop_reason = "Home Assistant recargó la integración"
            self._changed.set()
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._close_all()
        await self._store.async_save(self._data())

    # ---------------------------------------------------------------- storage
    def _cfg_signature(self) -> list[float]:
        return [
            float(self.config[CONF_DURATION1]),
            float(self.config[CONF_DURATION2]),
            float(self.config[CONF_INTERVAL]),
        ]

    def _data(self) -> dict[str, Any]:
        return {
            "cfg_seen": self._cfg_signature(),
            "enabled": self.enabled,
            "durations": {str(k): v for k, v in self.durations.items()},
            "interval": self.interval,
            "anchor": self.anchor.isoformat(),
            "last_scheduled": self.last_scheduled.isoformat() if self.last_scheduled else None,
            "rain_dates": self.rain_dates[-60:],
            "skips": sorted(self.skips)[-200:],
            "manual_events": self.manual_events,
            "history": self.history[-150:],
            "last_run_start": self.last_run_start.isoformat() if self.last_run_start else None,
            "last_run_end": self.last_run_end.isoformat() if self.last_run_end else None,
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 1)

    @callback
    def _update(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    # ------------------------------------------------------------- settings
    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = value
        if not value and self.is_running:
            await self.async_stop("Programa deshabilitado")
        if not self.is_running:
            self.state = STATE_IDLE if value else STATE_DISABLED
        self._save()
        self._update()

    def set_duration(self, zone: int, minutes: float) -> None:
        self.durations[zone] = float(minutes)
        self._save()
        self._update()

    def set_interval(self, days: int) -> None:
        self.interval = max(1, int(days))
        self._save()
        self._update()

    # ----------------------------------------------------------- conditions
    def _state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        return None if st is None else st.state

    def rain_sensor_active(self) -> bool:
        state = self._state(self.config.get(CONF_RAIN_SENSOR))
        if state is None or state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        if state.lower() in RAIN_STATES:
            return True
        try:
            return float(state) > 0
        except ValueError:
            return False

    @property
    def rain_confirm_seconds(self) -> float:
        return max(0.0, float(self.config.get(CONF_RAIN_CONFIRM) or 0)) * const.SECONDS_PER_MINUTE

    def rain_sensor_elapsed(self) -> float:
        """Segundos que lleva el sensor marcando lluvia (0 si no)."""
        if not self.rain_sensor_active():
            return 0.0
        st = self.hass.states.get(self.config[CONF_RAIN_SENSOR])
        return max(0.0, (dt_util.utcnow() - st.last_changed).total_seconds())

    def rain_sensor_confirmed(self) -> bool:
        """Lluvia real: el sensor lleva activo al menos el tiempo de confirmación."""
        if not self.rain_sensor_active():
            return False
        return self.rain_sensor_elapsed() >= self.rain_confirm_seconds - 0.001

    def rain_sensor_pending(self) -> bool:
        """El sensor marca lluvia pero todavía no se confirma."""
        return self.rain_sensor_active() and not self.rain_sensor_confirmed()

    def weather_rain_now(self) -> bool:
        return (self._state(self.config.get(CONF_WEATHER)) or "") in RAIN_CONDITIONS

    def is_raining_now(self) -> bool:
        return self.rain_sensor_confirmed() or self.weather_rain_now()

    def _rain_source(self) -> str:
        if self.rain_sensor_confirmed():
            return "sensor de lluvia"
        return "clima de Home Assistant"

    async def forecast_rain_today(self) -> tuple[bool, str | None]:
        """Revisa el pronóstico diario del día de hoy."""
        weather = self.config.get(CONF_WEATHER)
        if not weather or not self.config.get(CONF_USE_FORECAST):
            return False, None
        threshold = float(self.config.get(CONF_FORECAST_THRESHOLD, 70))
        forecast: list[dict[str, Any]] = []
        for ftype in ("daily", "twice_daily", "hourly"):
            try:
                resp = await asyncio.wait_for(
                    self.hass.services.async_call(
                        "weather",
                        "get_forecasts",
                        {"entity_id": weather, "type": ftype},
                        blocking=True,
                        return_response=True,
                    ),
                    timeout=20,
                )
            except Exception:  # noqa: BLE001 - tipo no soportado o servicio caído
                continue
            forecast = ((resp or {}).get(weather) or {}).get("forecast") or []
            if forecast:
                break
        if not forecast:
            return False, None
        today = dt_util.now().date()
        todays = []
        for item in forecast:
            when = dt_util.parse_datetime(str(item.get("datetime", "")))
            if when is None or dt_util.as_local(when).date() == today:
                todays.append(item)
        todays = todays or forecast[:1]
        for item in todays:
            prob = item.get("precipitation_probability")
            if prob is not None and float(prob) >= threshold:
                return True, f"pronóstico de lluvia {prob:.0f}% (umbral {threshold:.0f}%)"
            if prob is None and item.get("condition") in RAIN_CONDITIONS:
                return True, f"pronóstico: {item.get('condition')}"
        return False, None

    def openings_open(self) -> list[str]:
        """Devuelve los nombres de las puertas/ventanas abiertas."""
        abiertas = []
        for ent in self.openings:
            st = self.hass.states.get(ent)
            if st is not None and st.state.lower() in OPEN_STATES:
                abiertas.append(st.attributes.get("friendly_name", ent))
        return abiertas

    @property
    def last_rain(self) -> date | None:
        return date.fromisoformat(max(self.rain_dates)) if self.rain_dates else None

    async def _record_rain(self, source: str, notify: bool = True) -> None:
        today = dt_util.now().date().isoformat()
        if today in self.rain_dates:
            return
        self.rain_dates.append(today)
        self.rain_dates.sort()
        self._save()
        _LOGGER.info("%s: lluvia detectada (%s), programa reiniciado", self.name, source)
        if notify and self.config.get(CONF_NOTIFY_SKIPS):
            await self._notify(
                "🌧 Lluvia detectada",
                f"{self.name}: lluvia por {source}. El programa se reinició; "
                f"próximo riego: {fmt_dt(self.next_run())}.",
            )
        self._update()

    # ------------------------------------------------------------- schedule
    def _allowed_weekday(self, day: date) -> bool:
        return WEEKDAYS[day.weekday()] in (self.config.get(CONF_WEEKDAYS) or [])

    def _manual_instances(
        self, ev: dict[str, Any], start: datetime, end: datetime
    ) -> list[tuple[date | datetime, date | datetime]]:
        """Expande un evento manual (con o sin repetición) en el rango."""
        ev_start = _parse_iso(ev["start"])
        ev_end = _parse_iso(ev["end"])
        length = _to_local(ev_end) - _to_local(ev_start)
        out: list[tuple[date | datetime, date | datetime]] = []
        rule = ev.get("rrule")
        if rule and rrulestr is not None:
            try:
                if isinstance(ev_start, datetime):
                    rr = rrulestr(rule, dtstart=ev_start)
                    starts = rr.between(start - length, end, inc=True)
                else:
                    rr = rrulestr(rule, dtstart=datetime.combine(ev_start, time(0)))
                    naive_s = (start - length).replace(tzinfo=None)
                    naive_e = end.replace(tzinfo=None)
                    starts = [d.date() for d in rr.between(naive_s, naive_e, inc=True)]
            except (ValueError, TypeError) as err:
                _LOGGER.warning("RRULE inválida en %s: %s", ev.get("summary"), err)
                starts = [ev_start]
            exdates = set(ev.get("exdates", []))
            until = ev.get("until")
            for s in starts[:500]:
                rid = _rid(s)
                if rid in exdates or (until and rid >= until):
                    continue
                e = s + length if isinstance(s, datetime) else s + timedelta(days=length.days or 1)
                out.append((s, e))
        else:
            out.append((ev_start, ev_end))
        return [
            (s, e) for s, e in out if _to_local(e) > start and _to_local(s) < end
        ]

    def _is_skip_event(self, ev: dict[str, Any]) -> bool:
        return (ev.get("summary") or "").strip().lower().startswith(SKIP_PREFIXES)

    def _day_blocked(self, day: date) -> bool:
        """True si un evento 'No regar' cubre el día."""
        s = _to_local(day)
        e = s + timedelta(days=1)
        for ev in self.manual_events:
            if self._is_skip_event(ev) and self._manual_instances(ev, s, e):
                return True
        return False

    def due_dates(self, until: date) -> list[date]:
        """Días de riego automático desde hoy hasta `until`."""
        now = dt_util.now()
        today = now.date()
        times = self.times
        n = max(1, self.interval)
        rain = self.last_rain
        result: list[date] = []

        def usable(day: date) -> bool:
            return self._allowed_weekday(day) and not self._day_blocked(day)

        if not times or not self.config.get(CONF_WEEKDAYS):
            return result

        now_min = now.replace(second=0, microsecond=0).time()
        pending = [
            t for t in times
            if t >= now_min and f"{today.isoformat()}T{t:%H:%M}" not in self._fired
        ]

        if self.last_scheduled == today and rain != today:
            if usable(today) and pending:
                result.append(today)
            nxt = today + timedelta(days=n)
        else:
            base = self.last_scheduled + timedelta(days=n) if self.last_scheduled else self.anchor
            if rain is not None:
                base = max(base, rain + timedelta(days=n))
            nxt = max(base, today)
            if nxt == today and not pending:
                nxt = today + timedelta(days=1)

        while nxt <= until:
            probe = nxt
            for _ in range(60):
                if usable(probe):
                    break
                probe += timedelta(days=1)
            else:
                break
            if probe > until:
                break
            result.append(probe)
            nxt = probe + timedelta(days=n)
        return result

    def total_minutes(self) -> float:
        return sum(self.durations.values())

    def plan_text(self) -> str:
        return " → ".join(
            f"{self.zones[z][0]} ({self.durations[z]:g} min)" for z in (1, 2) if self.durations[z] > 0
        )

    def occurrences(
        self,
        start: datetime,
        end: datetime,
        include_skipped: bool = False,
        include_info: bool = True,
    ) -> list[Occurrence]:
        """Riegos programados + eventos informativos en el rango."""
        start = _to_local(start)
        end = _to_local(end)
        now_min = dt_util.now().replace(second=0, microsecond=0)
        total = timedelta(minutes=self.total_minutes() + (const.ZONE_GAP_SECONDS / 60))
        out: list[Occurrence] = []

        # 1. Riegos automáticos (solo futuros)
        if end > now_min:
            for day in self.due_dates(end.date()):
                for t in self.times:
                    s = datetime.combine(day, t, tzinfo=dt_util.get_default_time_zone())
                    if s < now_min or s < start - total or s >= end:
                        continue
                    key = f"{day.isoformat()}T{t:%H:%M}"
                    skipped = key in self.skips
                    if skipped and not include_skipped:
                        continue
                    if key in self._fired and not include_skipped:
                        continue
                    out.append(
                        Occurrence(
                            start=s,
                            end=s + total,
                            summary="💧 Riego programado",
                            description=self.plan_text(),
                            uid=f"auto-{key}",
                            kind="auto",
                            key=key,
                            skipped=skipped,
                        )
                    )

        # 2. Eventos manuales del usuario
        for ev in self.manual_events:
            skip_ev = self._is_skip_event(ev)
            for s, e in self._manual_instances(ev, start, end):
                rid = _rid(s) if ev.get("rrule") else None
                if skip_ev:
                    if include_info:
                        out.append(Occurrence(s, e, ev["summary"], ev["uid"], "skip_day",
                                              ev.get("description"), recurrence_id=rid,
                                              rrule=ev.get("rrule")))
                    continue
                if isinstance(s, datetime):
                    out.append(Occurrence(s, e, ev["summary"], ev["uid"], "manual",
                                          ev.get("description"), key=f"{ev['uid']}@{_rid(s)}",
                                          recurrence_id=rid, rrule=ev.get("rrule")))
                else:
                    # Evento de día completo: riega en los horarios configurados
                    day = s
                    while day < e:
                        if not self._day_blocked(day):
                            for t in self.times:
                                ts = datetime.combine(day, t, tzinfo=dt_util.get_default_time_zone())
                                if start <= ts < end and not include_info:
                                    out.append(Occurrence(ts, ts + total, ev["summary"], ev["uid"],
                                                          "manual", key=f"{ev['uid']}@{_rid(ts)}"))
                        day += timedelta(days=1)
                    if include_info:
                        out.append(Occurrence(s, e, ev["summary"], ev["uid"], "manual",
                                              ev.get("description"), recurrence_id=rid,
                                              rrule=ev.get("rrule")))

        # 3. Historial y lluvia (solo informativo)
        if include_info:
            for h in self.history:
                s, e = _parse_iso(h["start"]), _parse_iso(h["end"])
                if _to_local(e) > start and _to_local(s) < end:
                    out.append(Occurrence(s, e, h["summary"], h["uid"], "history", h.get("description")))
            for rd in self.rain_dates:
                d = date.fromisoformat(rd)
                if _to_local(d + timedelta(days=1)) > start and _to_local(d) < end:
                    out.append(Occurrence(d, d + timedelta(days=1), "🌧 Lluvia – programa reiniciado",
                                          f"rain-{rd}", "rain",
                                          f"Se detectó lluvia. Siguiente riego ≥ {self.interval} día(s) después."))
        out.sort(key=lambda o: _to_local(o.start))
        return out

    def next_run(self) -> datetime | None:
        now = dt_util.now()
        for horizon in (14, 90, 400):
            occ = [
                o for o in self.occurrences(now, now + timedelta(days=horizon), include_info=False)
                if o.kind in ("auto", "manual") and isinstance(o.start, datetime) and o.start >= now.replace(second=0, microsecond=0)
            ]
            if occ:
                return min(o.start for o in occ)
        return None

    async def async_skip_next(self) -> datetime | None:
        now = dt_util.now()
        occ = [o for o in self.occurrences(now, now + timedelta(days=90), include_info=False)
               if o.kind == "auto"]
        if not occ:
            return None
        self.skips.add(occ[0].key)
        self._save()
        self._update()
        return occ[0].start

    # ------------------------------------------------------ calendar CRUD
    def _event_from_dict(self, event: dict[str, Any], uid: str | None = None) -> dict[str, Any]:
        s = event["dtstart"]
        e = event.get("dtend") or s
        all_day = not isinstance(s, datetime)
        if all_day:
            if not isinstance(e, date) or e <= s:
                e = s + timedelta(days=1)
            s_iso, e_iso = s.isoformat(), e.isoformat()
        else:
            s, e = _to_local(s), _to_local(e)
            if e <= s:
                e = s + timedelta(minutes=max(1, self.total_minutes()))
            s_iso, e_iso = s.isoformat(), e.isoformat()
        return {
            "uid": uid or f"man-{uuid.uuid4().hex[:12]}",
            "summary": event.get("summary") or "Riego",
            "description": event.get("description"),
            "start": s_iso,
            "end": e_iso,
            "rrule": event.get("rrule") or None,
            "exdates": [],
            "until": None,
        }

    def create_event(self, event: dict[str, Any]) -> None:
        self.manual_events.append(self._event_from_dict(event))
        self._save()
        self._update()

    def _find_manual(self, uid: str) -> dict[str, Any] | None:
        return next((e for e in self.manual_events if e["uid"] == uid), None)

    def delete_event(self, uid: str, recurrence_id: str | None, recurrence_range: str | None) -> None:
        if uid.startswith("auto-"):
            self.skips.add(uid.removeprefix("auto-"))
        elif uid.startswith("rain-"):
            rd = uid.removeprefix("rain-")
            if rd in self.rain_dates:
                self.rain_dates.remove(rd)
        elif uid.startswith("hist-"):
            self.history = [h for h in self.history if h["uid"] != uid]
        else:
            ev = self._find_manual(uid)
            if ev is None:
                raise HomeAssistantError(f"Evento {uid} no encontrado")
            if recurrence_id and ev.get("rrule"):
                if recurrence_range == "THISANDFUTURE":
                    ev["until"] = recurrence_id
                else:
                    ev.setdefault("exdates", []).append(recurrence_id)
            else:
                self.manual_events.remove(ev)
        self._save()
        self._update()

    def update_event(
        self, uid: str, event: dict[str, Any], recurrence_id: str | None, recurrence_range: str | None
    ) -> None:
        if uid.startswith(("rain-", "hist-")):
            raise HomeAssistantError("El historial y los días de lluvia no se pueden editar (solo borrar)")
        if uid.startswith("auto-"):
            # Mover un riego automático = omitirlo y crear uno manual
            self.skips.add(uid.removeprefix("auto-"))
            self.manual_events.append(self._event_from_dict(event))
        else:
            ev = self._find_manual(uid)
            if ev is None:
                raise HomeAssistantError(f"Evento {uid} no encontrado")
            if recurrence_id and ev.get("rrule"):
                if recurrence_range == "THISANDFUTURE":
                    ev["until"] = recurrence_id
                    self.manual_events.append(self._event_from_dict(event))
                else:
                    ev.setdefault("exdates", []).append(recurrence_id)
                    new = dict(event)
                    new.pop("rrule", None)
                    self.manual_events.append(self._event_from_dict(new))
            else:
                self.manual_events[self.manual_events.index(ev)] = self._event_from_dict(event, uid)
        self._save()
        self._update()

    # ------------------------------------------------------------ listeners
    @callback
    def _openings_changed(self, event: Event) -> None:
        self._changed.set()
        self._update()

    def _cancel_rain_timer(self) -> None:
        if self._rain_timer is not None:
            self._rain_timer()
            self._rain_timer = None

    def _schedule_rain_confirm(self) -> None:
        """Revisa de nuevo cuando se cumpla el tiempo de confirmación."""
        self._cancel_rain_timer()
        left = self.rain_confirm_seconds - self.rain_sensor_elapsed()
        _LOGGER.info("%s: el sensor marca lluvia; se confirmará en %.0f s", self.name, max(left, 0))
        self._rain_timer = async_call_later(self.hass, max(left, 0.01), self._rain_confirm_due)

    async def _rain_confirm_due(self, _now: datetime) -> None:
        self._rain_timer = None
        if self.rain_sensor_pending():  # por si cambió last_changed
            self._schedule_rain_confirm()
            return
        await self._on_rain()

    async def _on_rain(self) -> None:
        if not self.is_raining_now():
            self._update()
            return
        await self._record_rain(self._rain_source())
        if self.is_running and self.config.get(CONF_STOP_ON_RAIN):
            await self.async_stop(f"lluvia detectada ({self._rain_source()})")

    async def _rain_changed(self, event: Event) -> None:
        self._changed.set()
        if event.data.get("entity_id") == self.config.get(CONF_RAIN_SENSOR):
            if self.rain_sensor_pending():
                self._schedule_rain_confirm()
                self._update()
                return
            if not self.rain_sensor_active() and self._rain_timer is not None:
                self._cancel_rain_timer()
                _LOGGER.info("%s: el sensor dejó de marcar lluvia antes de confirmarse (falsa alarma)", self.name)
        await self._on_rain()

    async def _valve_changed(self, event: Event) -> None:
        """Garantiza que nunca se abran las dos zonas a la vez."""
        new = event.data.get("new_state")
        if new is None or new.state not in ("on", "open", "opening"):
            return
        entity = new.entity_id
        mine = self.zones[self.active_zone][1] if self.active_zone else None
        if entity == mine:
            return
        others = [e for _, e in self.zones.values() if e != entity]
        if any((self._state(o) or "") in ("on", "open", "opening") for o in others):
            _LOGGER.warning("%s: %s se abrió mientras otra zona regaba; se cierra", self.name, entity)
            await self._set_valve(entity, False)

    # ------------------------------------------------------------ scheduler
    async def _tick(self, now: datetime) -> None:
        now_min = dt_util.as_local(now).replace(second=0, microsecond=0)
        occ = [
            o for o in self.occurrences(now_min - timedelta(seconds=1), now_min + timedelta(seconds=59),
                                        include_skipped=True, include_info=False)
            if isinstance(o.start, datetime) and _to_local(o.start) == now_min and o.key not in self._fired
        ]
        if not occ:
            return
        for o in occ:
            self._fired.add(o.key)
        if len(self._fired) > 500:
            self._fired = set(list(self._fired)[-100:])

        auto = [o for o in occ if o.kind == "auto"]
        if auto and all(o.skipped for o in occ):
            # Omitido por el usuario: cuenta como ciclo cumplido
            self.last_scheduled = now_min.date()
            self._save()
            self._update()
            return
        if not self.enabled:
            return
        if self.is_running:
            _LOGGER.info("%s: riego ya en curso, se ignora el horario %s", self.name, now_min)
            return
        is_auto = any(o.kind == "auto" and not o.skipped for o in occ)
        reason = "programa" if is_auto else f"calendario: {occ[0].summary}"
        await self.async_start(reason=reason, scheduled=True, auto=is_auto)

    # ------------------------------------------------------------ execution
    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def async_start(
        self,
        zones: list[int] | None = None,
        durations: dict[int, float] | None = None,
        reason: str = "manual",
        scheduled: bool = False,
        auto: bool = False,
    ) -> bool:
        """Solicita un riego. Devuelve False si se omitió."""
        if self.is_running:
            raise HomeAssistantError("Ya hay un riego en curso")
        zones = zones or [1, 2]
        mins = {z: float((durations or {}).get(z, self.durations[z])) for z in zones}
        plan = [(z, mins[z]) for z in sorted(zones) if mins[z] > 0]
        if not plan:
            return False

        if scheduled:
            skip = None
            today = dt_util.now().date()
            if self.rain_sensor_pending():
                # Esperar a ver si es lluvia real o falsa alarma
                await self._wait_until(
                    lambda: not self.rain_sensor_active() or self.rain_sensor_confirmed(),
                    self.rain_confirm_seconds - self.rain_sensor_elapsed() + 0.05,
                )
                if self.is_running:
                    return False
            if self.rain_sensor_confirmed():
                await self._record_rain("sensor de lluvia", notify=False)
                skip = "el sensor de lluvia detecta lluvia"
            elif self.weather_rain_now():
                await self._record_rain("clima de Home Assistant", notify=False)
                skip = f"el clima reporta lluvia ({self._state(self.config.get(CONF_WEATHER))})"
            elif self.last_rain == today:
                skip = "ya llovió hoy"
            else:
                rain_fc, why = await self.forecast_rain_today()
                if rain_fc:
                    skip = why
            if skip:
                await self._skipped(skip)
                return False
            if auto:
                self.last_scheduled = today
                self._save()

        self._stop_reason = None
        self.run_reason = reason
        self._task = self.hass.async_create_background_task(
            self._run(plan, reason), f"{DOMAIN}_run_{self.entry.entry_id}"
        )
        return True

    async def async_stop(self, reason: str = "detenido manualmente") -> None:
        if not self.is_running:
            await self._close_all()
            return
        self._stop_reason = reason
        self._changed.set()
        await asyncio.wait({self._task}, timeout=30)

    async def _skipped(self, reason: str) -> None:
        self.last_skip_reason = reason
        now = dt_util.now()
        self._add_history(now, now + timedelta(minutes=1), "⏭ Riego omitido", reason)
        _LOGGER.info("%s: riego omitido: %s", self.name, reason)
        if self.config.get(CONF_NOTIFY_SKIPS):
            await self._notify(
                "⏭ Riego omitido",
                f"{self.name}: no se riega porque {reason}. Próximo riego: {fmt_dt(self.next_run())}.",
            )
        self._update()

    def _add_history(self, start: datetime, end: datetime, summary: str, desc: str | None) -> None:
        self.history.append({
            "uid": f"hist-{uuid.uuid4().hex[:10]}",
            "start": start.isoformat(),
            "end": max(end, start + timedelta(minutes=1)).isoformat(),
            "summary": summary,
            "description": desc,
        })
        self.history = self.history[-150:]
        self._save()

    async def _wait_until(self, predicate: Callable[[], bool], timeout: float | None) -> bool:
        """Espera a que predicate() sea True. False si vence el timeout."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            self._changed.clear()
            if predicate():
                return True
            left = None if deadline is None else deadline - loop.time()
            if left is not None and left <= 0:
                return False
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=left)
            except TimeoutError:
                return predicate()

    def _set_state(self, state: str) -> None:
        self.state = state
        self._update()

    async def _run(self, plan: list[tuple[int, float]], reason: str) -> None:
        minute = const.SECONDS_PER_MINUTE
        max_wait = float(self.config.get(CONF_MAX_WAIT) or 0)
        wait_timeout = max_wait * minute if max_wait > 0 else None
        stopped = lambda: self._stop_reason is not None  # noqa: E731
        started_at: datetime | None = None
        watered: dict[int, float] = {}
        loop = asyncio.get_running_loop()
        self._remaining = {z: m * minute for z, m in plan}
        try:
            # 1. Esperar a que todo esté cerrado (condición de inicio)
            abiertas = self.openings_open()
            if abiertas:
                self.pause_reason = ", ".join(abiertas)
                self._set_state(STATE_WAITING)
                if self.config.get(CONF_NOTIFY_PAUSES):
                    await self._notify(
                        "⏳ Riego en espera",
                        f"{self.name}: esperando que se cierre {self.pause_reason} para iniciar.",
                    )
                await self._wait_until(lambda: stopped() or not self.openings_open(), wait_timeout)
                if stopped():
                    return
                if not ok:
                    await self._skipped(f"{', '.join(self.openings_open())} siguió abierta más de {max_wait:g} min")
                    return

            started_at = dt_util.now()
            self.last_run_start = started_at
            await self._notify("💧 Riego iniciado", f"{self.name}: {self._plan_str(plan)}. Motivo: {reason}.")

            for idx, (zone, _mins) in enumerate(plan):
                if stopped():
                    break
                if idx > 0:
                    await self._wait_until(stopped, const.ZONE_GAP_SECONDS)
                    if stopped():
                        break
                remaining = self._remaining[zone]
                self.active_zone = zone
                while remaining > 0.05 and not stopped():
                    abiertas = self.openings_open()
                    if abiertas:
                        # Pausa: cerrar válvula y esperar
                        await self._set_valve(self.zones[zone][1], False)
                        self.pause_reason = ", ".join(abiertas)
                        self.zone_ends_at = None
                        self._set_state(STATE_PAUSED)
                        if self.config.get(CONF_NOTIFY_PAUSES):
                            await self._notify(
                                "⏸ Riego en pausa",
                                f"{self.name}: se abrió {self.pause_reason}. "
                                f"Se reanudará {self.zones[zone][0]} al cerrar "
                                f"(faltan {remaining / minute:.0f} min).",
                            )
                        await self._wait_until(lambda: stopped() or not self.openings_open(), wait_timeout)
                        if stopped():
                            break
                        if not ok:
                            self._stop_reason = f"{self.pause_reason} siguió abierta más de {max_wait:g} min"
                            break
                        self.pause_reason = None
                        if self.config.get(CONF_NOTIFY_PAUSES):
                            await self._notify("▶️ Riego reanudado", f"{self.name}: continúa {self.zones[zone][0]}.")
                    # Regla: nunca dos zonas a la vez
                    await self._close_all(except_zone=zone)
                    await self._set_valve(self.zones[zone][1], True)
                    self.zone_ends_at = dt_util.now() + timedelta(seconds=remaining)
                    self._set_state(STATE_WATERING)
                    t0 = loop.time()
                    await self._wait_until(lambda: stopped() or bool(self.openings_open()), remaining)
                    elapsed = loop.time() - t0
                    remaining -= elapsed
                    watered[zone] = watered.get(zone, 0) + elapsed
                    self._remaining[zone] = max(0.0, remaining)
                    await self._set_valve(self.zones[zone][1], False)
                self._remaining[zone] = max(0.0, remaining)
        except asyncio.CancelledError:
            if self._stop_reason is None:
                self._stop_reason = "cancelado"
            raise
        finally:
            await self._close_all()
            self.active_zone = None
            self.zone_ends_at = None
            self.pause_reason = None
            self._remaining = {}
            self.state = STATE_IDLE if self.enabled else STATE_DISABLED
            if started_at is not None:
                end = dt_util.now()
                self.last_run_end = end
                resumen = ", ".join(
                    f"{self.zones[z][0]} {watered.get(z, 0) / minute:.0f} min" for z, _ in plan
                )
                total = (end - started_at).total_seconds() / 60
                if self._stop_reason:
                    title = "⛔ Riego detenido"
                    msg = f"{self.name}: {self._stop_reason}. Regado: {resumen}."
                else:
                    title = "✅ Riego terminado"
                    msg = f"{self.name}: {resumen}. Tiempo total {total:.0f} min. Próximo: {fmt_dt(self.next_run())}."
                self._add_history(started_at, end, title, msg)
                try:
                    await self._notify(title, msg)
                except Exception:  # noqa: BLE001
                    pass
            self._save()
            self._update()

    def _plan_str(self, plan: list[tuple[int, float]]) -> str:
        return " → ".join(f"{self.zones[z][0]} ({m:g} min)" for z, m in plan)

    def remaining_minutes(self) -> float:
        if not self.is_running:
            return 0.0
        total = 0.0
        for z, secs in self._remaining.items():
            if z == self.active_zone and self.zone_ends_at and self.state == STATE_WATERING:
                total += max(0.0, (self.zone_ends_at - dt_util.now()).total_seconds())
            else:
                total += secs
        return round(total / const.SECONDS_PER_MINUTE, 1)

    # --------------------------------------------------------------- valves
    async def _set_valve(self, entity_id: str, on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        if domain == "valve":
            service = "open_valve" if on else "close_valve"
        else:
            service = "turn_on" if on else "turn_off"
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("%s: no se pudo %s %s: %s", self.name, service, entity_id, err)
            if on:
                raise

    async def _close_all(self, except_zone: int | None = None) -> None:
        for z, (_, ent) in self.zones.items():
            if z == except_zone:
                continue
            if (self._state(ent) or "off") not in ("off", "closed", "closing"):
                await self._set_valve(ent, False)

    # --------------------------------------------------------- notifications
    async def _notify(self, title: str, message: str) -> None:
        for svc in self.config.get(CONF_NOTIFY) or []:
            domain, _, service = svc.partition(".")
            if not service:
                domain, service = "notify", domain
            try:
                await self.hass.services.async_call(
                    domain, service, {"title": title, "message": message}, blocking=False
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("%s: no se pudo notificar por %s: %s", self.name, svc, err)
