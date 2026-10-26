# 08: Runner de evals y Experiment en Langfuse

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T7

## Resumen

Se arma un examinador. Toma los ejercicios, hace trabajar al robot y corrige con regla fija. Saca notas por paso, por tipo de caso y por ejercicio. Si una nota no aplica, no cuenta como cero. Todo se sube a la bitácora como una prueba con nombre, para comparar corridas.

## Resultado vertical

`run_evals.py --split calibration` mide cada etapa y publica el Experiment. `outputs/eval_report.json` con métricas globales, por categoría, por kind y por caso, más el conteo de casos evaluables por métrica; el set publicado como Dataset de Langfuse y la corrida como Experiment con los checks como evaluadores.

## Cobertura de la consigna

- Requisitos: R05 (eval CER), R12–R13 (eval)
- Entregables: extras §13.3

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §5.1 (`EvalSettings`, `JUDGE_MODEL`)
- §6.6 (métricas)
- §6.7 (ejecución, N/A, baseline)

Decisiones del registro §8.4: 32, 36, 47

## Criterios de aceptación

- [ ] Los checks son funciones puras `(resultado, caso) -> score`, separadas del adaptador.
- [ ] El CER se calcula con jiwer.
- [ ] Se miden exactitud de kind, P/R micro, exactitud de `change_type`, Jaccard de temas y cobertura de `key_facts`.
- [ ] El juez usa `JUDGE_MODEL ≠ EXTRACTION_MODEL`.
- [ ] Se espera exit `10` en casos de revisión humana.
- [ ] Hay comparación contra `baseline.json` si existe.
- [ ] El reporte incluye el conteo de casos evaluables por métrica.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/evals/test_metrics.py` (cada métrica con casos sintéticos, N/A omitido en agregados)
- `tests/evals/test_eval_settings.py` (`JUDGE_MODEL` validado)

**GREEN (lo mínimo que los hace pasar):**

- `evals/run_evals.py`
- `evals/settings.py`
- `evals/metrics.py`

## Fuera de alcance

- Calibrar.
- Correr `validation`.

## Comments
