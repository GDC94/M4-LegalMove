# T11 calibration report

Ticket T11 (`.scratch/legalmove/issues/11-control-duro-referencias.md`) adds a hard check for unresolved references. The `ContextMap` declares each row's `instruction`, and a `delta` `new:` row marked `rewrites` or `removes` ends in `UnresolvedReferenceError` (decisions 80–87). The ticket also fixes the Judge (`judge-v2`). Everything was tuned on the `calibration` split only. The `validation` split was not run, read or used for any decision.

- Split: `calibration`, 10 reviewed cases. It has one dangling case, `boundary_delta_suministro_dangling`.
- Budget authorised by the author: 2 full runs plus 5 single-case repetitions, and one extra full run only if an adjustment was needed. Used: 2 full runs and 5 repetitions. **No adjustment was needed, so the third run was not used.**
- Command: `run_evals.run_cli(["--split", "calibration", ...])`, through a scratch wrapper. The wrapper also saved each `CaseRun` and every agent attempt: issue codes, section IDs, `instruction` values and kinds, never document text. The repository code ran unchanged, and the wrapper rejects any split but `calibration`.
- Prompt versions: vision 1, contextualization 3, extraction 5, Judge `judge-v2`.
- Reports: `outputs/calibration_t11_run{1,2}.json` and `outputs/eval_report-calibration-<timestamp>-boundary_delta_suministro_dangling.json`. They are local and not committed.

| Run | Name | Langfuse Experiment |
|---|---|---|
| 1 | `calibration-t11-run1` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/501eb93d-51a5-43ed-a648-74289dbe5856 |
| 2 | `calibration-t11-run2` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/0dc497f0-50e3-4f2d-aa7f-1c607da8b327 |

## Global metrics

Each value is followed by the number of cases on which the metric was evaluable. T9 run 3 (`outputs/calibration_run3.json`) is the baseline.

| Metric | Target (§6.6) | T9 run 3 | T11 run 1 | T11 run 2 |
|---|---|---|---|---|
| CER (lower is better) | ≤ 0.02 clean, ≤ 0.08 degraded | 0.0006 (9) | 0.0006 (8) | 0.0006 (8) |
| `amendment_kind` accuracy | ≥ 0.95 | 1.000 (10) | 1.000 (10) | 1.000 (10) |
| Change precision | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| Change recall | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| `change_type` accuracy | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| `topics_touched` Jaccard | ≥ 0.80 | 0.875 (8) | 0.875 (8) | 0.875 (8) |
| Key-facts coverage | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| Judge score (mean) | ≥ 7 | 8.375 (8), `judge-v1` | 10.000 (8), `judge-v2` | 9.625 (8), `judge-v2` |
| Human Review | 100 % | **0.5** (2) | 1.0 (2) | 1.0 (2) |

**The Judge score is not comparable with T9.** The Judge prompt changed from `judge-v1` to `judge-v2`, so the T9 and T11 means come from different graders. `compare_with_baseline` reports `judge_prompt_version_changed: true` and leaves `judge_score` out of the deltas.

**The baseline check flags `cer` and `cer.evaluable` in both runs. This is not a pipeline regression.**
- In T9 run 3 the dangling case ended with exit 0, so its `CaseRun` kept both parsed texts and counted towards CER.
- It now stops in contextualization, and a failed `CaseRun` carries no parsed text. CER therefore pools 8 cases instead of 9, and the pooled value moves by +0.00003 from the change in case mix.
- Every other delta is 0, except Human Review, which goes from 0.5 to 1.0.
- `amendment_kind` on the dangling case is now the declared `delta`. It is exact, so the Unknown allowance of decision 79 was not needed.

## Dangling case: 2 full runs + 5 repetitions

| Run | Exit | Error | Issue | Row `new:11` |
|---|---|---|---|---|
| Full run 1 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `instruction = rewrites`, no `original_ref` |
| Full run 2 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |
| Repetition 1 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |
| Repetition 2 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |
| Repetition 3 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |
| Repetition 4 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |
| Repetition 5 | 10 | `UnresolvedReferenceError` | `unresolved_reference` at `sections[8]` | `rewrites` |

All 7 runs ended in Human Review on the first map, with no ordinary map issues, no retry and no extraction call. Each time the model mapped the real target as `2` (`rewrites`) and the reference to the missing clause as `new:11` (`rewrites`). Prompt v2 in T9 held in 1 of 2 runs; the hard check held in 7 of 7.

The repetitions ran with `--case`. Each one wrote its own report and was not compared with the baseline (`{"compared": false, "reason": "case filter"}`).

Repetition experiments:
1. https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/65ad38f0-c4c2-4503-b93f-99c969871ff7
2. https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/537d1462-966e-4e7e-be85-0d69cab2b776
3. https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/6a3bf8a1-e3e0-42a4-a6bb-7f6d6a2be667
4. https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/242ef74b-6fc9-4163-a35f-4e270b1dce0c
5. https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/f7afd237-25a0-45b4-8320-98727bada8a1

## False-positive check

Across both full runs, the 8 legitimate cases and the `unknown` case showed none of these:
- `UnresolvedReferenceError`;
- an `instruction_mismatch` issue;
- any other map or extraction retry feedback (`context_retries` 0, `extraction_retries` 0 on every case).

The `instruction` values the model returned were consistent with the cases:
- **Restated** maps: `none` on every row, `new:` rows included.
- **Delta** maps: `rewrites` on modified Sections, `removes` on removed ones, `none` on rows no instruction names. The one legitimate addition (`variant_delta_servicios`, `new:8`) was `adds`.
- **`boundary_delta_servicios_identical`:** both instructions were `rewrites` and the run still reported the expected result. Instruction kind is structure, not a change verdict.
- **`new_section_gap`:** recorded in the traces, not in the report. The maps saved by the wrapper give 0 for `variant_delta_servicios` (`new:8` after an original that ends at 7). It is N/A on every other delta case, which has no numbered `new:` row.

## Judge reasoning (`judge-v2`)

| Case | T9 (`judge-v1`) | T11 run 1 | T11 run 2 |
|---|---|---|---|
| `boundary_delta_suministro_injection` | 4, 4, 4 | 10 | 7 |
| `boundary_restated_servicios_renumber` | 4, 4, 4 | 10 | 10 |

- **Renumber, both runs.** The reasoning says the candidate does not mention the renumbering, "but this is context and not a required key fact". The v2 rule is the cause of the fix.
- **Injection, run 1.** The reasoning says the freight detail "is not listed as a key fact and is not required for full credit".
- **Injection, run 2.** The Judge still called the missing freight detail ("flete a cargo del PROVEEDOR", unchanged by the amendment) and the start of the term a "minor imprecision", and gave 7. The v2 rule reduced the effect but did not remove it in every run. The criterion (≥ 7) holds at the threshold.

## Adjustments

None. Every acceptance criterion was met, so the authorised third full run was not used and no prompt, threshold or code changed after run 1.

## Acceptance (T11 ticket)

| Criterion | Status | Evidence |
|---|---|---|
| `SectionMapping.instruction` in the strict schema, with its rules in `checks.py` | Met | Decisions 80–82; unit tests |
| An unresolved reference ends in `UnresolvedReferenceError` without consuming the retry | Met | Unit tests; 7 of 7 paid runs stopped on the first map |
| Two full calibration runs without regression, Human Review at 100 % | Met | Human Review 1.0 (2) in both runs; precision, recall, `change_type`, topics and key facts unchanged from T9. The only flagged items are `cer` and `cer.evaluable`, explained above |
| ≥ 5 repetitions of `boundary_delta_suministro_dangling`, all in Human Review | Met | 5 of 5 (7 of 7 with the full runs) |
| Judge ≥ 7 on `boundary_delta_suministro_injection` and `boundary_restated_servicios_renumber`, no sealed case touched | Met, injection at the threshold | Renumber 10 and 10; injection 10 and 7. No golden case was edited |
| Each adjustment has a §8.4 row or a note here | Met | Decisions 80–87 in `plan.md`; this report |

## Known limitations and follow-ups

1. **The Judge is still not fully stable on the injection case** (10, then 7). If a future run drops it below 7, the next step is a `judge-v3` rule that names restated unchanged details explicitly. That needs its own calibration run.
2. **A failed `CaseRun` has no parsed text**, so cases that stop before extraction drop out of CER. A follow-up could keep the parsed texts on failures; it would change the CER denominator, so it needs a plan row.
3. **Some Langfuse span batches failed to export (OTLP timeout) during both full runs.** As decision 71 records, the SDK logs these errors without raising them. Reports and Experiments were written; some traces may be incomplete.
4. **Ground truth unchanged:** the topic of `boundary_restated_servicios_renumber` is still `object_scope`, while every run says `deliverables_sla` (T9 open item 3).
