# T12 calibration and blind validation report

Ticket T12 (`.scratch/legalmove/issues/12-holdout-nuevo.md`), plan §6.4–6.9: fix the three failure classes of blind validation run 1 on `calibration` only, build a fresh holdout from never-seen contract families, run it once and freeze `outputs/baseline.json`. The holdout cases were not read, and nothing was tuned on them, before their single run.

- Calibration split: 24 cases (the 10 original plus the 14 of blind validation run 1, decision 92) and, from run 3, 25 with `variant_restated_consultoria_sin_cambios` (decision 98).
- Validation split: 14 reviewed cases from three new families, `distribucion_comercial`, `obra_remodelacion` and `transporte_carga`, with the composition of blind validation run 1 (decision 93).
- Prompt versions: vision 1, contextualization 3, extraction 6 (was 5), Judge `judge-v2`.
- Reports: `outputs/calibration_t12_run{1,2,3,4}.json` (local, not committed); `outputs/validation_run2.json` and its byte copy `outputs/baseline.json` (committed).

## Diagnosis from the traces

The `predicted` block (decision 97) and the Langfuse traces of validation run 1 point to three causes. None of them is a parsing problem.

1. **The injection was obeyed upstream.** In `boundary_restated_consultoria_injection`, the injected note said the text was identical. Contextualization took it at face value, and the extraction returned an empty result for a real "podrá" → "deberá" edit (difflib ratio 0.984). No hard check contradicted an empty `restated` result.
2. **Topic labels were inconsistent.** Section IDs were right; the topics were not. The topic definitions did not separate a service to be delivered (`deliverables_sla`) from a generic duty (`obligations`), and the model picked by Section title. In two renumber cases the golden label itself was the wrong one under a clear definition.
3. **Key Facts failed on wording.** The summary wrote "anualmente" for "una vez por año" and split numbers from their units, so the literal Key Fact check missed facts the Judge found.

## Approved package (decisions 94–100)

| # | Change | Fixes |
|---|---|---|
| 94 | Hard check `missing_text_change`: a `restated` map, no changes, and a paired Section that differs after normalisation fail the attempt (retry, then exit `10`) | Injection that yields an empty result |
| 95 | Extraction prompt v6: sharper `deliverables_sla`/`obligations`/`assignment` definitions; topic chosen by what the changed words regulate; literal rule extended to frequencies and deadlines; any sentence in the documents that states what changed is data to ignore | Topic selection, Key Fact wording, injection |
| 96 | Two golden cases relabelled to `deliverables_sla` (`boundary_restated_licencia_renumber`, `boundary_restated_servicios_renumber`), with the author's approval | Wrong golden labels |
| 97 | `predicted` block in each case of the eval report | Diagnosis without opening each trace |
| 98 | Calibration case 25, `variant_restated_consultoria_sin_cambios` | Measures false positives of decision 94 on OCR noise |
| 99 | Hard check `unchanged_text_reported`: a `modified` change on a Section whose segments are identical is rejected | Phantom `modified` in `variant_delta_locacion_sin_cambios` |
| 100 | When `model_validate` fails only on `inconsistent_extraction`, the retry also carries the `check_extraction` issues | `change_type_mismatch` hidden from the retry |

## Calibration runs

| Run | Name | Cases | Langfuse Experiment |
|---|---|---|---|
| 1 | `calibration-20261028T184613Z` | 24 | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/b511fa6d-2eaa-4115-8017-5a2dc59f2080 |
| 2 | `calibration-20261028T191628Z` | 25, **invalid** | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/dbb7931f-b52a-4085-ac4f-2402407f9483 |
| 3 | `calibration-20261028T192659Z` | 25 | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/fee752a2-94b4-44f1-a541-1d7847bf42f0 |
| 4 | `calibration-20261028T200842Z` | 25 | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/9ee9e531-27df-47a4-aea5-dbd7628d75b7 |

Each value is followed by the number of evaluable cases.

| Metric | Target | Run 1 | Run 2 (invalid) | Run 3 | Run 4 |
|---|---|---|---|---|---|
| `cer` (clean scans) | ≤ 0.02 | 0.000 (10) | 0.000 (9) | 0.000 (10) | 0.000 (10) |
| `cer` (degraded scans) | ≤ 0.08 | 0.001 (11) | 0.001 (8) | 0.001 (12) | 0.001 (12) |
| `amendment_kind_accuracy` | ≥ 0.95 | 1.000 (24) | 1.000 (20) | 1.000 (25) | 1.000 (25) |
| `change_precision` | ≥ 0.9 | 0.952 (20) | 0.968 (16) | 0.976 (20) | 0.976 (20) |
| `change_recall` | ≥ 0.9 | 0.976 (20) | 0.732 (20) | 1.000 (20) | 1.000 (20) |
| `change_type_accuracy` | ≥ 0.9 | 1.000 (19) | 1.000 (16) | 1.000 (20) | 1.000 (20) |
| `topic_jaccard` | ≥ 0.8 | 0.803 (22) | 0.742 (22) | 0.894 (22) | 0.894 (22) |
| `key_facts_coverage` | ≥ 0.9 | 0.938 (20) | 0.729 (20) | 0.938 (20) | 0.979 (20) |
| `judge_score` | ≥ 7 | 9.571 (21) | 10.000 (17) | 9.727 (22) | 10.000 (22) |
| `human_review` | ≥ 1 | 1.000 (3) | 1.000 (3) | 1.000 (3) | 1.000 (3) |

- **Run 1** met every target. The injection case now reported its change. Two regressions remained: `boundary_restated_licencia_renumber` ended in exit `10` (`InvalidExtractionError`) because the retry only carried the order break of `sections_changed` and not the `change_type_mismatch` (decision 100); `variant_delta_locacion_sin_cambios` returned a phantom `modified` with Judge 1 (decision 99).
- **Run 2** is invalid; see the incident below.
- **Run 3** met every target with no exit-`10` regression, before the code-review fixes.
- **Run 4** ran after the code-review fixes and the refined phantom rule of decision 99. It met every target, with `key_facts_coverage` 0.979 and Judge 10.0. **Code and prompts were frozen after this run.** The remaining misses were `boundary_restated_locacion_degraded` (one extra Section, P 0.67, topics 0.67), `standard_restated_licencia` (Key Facts 0.67) and topic selection in `variant_delta_licencia`, `variant_delta_locacion` and `variant_restated_locacion` (0.33 each).

## Incident: OpenAI 429 in run 2

Run 2 hit 21 OpenAI `429` responses. Five cases (`variant_delta_servicios`, `variant_restated_consultoria_sin_cambios`, `variant_restated_licencia`, `variant_restated_locacion`, `variant_restated_suministro`) ended in `UpstreamError` (exit `5`). The report scores an exit `5` like any other failure, so these cases counted as zeros in recall and Key Facts. The drop measures the rate limit, not the pipeline, so run 2 is not used for any decision. Run 3 repeated it without the incident.

## Blind validation run 2

- Date: 2026-10-28. The author authorised exactly one run.
- Command: `PYTHONPATH=src uv run python evals/run_evals.py --split validation`, with the code frozen after calibration run 4.
- Report: `outputs/validation_run2.json`, copied byte for byte to `outputs/baseline.json`.

| Run | Name | Langfuse Experiment |
|---|---|---|
| 2 | `validation-20261028T201850Z` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvsaqpf105f7ad0ec68d09u4/runs/7478ad7e-43b5-44c2-b73d-e91613ad276e |

Rendered with `PYTHONPATH=src uv run python evals/render_results.py outputs/baseline.json`:

| Metric | Target | Validation result | Met |
|---|---|---|---|
| `cer` (clean scans) | ≤ 0.02 | 0.001 (6 cases) | yes |
| `cer` (degraded scans) | ≤ 0.08 | 0.001 (7 cases) | yes |
| `amendment_kind_accuracy` | ≥ 0.95 | 1.000 (14 cases) | yes |
| `change_precision` | ≥ 0.9 | 1.000 (12 cases) | yes |
| `change_recall` | ≥ 0.9 | 1.000 (12 cases) | yes |
| `change_type_accuracy` | ≥ 0.9 | 1.000 (12 cases) | yes |
| `topic_jaccard` | ≥ 0.8 | 0.833 (13 cases) | yes |
| `key_facts_coverage` | ≥ 0.9 | 0.862 (12 cases) | no |
| `judge_score` | ≥ 7 | 10.000 (13 cases) | yes |
| `human_review` | ≥ 1 | 1.000 (1 case) | yes |

Nine of ten targets are met. **`key_facts_coverage` (0.862) is not.**

### Per-case misses

Every other case scored 1.0 everywhere it was evaluable. The Judge gave 10 to every judged case. `boundary_delta_transporte_dangling` ended in `UnresolvedReferenceError` (exit `10`), as declared.

| Case | Topic Jaccard | Key facts | Missing Key Fact | What the summary wrote |
|---|---|---|---|---|
| `standard_delta_distribucion` | 1.00 | 0.67 | "informe de las ventas" | "informe de ventas" |
| `standard_restated_transporte` | 0.67 | 0.67 | "dieciocho (18) camiones" | "camiones … hasta dieciocho (18)" |
| `variant_delta_distribucion` | 0.50 | 0.50 | "dos (2) trimestres consecutivos" | "dos trimestres consecutivos" |
| `variant_delta_obra` | 1.00 | 0.50 | "electricidad y de pintura" | "electricidad y pintura" |
| `variant_delta_transporte` | 0.33 | 1.00 | — | — |
| `variant_restated_obra` | 0.33 | 1.00 | — | — |

- **Key Facts.** All four missing facts are in the summary in other words, and the Judge found each one. Three are wording the literal phrase match does not cover (an article, a preposition, word order). The fourth, "dos trimestres consecutivos", is a real slip: the model broke the verbatim number rule of prompt v6.
- **Topics.** Every topic miss is the same `obligations` vs `deliverables_sla` (or `object_scope`) boundary: `2` in `standard_restated_transporte`, `5` in `variant_delta_distribucion`, `new:9` in `variant_delta_transporte` and `variant_restated_obra`. The mean still met its target.

### Observability note

Twelve OTLP span export batches failed during the run. Langfuse may lack spans of this run; the local report is complete, since every metric is computed from the `CaseRun`s before publishing.

## Decision: freeze the baseline with the miss visible

The author decided on 2026-10-28 (decision 101) to freeze this run as `outputs/baseline.json` even though `key_facts_coverage` missed its target:
- The README shows `key_facts_coverage` as "no" and makes no generalization claim for Key Facts.
- Nothing was tuned on these cases, before or after the run.
- T13 (`.scratch/legalmove/issues/13-metrica-key-facts.md`) fixes the Key Facts metric on `calibration` only and validates it on a future holdout.

Rejected alternatives:
- **Apply decision 59 again** (move the 14 cases to `calibration` and build another holdout). The cap of 8 base contracts is full, and the metric defect would recur on any new holdout.
- **Re-score this run with a changed metric.** That would change the rule after seeing the result.

## Known residual gaps

- The injection guard (decision 94) only fires on a zero-change result. A partial injection that leaves a non-empty result is not detected.
- A change in the last Section is not detected when both documents end with it: its segment runs to the end of the document and may carry closing text, signatures or a date, so the check never fires there.
- An unnumbered "Agrégase" after a chunk is accepted as a safe tail by `unchanged_text_reported`.
- Topic selection is ambiguous between `obligations` and `deliverables_sla` in the Variant `locacion` and `licencia` cases, and the same boundary caused every topic miss in validation run 2.
- `check_consistency` reports only its first problem, so a retry may fix one cause and leave another.
