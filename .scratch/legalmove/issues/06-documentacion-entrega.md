# 06: Documentación y entrega de fase 1

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T5

## Resumen

Se escribe el manual de uso. Persona nueva baja el proyecto, sigue los pasos y llega al mismo resultado. Dibujo del recorrido se pone al día. Lista de trampas se actualiza con lo aprendido. Con esto, fase uno queda lista para entregar.

## Resultado vertical

Una persona que clona el repo público sigue el README y corre el pipeline sobre los pares A y B; el test del README pasa; el diagrama animado refleja `amendment_kind`, `unknown` y el gate de flush.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: E06 (README final de los pares), E07, E11 (verificación)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §8.2 (secciones obligatorias)
- §8.3
- §9 (pendiente del diagrama animado)
- §13 (tabla de entregables y vocabulario)

Decisiones del registro §8.4: 14, 35

## Criterios de aceptación

- [ ] El README tiene las 12 secciones de §8.2.
- [ ] El README incluye la tabla de variables de §5.1, los exit codes de §4.5, la captura de una traza real y la tabla de entregables.
- [ ] `CLAUDE.md` está actualizado con las trampas descubiertas en T1–T5.
- [ ] `requirements.txt` está re-exportado y verde.
- [ ] El diagrama animado refleja `amendment_kind`, `unknown` y el gate de flush.
- [ ] `data/test_contracts/README.md` describe los pares finales.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_readme.py` (secciones)
- Arquitectura: README en la lista de entregables.

**GREEN (lo mínimo que los hace pasar):**

- `README.md`
- `docs/diagrams/legalmove-pipeline.html`
- `data/test_contracts/README.md`

## Fuera de alcance

- Golden set.
- Evals.
- Resultados medidos (la tabla de "resultados medidos" se completa en T10).

## Comments
