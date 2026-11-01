# 17: Detalle de cambios con diff y carga en dos pasos

**Status:** implementado el 2026-11-01, pendiente de revisión del autor (grilling del 2026-11-01, 16 decisiones; decisiones 112 a 115 en `plan.md`)

**Fase:** 3 — interfaz web

**Bloqueado por:** —

## Resumen

Dos mejoras del front que salen del mockup del 2026-11-01 (artifact "LegalMove diff view"):

1. **Detalle de cambios con diff.** La vista de resultado suma, bajo el resumen, una tarjeta por cada `Change` con el texto antes y después. Hoy no se puede: la API solo devuelve los tres campos públicos de `ContractChangeOutput`, y `job_runner.py:116` descarta el resto de `AnalysisResult` al guardar el job.
2. **Carga en dos pasos.** El formulario muestra primero solo el upload del contrato original; al elegir un archivo válido avanza solo al paso de la adenda. `Analyze` se habilita con las dos imágenes.

`ContractChangeOutput` no cambia: es el contrato público del assignment y lo consumen el CLI, los evals y Compliance. El detalle viaja en campos nuevos del cuerpo de `GET /analyses/{id}`.

## Resultado vertical

### Dominio: `change_details.py`

- Módulo de dominio nuevo, sin imports de LangChain, OpenAI, Langfuse ni `config.py`. Se suma a `DOMAIN_MODULES` en `tests/test_layering.py` y a la tabla de capas de `CLAUDE.md`.
- Modelo Pydantic `ChangeDetail` con `section_id`, `section_title`, `change_type`, `topic`, `description`, `original_quote` y `amendment_quote`.
- Función pura `build_change_details(extraction, context_map) -> list[ChangeDetail]`:
  - una entrada por `Change`, en el orden de `extraction.changes` (el mismo orden que las oraciones del resumen, no el orden canónico de `sections_changed`);
  - conserva varios `Change` de la misma sección;
  - `section_title` sale del `title` del `SectionMapping` con el mismo `section_id`; si es `None` o no hay mapping, queda `None`;
  - las citas pasan sin modificarse.

### Jobs y runner

- `JobUpdate` y `Job` (`jobs.py`) ganan `changes: list[ChangeDetail] | None = None` y `amendment_kind: AmendmentKind | None = None`.
- `job_runner.py` los arma en el `Outcome` de `done` con `build_change_details(result.extraction, result.context_map)` y `result.context_map.amendment_kind`. `AnalysisResult` y `pipeline.py` no cambian.
- En `human_review` y `failed` quedan `None`: `InvalidExtractionError` no lleva el resultado y `ValidationIssue` nunca cita los documentos (`errors.py:63`).
- El output del span raíz en Langfuse sigue siendo `ContractChangeOutput`. Las citas ya viajan enmascaradas en el span `extraction_agent` (plan §5.4); el ticket no suma nada a la traza.

### Contrato HTTP

- `AnalysisBody` (`api_contract.py`) gana `changes` y `amendment_kind`, ambos `| None = None` (los campos requeridos siguen siendo `id`, `status`, `created_at` y `trace_id`), presentes solo cuando `status` es `done`, igual que `output`.
- El texto literal del contrato sale por HTTP sin enmascarar: quien lo pide es el dueño del documento, y enmascarar rompería el diff. Queda en el store en memoria durante `API_JOB_TTL_SECONDS`.
- Se regeneran `web/openapi.json` (`scripts/export_openapi.py`) y `web/src/api/schema.d.ts` (`pnpm gen:types`). El chequeo del CI (`git diff --exit-code`) los cubre.

### Front: contrato y estado

- `web/src/api/contract.ts` suma `changeDetailSchema`, anclado con `satisfies z.ZodType<Schemas["ChangeDetail"]>`, y el cuerpo de `GET /analyses/{id}` incluye `changes` y `amendment_kind`. Un `change_type` o un `topic` desconocidos son error de contrato.
- `machine.ts`: el estado `done` lleva `output`, `changes` y `amendment_kind`.
- `use-analysis.ts` los pasa del cuerpo validado al estado.
- `web/src/test/fixtures.ts`: `DONE_BODY` usa valores reales de `Topic` y `ChangeType`, y trae `changes` y `amendment_kind`.

### Front: diff

- Componente propio, inspirado en el `file-diff` de beUI pero sin vendorearlo: el bloque trae `shiki` (con wasm), modo streaming, gutter numérico y `whitespace-pre`, y no tiene diff por palabra.
- Archivos:
  - `web/src/analysis/change-lines.ts`: función pura que convierte un `ChangeDetail` en líneas tipadas (`removed`, `added`, `instruction`) con sus fragmentos;
  - `web/src/components/change-diff.tsx`: una tarjeta;
  - `web/src/components/change-diff-list.tsx`: la lista, con key por índice;
  - `web/src/components/motion/disclosure.tsx`: copia de `agent-disclosure.tsx` de beUI para el plegado animado.
- Dependencia nueva: `diff==9.0.0` (BSD-3, trae sus tipos; sin `@types/diff`).
- Reglas por `change_type`:
  - `modified`: `original_quote` como línea `removed` y `amendment_quote` como línea `added`, con diff por palabra (`diffWords`, que ignora las diferencias de espacios del OCR); los fragmentos se marcan con `<del>` y `<ins>`;
  - `added`: solo la línea `added`;
  - `removed`: la línea `removed`; en delta, el `amendment_quote` es la instrucción de derogación y se muestra como línea neutra rotulada "Amendment instruction", en itálica y gris, sin marca + ni − y sin diff por palabra.
- Tarjeta: `section_id` en monoespaciada, `section_title` (o solo el ID si es `None`), badge de `change_type`, botón de copiar y, debajo, `topic` y `description`. Sin contadores +/−. Arranca abierta; se pliega con un `<button>` con `aria-expanded`.
- Las líneas cortan (`white-space: pre-wrap`); el gutter muestra `orig` / `adenda`, porque las citas no traen números de línea.
- `ResultView` suma la sección "Change details" bajo el resumen y un badge con `amendment_kind` en el encabezado. Sin cambios, la sección no se muestra.

### Front: carga en dos pasos

- `upload-form.tsx` pasa a un estado de paso: `original → amendment`.
  - Paso 1: solo el `AttachmentUpload` del contrato.
  - Al elegir un archivo se valida con `imageFile` de `upload-form-schema.ts` (`AttachmentUpload` solo controla el tamaño, no el tipo). Si falla, el formulario se queda en el paso, descarta el archivo y muestra el error con `role="alert"`. Si pasa, avanza. Lo mismo en el paso 2.
  - En el paso 2 el contrato elegido queda como una fila con nombre, tamaño y botón `Change`. `Change` limpia solo el original y vuelve al paso 1; si había adenda, su fila queda visible en modo lectura debajo del upload. Al elegir un original válido se vuelve a la vista del paso 2 con las dos filas.
  - La adenda elegida se puede quitar.
  - `Analyze` deshabilitado hasta tener las dos imágenes. El `safeParse` del par al enviar se mantiene como defensa.
- Indicador de pasos propio: un `<ol>` de dos pasos con `aria-current="step"`, con las clases de `StepItem` de `stage-stepper.tsx`. Sin bloque nuevo de beUI (`adaptive-stepper` es un contador numérico).
- Foco y anuncio:
  - al avanzar, el foco va al título del paso 2 (`tabIndex=-1`, como `StageStepper`);
  - al tocar `Change`, el foco va al título del paso 1;
  - una live region `role="status"`, fuera del subárbol animado, anuncia "Step 2 of 2: amendment" o "Step 1 of 2: original contract".
- El multipart no cambia: se sigue enviando el mismo par con el rol correcto.

### README

- Fila del cuerpo de `GET /analyses/{id}` (hoy línea 237): suma `changes` y `amendment_kind`.
- Descripción del formulario (hoy línea 292): carga en dos pasos.

## Decisiones tomadas en el grilling (2026-11-01)

1. **`changes` y `amendment_kind` en `AnalysisBody`, no en `ContractChangeOutput`**: el contrato público no cambia; el cuerpo de la API es nuestro y puede crecer.
2. **`ChangeDetail` se arma en un módulo de dominio propio** (`change_details.py`), no en el adapter HTTP ni dentro de `models.py`.
3. **Se arma en `job_runner.py`, no en `AnalysisResult`**: hoy solo la API lo consume.
4. **Solo en `done`**: en `human_review` el error no lleva el resultado y los issues nunca citan documentos.
5. **El output del span raíz no cambia**; las citas ya están enmascaradas en el span de extracción.
6. **Citas sin enmascarar en HTTP y en el store durante el TTL**, registrado como riesgo en §7.1.
7. **Componente de diff propio**, inspirado en `file-diff`, sin `shiki`; se copia solo `agent-disclosure.tsx`.
8. **`diffWords` de `diff==9.0.0`** con `<del>` e `<ins>`.
9. **`removed` en delta**: la instrucción se muestra como línea neutra "Amendment instruction", no como `added`.
10. **Sin contadores +/−**: siempre darían 0/1 o 1/1.
11. **Orden de `extraction.changes`**, duplicados por sección permitidos, key por índice.
12. **Validación por archivo en cada paso** con `imageFile`; el `safeParse` del par se mantiene.
13. **`Change` limpia solo el original**; la adenda elegida se conserva.
14. **Indicador de pasos propio** (`<ol>` con `aria-current`).
15. **Foco al título del paso y anuncio en una live region** al cambiar de paso.
16. **Un solo ticket** para backend y front (pedido del autor).

Se anotan en `plan.md` como decisiones 112 a 115, más una fila en la tabla de riesgos de §7.1.

## Criterios de aceptación

- [ ] `GET /analyses/{id}` en `done` devuelve `changes` y `amendment_kind`; en `pending`, `running`, `human_review` y `failed` no los devuelve.
- [ ] `ContractChangeOutput`, la salida del CLI y el output del span raíz en Langfuse no cambian.
- [ ] `build_change_details` respeta el orden de `extraction.changes`, conserva duplicados por sección y resuelve `section_title`, con `None` cuando no hay título.
- [ ] `change_details.py` está en `DOMAIN_MODULES` y no importa infraestructura.
- [ ] `web/openapi.json` y `schema.d.ts` están regenerados y el chequeo del CI pasa.
- [ ] `changeDetailSchema` está anclado al tipo generado con `satisfies`.
- [ ] La vista de resultado muestra una tarjeta por cambio; `modified` con diff por palabra en `<del>`/`<ins>`; `removed` en delta con la línea "Amendment instruction".
- [ ] El formulario muestra primero solo el contrato, avanza solo con un archivo válido, se queda con un archivo inválido y habilita `Analyze` con las dos imágenes.
- [ ] Al cambiar de paso el foco va al título del paso y la live region lo anuncia.
- [ ] El front no depende de `shiki`.
- [ ] Biome, typecheck, tests y build del front en verde; style gate y unit tests de Python en verde.
- [ ] `plan.md` registra las decisiones 112 a 115 y la fila de riesgos.
- [ ] El README describe los campos nuevos y la carga en dos pasos.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan).**

En Python:

- `tests/test_change_details.py`: un `ChangeDetail` por `Change` y en orden; dos `Change` de la misma sección dan dos entradas; `section_title` desde el context map; `None` sin título o sin mapping; las citas pasan sin modificarse.
- `tests/test_job_runner.py`: un job `done` guarda `changes` y `amendment_kind`; `human_review` y `failed` no los guardan; el output del span raíz (`RecordingTracer`) sigue siendo `ContractChangeOutput`.
- `tests/test_api.py`: `GET /analyses/{id}` incluye `changes` y `amendment_kind` solo en `done`; `output` sigue igual.
- `tests/test_openapi.py`: `ChangeDetail` y los enums `ChangeType`, `Topic` y `AmendmentKind` están en `components.schemas`; los campos requeridos de `AnalysisBody` no cambian; el cuerpo terminal sigue pasando contra el modelo documentado.
- `tests/test_layering.py`: `change_details.py` es dominio.

En el front (Vitest, Testing Library, MSW, happy-dom):

- `changeDetailSchema` acepta un cuerpo válido y rechaza un `change_type` desconocido.
- `change-lines.ts`: `modified` da `removed` + `added` con fragmentos por palabra; `added` da solo `added`; `removed` en delta da `removed` + `instruction`; `removed` en restated da solo `removed`.
- `ChangeDiffList` renderiza una tarjeta por cambio (dos para la misma sección), con título o solo el ID, badge y `<del>`/`<ins>`; plegar y desplegar cambia `aria-expanded`.
- `ResultView` no muestra "Change details" cuando no hay cambios y muestra el badge de `amendment_kind`.
- El formulario:
  - al principio solo está el upload del contrato;
  - un PDF en el paso 1 no avanza y muestra el error;
  - una imagen válida avanza al paso 2, el foco queda en su título y la live region anuncia el paso;
  - `Change` vuelve al paso 1, conserva la adenda y lleva el foco al título del paso 1;
  - `Analyze` deshabilitado hasta tener el par;
  - el multipart manda `original` y `amendment` con el rol correcto.
- Se reescriben los tests de `analysis-screen.test.tsx` que hoy asumen los dos uploads a la vez.

**GREEN:** `change_details.py`, los campos nuevos en `jobs.py`, `job_runner.py` y `api_contract.py`, el OpenAPI y los tipos regenerados, `changeDetailSchema`, `machine.ts`, `use-analysis.ts`, `fixtures.ts`, `change-lines.ts`, `disclosure.tsx`, `change-diff.tsx`, `change-diff-list.tsx`, `ResultView`, el formulario en dos pasos y el README.

## Secciones de plan.md que gobiernan el trabajo

- §1 (Layout del repositorio y Arquitectura)
- §4.7 (sobre de salida)
- §5.4 (qué viaja a la traza)
- §7.1 (riesgos)
- Decisiones 109 a 111 (contrato por jobs y progreso)

## Tickets derivados

- Vista de diff lado a lado (original a la izquierda, adenda a la derecha).
- Mostrar el texto completo de cada documento como contexto del diff.

## Preguntas abiertas

- Ninguna.
