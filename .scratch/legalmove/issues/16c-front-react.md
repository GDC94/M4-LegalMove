# 16c: Front con React y TypeScript

**Status:** aprobado para implementar (grilling del 2026-10-29, 26 decisiones)

**Fase:** 3 — interfaz web

**Bloqueado por:** 16b

**Paraguas:** [T16](16-api-http-y-front-web.md)

## Resumen

Tercera rebanada: una app web en `web/` para subir el contrato original y la adenda, seguir el análisis etapa por etapa y ver los cambios legales, o el motivo de la revisión humana. Consume la API de 16a y el stream de 16b. Toca el backend solo para exportar el OpenAPI del que se generan los tipos, y el CI para sumar el job del front.

## Resultado vertical

### Proyecto `web/`

- Vite, React 19, TypeScript en modo estricto, Tailwind v4, TanStack Query, Zod.
- Componentes de UI de [beUI](https://beui.dev) (MIT, copy-paste al estilo shadcn): el bloque `AttachmentUpload` para el formulario de upload y lo que haga falta del mismo catálogo para el stepper y las vistas. Se agregan con `pnpm dlx shadcn add @beui/<componente>`; el código queda vendoreado en `web/src/components/` y formateado por Biome. Exige Tailwind v4 y React 19, y trae `motion`, `lucide-react`, `tailwind-merge`, `clsx`, `@floating-ui/dom`, los archivos de soporte de `lib/` y el bloque de tokens CSS de su tema en `globals.css`.
- `pnpm` como gestor de paquetes.
- Biome para lint y formato, con las reglas de hooks de React (`useExhaustiveDependencies`, `useHookAtTopLevel`).
- Vitest, Testing Library y MSW para tests.

### Contrato y tipos

- `scripts/export_openapi.py` construye la app con `ApiSettings` dobles (sin `.env`, sin Langfuse) y escribe `web/openapi.json`.
- `pnpm gen:types` corre `openapi-typescript` sobre ese archivo y genera los tipos del cliente.
- `web/openapi.json` y los tipos generados se commitean. El CI vuelve a exportar y regenerar, y falla con `git diff --exit-code` si difieren: el contrato tiene una sola fuente de verdad y, si cambia el backend, el front no compila.
- **Validación en runtime con Zod, anclada a los tipos generados.** Los tipos generados no validan nada en ejecución, y el front parsea a mano el stream SSE y las respuestas de la API. Se escriben esquemas Zod para las formas de frontera: el cuerpo de `GET /analyses/{id}`, los dos eventos (`stage` y `job`), el sobre de error y el formulario de upload. Cada esquema que corresponde a una forma del OpenAPI se declara con `satisfies z.ZodType<components["schemas"]["X"]>`: si el backend cambia, el tipo generado cambia y `tsc` falla en el esquema. El OpenAPI sigue siendo la única fuente de verdad; Zod no la duplica, la hace cumplir en runtime. El esquema del formulario (tipo PNG o JPEG, tamaño, dos archivos) vive solo en Zod porque no es parte del contrato HTTP.

### Cliente de API

- Chico y tipado: `createAnalysis`, `getAnalysis` y `streamAnalysisEvents`.
- `streamAnalysisEvents` usa `fetch` streaming con un parser SSE propio (`id:` y `data:`, comentarios ignorados) porque `EventSource` no permite enviar el header `X-API-Key`. Reenvía `Last-Event-ID` al reconectar.
- Política de reconexión: hasta 3 reintentos con backoff; después cae a polling de `GET /analyses/{id}` con TanStack Query (`refetchInterval` hasta un estado terminal). Llega al mismo resultado por cualquiera de los dos caminos.
- Discrimina los eventos por `kind` (`stage` | `job`) con `z.discriminatedUnion`, anclada a la unión generada desde el OpenAPI. Un evento o una respuesta que no pasan el esquema se tratan como error de contrato (vista de error), nunca se renderizan a medias.

### Pantalla

- Una sola pantalla, sin router, con una máquina de estados: `idle → uploading → running → done | human_review | failed`.
- Componentes con contenedor y presentación separados:
  - formulario de upload con **dos instancias de `AttachmentUpload`**, una por rol (original y adenda), cada una con `multiple={false}`, `maxFiles={1}`, `accept="image/png,image/jpeg"` y `maxFileSize` igual al límite de la API. El bloque solo elige y previsualiza (su barra de progreso es simulada con timers; no sube nada): el `POST /analyses` lo hace el cliente de API con los dos archivos. El esquema Zod del formulario valida tipo, tamaño y par completo antes de enviar, solo como ayuda de UX; la validación que vale es la del backend (`422`);
  - stepper con las cinco etapas de `StageName`: pendiente, en curso, lista o fallida. No hay "reintentando" (16b no emite `attempt`);
  - vista de resultado con los cambios legales (`sections_changed`, `topics_touched`, `summary_of_the_change`);
  - vista de revisión humana con los `issues` del sobre;
  - vista de error con un mensaje claro y la opción de volver a empezar (subir de nuevo; no hay reintento de job).
- Accesibilidad básica: el stepper anuncia los cambios con `aria-live` y todo se puede usar con teclado.

### Desarrollo local y configuración

- El proxy de Vite apunta a la API, así que no hace falta configurar CORS.
- `VITE_API_KEY` solo para la demo local. Es una limitación documentada: la key vive en el bundle, y un despliegue real necesita login o un backend-for-frontend (ticket derivado).

### CI y README

- `.github/workflows/ci.yml` suma un segundo job `web` (pnpm, Node 22): install, lint, typecheck, test, build, y el chequeo de `web/openapi.json` y los tipos generados (`git diff --exit-code`). Sin secretos.
- `tests/test_ci_workflow.py` se actualiza para el job nuevo.
- README: cómo levantar la API y el front juntos.

## Decisiones tomadas en el grilling (2026-10-29)

Las que gobiernan esta rebanada; el detalle completo está en [T16](16-api-http-y-front-web.md).

1. **`pnpm`** por velocidad y lockfile estricto.
2. **Biome** en lugar de ESLint: una herramienta para lint y formato, y hoy trae las reglas de hooks de React, que era el argumento a favor de ESLint.
3. **Una pantalla sin router**: una máquina de estados alcanza; React Router no aporta nada a un flujo lineal.
4. **Parser SSE propio** sobre `fetch` streaming (~40 líneas); 3 reconexiones con backoff y después polling.
5. **Sin "reintentando" en el stepper**: 16b no emite `attempt` (ver [16b](16b-progreso-sse.md)).
6. **OpenAPI congelado en el repo** y verificado en el CI.
7. **`VITE_API_KEY` solo para la demo local**, documentado como limitación.
8. **Demo local**: `uvicorn` + `vite dev`; contenedor o PaaS es un ticket posterior.
9. **Zod a mano, anclado a los tipos generados** (agregado por el autor el 2026-10-29). La alternativa era generar los esquemas desde el OpenAPI (`openapi-zod-client`): cero duplicación, pero un generador más en el CI, sin lugar para el esquema del formulario y con una herramienta menos estable que `openapi-typescript`. Con `satisfies z.ZodType<...>` los esquemas a mano no pueden derivar del contrato.
10. **beUI para los componentes, con dos `AttachmentUpload`** (agregado por el autor el 2026-10-29). Verificado: MIT, repo `starc007/ui-components` activo, Tailwind v4 y React 19 como los nuestros, input nativo con drag-and-drop. Una sola instancia con `maxFiles={2}` perdería cuál archivo es el original y cuál la adenda; dos instancias fijan el rol por posición. Los defaults del bloque (`multiple`, 12 archivos, 500 MB) se sobreescriben. Riesgo a vigilar: `motion` en jsdom durante los tests de Vitest; si molesta, se mockea o se respeta `prefers-reduced-motion`.

## Configuración

- `VITE_API_KEY`: la key que el front envía en `X-API-Key`. Solo para la demo local.
- `VITE_API_URL` o el proxy de Vite, a elección de la implementación; en desarrollo se usa el proxy.

## Criterios de aceptación

- [ ] Se puede subir un par de imágenes y ver el resultado de punta a punta contra la API local.
- [ ] El stepper avanza con los eventos `stage` del stream y marca la etapa fallida cuando llega `failed`.
- [ ] Se renderizan los tres estados terminales: `done`, `human_review` y `failed`.
- [ ] Si el stream se corta, la app reconecta con `Last-Event-ID`; tras 3 fallos cae a polling y llega al mismo resultado.
- [ ] `web/openapi.json` y los tipos se generan desde la app con settings dobles, y el CI falla si no coinciden con lo commiteado.
- [ ] Toda respuesta de la API y todo evento SSE pasan por un esquema Zod antes de llegar al estado; cada esquema del contrato está anclado al tipo generado con `satisfies`.
- [ ] Lint (Biome), typecheck, tests y build del front están en verde en el job `web` del CI.
- [ ] `tests/test_ci_workflow.py` cubre el job nuevo.
- [ ] El README explica cómo correr la API y el front juntos y documenta la limitación de `VITE_API_KEY`.
- [ ] El style gate y los unit tests de Python siguen en verde.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan),** con Vitest, Testing Library y MSW:

- el formulario rechaza tipos y tamaños inválidos y envía las dos imágenes en el multipart con el rol correcto (`original` del primer `AttachmentUpload`, `amendment` del segundo); no envía con un solo archivo;
- el parser SSE separa eventos por `id:` y `data:`, ignora comentarios y entrega objetos validados con Zod; un `data` que no pasa el esquema produce un error de contrato;
- los esquemas Zod del contrato compilan con `satisfies` contra los tipos generados (test de tipos con `expectTypeOf` o `tsc` en el job `web`);
- el esquema del formulario rechaza un tipo que no es PNG o JPEG, un tamaño por encima del límite y un par incompleto;
- el stepper avanza con una secuencia simulada de eventos `stage` y marca la etapa fallida;
- cada estado terminal (`done`, `human_review`, `failed`) renderiza su vista con el cuerpo de `GET /analyses/{id}`;
- el cliente reconecta con `Last-Event-ID` y, tras 3 fallos, cae a polling hasta el estado terminal;
- la máquina de estados no permite transiciones inválidas.

En Python:

- `tests/test_ci_workflow.py`: el job `web` existe con sus pasos y sin secretos.
- `tests/test_openapi_export.py`: `scripts/export_openapi.py` produce un JSON válido sin `.env`.

**GREEN:** el proyecto `web/`, los esquemas Zod, el cliente de API, el parser SSE, los componentes (los de beUI vendoreados y los propios), `scripts/export_openapi.py`, la configuración del CI y el README.

## Secciones de plan.md que gobiernan el trabajo

- §1 (Layout del repositorio y Arquitectura)
- §4.7 (sobre de salida)
- §8.2 (README)

## Tickets derivados

- Autenticación real para el front (login o backend-for-frontend).
- "Reintentando" en el stepper cuando exista el evento `retrying` (ticket derivado de 16b).
- `docker-compose` y despliegue.

## Preguntas abiertas

- Ninguna.
