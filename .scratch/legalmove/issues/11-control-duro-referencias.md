# 11: Control duro de referencias inexistentes

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T9

## Resumen

Cuando la adenda cita una cláusula que el contrato original no tiene, el robot a veces inventa una cláusula nueva en lugar de pedir ayuda. Una instrucción en el prompt no alcanza: funcionó una vez de dos. Se agrega un control en código que lo frena siempre y manda el caso a una persona. De paso se corrige al juez, que le baja la nota a resúmenes correctos cuando la respuesta de referencia menciona cosas que no son cambios.

## Resultado vertical

Cada fila del `ContextMap` declara qué hace la instrucción de la adenda sobre esa sección (`instruction`: `none | adds | rewrites | removes`). En una adenda `delta`, una fila `new:` cuya instrucción es `rewrites` o `removes` es una referencia no resuelta: un check duro en `checks.py`, que corre solo sobre un mapa sin otros errores, deriva a revisión humana (`UnresolvedReferenceError`, exit `10`) sin consumir el reintento. El único caso `dangling` de `calibration` (`boundary_delta_suministro_dangling`) termina en revisión humana en todas las corridas, sin falsos positivos en los casos legítimos. El juez no penaliza la ausencia de hechos que no son cambios (renumeración, datos sin modificar).

## Secciones de plan.md que gobiernan el trabajo

- §2.3, §2.6
- §3.3, §4.4–4.5 (checks del mapa)
- §6.6

Decisiones del registro §8.4: 37, 60, 75, 78, 79

## Criterios de aceptación

- [ ] `SectionMapping.instruction` (`InstructionKind`) es obligatorio en el schema estricto y tiene sus reglas en `checks.py`: terminal para una fila `new:` de `delta` marcada `rewrites` o `removes`; reintento normal (`instruction_mismatch`) para las demás incoherencias.
- [ ] Una referencia no resuelta produce `UnresolvedReferenceError` (exit `10`, issue `unresolved_reference`) sin consumir el reintento, igual que `unknown`.
- [ ] En `calibration`, dos corridas completas autorizadas por el autor sin regresiones contra la línea base, con Revisión humana al 100 %.
- [ ] Al menos 5 repeticiones de `boundary_delta_suministro_dangling` (`run_evals.py --case`, cada una con su propio reporte y sin comparar contra la línea base), todas terminadas en revisión humana.
- [ ] El juez puntúa ≥ 7 los resúmenes correctos de `boundary_delta_suministro_injection` y `boundary_restated_servicios_renumber`, sin tocar casos sellados.
- [ ] Cada ajuste tiene su fila en §8.4 o su nota en `docs/calibration/`.

## Tests requeridos (TDD estricto)

**RED:** un `ContextMap` `delta` con una fila `new:` marcada `rewrites` pasa los checks; el juez no recibe la regla sobre hechos que no son cambios.

**GREEN:** el check duro y el ajuste del prompt del juez.

## Fuera de alcance

- Mirar `validation`.

## Comments
