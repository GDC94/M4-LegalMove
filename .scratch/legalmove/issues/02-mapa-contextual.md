# 02: Mapa contextual validado (agente 1)

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T1

## Resumen

Primer robot lee los dos textos y arma un mapa. Mapa dice: esta parte vieja se parece a esta parte nueva. Un revisor chequea el mapa con reglas fijas. Si mapa sale mal, robot lo intenta una vez más. Si sigue mal, para y avisa por qué. Si enmienda no se entiende, para y pide a una persona.

## Resultado vertical

De dos textos a un `ContextMap` que pasó sus checks duros. El agente 1 real, sobre los textos fuente de los pares A y B, devuelve `restated` con IDs `1`–`7` + `new:8`, y `delta` con las secciones referidas resueltas; la traza muestra el span `contextualization_agent` con `amendment_kind`, `kind_heuristic`, `context_retries` y versión de prompt. Un mapa inválido se reintenta una vez y la segunda falla termina en `InvalidContextMapError` con `issues`; `unknown` termina en `UnclassifiableAmendmentError` sin reintento.

## Cobertura de la consigna

- Requisitos: R08, R09, R10, R20 (parte)
- Entregables: E02

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §2.2–2.4 (kind, heurística `delta | inconclusive`, gramática y orden canónico)
- §3.2–3.3 (`SectionMapping`, `ContextMap`, render `<context_map>`)
- §4.1–4.4 (prompts, serialización segura, retry semántico, `ValidationIssue`/`RetryFeedback`)
- §4.5

Decisiones del registro §8.4: 5, 9, 19, 25, 30, 37, 41, 44, 47, 48, 54, 60

## Criterios de aceptación

- [ ] `section_id` tiene un `field_validator` de gramática.
- [ ] `checks.py` valida orden canónico, fila sin ambos refs `None` y refs grounded tras normalización (NFKC, `casefold`, comillas/guiones).
- [ ] La heurística no comparte marcadores con el prompt.
- [ ] Existe una sola función de serialización segura en `src/prompt_blocks.py` que neutraliza cierres de etiqueta.
- [ ] `render_context_map()` produce el formato de §3.3.
- [ ] El builder devuelve un `Runnable` que produce `ContextMap`.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_models.py` (gramática, `ContextMap` sin duplicados)
- `tests/test_checks.py` (orden, refs, heurística con frases no listadas, normalización)
- `tests/test_prompt_blocks.py` (inyección de cierre de etiqueta)
- `tests/agents/test_contextualization_agent.py`: el prompt contiene ambos textos delimitados; retry con `RunnableLambda`: inválido→válido = 1 retry; inválido×2 = error con `issues`; `unknown` = 0 retries.
- Arquitectura: schema strict sin keywords prohibidos para `ContextMap`/`SectionMapping`.
- Integración: salida estructurada real sobre A y B.

**GREEN (lo mínimo que los hace pasar):**

- `src/models.py` (enums + mapa)
- `src/checks.py` (mapa + normalización + heurística)
- `src/prompt_blocks.py`
- `src/agents/contextualization_agent.py`
- `src/errors.py` (`NeedsHumanReviewError`, `ValidationIssue` y derivados del agente 1)

## Fuera de alcance

- `Change`.
- Extracción.
- difflib.
- CLI.

## Comments
