# 16a: API de jobs con polling

**Status:** aprobado para implementar (grilling del 2026-10-29, 26 decisiones)

**Fase:** 3 — interfaz web

**Bloqueado por:** ninguno

**Paraguas:** [T16](16-api-http-y-front-web.md)

## Resumen

Primera rebanada de la capa web: una API HTTP con FastAPI que recibe un par de imágenes, crea un job, lo corre en segundo plano con concurrencia acotada y deja consultar el estado y el resultado por polling. Todavía no hay progreso por etapa (16b) ni front (16c). El dominio no cambia y `pipeline.py` cambia en una sola cosa: usa `StageName` en lugar de un string.

Este commit también escribe en `plan.md` el cambio de alcance (decisiones 108, 109 y 110).

## Resultado vertical

### `composition.py`: raíz de composición compartida

- Se mudan de `main.py`: `build_stages(settings, tracer, budget)`, la construcción del contenedor de etapas, `build_envelope(error, context)` y la conexión a Langfuse.
- `build_langfuse_tracer` se parte en dos: `connect_langfuse(...)` (cliente + `auth_check()`) y `build_tracer(client)` (un `LangfuseTracer` con su callback handler sobre un cliente ya conectado). La CLI sigue haciendo las dos cosas por corrida; la API conecta una vez y construye un tracer por job.
- `main.py` y `api.py` importan `composition.py`; ningún root importa al otro.

### `stages.py`: nombres de etapa

- `StageName` (`StrEnum`): `parse_original_contract`, `parse_amendment_contract`, `contextualization_agent`, `extraction_agent`, `output_validation`.
- `image_parser.py`, los dos agentes y `pipeline.py` lo usan en lugar de literales. Es el único cambio a `pipeline.py` de todo T16.
- Un test AST falla si algún `tracer.stage(...)` en `src/` recibe un literal que no es un `StageName`.

### `jobs.py`: dominio de jobs

- `JobState` (`StrEnum`): `pending | running | done | human_review | failed`. Documentado como extensible; no hay `cancelled`.
- `Job`: `id` (uuid4 hex; es también el `run_id` del sobre y el `session_id` de Langfuse), `owner_key_hash`, `state`, `created_at`, `trace_id: str | None`, y el cuerpo terminal (`output: ContractChangeOutput | None` o `error: <sobre>`).
- `JobEvent` (se usa desde 16b, pero el tipo nace acá para que el puerto quede completo).
- `Protocol`s: `JobStore` (crear, leer por id y key, transicionar, vencer), `JobEvents` (append, leer desde un `seq`, esperar), `JobRunner` (encolar con tope), `RateLimiter` (consumir por key).
- Sin imports de infraestructura: entra a `DOMAIN_MODULES`.

### `jobs_memory.py`: adaptadores en memoria

- `InMemoryJobStore`: dict protegido por lock, TTL para jobs terminados (`API_JOB_TTL_SECONDS`), barrido perezoso al leer.
- `InMemoryJobEvents`: lista append-only con `seq` creciente y `threading.Condition` (lo consume 16b).
- `InMemoryRateLimiter`: ventana deslizante por hash de key, `API_RATE_LIMIT_REQUESTS` en `API_RATE_LIMIT_WINDOW_SECONDS`.

### `job_runner.py`: ejecución y ciclo de vida

- `ThreadPoolJobRunner`: `ThreadPoolExecutor` con `API_MAX_CONCURRENT_JOBS` workers y cola de pendientes acotada por `API_MAX_PENDING_JOBS`; al superarla, `submit` rechaza y la API responde `503` con `Retry-After`. No se usa `BackgroundTasks` porque no limita la concurrencia.
- `execute_job(job, images, ...)`, dentro del thread del worker:
  1. escribe las dos imágenes en un `tempfile.TemporaryDirectory` privado del job;
  2. construye `CallBudget(pipeline_timeout_seconds)` para ese job;
  3. construye un `LangfuseTracer` propio sobre el cliente del proceso (`build_tracer(client)`);
  4. arma `PipelineStages` con ese tracer y ese presupuesto;
  5. abre la raíz con `open_root(..., session_id=job.id)` y captura `trace_id` dentro del thread;
  6. llama a `run_analysis()`;
  7. mapea el resultado: `AnalysisResult` → `done` con `output`; `NeedsHumanReviewError` → `human_review` con el sobre y sus `issues`; cualquier otra excepción → `failed` con el sobre sanitizado (`UnexpectedError`, sin stack; el stack va al log);
  8. guarda el estado terminal en `JobStore`;
  9. en `finally`: `flush()` del tracer y borrado del directorio temporal.
- Es, junto con `main.py`, uno de los dos dueños del ciclo de vida del tracer (`open_root`, `flush`).
- No hay timeout propio de la API: el presupuesto acota cada llamada al modelo; el peor caso es `pipeline_timeout_seconds` más una llamada.

### `api.py`: segunda raíz de composición

- `ApiSettings(Settings)` y `load_api_settings()`, el mismo patrón que `EvalSettings` (decisión 32). `API_KEYS` es obligatoria para la API y no existe para la CLI; en `Settings` rompería la CLI.
- `lifespan`: `connect_langfuse` con `auth_check()`; si falla, la API no arranca. `LANGFUSE_MEDIA_UPLOAD_ENABLED=false` se fija antes, como hoy. En el shutdown, `flush()` final.
- Endpoints:
  - `POST /analyses` (multipart `original` y `amendment`) → `202 {id}`. Orden de chequeos: key (`401`), rate limit (`429`), tamaño (`413`), validación sincrónica de cada imagen con `detect_mime_type` e `inspect_image` (`422` con el mensaje del `InvalidImageError`), cola llena (`503` + `Retry-After`). El job vuelve a validar al parsear; es idempotente.
  - `GET /analyses/{id}` → `{id, status, created_at, trace_id}` y, según el estado, `output` (`done`) o `error` (`human_review`, `failed`). El `error` es el sobre idéntico al stderr de la CLI: `error, stage, message, run_id, trace_id, issues`. `404` para id inexistente, vencido o de otra key.
  - `GET /health` → `{"status": "ok"}`, sin auth, sin tocar Langfuse ni OpenAI.
- Auth: header `X-API-Key` con `Security` de FastAPI; las keys se guardan como `sha256` y se comparan con `hmac.compare_digest`. El hash de la key queda en el `Job`.
- Comando: `uv run uvicorn --app-dir src api:app` (los imports son planos desde `src/`), documentado en `CLAUDE.md` y en el README.

### Dependencias

- Runtime, pinneadas con `==`: `fastapi==0.162.0`, `uvicorn==0.54.0`, `python-multipart==0.0.32`. Se regenera `requirements.txt` con `--no-dev`.
- `dev`: `httpx==0.28.1` (lo exige `TestClient`).
- No entran `slowapi` ni `sse-starlette`.

## Decisiones tomadas en el grilling (2026-10-29)

Las que gobiernan esta rebanada; el detalle y el porqué completo están en [T16](16-api-http-y-front-web.md).

1. El cambio de alcance (108–110) se escribe en este commit.
2. Sin cancelación ni `cancelled`.
3. Imágenes en un directorio temporal por job, borrado en `finally`; `pipeline.py` sigue recibiendo paths.
4. Jobs ligados a la key (`sha256`), `404` para otra key.
5. Rate limit como puerto propio; sin `slowapi`.
6. `API_MAX_PENDING_JOBS` → `503` con `Retry-After`.
7. `422` sincrónico en `POST` con los helpers por bytes.
8. Layout: `composition.py`, `stages.py`, `jobs.py`, `jobs_memory.py`, `job_runner.py`, `api.py`.
9. Reglas de capas: `COMPOSITION_ROOTS = {main.py, api.py}`, `LIFECYCLE_OWNERS = {main.py, job_runner.py}`, `langfuse` solo en `{main.py, composition.py, observability.py, job_runner.py}`, `fastapi`/`starlette` solo en `api.py`, dominio y `pipeline.py` nunca importan los adaptadores nuevos, `jobs.py` y `stages.py` en `DOMAIN_MODULES`.
10. Langfuse: cliente por proceso en el `lifespan`, tracer y raíz por job dentro del thread, `flush()` por job y en el shutdown.
11. Sobre compartido en `composition.py`; `run_id` = id del job = `session_id`.
12. Sin timeout propio; `CallBudget` por job.
13. `ApiSettings(Settings)`.
14. `StageName` con test AST.
15. Configuración con enteros y keys hasheadas.
16. Versiones y comando según arriba.

## Configuración

Todas en `ApiSettings` y en `.env.example`:

- `API_KEYS`: keys válidas separadas por coma. Sin ella, la API no arranca (`ConfigurationError`).
- `API_MAX_CONCURRENT_JOBS`, por defecto `2`.
- `API_MAX_PENDING_JOBS`, por defecto `10`.
- `API_RATE_LIMIT_REQUESTS`, por defecto `10`.
- `API_RATE_LIMIT_WINDOW_SECONDS`, por defecto `60`.
- `API_MAX_UPLOAD_BYTES`, por defecto el valor de `max_image_bytes`.
- `API_JOB_TTL_SECONDS`, por defecto `3600`.

## Criterios de aceptación

- [ ] Los módulos de dominio no cambian y `pipeline.py` cambia solo para usar `StageName`; los tests de arquitectura lo confirman.
- [ ] `StageName` cubre toda etapa que emite el pipeline; el test AST rechaza literales sueltos en `tracer.stage(...)`.
- [ ] La CLI se comporta exactamente como hoy (mismos exit codes y mismo sobre) usando `composition.py`.
- [ ] `POST /analyses` responde `202` con un `id` sin esperar al análisis.
- [ ] Sin API key válida, `401`; al superar el rate limit, `429`; con un upload demasiado grande, `413`; con una imagen inválida, `422` **antes** de crear el job; con la cola llena, `503` con `Retry-After`.
- [ ] `GET /analyses/{id}` refleja los cinco estados y, al terminar, devuelve `output` o el mismo sobre que la CLI.
- [ ] Un `id` inexistente, vencido por TTL o creado con otra key responde `404`.
- [ ] La concurrencia de jobs nunca supera `API_MAX_CONCURRENT_JOBS` y la cola nunca supera `API_MAX_PENDING_JOBS`.
- [ ] Un error inesperado deja el job en `failed` con un sobre sanitizado, sin stack.
- [ ] Al agotarse el presupuesto, el job queda en `failed` con el mensaje de `UpstreamError`.
- [ ] El directorio temporal del job no existe después de terminar, en cualquier estado terminal.
- [ ] Cada job produce una traza propia en Langfuse con `session_id` = id del job, el `trace_id` queda en el `Job`, y las imágenes no salen sin enmascarar.
- [ ] Sin Langfuse válido, la API no arranca.
- [ ] `requirements.txt` incluye las dependencias nuevas pinneadas y ninguna de `dev`.
- [ ] `plan.md` registra 108, 109 y 110 y ajusta §1, §7.2, §4.6, §5.7 y §6.2.
- [ ] `CLAUDE.md` y el README documentan el comando para levantar la API.
- [ ] El style gate y los unit tests están en verde.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_stages.py`: `StageName` tiene los cinco valores; AST: todo `tracer.stage(...)` en `src/` recibe un `StageName`.
- `tests/test_composition.py`: `build_stages` arma las etapas con el tracer y el presupuesto recibidos; `build_envelope` produce el sobre de §4.7 para `LegalMoveError`, `NeedsHumanReviewError` (con `issues`) y una excepción genérica; `connect_langfuse` y `build_tracer` se pueden usar por separado (con dobles).
- `tests/test_jobs.py`: transiciones válidas (`pending → running → terminal`) y rechazo de las inválidas; `Job.id` es uuid4 hex.
- `tests/test_jobs_memory.py`: `InMemoryJobStore` guarda, lee por id y key, vence por TTL con un reloj inyectado; `InMemoryRateLimiter` permite N en la ventana y rechaza el N+1, y vuelve a permitir al deslizar.
- `tests/test_job_runner.py`, con etapas dobles y `RecordingTracer`: mapeo de `AnalysisResult`, `NeedsHumanReviewError` y error genérico a estado terminal y sobre; el tope de concurrencia se respeta; la cola rechaza al superar `API_MAX_PENDING_JOBS`; el directorio temporal desaparece en `finally`; el tracer hace `flush()` al terminar cada job; el `trace_id` queda en el `Job`.
- `tests/test_api.py`, con `TestClient`, `ApiSettings` doble y etapas dobles (sin red ni `.env`): `202` con `id`; `401`; `404` para inexistente, vencido y otra key; `413`; `422` sin crear job; `429`; `503` con `Retry-After`; forma de `GET /analyses/{id}` en los cinco estados; `/health` sin auth; la API no arranca si `auth_check` falla.
- `tests/test_main.py`: la CLI usa `composition.py` y sus salidas y exit codes no cambian.
- `tests/test_layering.py`: `COMPOSITION_ROOTS`, `LIFECYCLE_OWNERS`, conjunto de importadores de `langfuse`, `fastapi`/`starlette` solo en `api.py`, dominio y `pipeline.py` sin imports de los adaptadores nuevos, `jobs.py` y `stages.py` en `DOMAIN_MODULES`.
- `tests/test_requirements.py`: sigue en verde con las dependencias nuevas.
- `tests/test_ci_workflow.py`: sin cambios en esta rebanada.

**GREEN:** `composition.py`, `stages.py`, `jobs.py`, `jobs_memory.py`, `job_runner.py`, `api.py`, `ApiSettings`, los cambios mínimos en `main.py`, `image_parser.py`, los agentes y `pipeline.py` para usar `StageName`, las dependencias y `plan.md`.

## Secciones de plan.md que gobiernan el trabajo

- §1 (Fuera de alcance y Arquitectura) y §7.2
- §4.5, §4.6 y §4.7
- §5.1, §5.6 y §5.7
- §6.1 y §6.2
- §8.4: decisiones 108, 109 y 110 (texto en [T16](16-api-http-y-front-web.md))

## Tickets derivados

Ver [T16](16-api-http-y-front-web.md).

## Preguntas abiertas

- Ninguna.
