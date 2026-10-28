# 11: Control duro de referencias inexistentes

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T9

## Resumen

Cuando la adenda cita una cláusula que el contrato original no tiene, el robot a veces inventa una cláusula nueva en lugar de pedir ayuda. Una instrucción en el prompt no alcanza: funcionó una vez de dos. Se agrega un control en código que lo frena siempre y manda el caso a una persona. De paso se corrige al juez, que le baja la nota a resúmenes correctos cuando la respuesta de referencia menciona cosas que no son cambios.

## Resultado vertical

El `ContextMap` declara las referencias de la adenda que no pudo resolver contra el original; un check duro en `checks.py` deriva a revisión humana si hay al menos una. Los dos casos `dangling` de `calibration` terminan en revisión humana en todas las corridas, sin falsos positivos en los casos legítimos. El juez no penaliza la ausencia de hechos que no son cambios (renumeración, datos sin modificar).

## Secciones de plan.md que gobiernan el trabajo

- §2.3, §2.6
- §3.3, §4.4–4.5 (checks del mapa)
- §6.6

Decisiones del registro §8.4: 37, 60, 75, 78, 79

## Criterios de aceptación

- [ ] El `ContextMap` tiene un campo para referencias no resueltas, con su schema estricto y su regla en `checks.py`.
- [ ] Una referencia no resuelta produce un error de revisión humana sin consumir reintento si el plan así lo decide (se discute en chat antes de escribirlo en `plan.md`).
- [ ] En `calibration`, Revisión humana llega a 100 % y la precisión de los casos legítimos no baja, en dos corridas consecutivas autorizadas por el autor.
- [ ] El juez puntúa ≥ 7 los resúmenes correctos de `boundary_delta_suministro_injection` y `boundary_restated_servicios_renumber`, sin tocar casos sellados.
- [ ] Cada ajuste tiene su fila en §8.4 o su nota en `docs/calibration/`.

## Tests requeridos (TDD estricto)

**RED:** un `ContextMap` con una referencia no resuelta pasa los checks; el juez no recibe la regla sobre hechos que no son cambios.

**GREEN:** el check duro y el ajuste del prompt del juez.

## Fuera de alcance

- Mirar `validation`.

## Comments
