# 16b: Progreso por etapa con SSE

**Status:** cerrado el 2026-11-01 (commit `8c7a07b`; grilling del 2026-10-29, 26 decisiones)

**Fase:** 3 — interfaz web

**Bloqueado por:** 16a

**Paraguas:** [T16](16-api-http-y-front-web.md)

## Resumen

Segunda rebanada: cada job publica un evento al iniciar, terminar o fallar cada etapa del pipeline, y un endpoint SSE los transmite al cliente. Los eventos salen de un decorador del puerto `TraceLifecycle`, así que `pipeline.py` y las etapas no cambian. El polling de 16a sigue siendo la fuente de verdad y el respaldo.

No hay `attempt`: el reintento semántico corre dentro del mismo span y no es observable desde el puerto. Este commit escribe en `plan.md` la decisión 111.

## Resultado vertical

### `progress_tracer.py`

- `ProgressTracer`: decorador de `TraceLifecycle`. Recibe el tracer real (el `LangfuseTracer` del job) y el `JobEvents` del job.
- Pasa `open_root`, `flush`, `score`, `trace_id` y `callbacks` sin tocarlos.
- Envuelve `stage(name, ...)`: publica `started` al entrar, y al salir del context manager `done` o `failed` según haya habido excepción. Envuelve también el `StageSpan` devuelto para delegar `set_output`, `set_metadata` y `set_error`.
- Nunca traga ni reemplaza el error original: el `failed` se publica y la excepción sigue su camino.
- Las trazas de Langfuse son idénticas con y sin `ProgressTracer`.

### Eventos

- Dos tipos en el mismo stream, con discriminador `kind`:

  ```json
  {"seq": 4, "kind": "stage", "stage": "extraction_agent", "status": "started", "at": "2026-10-29T18:03:11.412Z"}
  ```

  ```json
  {"seq": 11, "kind": "job", "status": "done", "at": "2026-10-29T18:03:40.009Z"}
  ```

- `stage` es un `StageName`; `status` de etapa es `started | done | failed`; `status` de job es `done | human_review | failed`.
- El evento `job` lo publica `job_runner.py` al guardar el estado terminal, después del último evento de etapa.
- Ambas formas van al OpenAPI como `oneOf` discriminado por `kind`, para que 16c genere una unión.

### Log de eventos

- `InMemoryJobEvents` (nace en 16a, se completa acá): append-only, `seq` creciente desde 1, `threading.Condition`.
- `since(seq)` devuelve los eventos posteriores; `wait(seq, timeout)` bloquea hasta que llegue uno nuevo o venza el timeout.
- El puerto `JobEvents` permite pasar más adelante a Redis Streams sin cambiar el endpoint.

### `GET /analyses/{id}/events`

- `text/event-stream` con `StreamingResponse`, a mano: `id: <seq>` y `data: {...}` por evento. Sin `sse-starlette`.
- Respeta `Last-Event-ID`: arranca desde el evento siguiente, sin pérdidas ni duplicados.
- Comentario de keep-alive (`: keep-alive`) cada 15 s mientras no haya eventos.
- Cierra el stream después del evento `job`. Conectarse a un job ya terminado devuelve los eventos pendientes y el terminal, y cierra.
- `404` solo al abrir (id inexistente, vencido o de otra key); si el job vence a mitad del stream, el stream ya tiene el terminal.
- Puente thread → asyncio: el generador espera con `await asyncio.to_thread(events.wait(seq, timeout))`, nunca bloquea el event loop.
- Detecta la desconexión del cliente con `request.is_disconnected()` y corta el generador.
- Misma auth (`X-API-Key`) y mismo ligado a la key que el resto de la API.

## Decisiones tomadas en el grilling (2026-10-29)

Las que gobiernan esta rebanada; el detalle completo está en [T16](16-api-http-y-front-web.md).

1. **`attempt` sale** (revierte el ticket original). El reintento semántico corre dentro del mismo span `contextualization_agent` / `extraction_agent` y solo cambia el `run_name` de LangChain; un decorador de `stage()` no lo ve. Observarlo exige un `BaseCallbackHandler` acoplado a strings de los agentes para un evento raro en la demo. Queda como ticket derivado.
2. **SSE a mano con `StreamingResponse`**, sin `sse-starlette` (revierte).
3. **Puente thread → asyncio** con `Condition` + `asyncio.to_thread`: el log se testea sin event loop y sirve al polling y al stream.
4. **Discriminador `kind`** para distinguir eventos de etapa y evento terminal en el mismo stream.
5. **Keep-alive cada 15 s**, cierre tras el evento `job`, `404` solo al abrir.
6. **`ProgressTracer` decora `TraceLifecycle` completo**, no solo `PipelineTracer`, porque el runner necesita `open_root` y `flush` a través de él.
7. **Decisión 111** en este commit.

## Configuración

Sin variables nuevas. El intervalo de keep-alive es una constante del módulo.

## Criterios de aceptación

- [ ] `pipeline.py` y los módulos de dominio no cambian.
- [ ] El stream emite un evento `stage` por inicio, fin o error de cada etapa, con `seq`, `kind`, `stage`, `status` y `at`, y cierra con el evento `job`.
- [ ] Una etapa que falla publica `failed` y la excepción original llega intacta al runner.
- [ ] Una reconexión con `Last-Event-ID` continúa desde el evento siguiente, sin pérdidas ni duplicados.
- [ ] Conectarse a un job ya terminado devuelve los eventos pendientes y el terminal, y cierra.
- [ ] Sin eventos nuevos, el stream emite un comentario de keep-alive cada 15 s.
- [ ] Sin API key válida, `401`; con un `id` inexistente, vencido o de otra key, `404`.
- [ ] El generador SSE nunca bloquea el event loop (la espera corre en `asyncio.to_thread`).
- [ ] Las trazas de Langfuse son idénticas con y sin `ProgressTracer`.
- [ ] El OpenAPI documenta las dos formas de evento como `oneOf` por `kind`.
- [ ] `plan.md` registra la decisión 111.
- [ ] El style gate y los unit tests están en verde.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_progress_tracer.py`, con `RecordingTracer` como tracer interno: delega cada llamada (`open_root`, `flush`, `score`, `trace_id`, `callbacks`); publica `started` y `done` con `seq` creciente; publica `failed` y relanza el error original; el `StageSpan` envuelto delega `set_output`, `set_metadata` y `set_error`; lo que registra el `RecordingTracer` es idéntico con y sin decorador.
- `tests/test_jobs_memory.py` (ampliación): `InMemoryJobEvents` entrega los eventos desde un `seq` dado, `wait` despierta a quien espera cuando llega uno nuevo y vence por timeout sin evento.
- `tests/test_job_runner.py` (ampliación): el evento `job` se publica después del último evento de etapa, con el estado terminal correcto.
- `tests/test_api_events.py`, con `TestClient`: orden de eventos, reanudación con `Last-Event-ID`, cierre tras el evento `job`, keep-alive con un reloj inyectado, job ya terminado, `401`, `404` para inexistente y otra key.
- `tests/test_openapi.py`: el esquema de `/analyses/{id}/events` documenta las dos formas con `oneOf` y `kind`.

**GREEN:** `progress_tracer.py`, la ampliación de `InMemoryJobEvents`, el evento terminal en `job_runner.py`, el endpoint SSE en `api.py` y `plan.md`.

## Secciones de plan.md que gobiernan el trabajo

- §5.2 y §5.3 (puerto de observabilidad y jerarquía de la traza)
- §4.4 (reintentos con feedback: por qué el reintento no es observable)
- §6.1 y §6.2
- §8.4: decisión 111 (texto en [T16](16-api-http-y-front-web.md))

## Tickets derivados

- Progreso de reintentos vía callbacks de LangChain: un `BaseCallbackHandler` enganchado a `tracer.callbacks` que observe los `run_name` `contextualization_retry` y `extraction_retry` y publique un evento `retrying`.

## Preguntas abiertas

- Ninguna.
