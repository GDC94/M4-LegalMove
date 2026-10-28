# 13: Métrica de key facts robusta

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T12

## Resumen

En el examen nuevo el robot sacó la nota casi perfecta, pero la cuenta de "datos clave" le restó puntos por decir lo mismo con otras palabras: "informe de ventas" en lugar de "informe de las ventas". El juez vio que los datos estaban todos. Una sola vez el robot sí se equivocó: escribió "dos trimestres" sin el "(2)" que exige la regla. Este ticket arregla la regla de conteo para que mida datos y no redacción, y hace cumplir la regla del número escrito tal cual. Se ajusta solo con práctica y se confirma con un examen futuro.

## Resultado vertical

La cobertura de `key_facts` mide hechos, no redacción, sin coincidencia difusa (en el espíritu de la decisión 76: normalizar lo que no cambia el dato, nunca aceptar "parecidos"). Las opciones se deciden solo con `calibration`:
- **Key facts atómicos:** cada key fact es el hecho mínimo. Un número va junto a su unidad tal como lo escribe la adenda ("dieciocho (18)" y no "dieciocho (18) camiones"); un hecho que es una frase se reduce a sus palabras distintivas ("informe" + "ventas" en lugar de "informe de las ventas").
- **Normalización del matcher:** ignorar artículos y preposiciones ("de", "las", "el") al comparar, sin tocar números, unidades ni signos.
- Una combinación de ambas.

Además, la regla literal de números (prompt v6, decisión 95) se hace cumplir en código: si la adenda escribe un número en palabras seguido de su número entre paréntesis ("dos (2)"), la descripción y el resumen lo conservan; si no, el intento falla con un issue nombrado y va al reintento. Los `key_facts` de `calibration` se reescriben donde haga falta, con revisión del autor. La línea base congelada (decisión 101) no se re-puntúa y los casos de `validation` no se tocan.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: E07 (sección "Testing y evals")

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §4.3–4.5 (checks de la extracción y reintento)
- §6.4 (golden set y revisión)
- §6.6–6.7 (métricas, agregación y comparación contra la línea base)

Decisiones del registro §8.4: 36, 59, 76, 95, 101

## Contexto de T12

- Validación ciega 2 (`outputs/validation_run2.json` = `outputs/baseline.json`): `key_facts_coverage` 0,862 con el juez en 10,0. Hechos faltantes: "informe de las ventas" (resumen: "informe de ventas"), "dieciocho (18) camiones" ("camiones … hasta dieciocho (18)"), "dos (2) trimestres consecutivos" ("dos trimestres consecutivos") y "electricidad y de pintura" ("electricidad y pintura"). Detalle en `docs/calibration/t12-calibration.md`.
- La métrica vive en `evals/metrics.py` (`score_key_facts`, `normalize_fact_text`).
- El tope de 8 contratos base está lleno (`test_there_are_eight_base_contracts`).

## Criterios de aceptación

- [x] La regla elegida (key facts atómicos, normalización del matcher o ambas) se decide solo con `calibration` y tiene su fila en §8.4; no acepta coincidencia difusa (distancia de edición, similitud o embeddings).
- [x] La normalización nunca borra ni cambia números, unidades, signos de moneda o de porcentaje.
- [x] Los `key_facts` reescritos de `calibration` tienen revisión del autor (`reviewed_by`, `reviewed_at`) y su `declaration_sha256` recalculado; los de `validation` no se tocan.
- [x] Un número en palabras con su número entre paréntesis en la adenda que el resumen o una descripción escriben sin él produce un issue nombrado y un reintento.
- [x] Las corridas de `calibration` autorizadas por el autor alcanzan los objetivos de §6.6, sin regresiones en los demás.
- [x] `outputs/baseline.json` no cambia y la tabla del README sigue renderizada desde él.
- [x] La validación de la métrica nueva con un holdout futuro queda planteada como pregunta abierta, sin decidirse aquí.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** en `tests/evals/test_metrics.py`, los cuatro pares de T12 como ejemplos generales (artículo o preposición omitidos, orden distinto de número y sustantivo) cuentan como encontrados según la regla elegida; en el par de orden distinto se afirma que la forma atómica "dieciocho (18)" se encuentra en "camiones … hasta dieciocho (18)", no "dieciocho (18) camiones", y un número distinto, una unidad distinta o un "dos" sin "(2)" no cuentan. En `tests/test_checks.py`, el check del número literal dispara con "dos trimestres" frente a "dos (2) trimestres" y no dispara cuando la adenda no trae paréntesis. En `tests/evals/test_golden_data.py`, los `key_facts` de `calibration` cumplen la forma elegida.

**GREEN (lo mínimo que los hace pasar):** el cambio en `evals/metrics.py`, el check en `checks.py` con su issue, y los `key_facts` reescritos y revisados.

## Preguntas abiertas

- ¿Con qué holdout se valida la métrica nueva? Requiere subir el tope de 8 contratos base o crear un split nuevo; no se decide en este ticket.
- ¿Cómo se compara una corrida futura contra una línea base medida con la métrica anterior? El gate (decisión 88) compararía métricas distintas en `key_facts_coverage`.

## Fuera de alcance

- Split `real`.
- Fallback de modelos.

## Comments
