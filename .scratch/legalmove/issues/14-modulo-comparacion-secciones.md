# 14: Módulo de comparación de secciones

**Status:** ready-for-agent

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** T13

## Resumen

`checks.py` pasó de 160 a 1064 líneas, y los defectos posteriores al T12 cayeron en la lógica que compara el texto de una sección en el contrato original y en la adenda. Esa lógica solo se podía probar armando un `ContextMap` y un `ExtractionResult` completos. Este ticket la mueve a su propio módulo, sin cambiar el comportamiento, para probar sus casos difíciles de forma directa.

## Resultado vertical

`src/section_comparison.py`, en la capa de dominio, contiene la segmentación por `ref`, el emparejado de secciones, el veredicto igual/distinta/desconocida, las reglas de reformulación citada y de cola, las etiquetas de sección y el tratamiento de `[ILLEGIBLE]`. Su interfaz pública es chica: `DocumentSegments`, `SectionPair`, `TextComparison`, `segment_document`, `segment_both_documents`, `pair_sections`, `compare_section` y `describe_text_difference`. `checks.py` conserva los checks de las decisiones 94 y 99 y la señal blanda `diff_agreement` (decisión 20), que importan el módulo nuevo; el módulo nuevo nunca importa `checks.py`.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: ninguno nuevo

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §4.4 (checks de la extracción)
- §6.2 (tests de arquitectura)

Decisiones del registro §8.4: 20, 94, 99, 104

## Criterios de aceptación

- [x] La lógica de comparación vive en `src/section_comparison.py` y no importa `checks.py`, LangChain, OpenAI, Langfuse ni `config.py`.
- [x] `checks.py` conserva `check_missing_text_changes`, `check_unchanged_text_reports`, sus mensajes de reintento y `compute_diff_agreement`.
- [x] Ningún test existente se borra ni se debilita; solo cambian líneas de import.
- [x] `tests/test_layering.py` incluye el módulo nuevo en las listas de dominio y de módulos sin Langfuse.
- [x] `CONTEXT.md` define **Section Comparison**.
- [x] Tests unitarios y style gate en verde.

## Tests requeridos (TDD estricto)

Es un refactor: los tests nuevos pasan desde el primer momento, y eso es lo esperado. En `tests/test_section_comparison.py`, contra la interfaz pública: reformulación citada (incluida la forma `"texto".`), cola con ordinal que nombra solo otras secciones o no, instrucciones encadenadas, una corrida de `[ILLEGIBLE]` dentro y fuera del límite (desconocida o distinta, nunca igual), tolerancia al cierre en la última sección, cola abierta, segmentación con `ref` duplicadas o ausentes, y la cita acotada de `describe_text_difference`.

## Fuera de alcance

- Cualquier cambio de comportamiento en los checks o en `diff_agreement`.
- Corridas de evals o tests de integración.
