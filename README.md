# 💧 Riego Inteligente (Home Assistant · HACS)

Controlador de riego de **2 zonas** para Home Assistant con calendario editable, reinicio por lluvia (sensor Shelly + clima), bloqueo por puertas/ventanas y notificaciones al celular. Todo se configura desde la interfaz, sin YAML.

## Qué hace

| # | Función | Cómo |
|---|---------|------|
| 1 | Regar 2 zonas según un calendario | Programa por **días permitidos** + **cada N días** + horarios |
| 2 | Calendario editable en HA | Entidad `calendar.*_calendario`: ver, crear, mover y borrar riegos desde **Calendario** |
| 3 | Lluvia (Shelly) reinicia los días | Al detectar lluvia se registra el día y el siguiente riego queda **N días después de la lluvia** |
| 3b | Evitar falsas alarmas del sensor | El Shelly debe marcar lluvia **sin interrupción** durante X minutos (10 por defecto) para contar como lluvia. Si se apaga antes, se ignora |
| 4 | Sólo inicia con puertas/ventanas cerradas | Eliges las entidades; si algo está abierto, espera (tiempo máximo configurable) |
| 5 | Minutos por zona | Entidades `number` (cambiables desde el dashboard) |
| 6 | Pausa al abrir / reanuda al cerrar | Cierra la válvula, congela el tiempo restante y reanuda donde se quedó |
| 7 | Horarios del día | Hasta 3 horarios por día de riego |
| 8 | Clima de HA como segunda condición | Si el clima actual es lluvia **o** el pronóstico de hoy supera el umbral (p. ej. 70 %), ese día no se riega |
| 9 | Nunca dos zonas a la vez | Riego secuencial con pausa de seguridad entre zonas; si alguien abre la otra zona a mano mientras una riega, se cierra sola |
| 10 | Notificaciones | Al iniciar y al terminar (más avisos opcionales de omisión y pausa) |

## Instalación

### Opción A — HACS (repositorio personalizado)
1. Repositorio: [Pvprwrks/sistema-de-riego](https://github.com/Pvprwrks/sistema-de-riego) (público, con `hacs.json`, `README.md` y `custom_components/` en la raíz).
2. HACS → ⋮ → **Repositorios personalizados** → `https://github.com/Pvprwrks/sistema-de-riego`, tipo **Integración**.
3. Busca **Riego Inteligente** → Descargar → reinicia Home Assistant.

### Opción B — Manual
Copia `custom_components/riego_inteligente/` a `/config/custom_components/` y reinicia Home Assistant.

### Configurar
**Configuración → Dispositivos y servicios → Agregar integración → Riego Inteligente**. El asistente tiene 4 pasos:

1. **Zonas** – nombre y válvula/relé de cada zona (`switch`, `valve`, `input_boolean` o `light`).
2. **Condiciones** – sensor de lluvia Shelly, entidad de clima, umbral de pronóstico, puertas y ventanas, espera máxima.
3. **Programa** – días permitidos, cada N días, horarios, minutos por zona.
4. **Notificaciones** – p. ej. `notify.mobile_app_iphone_de_eduardo`.

Todo se puede cambiar después en **Configurar** (menú por sección).

## Cómo funciona el programa

- **Cada N días + días permitidos:** después de un riego, el siguiente es el primer día permitido que caiga **N días o más** después.
- **Confirmación de lluvia:** el Shelly sólo cuenta como lluvia si la marca de forma continua durante los minutos configurados. Si a la hora de regar el sensor apenas se activó, el riego espera ese tiempo: si se apaga, riega normal; si sigue, se omite el día. Durante un riego, sólo se detiene cuando la lluvia se confirma.
- **Lluvia:** si el Shelly o el clima reportan lluvia (en cualquier momento del día), el día queda marcado en el calendario (🌧) y el siguiente riego queda al menos N días después.
- **Pronóstico:** si a la hora de regar el pronóstico de hoy supera el umbral, se omite **sin** reiniciar el conteo, y se vuelve a intentar al día siguiente.
- **Puertas/ventanas:** si al iniciar hay algo abierto, espera; si pasa la espera máxima, se cancela y se reintenta al día siguiente. Si se abre algo durante el riego, pausa; el tiempo en pausa no cuenta.

## Seguimiento para tu interfaz (v1.2)

| Qué | Entidad | Atributos útiles |
|-----|---------|------------------|
| Última vez que se regó | `sensor.riego_jardin_ultimo_riego` (fecha/hora de inicio) | `inicio`, `fin`, `texto` ("lun 28 sep 06:00 – 06:31"), `resultado`, `zonas` |
| Tiempo encendido | `sensor.riego_jardin_duracion_ultimo_riego` (min con válvula abierta) | `duracion_total_min` (incluye pausas), `por_zona` |
| Agua usada | `sensor.riego_jardin_agua_ultimo_riego` (L) | `por_zona` |
| Agua acumulada | `sensor.riego_jardin_agua_total_riego` (L, se puede agregar en **Energía → Agua**) | — |
| Próximo riego | `sensor.riego_jardin_proximo_riego` | `fecha`, `hora`, `dia`, `dias_restantes`, `texto` ("Mañana 29 sep a las 06:00"), `plan` |
| Aviso previo activo | `binary_sensor.riego_jardin_aviso_de_riego_proximo` | `inicio`, `abiertas` |

**Agua:** en *Configurar → Zonas* elige el sensor del Sonoff para cada zona. Puede ser de **caudal** (L/min, m³/h: se integra en el tiempo) o de **total acumulado** (L, m³: se resta inicio contra fin). Si es un solo medidor para las dos zonas, elige el mismo en ambas; como nunca riegan juntas, el reparto por zona es correcto. Los sensores de agua sólo aparecen si configuraste un medidor.

**Aviso previo:** X minutos antes de cada riego (15 por defecto, *Configurar → Notificaciones*) llega "El sistema de riego está próximo a iniciar en 15 minutos. Por favor cierra puertas y ventanas", con la lista de lo que esté abierto, al celular y a la campana de Home Assistant. No se manda si ese riego se va a omitir por lluvia o pronóstico. También se dispara el evento `riego_inteligente_aviso_previo` para automatizaciones (por ejemplo, anunciarlo en bocinas). El riego no inicia hasta que todo esté cerrado.

## Calendario

Abre **Calendario** en la barra lateral y activa *Riego Jardín – Calendario*:

- **💧 Riego programado** → riegos automáticos. **Borrarlo** omite ese riego (cuenta como ciclo cumplido). **Moverlo** lo convierte en un riego manual a la nueva hora.
- **Crear evento con hora** → riego extra a esa hora (admite repetición, p. ej. cada martes).
- **Crear evento de día completo** → riega ese día en tus horarios configurados.
- **Evento llamado "No regar"** (o "Sin riego" / "Omitir") → bloquea el riego esos días (vacaciones, fumigación, jardinero). El programa se recorre al siguiente día libre.
- Historial (✅ terminado, ⛔ detenido, ⏭ omitido) y días de 🌧 lluvia. Si borras un día de lluvia, se deshace el reinicio.

## Entidades

| Entidad | Uso |
|---------|-----|
| `switch.…_programa_activo` | Encender/apagar el programa automático |
| `sensor.…_estado` | inactivo · esperando cierre · regando · en pausa · deshabilitado (atributos: zona activa, motivo, qué está abierto…) |
| `sensor.…_proximo_riego` / `…_ultimo_riego` / `…_ultima_lluvia` | Fechas |
| `sensor.…_tiempo_restante` | Minutos restantes del ciclo |
| `number.…_minutos_<zona>` / `number.…_cada_n_dias` | Ajustes rápidos |
| `button.…_regar_ahora` / `…_regar_<zona>` / `…_detener_riego` / `…_omitir_proximo_riego` | Acciones |
| `binary_sensor.…_regando` / `…_lluvia` / `…_puertas_y_ventanas` | Estado combinado |
| `calendar.…_calendario` | Calendario editable |

Los botones de "Regar ahora" **ignoran la lluvia**, pero sí respetan puertas/ventanas y la secuencia de zonas.

## Servicios (para automatizaciones)

```yaml
action: riego_inteligente.start
data:
  zones: ["1"]            # opcional, default ambas
  duration_zone_1: 8      # opcional
  ignore_conditions: false  # revisar lluvia/pronóstico antes
---
action: riego_inteligente.stop
---
action: riego_inteligente.skip_next
```

## Tarjeta de ejemplo

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.riego_jardin_estado
  - type: entities
    entities:
      - switch.riego_jardin_programa_activo
      - sensor.riego_jardin_proximo_riego
      - sensor.riego_jardin_tiempo_restante
      - number.riego_jardin_minutos_zona_1
      - number.riego_jardin_minutos_zona_2
      - number.riego_jardin_cada_n_dias
      - binary_sensor.riego_jardin_lluvia
      - binary_sensor.riego_jardin_puertas_y_ventanas
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.riego_jardin_regar_ahora
        tap_action:
          action: perform-action
          perform_action: button.press
          target: {entity_id: button.riego_jardin_regar_ahora}
      - type: button
        entity: button.riego_jardin_detener_riego
        tap_action:
          action: perform-action
          perform_action: button.press
          target: {entity_id: button.riego_jardin_detener_riego}
      - type: button
        entity: button.riego_jardin_omitir_proximo_riego
        tap_action:
          action: perform-action
          perform_action: button.press
          target: {entity_id: button.riego_jardin_omitir_proximo_riego}
  - type: calendar
    entities: [calendar.riego_jardin_calendario]
    initial_view: listWeek
```
*(Los `entity_id` dependen del nombre que le des al sistema y a las zonas.)*

## Seguridad
- Al arrancar o recargar, la integración **cierra ambas válvulas**.
- Un sensor `unavailable` no bloquea el riego; revisa que tus Shelly estén en línea.
- Los estados que se toman como lluvia son `on`, `wet` o un valor numérico > 0. Los que se toman como abierto son `on`, `open` u `opening`.
