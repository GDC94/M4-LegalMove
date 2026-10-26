# 01: Parsing de visión trazado, enmascarado y cacheado

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T0

## Resumen

Robot aprende a leer foto de contrato. Devuelve texto escrito. Guarda copia para no releer dos veces: ahorra plata y tiempo. Anota cada lectura en bitácora. Datos personales tapados antes de anotar. Foto nunca sale a la bitácora.

## Resultado vertical

De una imagen a `ParsedContract` con traza en Langfuse. `uv run pytest -m integration -k parse` lee `data/test_contracts/pair_a/original.png` con GPT-4o real y deja en Langfuse una traza con el span `parse_original_contract`, su generation (tokens, latencia), `image_sha256` y dimensiones en lugar de la imagen, y texto enmascarado. Una segunda corrida muestra `cache_hit=true` sin generation. Los dos pares de prueba existen y fueron revisados por el autor.

## Cobertura de la consigna

- Requisitos: R02, R03, R04, R05 (unit), R07
- Entregables: E04, E06 (imágenes + README inicial), E09 (completo), E10 (parte)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §3.3 (`ParsedContract`)
- §4.1–4.2 (visión)
- §4.5 (`InvalidImageError`, `UpstreamError`, `IllegibleDocumentError`)
- §4.6
- §5.1 (`Settings`, límites de imagen, caché sensible, presupuesto)
- §5.2 (puerto, `StageSpan`, `RecordingTracer`)
- §5.4 (span `parse_*`)
- §5.6 (masking)
- §6.5 (fase 1: contrato base, pares A y B, `render_plain.py`)

Decisiones del registro §8.4: 11, 23, 24, 26, 27, 31, 34, 39, 42, 43, 49, 52, 55, 57, 58

## Criterios de aceptación

- [ ] `validate_image` rechaza por magic bytes, bytes, lados, píxeles y decompression bomb con `InvalidImageError`.
- [ ] `Settings` valida rangos cruzados: `PIPELINE_TIMEOUT_SECONDS ≥ LLM_TIMEOUT_SECONDS`, `MAX_IMAGE_SIDE_PX ≥ MIN_IMAGE_SIDE_PX`, host aprobado en `production`, caché según `APP_ENV`.
- [ ] El caché usa clave `sha256(imagen) + sha256(VISION_PROMPT) + VISION_MODEL + VISION_DETAIL`.
- [ ] El caché usa permisos `0700/0600`, escritura atómica, TTL y evicción de entradas corruptas.
- [ ] `LangfuseTracer` registra el hook `mask` y nunca envía la imagen.
- [ ] `ParsedContract.text` contiene "Cláusula Primera" … "Cláusula Séptima" e `illegible_count == 0` sobre el par A.
- [ ] El autor confirmó el texto del contrato base y los PNG (redactado por un LLM a su pedido, como en `orchestration`).

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_config.py`
- `tests/test_image_parser.py` (bytes mínimos generados en el test, límites, caché)
- `tests/observability/test_masking.py` (cada patrón: DNI/CUIT/CUIL, CBU/IBAN, email, teléfono)
- `tests/observability/test_tracer.py` (`RecordingTracer`, imagen ausente del input)
- `tests/test_layering.py` (`image_parser.py` no importa `langfuse`)
- Integración: visión real + `cache_hit`.

**GREEN (lo mínimo que los hace pasar):**

- `src/config.py`
- `src/errors.py` (jerarquía base + errores de esta etapa)
- `src/observability.py`
- `src/image_parser.py`
- `src/models.py` (`DocumentRole`, `ParsedContract`)
- `evals/render_plain.py`
- `data/test_contracts/` (pair_a, pair_b, `source_*.txt`, README inicial)

## Fuera de alcance

- Agentes.
- CLI.
- `pipeline.py`.
- Scores.

## Comments
