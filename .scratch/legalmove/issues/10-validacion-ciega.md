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

- [ ] Se hace una sola corrida de `validation` antes de congelar.
- [ ] Cualquier reintento exige un holdout nuevo.
- [ ] El gate de regresión falla si una métrica cae por debajo de la baseline.
- [ ] `outputs/baseline.json` está commiteado.
- [ ] El README muestra la tabla de resultados medidos.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** `tests/evals/test_regression_gate.py` (regresión detectada contra una baseline sintética).

**GREEN (lo mínimo que lo hace pasar):** comparación en `run_evals.py`, `outputs/baseline.json`, README.

## Fuera de alcance

- Split `real`.
- Fallback de modelos.

## Comments
