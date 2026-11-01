# 16: API HTTP con FastAPI y front web con React

**Status:** aprobado para implementar (grilling del 2026-10-29, 26 decisiones); ticket paraguas, se implementa por rebanadas: 16a, 16b y 16c

**Fase:** 3 — interfaz web

**Bloqueado por:** ninguno (T14 cerrado en `c6da650`)

## Resumen

Hoy LegalMove solo se usa desde la CLI. Este ticket suma una API HTTP con FastAPI y un front con Vite, React y TypeScript, para que se pueda subir un par de imágenes desde el navegador y ver cómo avanza el análisis etapa por etapa. El uso previsto es una demo local, pero el diseño sigue un criterio de producción: los contratos y los puertos quedan listos para escalar desde el primer día, y la infraestructura para escalar (cola, Redis, varias réplicas) se agrega solo cuando haga falta, cambiando adaptadores.

El dominio no cambia y `pipeline.py` cambia en una sola cosa: usa la constante compartida de nombres de etapa. La API es un segundo adaptador de entrada que llama al mismo `run_analysis()` que la CLI.

## Cambio de alcance

`plan.md` declara hoy la API HTTP y la ejecución asíncrona fuera de alcance (§1 "Fuera de alcance" y §7.2). La decisión 108 de §8.4 lo reemplaza y ajusta esas dos secciones. Se escribe **dentro del commit de 16a**, junto con 108 y 109, no en un commit previo aparte: un commit de "solo plan" dejaría el plan contradiciendo al repo hasta que llegue el código. La decisión 110 se escribe en el commit de 16b.

## Flujo de punta a punta

1. **Upload.** En el front, el usuario elige la imagen original y la adenda. React valida el tipo (PNG o JPEG) y el tamaño solo como ayuda de UX; la validación que vale es la del backend.
2. **`POST /analyses`** (multipart `original` y `amendment`, header `X-API-Key`). FastAPI valida la key (`401`), el rate limit (`429`), el tamaño del upload (`413`) y, de forma sincrónica, el tipo y las dimensiones de cada imagen con los helpers por bytes de `image_parser` (`422`). Si la cola de pendientes está llena responde `503` con `Retry-After`. Si todo pasa, crea el job en `JobStore` con estado `pending`, ligado al hash de la key, lo encola en `JobRunner` y responde `202 {id}` de inmediato.
3. **Suscripción.** El front abre `GET /analyses/{id}/events` con `fetch` streaming. No se usa `EventSource` porque no permite enviar el header de la API key.
4. **Ejecución.** Un thread del pool de `JobRunner` toma el job. `execute_job` escribe las dos imágenes en un directorio temporal privado del job, construye un `CallBudget` propio, un `LangfuseTracer` propio sobre el cliente del proceso envuelto en `ProgressTracer`, arma `PipelineStages` con ese tracer, abre la raíz de la traza con `session_id = id del job` y llama a `run_analysis()`.
5. **Progreso.** Cada `tracer.stage(...)` delega en Langfuse y además agrega al log del job un evento `started` y después `done` o `failed`, por etapa: `parse_original_contract`, `parse_amendment_contract`, `contextualization_agent`, `extraction_agent` y `output_validation`. El reintento semántico no es visible (ocurre dentro del mismo span); no hay `attempt`.
6. **Stream.** El endpoint SSE emite los eventos a partir de `Last-Event-ID` con `id: <seq>` y `data: {...}`, con un comentario de keep-alive cada 15 s. Si la conexión se corta, el front reconecta con el último `seq` y no pierde ni repite eventos.
7. **Render.** El front muestra un stepper por etapa: pendiente, en curso, lista o fallida.
8. **Fin.** `execute_job` traduce el resultado a un estado terminal, lo guarda en `JobStore`, publica el evento terminal (`kind: "job"`) y el stream SSE se cierra:
   - `AnalysisResult` → `done`, con el `ContractChangeOutput`;
   - `NeedsHumanReviewError` → `human_review`, con el sobre de la CLI y sus `issues`;
   - cualquier otra excepción → `failed`, con el sobre de la CLI sanitizado y sin stack.
9. **Limpieza.** El directorio temporal se borra en un `finally`, haya terminado como haya terminado el job. Las imágenes no sobreviven al job.
10. **Flush.** Al terminar cada job, el runner hace `flush()` del tracer, igual que hoy lo hace `main.py` al cerrar la corrida. También hay un `flush()` en el shutdown de la API.
11. **Resultado.** El front muestra los cambios legales o el aviso de revisión humana. `GET /analyses/{id}` devuelve lo mismo y sirve de respaldo por polling si SSE falla.
12. **Retención.** Los jobs terminados viven `API_JOB_TTL_SECONDS` y después `GET` responde `404`. No hay reintento de un job: quien quiere reintentar vuelve a subir.

## Resultado vertical

### Backend

- **Contrato HTTP basado en jobs**, que es lo más caro de cambiar después:
  - `POST /analyses` → `202 {id}`;
  - `GET /analyses/{id}` → `{id, status, created_at, trace_id}` más `output` (en `done`) o `error` (en `human_review` y `failed`), con `status ∈ pending | running | done | human_review | failed`;
  - `GET /analyses/{id}/events` → stream SSE (16b);
  - `GET /health` → `{"status": "ok"}`, sin auth y sin tocar Langfuse ni OpenAI.
- **Puertos nuevos** (`Protocol`), con un adaptador de hoy y uno previsto para mañana:

  | Puerto | Hoy | Mañana |
  |---|---|---|
  | `JobStore` | en memoria, con TTL | Postgres o Redis |
  | `JobEvents` | lista en memoria con `Condition`, `seq` creciente | Redis Streams (mapea 1 a 1 con `seq` y `Last-Event-ID`) |
  | `JobRunner` | `ThreadPoolExecutor` con `API_MAX_CONCURRENT_JOBS` workers y cola acotada | cola en Redis con `arq` o `RQ` |
  | `RateLimiter` | ventana deslizante en memoria por key | Redis |

  No se usa `BackgroundTasks` de FastAPI: el pipeline es bloqueante y `BackgroundTasks` no limita la concurrencia, así que una ráfaga de requests dispararía corridas y gasto sin techo.
- **`ProgressTracer`** (16b): decorador de `TraceLifecycle` que delega todo en el tracer real y publica los eventos de etapa. El pipeline sigue recibiendo un `PipelineTracer` y no se entera.
- **`StageName`**: `StrEnum` de dominio con los cinco nombres de etapa; las etapas y `pipeline.py` lo usan en lugar de strings sueltos, y un test AST falla si `tracer.stage(...)` recibe un literal que no es un `StageName`.
- **Raíz de composición compartida.** `composition.py` recibe de `main.py` las factories de etapas, el sobre de errores y la conexión a Langfuse; la CLI y la API lo importan y ningún root importa al otro. Como el tracer se inyecta en cada etapa al construirla, las etapas se arman por job.
- **`api.py`**: segunda raíz de composición, junto a `main.py`. Carga `ApiSettings`, construye la app de FastAPI y conecta adaptadores con puertos.
- **Protecciones**: API key por header (`Security` de FastAPI, sin librería extra), rate limit como puerto propio, límite de tamaño de upload, validación sincrónica de imagen, techo de jobs pendientes, jobs ligados a la key que los creó y retención por TTL (los contratos son información sensible).

### Layout de módulos nuevos en `src/`

| Módulo | Capa | Contenido |
|---|---|---|
| `composition.py` | composición compartida | `build_stages`, la construcción del contenedor, `build_envelope`, y la conexión a Langfuse partida en "conectar cliente" y "construir tracer sobre cliente"; lo importan `main.py` y `api.py` |
| `stages.py` | dominio | `StageName` (`StrEnum`) |
| `jobs.py` | dominio/aplicación | `JobState` (`StrEnum`), `Job`, `JobEvent`, y los `Protocol` `JobStore`, `JobEvents`, `JobRunner`, `RateLimiter`; sin imports de infraestructura |
| `jobs_memory.py` | adaptadores | `InMemoryJobStore`, `InMemoryJobEvents`, `InMemoryRateLimiter` |
| `job_runner.py` | adaptador con lifecycle | `ThreadPoolJobRunner` y `execute_job`: tempdir, `open_root`, `run_analysis`, mapeo a estado terminal, `flush` |
| `progress_tracer.py` | adaptador | decorador de `TraceLifecycle` (16b) |
| `api.py` | raíz de composición | `ApiSettings`, app de FastAPI, endpoints, wiring |

`main.py` queda como hoy menos lo que se muda a `composition.py`.

### Reglas de capas reescritas

Hoy `test_layering.py` dice "solo `main.py`" en tres lugares y los tres rompen con `api.py` por construcción. Las reglas pasan a:

- `COMPOSITION_ROOTS = {main.py, api.py}`: los únicos que llaman a `load_settings()` o `load_api_settings()`.
- `LIFECYCLE_OWNERS = {main.py, job_runner.py}`: los únicos que usan `open_root()` y `flush()` (`TraceLifecycle`).
- `langfuse` se importa solo desde `{main.py, composition.py, observability.py, job_runner.py}`.
- `fastapi` y `starlette` se importan solo desde `api.py`.
- `pipeline.py` y los módulos de dominio nunca importan `api`, `jobs_memory`, `job_runner` ni `progress_tracer`.
- `jobs.py` y `stages.py` entran a `DOMAIN_MODULES` (no importan LangChain, OpenAI, Langfuse, `config` ni `observability`).
- La regla de §4.6 ("solo `main.py` captura errores del dominio") se reescribe en el plan como "solo `main.py` y `job_runner.py`" y sigue sin test AST, como hoy.

### Ciclo de vida de Langfuse en la API

Hoy la CLI construye un cliente de Langfuse y corre `auth_check()` por corrida, aunque §5.7 diga "un cliente por proceso". La API hace lo que el plan describe:

- **Por proceso:** un cliente, construido en el `lifespan` de FastAPI con `auth_check()`. Si falla, la API no arranca (mismo fail-fast que la CLI). `LANGFUSE_MEDIA_UPLOAD_ENABLED=false` se fija antes, como hoy.
- **Por job, dentro del thread del worker:** un `LangfuseTracer` propio sobre ese cliente, con un callback handler nuevo (no se comparte entre threads), `open_root` con `session_id = id del job`, `trace_id` capturado dentro del thread (se lee del contexto OTel, que es por thread) y guardado en el `Job`, y `flush()` al terminar cada job.
- **En el shutdown:** un `flush()` final.
- Esto exige partir `build_langfuse_tracer` en "conectar" y "construir tracer sobre cliente"; la CLI sigue haciendo las dos cosas por corrida.

### Frontend (16c)

- `web/` con Vite, React 19, TypeScript estricto, Tailwind v4, TanStack Query; `pnpm` y Biome.
- Una pantalla con máquina de estados, sin router.
- Tipos generados con `openapi-typescript` desde `web/openapi.json`, congelado en el repo y verificado en el CI.
- Parser SSE propio sobre `fetch` streaming; tras 3 reconexiones fallidas cae a polling.
- Vitest, Testing Library y MSW.

### Integración

- El CI suma un job `web` (lint, typecheck, tests, build, chequeo del OpenAPI y de los tipos generados). Sigue sin secretos de API.
- La demo corre local: `uvicorn` + `vite dev`, con `APP_ENV` no productivo. Contenedor o PaaS es un ticket posterior.

## Decisiones tomadas en el grilling (2026-10-29)

Las marcadas con **(revierte)** cambian lo que proponía el ticket original.

1. **El cambio de alcance se escribe en el commit de 16a** (108–110) y 111 en el de 16b. Un commit de "solo plan" deja el plan contradiciendo al repo.
2. **Sin cancelación y sin estado `cancelled`** **(revierte la pregunta abierta)**. El pipeline es bloqueante y cancelarlo a mitad obliga a cooperación de las etapas. Con eso cae también el único argumento para reconsiderar WebSockets. `JobState` queda en cinco valores, documentado como extensible.
3. **Retención.** Las imágenes no sobreviven al job; no hay reintento de un job; los jobs terminados viven `API_JOB_TTL_SECONDS` (3600) y después `404`.
4. **Demo local**, `APP_ENV` no productivo; la regla de §5.1 (`production` rechaza el host público de Langfuse) queda intacta.
5. **Jobs ligados a la key que los creó.** Se guarda el `sha256` de la key; otra key recibe `404` (no `403`, para no confirmar existencia).
6. **Rate limit como puerto propio, sin `slowapi`** **(revierte)**. `slowapi` exige un `Limiter` global, `request: Request` en cada endpoint y un handler propio; es una dependencia más para una ventana deslizante de ~30 líneas detrás de un `Protocol`, consistente con los otros puertos.
7. **SSE a mano con `StreamingResponse`, sin `sse-starlette`** **(revierte)**. El formato es `id:`/`data:`/comentario; la desconexión se detecta con `request.is_disconnected()`. Una librería no aporta nada sobre un log de eventos propio.
8. **Puente thread → asyncio:** el log de eventos es thread-safe (`Condition`) y el generador SSE espera con `await asyncio.to_thread(events.wait(seq, timeout))`. El log se testea sin event loop y el mismo adaptador sirve al polling y al stream.
9. **Techo de cola:** `API_MAX_PENDING_JOBS` (10); al superarlo, `503` con `Retry-After`. Sin techo, una ráfaga difiere el gasto en vez de evitarlo.
10. **Front: `pnpm` y Biome.** Biome resuelve lint y formato en una herramienta y trae las reglas de hooks de React, que era el argumento de ESLint.
11. **`attempt` sale de 16b** **(revierte)**. El reintento semántico corre dentro del mismo span (`contextualization_agent.py:161-193`) y solo cambia el `run_name` de LangChain; un decorador de `stage()` nunca lo ve. Observarlo exige un `BaseCallbackHandler` acoplado a strings de los agentes para un evento que en la demo aparece rara vez. Queda como ticket derivado.
12. **Directorio temporal por job, pipeline intacto** **(revierte "las imágenes nunca tocan disco")**. `AnalysisRequest` y `validate_image` exigen paths. Un `tempfile.TemporaryDirectory` por job, borrado en `finally`, evita tocar dominio y aplicación.
13. **`422` sincrónico en `POST`** con `detect_mime_type` e `inspect_image` (ya existen en `image_parser.py`) antes de crear el job; el job vuelve a validar al parsear.
14. **Layout de módulos** según la tabla de arriba.
15. **Reglas de capas** según la sección de arriba.
16. **Ciclo de vida de Langfuse** según la sección de arriba; §5.7 pasa a decir lo que de verdad ocurre.
17. **Cuerpo terminal y sobre compartido.** `build_envelope` se muda a `composition.py`; `human_review` y `failed` devuelven el sobre idéntico al stderr de la CLI (`error, stage, message, run_id, trace_id, issues`); `run_id` es el id del job (uuid4 hex), que también es el `session_id` de Langfuse.
18. **Sin timeout propio de la API.** Cada job construye su `CallBudget(pipeline_timeout_seconds)`; al agotarse, `UpstreamError` → `failed`. El peor caso es `pipeline_timeout_seconds` más una llamada.
19. **`ApiSettings(Settings)`** **(revierte "se agregan a `Settings`")**. `API_KEYS` es obligatoria para la API pero no existe para la CLI; en `Settings` rompería la CLI. Mismo patrón que `EvalSettings` (decisión 32).
20. **`StageName` como `StrEnum` de dominio** en `stages.py`, con test AST. Es el único cambio a `pipeline.py` de todo T16.
21. **Eventos con discriminador `kind`:** `stage` (`started | done | failed`) y `job` (terminal), ambos con `seq` y `at`; keep-alive cada 15 s; cierre tras el evento `job`; `404` solo al abrir; `oneOf` en el OpenAPI.
22. **Configuración sin parsers:** dos enteros para el rate limit en vez de `"10/minute"`; keys separadas por coma, guardadas como `sha256` y comparadas con `hmac.compare_digest`.
23. **Comando y dependencias:** `uv run uvicorn --app-dir src api:app`; `fastapi==0.162.0`, `uvicorn==0.54.0`, `python-multipart==0.0.32` en runtime; `httpx==0.28.1` en `dev` (lo exige `TestClient`).
24. **OpenAPI congelado en el repo:** `scripts/export_openapi.py` → `web/openapi.json`; el CI lo regenera y falla con `git diff --exit-code`.
25. **Front: una pantalla sin router, parser SSE propio**, 3 reconexiones con backoff y después polling.
26. **Decisiones 108–111 en `plan.md`** (texto abajo).

## Configuración

Ver [16a](16a-api-jobs-polling.md). Las variables viven en `ApiSettings`, no en `Settings`.

## Cobertura de la consigna

- Requisitos: ninguno nuevo. Es una extensión fuera de la consigna original; la CLI sigue siendo el entregable.
- Entregables: el README suma una sección para correr la API y el front.

## Secciones de plan.md que gobiernan el trabajo

- §1 (Fuera de alcance y Arquitectura) y §7.2: cambio de alcance
- §4.5, §4.6 y §4.7 (errores, dónde se capturan, sobre de salida)
- §5.1, §5.2, §5.6 y §5.7 (configuración, puerto de observabilidad, enmascarado, ciclo de vida)
- §6.1 y §6.2 (unit tests y tests de arquitectura)

Cambios a `plan.md` (discutidos en el grilling; se escriben en los commits indicados):

- §1 "Fuera de alcance": se quita "API HTTP, procesamiento asíncrono"; queda "multi-página". La tabla de arquitectura suma la fila de `api.py` y `composition.py`.
- §7.2: "Una corrida por par, síncrona" pasa a describir la CLI; se agrega que la API corre jobs en un pool acotado.
- §4.6: "Solo `main.py` captura errores del dominio" → "solo `main.py` y `job_runner.py`".
- §5.7: cliente por proceso (CLI: por corrida; API: en el `lifespan`), tracer y raíz por corrida, `flush()` por corrida y en el shutdown de la API.
- §6.2: las reglas de capas reescritas.
- §8.4, decisiones nuevas:
  - **108** (commit 16a): la API HTTP y la ejecución por jobs entran en alcance como fase 3; la CLI sigue siendo el entregable de la consigna. Reemplaza §1 y §7.2.
  - **109** (commit 16a): contrato por jobs con polling y SSE; puertos `JobStore`, `JobEvents`, `JobRunner` y `RateLimiter` con adaptadores en memoria; la infraestructura real entra por adaptadores.
  - **110** (commit 16a): dos raíces de composición (`main.py`, `api.py`) y dos dueños del ciclo de vida del tracer (`main.py`, `job_runner.py`); cliente de Langfuse por proceso, tracer y raíz por corrida. Reescribe §4.6, §5.7 y §6.2.
  - **111** (commit 16b): progreso por etapa vía decorador del tracer, sin `attempt`; el reintento semántico no es observable desde el puerto.

## Rebanadas

La regla es un commit por ticket, así que el trabajo se parte en tres. Cada rebanada tiene sus propios criterios de aceptación y tests:

1. [16a](16a-api-jobs-polling.md): API de jobs con polling. Es la base; no tiene bloqueos.
2. [16b](16b-progreso-sse.md): `ProgressTracer` y stream SSE. Bloqueado por 16a.
3. [16c](16c-front-react.md): front con React y TypeScript. Bloqueado por 16b.

## Tickets derivados (no se hacen acá)

- Progreso de reintentos vía callbacks de LangChain (`contextualization_retry`, `extraction_retry`) para mostrar "reintentando" en el stepper.
- `docker-compose` (api, front y, más adelante, Redis) y despliegue en contenedor o PaaS.
- Autenticación real para el front (login o backend-for-frontend); hoy la key vive en `VITE_API_KEY` y solo sirve para la demo local.
- Adaptadores de Redis (`JobStore`, `JobEvents`, `RateLimiter`) y cola (`arq` o `RQ`) cuando haga falta más de una réplica.

## Preguntas abiertas

- Ninguna: cancelación, retención, despliegue y `attempt` quedaron cerradas en el grilling.
