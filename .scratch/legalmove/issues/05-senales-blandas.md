# 05: Señales blandas en `output_validation`

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T4

## Resumen

Se agregan alarmas suaves. Un programa viejo compara los textos línea por línea y dice si coincide con lo que dijo el robot. Otra alarma mira si el tipo de enmienda cuadra. Otra cuenta cuántos temas quedaron en "otros". Las alarmas solo avisan en la bitácora. Nunca frenan nada ni obligan a repetir.

## Resultado vertical

`difflib`, acuerdo de formato y proporción de `other` como telemetría. La traza del par A muestra `diff_agreement` con cobertura `sections_diffed / sections_total` y `kind_agreement`; la del par B no registra `diff_agreement` (N/A) y sí `kind_agreement = 1`; `other_topic_rate` es N/A sin cambios. Nada de esto reintenta ni bloquea.

## Cobertura de la consigna

- Requisitos: refuerza R18
- Entregables: extras de §13.3 (checks deterministas)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §2.3 y §2.5 (segmentación, umbral 0.97 constante, Jaccard, blandura)
- §5.4–5.5 (semántica N/A)

Decisiones del registro §8.4: 20, 45, 47

## Criterios de aceptación

- [ ] La segmentación usa `original_ref`/`amendment_ref` normalizados.
- [ ] Las refs no encontradas quedan fuera y bajan la cobertura.
- [ ] El valor es `1.0` si ambos conjuntos están vacíos con cobertura.
- [ ] El valor es N/A sin segmentables o en `delta`.
- [ ] Los agregados nunca convierten N/A en cero.
- [ ] Ninguna señal blanda reintenta ni bloquea.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_checks.py` (segmentación con ref ausente; ratio en el umbral; Jaccard; N/A)
- `tests/test_pipeline.py` (scores blandos registrados o ausentes según el caso)

**GREEN (lo mínimo que los hace pasar):**

- `src/checks.py` (difflib)
- `src/pipeline.py` (`output_validation`)

## Fuera de alcance

- Calibrar el umbral (T9).
- Promover `difflib` a duro.

## Comments
