# 12: Holdout nuevo y línea base congelada

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T10

## Resumen

El primer examen salió mal en dos materias: elegir los temas y no olvidar los datos clave del resumen. Esos ejercicios ya se vieron, así que pasan a práctica. Se practica con ellos hasta resolver los tres errores que mostraron. Después se arma un examen nuevo con contratos de familias que el robot nunca vio, se rinde una sola vez y esa nota se congela como marca de referencia.

## Resultado vertical

Con los 24 casos de `calibration` (los 10 originales más los 14 de la validación ciega 1, decisión 92) se corrigen las tres fallas de T10: el caso de inyección que no reportó ningún cambio, la selección de temas en renumeración y en Variant, y la cobertura de `key_facts`. Se agrega un holdout nuevo de `validation` con al menos una familia contractual nunca vista que respeta la cobertura de §6.5. Se corre `validation` una sola vez con el OK explícito del autor, se congela `outputs/baseline.json` y la tabla del README se renderiza desde la línea base: el test de divergencia de `tests/test_readme.py` deja de saltearse y pasa.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: E07 (sección "Testing y evals" con la línea base congelada)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §6.4 (separación sin fuga por familia)
- §6.5 (cobertura mínima)
- §6.6–6.7
- §6.9 (`baseline.json` commiteado)
- §8.2

Decisiones del registro §8.4: 36, 50, 59, 88–92

## Contexto de T10

- Reporte de la validación ciega 1: `outputs/validation_run1.json`; análisis en `docs/calibration/t10-validation.md`.
- `boundary_restated_consultoria_injection`: exit 0 sin cambios; recall, temas y `key_facts` en 0, juez 1.
- Temas: 0 en `boundary_restated_licencia_renumber` (P/R 1,0); 0,33 en `variant_delta_licencia`, `variant_delta_locacion` y `variant_restated_locacion`; 0,67 en `boundary_restated_locacion_degraded`.
- `key_facts`: 0,5 en `variant_delta_licencia` (el juez encontró todos los hechos en el resumen) y 0,67 en `standard_restated_licencia`.
- Las familias `consultoria_profesional`, `licencia_software` y `locacion_oficina` quedan en `calibration` para siempre: `tests/evals/test_golden_data.py` lo verifica.
- El Dataset `legalmove-validation` de Langfuse conserva los 14 ítems de la corrida 1; el Experiment solo selecciona los casos de la corrida, así que no se mezclan.
- `test_there_are_five_to_eight_base_contracts` admite hasta 8 contratos base: hay lugar para 3 familias nuevas.

## Criterios de aceptación

- [ ] La falla de inyección, la selección de temas y la cobertura de `key_facts` se ajustan solo con `calibration`; cada ajuste tiene su fila en §8.4 o su nota en `docs/calibration/`.
- [ ] Las corridas de `calibration` autorizadas por el autor alcanzan los objetivos de §6.6 sobre los 24 casos.
- [ ] El holdout nuevo usa al menos una familia contractual nunca vista, revisada (`reviewed_by`, `reviewed_at`), y respeta la cobertura de §6.5; ninguna familia de la validación ciega 1 vuelve a `validation`.
- [ ] Ningún caso del holdout nuevo se lee ni se usa para ajustar antes de su corrida.
- [ ] Se hace una sola corrida de `validation` con el OK explícito del autor. Si no alcanza un umbral, vuelve a aplicar la decisión 59.
- [ ] `outputs/baseline.json` está commiteado y sale de esa única corrida.
- [ ] La tabla de "Measured results" del README se renderiza desde `outputs/baseline.json` con `evals/render_results.py`, y el test de divergencia pasa sin saltearse.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** en `tests/evals/test_golden_data.py`, la composición nueva por split (`validation` deja de estar vacío) y la vuelta de "ambos splits se usan"; la cobertura de §6.5 del holdout; los ajustes de calibración donde un unit test los pueda fijar.

**GREEN (lo mínimo que los hace pasar):** los casos nuevos generados y revisados, los ajustes de prompts o heurísticas, `outputs/baseline.json` y la tabla del README.

## Fuera de alcance

- Split `real`.
- Fallback de modelos.

## Comments
