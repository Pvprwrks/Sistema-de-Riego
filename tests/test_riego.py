"""Pruebas de Riego Inteligente contra Home Assistant real."""

import asyncio
from datetime import date, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from custom_components.riego_inteligente.const import DOMAIN

from .conftest import BASE

TZ = "America/Mexico_City"


async def setup_entry(hass, **over):
    entry = MockConfigEntry(domain=DOMAIN, data={**BASE, **over}, title="Riego Jardín")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def wait_idle_frozen(hass, engine, freezer, steps=400):
    for _ in range(steps):
        if not engine.is_running:
            return
        freezer.tick(0.05)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    raise AssertionError("el riego no terminó")


async def wait_idle(engine, timeout=5):
    for _ in range(int(timeout / 0.02)):
        if not engine.is_running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("el riego no terminó")


# ----------------------------------------------------------------- config flow
async def test_config_flow_completo(hass: HomeAssistant, rig):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    # misma válvula en las dos zonas -> error
    bad = {k: BASE[k] for k in ("name", "zone1_name", "zone1_entity", "zone2_name")} | {"zone2_entity": "input_boolean.valvula_1"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], bad)
    assert r["errors"] == {"base": "same_entity"}
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {k: BASE[k] for k in ("name", "zone1_name", "zone1_entity", "zone2_name", "zone2_entity")})
    assert r["step_id"] == "conditions"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "rain_sensor": BASE["rain_sensor"], "weather_entity": "weather.casa", "use_forecast": True,
        "forecast_threshold": 70, "stop_on_rain": True, "openings": BASE["openings"], "max_wait": 60})
    assert r["step_id"] == "schedule"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "weekdays": ["mon", "wed", "fri"], "interval_days": 1, "time1": "06:00:00", "time2": "19:30:00",
        "duration1": 10, "duration2": 12})
    assert r["step_id"] == "notify"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "notify_services": ["notify.mobile_app_iphone"], "notify_skips": True, "notify_pauses": True})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    states = {s.entity_id for s in hass.states.async_all()}
    for ent in ("calendar.riego_jardin_calendario", "sensor.riego_jardin_estado",
                "sensor.riego_jardin_proximo_riego", "switch.riego_jardin_programa_activo",
                "button.riego_jardin_regar_ahora", "number.riego_jardin_minutos_jardin_frontal",
                "binary_sensor.riego_jardin_lluvia", "binary_sensor.riego_jardin_puertas_y_ventanas"):
        assert ent in states, (ent, sorted(s for s in states if "riego" in s))

    # Opciones: cambiar programa desde la interfaz
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "schedule"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "weekdays": ["sat"], "interval_days": 1, "time1": "07:15:00", "duration1": 5, "duration2": 5})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    eng = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert [t.strftime("%H:%M") for t in eng.times] == ["07:15"]
    assert eng.durations == {1: 5.0, 2: 5.0}


# ------------------------------------------------------------ secuencia y notif
async def test_regar_ahora_secuencial_y_notifica(hass, rig):
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    await hass.services.async_call("button", "press", {"entity_id": "button.riego_jardin_regar_ahora"}, blocking=True)
    await asyncio.sleep(0.03)
    assert hass.states.get("sensor.riego_jardin_estado").state == "regando"
    assert hass.states.get("input_boolean.valvula_1").state == "on"
    assert hass.states.get("input_boolean.valvula_2").state == "off"
    await wait_idle(eng)
    await hass.async_block_till_done()
    assert not rig.overlap
    opens = [e for e, on in rig.log if on]
    assert opens == ["input_boolean.valvula_1", "input_boolean.valvula_2"]
    assert rig.titles()[0] == "💧 Riego iniciado"
    assert rig.titles()[-1] == "✅ Riego terminado"
    assert hass.states.get("input_boolean.valvula_1").state == "off"
    assert hass.states.get("input_boolean.valvula_2").state == "off"
    assert hass.states.get("sensor.riego_jardin_ultimo_riego").state not in ("unknown", None)


async def test_pausa_con_puerta_y_reanuda(hass, rig):
    entry = await setup_entry(hass, duration1=4, duration2=0)
    eng = entry.runtime_data
    await eng.async_start(zones=[1])
    await asyncio.sleep(0.08)
    hass.states.async_set("binary_sensor.puerta_cocina", "on", {"friendly_name": "Puerta cocina"})
    await asyncio.sleep(0.03)
    assert eng.state == "pausado"
    assert hass.states.get("input_boolean.valvula_1").state == "off"
    await asyncio.sleep(0.3)  # tiempo en pausa no cuenta
    assert eng.is_running
    hass.states.async_set("binary_sensor.puerta_cocina", "off", {"friendly_name": "Puerta cocina"})
    await asyncio.sleep(0.03)
    assert eng.state == "regando"
    assert hass.states.get("input_boolean.valvula_1").state == "on"
    await wait_idle(eng)
    await hass.async_block_till_done()
    titles = rig.titles()
    assert "⏸ Riego en pausa" in titles and "▶️ Riego reanudado" in titles
    assert titles[-1] == "✅ Riego terminado"
    assert not rig.overlap


async def test_espera_a_que_cierren_para_iniciar(hass, rig):
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    hass.states.async_set("binary_sensor.ventana_sala", "on", {"friendly_name": "Ventana sala"})
    await eng.async_start()
    await asyncio.sleep(0.05)
    assert eng.state == "esperando_cierre"
    assert hass.states.get("input_boolean.valvula_1").state == "off"
    assert "💧 Riego iniciado" not in rig.titles()
    hass.states.async_set("binary_sensor.ventana_sala", "off", {"friendly_name": "Ventana sala"})
    await asyncio.sleep(0.03)
    assert eng.state == "regando"
    await wait_idle(eng)


async def test_espera_vencida_cancela(hass, rig):
    entry = await setup_entry(hass, max_wait=2)  # 2 "min" = 0.1 s
    eng = entry.runtime_data
    hass.states.async_set("binary_sensor.ventana_sala", "on", {"friendly_name": "Ventana sala"})
    await eng.async_start()
    await wait_idle(eng)
    await hass.async_block_till_done()
    assert "⏭ Riego omitido" in rig.titles()
    assert not any(on for _, on in rig.log)


async def test_interbloqueo_de_zonas(hass, rig):
    entry = await setup_entry(hass, duration1=6, duration2=0)
    eng = entry.runtime_data
    await eng.async_start(zones=[1])
    await asyncio.sleep(0.05)
    # alguien abre la zona 2 a mano
    await hass.services.async_call("input_boolean", "turn_on", {"entity_id": "input_boolean.valvula_2"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_boolean.valvula_2").state == "off"
    await eng.async_stop()
    assert hass.states.get("input_boolean.valvula_1").state == "off"
    assert rig.titles()[-1] == "⛔ Riego detenido"


async def test_lluvia_durante_riego_detiene(hass, rig):
    entry = await setup_entry(hass, duration1=10)
    eng = entry.runtime_data
    await eng.async_start()
    await asyncio.sleep(0.05)
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()
    await wait_idle(eng)
    assert hass.states.get("input_boolean.valvula_1").state == "off"
    assert eng.last_rain == dt_util.now().date()
    assert any("lluvia" in m for _, m in rig.notes)


# ------------------------------------------------------------------ programa
async def test_programa_y_reinicio_por_lluvia(hass, rig, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 0, tzinfo=dt_util.get_time_zone(TZ)))
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    assert eng.due_dates(date(2026, 10, 6)) == [date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 2),
                                               date(2026, 10, 4), date(2026, 10, 6)]
    # llueve el 28 -> se reinicia: siguiente = 30
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()
    assert eng.last_rain == date(2026, 9, 28)
    assert eng.due_dates(date(2026, 10, 3)) == [date(2026, 9, 30), date(2026, 10, 2)]
    nxt = dt_util.as_local(eng.next_run())
    assert (nxt.date(), nxt.hour) == (date(2026, 9, 30), 6)
    assert "🌧 Lluvia detectada" in rig.titles()
    hass.states.async_set("binary_sensor.lluvia_shelly", "off")
    # llueve de nuevo el 29 -> siguiente = 1 oct
    freezer.move_to(datetime(2026, 9, 29, 12, 0, tzinfo=dt_util.get_time_zone(TZ)))
    hass.states.async_set("weather.casa", "rainy")
    await hass.async_block_till_done()
    assert eng.due_dates(date(2026, 10, 3)) == [date(2026, 10, 1), date(2026, 10, 3)]


async def test_dias_permitidos(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 0, tzinfo=dt_util.get_time_zone(TZ)))  # lunes
    entry = await setup_entry(hass, weekdays=["mon", "thu"], interval_days=1)
    eng = entry.runtime_data
    assert eng.due_dates(date(2026, 10, 6)) == [date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 5)]


async def test_disparo_programado(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    tz = dt_util.get_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 59, 30, tzinfo=tz))
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    freezer.move_to(datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    async_fire_time_changed(hass, datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    await hass.async_block_till_done()
    assert eng.is_running
    await wait_idle_frozen(hass, eng, freezer)
    assert eng.last_scheduled == date(2026, 9, 28)
    assert rig.titles()[0] == "💧 Riego iniciado"
    assert eng.due_dates(date(2026, 10, 2))[:2] == [date(2026, 9, 30), date(2026, 10, 2)]


async def test_disparo_con_lluvia_omite_y_reinicia(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    tz = dt_util.get_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 59, 30, tzinfo=tz))
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    # el sensor ya marcaba lluvia antes (sin registrar evento de cambio)
    hass.states.async_set("binary_sensor.lluvia_shelly", "wet", force_update=True)
    eng.rain_dates.clear()
    freezer.move_to(datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    async_fire_time_changed(hass, datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    await hass.async_block_till_done()
    assert not eng.is_running
    assert eng.last_rain == date(2026, 9, 28)
    assert "⏭ Riego omitido" in rig.titles()
    assert not any(on for _, on in rig.log)


async def test_pronostico_omite_sin_reiniciar(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    tz = dt_util.get_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 59, 30, tzinfo=tz))
    rig.forecast = [{"datetime": "2026-09-28T12:00:00-06:00", "precipitation_probability": 85, "condition": "rainy"}]
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    freezer.move_to(datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    async_fire_time_changed(hass, datetime(2026, 9, 28, 6, 0, 0, tzinfo=tz))
    await hass.async_block_till_done()
    assert not eng.is_running
    assert eng.last_rain is None
    assert any("85%" in m for _, m in rig.notes)
    # como no regó, se intenta mañana
    assert eng.due_dates(date(2026, 9, 30))[0] == date(2026, 9, 29)


# ------------------------------------------------------------------ calendario
async def test_calendario_editable(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    tz = dt_util.get_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 0, tzinfo=tz))
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    cal = "calendar.riego_jardin_calendario"

    async def events(start, end):
        r = await hass.services.async_call("calendar", "get_events",
                                           {"entity_id": cal, "start_date_time": start, "end_date_time": end},
                                           blocking=True, return_response=True)
        return r[cal]["events"]

    ev = await events("2026-09-28T00:00:00-06:00", "2026-10-03T00:00:00-06:00")
    assert [e["start"][:16] for e in ev] == ["2026-09-28T06:00", "2026-09-30T06:00", "2026-10-02T06:00"]

    # Crear riego extra el 29 a las 20:00
    await hass.services.async_call("calendar", "create_event", {
        "entity_id": cal, "summary": "Riego extra", "start_date_time": "2026-09-29 20:00:00",
        "end_date_time": "2026-09-29 20:30:00"}, blocking=True)
    # Vacaciones: no regar el 2 de octubre
    await hass.services.async_call("calendar", "create_event", {
        "entity_id": cal, "summary": "No regar", "start_date": "2026-10-02", "end_date": "2026-10-03"}, blocking=True)
    ev = await events("2026-09-28T00:00:00-06:00", "2026-10-05T00:00:00-06:00")
    summaries = [(e["start"][:16], e["summary"]) for e in ev]
    assert ("2026-09-29T20:00", "Riego extra") in summaries
    assert ("2026-10-02", "No regar") in summaries
    assert ("2026-10-02T06:00", "💧 Riego programado") not in summaries
    assert ("2026-10-03T06:00", "💧 Riego programado") in summaries  # se recorre al día siguiente

    # Borrar (omitir) el riego automático del 30 desde el calendario
    await eng_delete(hass, cal, "auto-2026-09-30T06:00")
    ev = await events("2026-09-28T00:00:00-06:00", "2026-10-01T00:00:00-06:00")
    assert not any(e["start"].startswith("2026-09-30T06:00") for e in ev)

    # El riego extra se dispara a su hora
    freezer.move_to(datetime(2026, 9, 29, 20, 0, 0, tzinfo=tz))
    async_fire_time_changed(hass, datetime(2026, 9, 29, 20, 0, 0, tzinfo=tz))
    await hass.async_block_till_done()
    assert eng.is_running and "calendario" in eng.run_reason
    await wait_idle_frozen(hass, eng, freezer)


async def eng_delete(hass, cal, uid):
    entity = hass.data["calendar"].get_entity(cal)
    await entity.async_delete_event(uid)


async def test_recurrente_semanal(hass, rig, freezer):
    await hass.config.async_set_time_zone(TZ)
    tz = dt_util.get_time_zone(TZ)
    freezer.move_to(datetime(2026, 9, 28, 5, 0, tzinfo=tz))
    entry = await setup_entry(hass, weekdays=["sun"])  # casi sin programa automático
    eng = entry.runtime_data
    cal = hass.data["calendar"].get_entity("calendar.riego_jardin_calendario")
    tzinfo = dt_util.get_default_time_zone()
    await cal.async_create_event(dtstart=datetime(2026, 9, 29, 19, 0, tzinfo=tzinfo),
                                 dtend=datetime(2026, 9, 29, 19, 30, tzinfo=tzinfo),
                                 summary="Riego martes", rrule="FREQ=WEEKLY;BYDAY=TU")
    evs = await cal.async_get_events(hass, datetime(2026, 9, 28, tzinfo=tzinfo), datetime(2026, 10, 21, tzinfo=tzinfo))
    tues = [e for e in evs if e.summary == "Riego martes"]
    assert [e.start.day for e in tues] == [29, 6, 13, 20]
    # borrar solo la del 6 de octubre
    await cal.async_delete_event(tues[1].uid, recurrence_id=tues[1].recurrence_id)
    evs = await cal.async_get_events(hass, datetime(2026, 9, 28, tzinfo=tzinfo), datetime(2026, 10, 21, tzinfo=tzinfo))
    assert [e.start.day for e in evs if e.summary == "Riego martes"] == [29, 13, 20]


async def test_numeros_y_switch(hass, rig):
    entry = await setup_entry(hass)
    eng = entry.runtime_data
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.riego_jardin_minutos_jardin_frontal", "value": 25}, blocking=True)
    assert eng.durations[1] == 25
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.riego_jardin_programa_activo"},
                                   blocking=True)
    assert eng.enabled is False
    assert hass.states.get("sensor.riego_jardin_estado").state == "deshabilitado"
    # persiste tras recargar
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    eng2 = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
    assert eng2.durations[1] == 25 and eng2.enabled is False


# ------------------------------------------------- confirmación de lluvia
async def test_falsa_alarma_no_cuenta_como_lluvia(hass, rig):
    entry = await setup_entry(hass, rain_confirm_minutes=4, duration1=14, duration2=0)  # 4 min = 0.2 s
    eng = entry.runtime_data
    await eng.async_start(zones=[1])
    await asyncio.sleep(0.03)
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()
    assert eng.rain_sensor_pending() and not eng.is_raining_now()
    assert hass.states.get("binary_sensor.riego_jardin_lluvia").attributes["sensor_lluvia_sin_confirmar"]
    await asyncio.sleep(0.1)
    hass.states.async_set("binary_sensor.lluvia_shelly", "off")
    await hass.async_block_till_done()
    await asyncio.sleep(0.25)
    assert eng.last_rain is None
    assert eng.is_running  # siguió regando
    await wait_idle(eng)
    assert rig.titles()[-1] == "✅ Riego terminado"


async def test_lluvia_confirmada_detiene_y_reinicia(hass, rig):
    entry = await setup_entry(hass, rain_confirm_minutes=2, duration1=20, duration2=0)  # 0.1 s
    eng = entry.runtime_data
    await eng.async_start(zones=[1])
    await asyncio.sleep(0.03)
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()
    assert eng.last_rain is None and eng.is_running
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
    await wait_idle(eng)
    assert eng.last_rain == dt_util.now().date()
    assert rig.titles()[-1] == "⛔ Riego detenido"
    assert "🌧 Lluvia detectada" in rig.titles()


async def test_programado_espera_confirmacion_falsa_alarma(hass, rig):
    entry = await setup_entry(hass, rain_confirm_minutes=4)
    eng = entry.runtime_data
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()

    async def secar():
        await asyncio.sleep(0.08)
        hass.states.async_set("binary_sensor.lluvia_shelly", "off")

    hass.async_create_task(secar())
    assert await eng.async_start(scheduled=True, reason="programa") is True
    await wait_idle(eng)
    assert eng.last_rain is None
    assert rig.titles()[-1] == "✅ Riego terminado"


async def test_programado_espera_confirmacion_lluvia_real(hass, rig):
    entry = await setup_entry(hass, rain_confirm_minutes=2)
    eng = entry.runtime_data
    hass.states.async_set("binary_sensor.lluvia_shelly", "on")
    await hass.async_block_till_done()
    assert await eng.async_start(scheduled=True, reason="programa") is False
    assert eng.last_rain == dt_util.now().date()
    assert "⏭ Riego omitido" in rig.titles()
    assert not any(on for _, on in rig.log)
