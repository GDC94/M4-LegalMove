# 03: Extracción validada (agente 2)

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T2

## Resumen

Segundo robot usa el mapa y lista qué cambió. Cada cambio trae la frase exacta del papel como prueba. Revisor chequea que la frase exista de verdad y que las cuentas cierren. Si algo falla, robot reintenta una vez. Al final sale un resumen corto, siempre igual para los mismos datos.

## Resultado vertical

Del mapa y los textos a un `ExtractionResult` grounded y consistente. El agente 2 real, sobre el par A, devuelve `changes` para `5` (`modified`, `price_payment`) y `new:8` (`added`, `confidentiality`) con citas grounded; sobre el par B, `4` (`modified`, `term_duration`) y la sección de penalidades (`removed`, `penalties`, con `amendment_quote`). El span `extraction_agent` registra `extraction_retries`, `schema_valid`, `consistency`, `grounding_rate` y el `RetryFeedback` enmascarado. `render_summary()` produce el resumen determinista.

## Cobertura de la consigna

- Requisitos: R11, R12, R13, R14, R15, R20 (parte)
- Entregables: E03, E05

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §2.5 (granularidad, estructura dura en `restated`)
- §3.3 (`Change`, `ContractChangeOutput`, `ExtractionResult`, consistencia, render del resumen)
- §3.4 (`Topic`, `TOPIC_DESCRIPTIONS`)
- §3.5
- §4.2–4.5

Decisiones del registro §8.4: 6, 7, 8, 15, 16, 18, 28, 29, 38, 44, 45, 46, 48, 56

## Criterios de aceptación

- [ ] `ExtractionResult` tiene el orden de campos `reasoning, changes, sections_changed, topics_touched, summary_of_the_change` y no usa herencia.
- [ ] Los validadores de listas son compartidos.
- [ ] `Change` solo contiene reglas independientes del kind.
- [ ] `checks.py` aplica la regla `removed` por kind.
- [ ] `checks.py` valida el grounding de citas (cualquier falla es dura).
- [ ] `checks.py` valida referencias contra el mapa (`unresolved_section_reference`).
- [ ] `checks.py` valida `added`/`removed` estructurales en `restated` y el orden canónico.
- [ ] `InvalidExtractionError.issues` no contiene texto documental.
- [ ] Un resumen sin cambios es `"No se identificaron cambios."`.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_models.py` (citas por tipo, consistencia, orden, resumen vacío, orden de campos)
- `tests/test_checks.py` (grounding, `removed` por kind, estructura, `grounding_rate` N/A)
- `tests/agents/test_extraction_agent.py` (prompt con mapa + textos; retry; `issues`)
- Arquitectura: paridad de los 3 campos y schema strict de `Change`/`ExtractionResult`.
- Integración: salida real sobre A y B.

**GREEN (lo mínimo que los hace pasar):**

- `src/models.py` (resto)
- `src/checks.py` (extracción)
- `src/agents/extraction_agent.py`
- `src/errors.py` (`InvalidExtractionError`)

## Fuera de alcance

- `pipeline.py`.
- `main.py`.
- Scores blandos.

## Comments
