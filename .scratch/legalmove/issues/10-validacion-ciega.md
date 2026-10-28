# 10: Validación ciega y línea base congelada

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T9, T11

## Resumen

Llega el examen final con ejercicios nunca vistos. Se corre una sola vez. La nota se congela como marca de referencia. Desde ahí, si una corrida nueva saca peor nota, alarma. Si el examen sale mal, esos ejercicios pasan a práctica y se arma un examen nuevo. Resultados se muestran en el manual.

## Resultado vertical

`outputs/baseline.json` en el repo; `run_evals.py` marca regresiones contra él; el README muestra la tabla de resultados medidos; si algún umbral no se alcanza, se aplica la política de la decisión 59 (los casos vistos pasan a calibración y se genera un holdout nuevo).

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: E07 (sección "Testing y evals" completa)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §6.6–6.7
- §6.9 (`baseline.json` commiteado)
- §8.2

Decisiones del registro §8.4: 36, 59

## Criterios de aceptación

- [x] Se hace una sola corrida de `validation` antes de congelar.
- [x] Cualquier reintento exige un holdout nuevo.
- [x] El gate de regresión falla si una métrica cae por debajo de la baseline.
- [ ] `outputs/baseline.json` está commiteado. **Pasa a T12 por la decisión 92** (el ticket gana sobre el plan; aprobado por el autor): la validación ciega 1 no alcanzó dos umbrales y sus casos pasaron a `calibration`.
- [x] El README muestra la tabla de resultados medidos (la de la validación ciega 1, marcada como no cumplida; T12 la reemplaza por la de la línea base).

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** `tests/evals/test_regression_gate.py` (regresión detectada contra una baseline sintética).

**GREEN (lo mínimo que lo hace pasar):** comparación en `run_evals.py`, `outputs/baseline.json`, README.

## Fuera de alcance

- Split `real`.
- Fallback de modelos.

## Comments

**2026-10-28 — Validación ciega 1.** Una sola corrida autorizada por el autor sobre los 14 casos de `validation` (`validation-20261028T154123Z`; reporte `outputs/validation_run1.json`; detalle en `docs/calibration/t10-validation.md`). Cumplidos: CER 0,000 en limpias (6) y 0,001 en degradadas (7), `amendment_kind` 1,000, precision 0,962, recall 0,962, `change_type` 1,000, juez 9,308 y revisión humana 1,000. **No cumplidos:** `topic_jaccard` 0,667 (objetivo 0,80) y `key_facts_coverage` 0,897 (objetivo 0,90). Fallas por caso: `boundary_restated_consultoria_injection` no reportó ningún cambio; Jaccard de temas 0 en `boundary_restated_licencia_renumber` (con P/R 1,0) y 0,33 en `variant_delta_licencia`, `variant_delta_locacion` y `variant_restated_locacion`; `key_facts` 0,5 en `variant_delta_licencia` y 0,67 en `standard_restated_licencia`. Un lote de exportación OTLP de spans venció por timeout: Langfuse puede no tener algunos spans; el reporte local está completo.

Por la decisión 59 (registrada como 92) las tres familias y sus 14 casos pasan a `calibration`, T10 cierra sin `outputs/baseline.json` y T12 (`12-holdout-nuevo.md`) arma un holdout nuevo y congela la línea base con su única corrida. Se registran además las decisiones 88–91 (exit `20` del gate, umbrales como datos y tabla renderizada, `by_scan`, métricas de §6.6 sin umbral automático).
