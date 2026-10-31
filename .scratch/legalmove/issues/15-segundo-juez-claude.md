# 15: Segundo juez con Claude en las evals

**Status:** cerrado el 2026-10-31 (commit `7652674`; grilling del 2026-10-29, 21 decisiones; corrida de calibración registrada abajo)

**Fase:** 2 — golden set y evaluación

**Bloqueado por:** ninguno (T14 cerrado en `c6da650`)

## Resumen

Hoy el juez de las evals es `gpt-4.1` y evalúa resúmenes que escribió `gpt-4o`: es la misma familia de modelos, y un juez tiende a preferir textos parecidos a los suyos. Este ticket agrega un segundo juez de otra familia, Claude Opus 5.5, que puntúa el mismo resumen con la misma rúbrica. Así la nota del juez deja de depender de un único proveedor, y la distancia entre las dos notas pasa a ser un dato propio. El juez nuevo solo corre en las evals: el pipeline de producción y la CLI no cambian.

El segundo juez es **informativo**: no tiene objetivo en §6.6 y no entra en el gate de regresiones. Si después de dos corridas (calibración y validación) la distancia es estable, se discute un objetivo.

## Resultado vertical

- Un adaptador nuevo, `evals/claude_judge.py`, con la clase `ClaudeSummaryJudge` que cumple el puerto `SecondJudge` (`Callable[[str, GoldenCase], SecondJudgeResult]`): recibe el mismo resumen y el mismo `GoldenCase` que `SummaryJudge`, pero devuelve un `SecondJudgeResult` y no un `JudgeVerdict`, porque el resultado tiene que llevar estado, tokens y latencia (ajuste de implementación del 2026-10-31: las dos exigencias del ticket original no cabían en un solo tipo de retorno). Usa `JUDGE_SYSTEM_PROMPT` (`judge-v2`, sin cambios) como `system` y los mismos tres bloques (`reference_summary`, `key_facts`, `candidate_summary`) como único mensaje `user`. La única diferencia entre los dos jueces es el modelo.
- En `evals/judge.py` se extrae la función pura `render_judge_input(summary, case) -> str` (los tres bloques unidos) que usan `build_judge_messages` y el adaptador nuevo. El adaptador no importa `langchain_core`.
- Llama a Claude con el SDK oficial `anthropic==2.3.0`, con `client.messages.create(output_config={"effort": ..., "format": {"type": "json_schema", "schema": JudgeVerdict.model_json_schema()}})` y valida la respuesta con `JudgeVerdict.model_validate_json` **después** de leer `stop_reason`. No se usa `messages.parse`: en 2.3.0 valida el JSON antes de devolver el mensaje, así que un rechazo con texto o un corte por `max_tokens` levantan `ValidationError` y se perderían la categoría y los tokens (hallazgo de la revisión del 2026-10-31). El esquema enviado es el mismo que `parse` habría enviado. Sin `fallbacks`, sin ruta beta.
- `run_evals.py` corre los dos jueces sobre cada caso juzgable, secuencialmente y sobre el mismo conjunto (`expects_output` y salida presente; Human Review y corridas fallidas no se juzgan). El reporte suma:
  - `judge_score_claude`: media por caso de la nota de Claude (`Aggregation.MEAN`);
  - `judge_gap`: media de |nota gpt − nota Claude| sobre los casos con ambas notas (`MEAN`; gap bajo = acuerdo alto);
  - `judge_claude_scored`: casos con nota de Claude sobre casos juzgables (`MICRO`);
  - `judge_claude_spread`: media de |rep 1 − rep k| por caso sobre las repeticiones con nota; con `--claude-judge-reps 1` queda N/A (`value: null, evaluable: 0`), igual que cualquier métrica sin casos evaluables, y el bloque `claude_judge.reps` del reporte dice cuántas repeticiones hubo;
  - `judge_disagreements`: clave de nivel superior del reporte, lista de `{case_id, judge_score, claude_score, judge_reasoning, claude_reasoning}` para |diferencia| ≥ 3.
  - No se reporta correlación: con 21 casos sería ruido.
- Las métricas nuevas se declaran con `Metric(..., gated=False)`; `compare_with_baseline` ignora las no gateadas. `Metric.gated` existe desde este ticket con default `True`.
- El juez de Claude es opcional. Sin `ANTHROPIC_API_KEY`, `run_evals.py` corre exactamente como hoy y el reporte y la metadata del Experiment dicen `claude_judge: "not_run"`; nunca falla por eso.
- Cada caso registra el resultado del segundo juez en `CaseRun.second_judge: SecondJudgeResult | None` (`None` = no corrió):
  - `status: Literal["scored", "refused", "error"]`, `score: int | None`, `reasoning: str | None`, `refusal_category: str | None`, `model: str` (el servido), `input_tokens`, `output_tokens`, `latency_s`.
  - `stop_reason == "refusal"` → `refused` con `stop_details.category`; `stop_reason == "max_tokens"`, excepción del SDK o nota fuera de rango → `error`. Ningún estado produce un 0.
  - Se registran tokens, no dólares: el costo se calcula a mano en este ticket.
- El request: `model` y `output_config={"effort": ...}` desde `EvalSettings`, `max_tokens=8000` (el thinking cuenta dentro), `thinking` omitido (adaptativo por defecto), sin `temperature`. El cliente se construye con `timeout=llm_timeout_seconds` y `max_retries=llm_max_retries`, los mismos settings que usa el juez actual. El adaptador afirma que `response.model` empieza con el modelo configurado; si no, `status="error"` (un modelo sustituido invalida la comparación).
- `CaseRunner` gana los campos `second_judge: SecondJudge | None` y `second_judge_reps: int` (≥ 1). El segundo juez corre después del primero sobre el mismo conjunto juzgable, aunque el primero haya fallado: su fallo no esconde el dato de Claude, y `judge_gap` solo usa los casos con las dos notas. El runner no envuelve al segundo juez en `try/except`: el adaptador es la frontera que convierte fallos del SDK y de validación en `status="error"`.
- `run_evals.py` gana el flag `--claude-judge-reps N` (default 1). Con N > 1 y sin `ANTHROPIC_API_KEY`, falla antes de gastar (`ConfigurationError`): una corrida pagada sin la medición de variación sería un desperdicio silencioso. Con N > 1 la nota del caso es la de la **primera** repetición, así `judge_score_claude` significa lo mismo con cualquier N; las demás repeticiones solo alimentan `judge_claude_spread`.
- La metadata del Experiment en Langfuse suma `claude_judge_model` y `claude_judge_effort` (o `claude_judge: "not_run"`). Las métricas nuevas se publican como scores del Experiment porque salen de `METRICS`.
- **Sin span de Langfuse** para ninguno de los dos jueces en este ticket: el juez actual tampoco lo tiene (`case_runner.py` lo llama fuera de `run_traced`). Trazar los dos jueces es un ticket aparte.

## Decisiones tomadas en el grilling (2026-10-29)

1. **SDK oficial `anthropic`**, no `langchain-anthropic`. El argumento del tracing por callbacks no aplica (el juez actual no tiene tracing) y `with_structured_output` de LangChain es intesteable con dobles; la llamada al SDK se cubre con un doble que registra la llamada. (Implementación: `messages.create` con `output_config.format` en lugar de `messages.parse`; ver "Resultado vertical".)
2. **No hay `temperature`.** La API de Claude devuelve 400 con `temperature` desde 2026-10-21 en todos los modelos. El juez no es determinista. Se mitiga con salida estructurada estricta y se **mide** una vez con `--claude-judge-reps 2` en la primera corrida autorizada; no es comportamiento permanente.
3. **`effort` arranca en `medium`** (el valor por defecto de Opus 5.5), no en `low`. `low` está pensado para chat y clasificación; arrancar bajo y "subir si no alcanza" obliga a una segunda corrida pagada. Se baja a `low` solo si la calibración muestra que `judge_gap` no cambia.
4. **Sin fallback.** `fallbacks: "default"` produce una nota de otro modelo (Opus 4.8) que entraría a `judge_score_claude` como si fuera de Opus 5.5; la guía de evals de Anthropic pide justo lo contrario (abortar si el modelo servido no es el pedido). Un rechazo es "sin nota", nunca otra nota ni un 0.
5. **Rúbrica `judge-v2` intacta.** Termina con "Give your reasoning first, then the score", que Opus 5.5 podría rechazar como `reasoning_extraction` (y ese rechazo no se reintenta en ningún fallback). Pedir una explicación de la respuesta está permitido; se mantiene verbatim y se cuentan los rechazos por categoría en la primera corrida. Solo si aparece `reasoning_extraction` se abre `judge-v3` ("brief justification") **para los dos jueces**, en un ticket aparte.
6. **Modelo: `claude-opus-5-5`.** La rúbrica es matizada (resumen legal contra referencia y key facts); Sonnet 5.5 queda como alternativa si el costo por corrida pesa.
7. **Pointwise, no pairwise.** El objetivo es medir el sesgo del juez actual con la única variable cambiada (el modelo); cambiar la rúbrica a la vez haría `judge_gap` ininterpretable.
8. **Nombres con proveedor.** El adaptador es solo Anthropic; un `SECOND_JUDGE_MODEL` genérico prometería algo que no cumple.
9. **Privacidad.** Los resúmenes y key facts de los casos golden se envían a un segundo proveedor. Son contratos sintéticos, sin datos reales; queda escrito en el README.
10. **Preguntas abiertas diferidas** (ver al final) hasta tener el dato de `judge_gap` de dos corridas.

## Configuración

Las tres variables van a `EvalSettings` (decisión 32: no son configuración de runtime) y a `.env.example`:

- `ANTHROPIC_API_KEY` (`SecretStr | None`), opcional: sin ella el segundo juez no corre.
- `CLAUDE_JUDGE_MODEL`, por defecto `claude-opus-5-5`.
- `CLAUDE_JUDGE_EFFORT`, por defecto `medium`, `Literal["low", "medium", "high", "xhigh", "max"]`.

`requirements.txt` no cambia: `anthropic==2.3.0` (requiere Python ≥ 3.10) solo lo usan las evals y va al grupo `dev`.

## Costo estimado

Medido sobre el golden set: unos 3.800 caracteres por caso entre original y adenda, unos 3.000 tokens de entrada por llamada del juez y entre 1.000 y 2.000 de salida (incluido el thinking, que en Opus 5.5 no se puede apagar). A $4 / $20 por millón de tokens de entrada / salida:

| Corrida | Casos juzgados | Reps | Costo estimado |
|---|---|---|---|
| Calibración (primera, mide variación) | 21 | 2 | ~US$ 1,80 |
| Calibración (normal) | 21 | 1 | ~US$ 0,90 |
| Validación | 14 | 1 | ~US$ 0,60 |

Son estimaciones con `effort: medium`; el costo real se calcula a mano desde los tokens del reporte en la primera corrida autorizada.

## Resultado de la corrida de calibración (2026-10-31)

`PYTHONPATH=src uv run python evals/run_evals.py --split calibration --claude-judge-reps 2 --run-name calibration-t15-reps2`, 25 casos, 22 juzgables, todos los objetivos de §6.6 cumplidos (sin regresiones; la línea base es de `validation`, así que no se compara). Experiment: `runs/503828dc-543d-4309-ab29-be3e8f9fd702`.

| Dato | Valor |
|---|---|
| Llamadas al segundo juez | 44 (22 casos × 2 reps), 44 `scored`, 0 `refused`, 0 `error` |
| Rechazos por categoría | ninguno (tampoco `reasoning_extraction`: la rúbrica `judge-v2` queda intacta) |
| Modelo servido | `claude-opus-5-5` en las 44 llamadas |
| Tokens | 42.944 de entrada / 11.055 de salida (≈ 976 / 251 por llamada) |
| Costo real | US$ 0,39 a $4 / $20 por millón (la estimación de US$ 1,80 suponía 1.000–2.000 tokens de salida por llamada; con `effort: medium` el thinking fue mucho más corto) |
| Latencia | media 3,8 s, máxima 7,8 s |
| `judge_score` (gpt-4.1) | 9,73 (21 de 22 casos con 10) |
| `judge_score_claude` | 9,18 |
| `judge_gap` | 1,00 |
| `judge_claude_spread` | 0,09 (dos casos con una diferencia de 1 punto entre repeticiones; los otros 20, idénticas) |
| `judge_disagreements` | 2 |

Los dos desacuerdos (|diferencia| ≥ 3) van en el mismo sentido: **Claude aplicó la rúbrica y `gpt-4.1` no.**

- `boundary_restated_locacion_degraded`: gpt 10, Claude 4 y 4. El pipeline inventó un cambio en la Cláusula Sexta (`change_precision` 0,667 en el caso). Claude lo llamó "cambio inventado" y bajó al nivel 4, que es lo que dice la rúbrica. El juez de gpt-4.1 **vio** el cambio extra y escribió que "no afecta la nota". Ese es el sesgo que motivó el ticket.
- `boundary_delta_suministro_injection`: gpt 4, Claude 9 y 8. Precisión, recall y key facts del caso en 1,0. gpt-4.1 penalizó como "key fact faltante" el flete a cargo del PROVEEDOR, que no está en `key_facts` y es un término restated; la rúbrica dice explícitamente que eso es contexto. Claude lo trató como contexto.

Lectura: el gap de 1 punto es sobre todo Claude siendo un punto más exigente (9 contra 10 en nueve casos), más dos errores de criterio de gpt-4.1. gpt-4.1 casi no discrimina (21 de 22 en 10); Claude distribuye entre 8 y 10 y detectó el único resumen con un cambio inventado. Con una sola corrida no se cambia nada: la decisión 10 del grilling difiere las preguntas abiertas hasta tener también el dato de `validation`.

## Cobertura de la consigna

- Requisitos: ninguno nuevo
- Entregables: E07 (sección "Testing y evals" del README: párrafo sobre el segundo juez, cómo activarlo y la nota de privacidad; la tabla renderizada desde `outputs/baseline.json` no cambia)

## Secciones de plan.md que gobiernan el trabajo

- §6.6 (métricas y objetivos)
- §6.7 (agregación y comparación contra la línea base)
- §5.1 (configuración)

Decisiones del registro §8.4: 32 (`JUDGE_MODEL` vive en `EvalSettings`), 59, 88 (gate por regresión contra baseline), 101.

Cambios a `plan.md` al cerrar el ticket (discutidos en el grilling; se escriben recién al cerrar):

- §5.1: las tres variables nuevas.
- §6.6: "segundo juez Claude (`CLAUDE_JUDGE_MODEL`), informativo, sin objetivo".
- §6.7: las métricas nuevas y que no gatean (`Metric.gated`).
- §8.4, decisiones nuevas:
  - 105: segundo juez de otra familia de modelos, informativo, sin objetivo ni gate.
  - 106: sin fallback; rechazo = sin nota con categoría; aserción sobre el modelo servido.
  - 107: sin tracing de jueces en T15; tokens y latencia por caso en el reporte; trazar ambos jueces es un ticket aparte.

## Criterios de aceptación

- [x] `ClaudeSummaryJudge` cumple `SecondJudge` y usa `JUDGE_SYSTEM_PROMPT` y `render_judge_input` sin cambios en la rúbrica `judge-v2`.
- [x] `render_judge_input` existe en `evals/judge.py` y `build_judge_messages` la usa; el mensaje `user` de los dos jueces es byte a byte el mismo.
- [x] Sin `ANTHROPIC_API_KEY`, `run_evals.py` se comporta exactamente como hoy y el reporte y la metadata del Experiment dicen `claude_judge: "not_run"`.
- [x] El reporte suma `judge_score_claude`, `judge_gap`, `judge_claude_scored` y `judge_disagreements`; `judge_claude_spread` solo tiene valor con `--claude-judge-reps` > 1 (N/A en caso contrario).
- [x] Un rechazo de Claude deja `status="refused"` con `refusal_category`; un `max_tokens`, una excepción o una nota fuera de rango dejan `status="error"`. Ningún estado produce un 0.
- [x] Un `response.model` que no empieza con `CLAUDE_JUDGE_MODEL` deja `status="error"`.
- [x] Cada caso con segundo juez registra `model`, `input_tokens`, `output_tokens` y `latency_s`.
- [x] Las métricas nuevas tienen `gated=False`, no tienen objetivo en §6.6 y `compare_with_baseline` las ignora; `outputs/baseline.json` no cambia y la tabla del README sigue renderizada desde él.
- [x] Con `--claude-judge-reps 2`, la nota del caso es la de la primera repetición.
- [x] `tests/test_layering.py`: `anthropic` está en `INFRASTRUCTURE` y solo `evals/claude_judge.py` lo importa.
- [x] README "Testing y evals" documenta el segundo juez y la nota de privacidad.
- [x] Una corrida de `calibration` autorizada por el autor, con `--claude-judge-reps 2`, registra en este ticket el costo real (desde tokens), `judge_gap`, `judge_claude_spread` y los rechazos por categoría.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):**

- En `tests/evals/test_judge.py`: `render_judge_input` devuelve los tres bloques en orden y `build_judge_messages` la usa (el contenido del `HumanMessage` es igual al string).
- En `tests/evals/test_claude_judge.py`, con un cliente doble que registra las llamadas:
  - el request usa `CLAUDE_JUDGE_MODEL`, `output_config.effort`, `max_tokens=8000`, `output_format=JudgeVerdict`, y no envía `temperature` ni `thinking`;
  - `system` es `JUDGE_SYSTEM_PROMPT` y el único mensaje `user` es `render_judge_input(summary, case)`;
  - una respuesta válida se convierte en `JudgeVerdict` y el resultado registra modelo, tokens y latencia;
  - `stop_reason == "refusal"` → `status="refused"` con la categoría; `stop_reason == "max_tokens"` → `status="error"`; nota fuera de rango → `status="error"`;
  - un `response.model` distinto del configurado → `status="error"`.
- En `tests/evals/test_case_runner.py`:
  - sin segundo juez, `CaseRun.second_judge` es `None`;
  - con un juez doble, el segundo juez corre solo sobre los casos que el primero juzga, y después de él;
  - con reps = 2, `score` es el de la primera repetición y las dos quedan disponibles para `judge_claude_spread`.
- En `tests/evals/test_run_evals.py`:
  - sin key no se construye el segundo juez y la metadata dice `not_run`;
  - con key, la metadata lleva `claude_judge_model` y `claude_judge_effort`;
  - `--claude-judge-reps` llega a `CaseRunner`.
- En `tests/evals/test_metrics.py` y `tests/evals/test_eval_report.py`:
  - `judge_gap` y `judge_claude_scored` se calculan solo sobre los casos que corresponden; `judge_claude_spread` solo con reps > 1;
  - `judge_disagreements` lista los casos con |diferencia| ≥ 3 con los dos razonamientos;
  - `Metric.gated=False` excluye la métrica de `compare_with_baseline`, incluida la caída en `evaluable`.
- En `tests/evals/test_settings.py`: las tres variables nuevas, sus defaults y el `Literal` de `effort`.
- En `tests/test_layering.py`: `anthropic` en `INFRASTRUCTURE`; solo `evals/claude_judge.py` lo importa.

**GREEN:** `render_judge_input`, el adaptador, `SecondJudgeResult`, la configuración, el cableado en `case_runner.py` y `run_evals.py`, `Metric.gated`, las métricas y el reporte.

## Tickets derivados (no se hacen acá)

- Trazar los dos jueces en Langfuse (span por llamada con modelo, `effort`, tokens y latencia).
- `judge-v3` ("brief justification") para los dos jueces, solo si la primera corrida registra rechazos `reasoning_extraction`.

## Preguntas abiertas (diferidas hasta tener `judge_gap` de calibración y validación)

- Si el juez de Claude debería reemplazar al de `gpt-4.1` en lugar de sumarse.
- Si conviene que el juez vea también los textos de los contratos y no solo el resumen de referencia, para detectar errores que la referencia no cubre. Eso sube el costo y cambia la rúbrica.
- Si más adelante tiene sentido una etapa de control de calidad con Claude en producción (la opción A que se descartó por ahora), y con qué evidencia se justificaría.
