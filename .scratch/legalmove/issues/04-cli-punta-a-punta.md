# 04: CLI de punta a punta (tracer bullet público)

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** T3

## Resumen

Ahora todo anda junto. Se escribe un comando, se dan dos fotos, sale la respuesta en orden. Cada paso queda anotado en la bitácora con su número de corrida. Si algo falla, el programa avisa con un código claro y no escupe medias respuestas. Si la bitácora no guarda, mejor no responder. El proyecto se sube a un sitio público.

## Resultado vertical

`uv run python src/main.py original.png amendment.png` devuelve el JSON validado con la traza confirmada. Sobre los pares A y B, stdout contiene solo `ContractChangeOutput`, exit `0`, stderr termina con `run_id` y `trace_id`, y Langfuse muestra `contract-analysis` con los 4 spans sugeridos + `output_validation`, una generation por llamada y scores `outcome`, `schema_valid`, `consistency`, `grounding_rate`, `context_retries`, `extraction_retries`. Cada error de §4.5 produce su exit code y su sobre. Se agrega el CI mínimo al remoto y queda en verde.

## Cobertura de la consigna

- Requisitos: R01, R06, R16, R17, R18, R19
- Entregables: E01, E10, E11

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §1 (pipeline)
- §3.3 (`AnalysisResult`, paso 5)
- §4.4 (`output_validation`)
- §4.5–4.7 (errores, captura solo en `main.py`, contrato de la CLI)
- §5.1 (presupuesto monotónico)
- §5.3–5.5
- §5.7 (lifecycle, flush fail-closed)
- §6.2 (AST: `load_settings` solo en `main.py`)
- §6.9 (remoto y CI mínimo)

Decisiones del registro §8.4: 10, 13, 35, 40, 43, 51, 52, 53, 57, 58

## Criterios de aceptación

- [ ] `run_analysis()` es secuencial vía el puerto, sin `flush()` ni `langfuse`.
- [ ] El presupuesto de 300 s se consulta antes de cada intento, con timeout efectivo = mín(por llamada, remanente).
- [ ] `main.py` genera `run_id`, carga `Settings` una vez y arma el contenedor `@dataclass(frozen=True)`.
- [ ] `main.py` bufferiza stdout hasta un `flush()` exitoso.
- [ ] Si el flush falla, `main.py` devuelve `ObservabilityError` (exit 5).
- [ ] Si también falla el flush del `finally`, se preserva el error original.
- [ ] `--session-id` propaga `session_id`.
- [ ] `trace_id` es `null` si Langfuse no inicializó.
- [ ] El remoto `GDC94/M4-LegalMove` está actualizado (push con la cuenta GDC94).
- [ ] `.github/workflows/ci.yml` existe según §6.9 (`uv sync`, ruff check, ruff format --check, mypy, pytest; sin integración ni evals) y la primera corrida en GitHub está en verde.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- `tests/test_pipeline.py` (orden de etapas y datos con stubs y `RecordingTracer`; jerarquía; scores; presupuesto agotado; `level=ERROR`)
- `tests/test_main.py` (cada exit code y sobre; stdout vacío en error; stdout solo tras flush; `run_id` siempre; error preservado)
- Arquitectura: solo `main.py` llama `load_settings`; solo `observability.py`/`main.py` importan `langfuse`; entregables en sus paths salvo README.
- Integración: e2e A y B; masking por `CallbackHandler` con DNI falso y polling de `langfuse.api.trace.get` ≤ 30 s; falla controlada del adaptador → stdout vacío y exit 5.

**GREEN (lo mínimo que los hace pasar):**

- `src/pipeline.py`
- `src/main.py`
- Etapa `output_validation` en el pipeline.

## Fuera de alcance

- `diff_agreement`, `kind_agreement`, `other_topic_rate`.
- README.

## Comments
