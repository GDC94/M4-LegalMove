# 09: Cobertura completa y calibración

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T8

## Resumen

Se suman ejercicios difíciles y trampas: textos con órdenes escondidas, citas a partes que no existen, papeles muy borrosos. Se llega a veinticuatro casos, todos revisados. Con el grupo de práctica se afinan las instrucciones y los límites del robot. El grupo de examen no se mira. Cada ajuste queda anotado.

## Resultado vertical

24 casos revisados y prompts/umbrales ajustados solo con `calibration`. Variant (9) y Boundary (9) generados y revisados, incluidos inyección, referencia inexistente, cláusula reescrita igual, párrafo interno, renumeración, degradación y `unknown`; una corrida de calibración documenta los ajustes de `*_PROMPT_VERSION`, `ILLEGIBLE_MAX_MARKS`, el umbral 0.97 y los marcadores de la heurística.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: extras §13.3 (inyección, revisión humana, umbrales)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §2.6
- §4.3 (casos adversariales)
- §6.5
- §6.6 (ajuste solo con calibración)

Decisiones del registro §8.4: 36, 46, 50, 60

## Criterios de aceptación

- [ ] Los 24 casos tienen `reviewed_at`.
- [ ] Ningún caso de `validation` fue leído ni usado para ajustar.
- [ ] Cada ajuste tiene su fila en el registro §8.4 o su nota en el reporte de calibración.
- [ ] Los casos Variant y Boundary cubren inyección, referencia inexistente, cláusula reescrita igual, párrafo interno, renumeración, degradación y `unknown`.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** casos nuevos agregados al dataset hacen fallar las métricas objetivo en calibración donde corresponda.

**GREEN (lo mínimo que los hace pasar):** ajustes de prompts/umbrales hasta alcanzar objetivos en `calibration`.

## Fuera de alcance

- Mirar `validation`.

## Comments
