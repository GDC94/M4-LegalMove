# 07: Fábrica de golden cases y casos Standard

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T6

## Resumen

Se arma una fábrica de ejercicios con respuesta conocida. Primero se escribe qué debe pasar, después se dibuja el papel. Algunos papeles salen sucios, torcidos o borrosos, como en la vida real. Una persona revisa cada respuesta. La fábrica no pisa lo ya revisado. Seis ejercicios de base quedan listos.

## Resultado vertical

`generate_golden.py` produce casos con la verdad declarada primero. 6 casos Standard (3 `restated`, 3 `delta`) generados desde `case.yaml`, con PNG renderizados por Playwright y degradados por Pillow con semilla, `expected.json` derivado y revisado (`reviewed_by/at`); dos de ellos reemplazan a los pares A y B en `data/test_contracts/` (decisión 14).

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: extras §13.3 (golden); refuerza E06

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §2.6
- §6.4 (estructura, `case.yaml`, generador, no-sobrescritura, revisión humana, split por familia)
- §6.5 (cobertura)
- §10 (grupo `dev`)

Decisiones del registro §8.4: 12, 27, 34, 36, 50

## Criterios de aceptación

- [ ] Hay 5–8 contratos base revisados por el autor.
- [ ] Hay 4 templates HTML.
- [ ] Las mutaciones disponibles son `modify/add/remove/renumber/noop`.
- [ ] Las instrucciones `delta` usan frases variadas que la heurística no conoce.
- [ ] El generador se niega a pisar un `expected.json` con `reviewed_at`.
- [ ] El split se asigna por familia.
- [ ] Ningún texto de golden aparece en `src/` (test AST).
- [ ] Dos casos Standard reemplazan a los pares A y B en `data/test_contracts/`.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/evals/test_generate_golden.py` (mutaciones aplicadas, `delta` con frases fuera de la heurística, no-overwrite, split por familia, semilla determinista)
- Arquitectura: texto golden ausente de `src/`.

**GREEN (lo mínimo que los hace pasar):**

- `evals/generate_golden.py`
- `data/golden/`

## Fuera de alcance

- Correr el pipeline sobre los casos.
- Métricas.

## Comments
