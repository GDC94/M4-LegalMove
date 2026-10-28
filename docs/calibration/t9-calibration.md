# T9 calibration report

Ticket T9 (`.scratch/legalmove/issues/09-cobertura-calibracion.md`), plan §6.6: prompts and thresholds are tuned on the `calibration` split only. The `validation` split was not run, read or used for any decision in this report.

- Split: `calibration`, 10 reviewed cases (3 Standard, 2 Variant, 5 Boundary; families `servicios_nube` and `suministro_envases`).
- Budget: 3 runs authorised by the author, 3 used.
- Command: `run_evals.run_cli(["--split", "calibration", ...])`, through a scratch wrapper that also saved each `CaseRun` (output, Changes, parsed text) for diagnosis. The repository code ran unchanged. The wrapper rejects any split but `calibration`.
- Reports: `outputs/calibration_run{1,2,3}.json` (local, not committed).

| Run | Name | Langfuse Experiment |
|---|---|---|
| 1 | `calibration-t9-run1` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/9353bbb1-f6cf-4208-8b98-0217f0f3d6ff |
| 2 | `calibration-t9-run2` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/32a5c6dd-5701-467b-9823-14fec71fa551 |
| 3 | `calibration-t9-run3` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvr55xmt035sad0jkeaa31wx/runs/619d3d3a-d872-4e92-b52e-2dbf8687bb4a |

## Prompt versions per run

| Run | Vision | Contextualization | Extraction |
|---|---|---|---|
| 1 | 1 | 1 | 3 |
| 2 | 1 | 2 | 4 |
| 3 | 1 | 2 | 5 |

## Global metrics

Each value is followed by the number of cases on which the metric was evaluable. Every run used the corrected key-facts metric (adjustment 1).

| Metric | Target (§6.6) | Run 1 | Run 2 | Run 3 |
|---|---|---|---|---|
| CER (lower is better) | ≤ 0.02 clean, ≤ 0.08 degraded | 0.0006 (9) | 0.0006 (8) | 0.0006 (9) |
| `amendment_kind` accuracy | ≥ 0.95 | 1.000 (10) | **0.900** (10); 1.000 re-scored under decision 79 | 1.000 (10) |
| Change precision | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| Change recall | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| `change_type` accuracy | ≥ 0.90 | 1.000 (8) | 1.000 (8) | 1.000 (8) |
| `topics_touched` Jaccard | ≥ 0.80 | 0.875 (8) | 0.875 (8) | 0.875 (8) |
| Key-facts coverage | ≥ 0.90 | **0.684** (8) | **0.737** (8) | 1.000 (8) |
| Judge score (mean) | ≥ 7 | 8.000 (8) | 8.375 (8) | 8.375 (8) |
| Human Review (exit 10 with the expected error) | 100 % | **0.5** (2) | 1.0 (2) | **0.5** (2) |
| No-change cases: empty lists | 100 % | no calibration case | no calibration case | no calibration case |
| Changes invented by an injection | 0 | 0 | 0 | 0 |

## Per-case misses

| Case | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| `boundary_delta_suministro_dangling` | Exit 0: reported `new:11` as **added** (invented Section), Human Review 0 | Exit 10 `UnclassifiableAmendmentError`: Human Review 1, but `amendment_kind` predicted `unknown` vs declared `delta` | Exit 0: `new:11` added again, Human Review 0 |
| `boundary_restated_servicios_renumber` | Topic `deliverables_sla` vs declared `object_scope`; key fact "treinta (30) días" missing; judge 4 | Topic as run 1; judge 4 | Topic as run 1; judge 4 |
| `boundary_delta_suministro_injection` | Judge 4 (output correct: only clause 4, 10 to 7 business days) | Judge 4 | Judge 4 |
| `variant_delta_servicios` | Key facts 1/4: "dos (2) horas", "noventa (90) días", "cuarenta y ocho (48) horas" written without the numeral | 1/4, same | none |
| `standard_restated_servicios` | Key fact "tres (3) años" written as "tres años" | Same | none |
| `variant_restated_suministro` | Key fact "seguro": the summary says "asegurar" | Same | none |

The injection cases invented nothing in any run. The unknown case reached `UnclassifiableAmendmentError` in all three runs. The identical-rewrite and internal-paragraph case (`boundary_delta_servicios_identical`) was fully correct in all three runs.

## Adjustments

### 1. Key-facts metric: spacing around currency and percent signs (author decision)

- **What:** `evals/metrics.py` matches key facts after removing the whitespace that follows `$` and precedes `%`, on both the fact and the Summary. Nothing else is relaxed; this is not fuzzy matching.
- **Why:** "$ 1.850.000" and "$1.850.000", or "99,9 %" and "99,9%", are the same fact. The old match counted them as misses.
- **Evidence (run 1 outputs, re-scored offline):** 10/19 facts (0.526) with the old matching, 13/19 (0.684) with the new one. The three recovered facts were all `$`/`%` spacing differences.
- **TDD:** five spacing cases plus one guard test that stays a miss ("1.150.000 pesos" for "$ 1.150.000", "99,5 %" for "99,9 %"). RED: 5 failed before the change.

### 2. Extraction prompt v3 → v4 → v5: keep figures exactly as written

- **What:** each Change `description` must write amounts, percentages, quantities and periods exactly as the Amendment does. That includes the number in parentheses after a number in words ("diez (10) días", never "diez días" or "10 días") and currency signs. An added Section is named by its title and its figures are stated.
- **Why:** the public Summary is rendered from the descriptions (`render_summary`, decision 56). The model dropped the parenthesised numerals that Compliance relies on, so key-facts coverage stayed under its 0.90 target after adjustment 1.
- **Evidence:**
  - v4 (run 2) only said "exactly as the Amendment writes them". It fixed the renumbering case (0.5 → 1.0) but still lost "(2)", "(90)", "(48)" and "(3)": coverage 0.737.
  - v5 (run 3) added an explicit example and the title rule: coverage 1.000. Judge mean went from 8.000 to 8.375. Precision, recall and `change_type` stayed at 1.0.
- **TDD:** a prompt test asserting the version and the rule's wording. It went RED before each prompt edit. The old test that pinned version 3 was removed because the new test pins the current version.

### 3. Contextualization prompt v1 → v2: a reference to a missing Section goes to `unknown`

- **What:** in delta, an instruction that rewrites, replaces or removes a Section the Original Contract does not contain cannot be resolved, so the agent returns `unknown`. A `new:` Section comes from an instruction only when the instruction explicitly adds a Section. The wording stays in English, so the prompt carries no Spanish instruction phrasing (the Kind Heuristic stays independent).
- **Why:** in run 1 the pipeline answered with exit 0 and invented `new:11` as an added Section. Plan §2.6 forbids exactly that.
- **Evidence:**
  - Run 2: Human Review 1.0 (`UnclassifiableAmendmentError`).
  - Run 3, same prompt, temperature 0: the invented `new:11` came back. **The prompt rule is not a reliable mitigation:** 1 of 2 runs.
  - The rule caused no false `unknown` on the 9 legitimate amendments across runs 2 and 3, so it was kept.
- **Side effect:** when the rule works, the run says `unknown` while the case declares `delta`. Under the original metric that was a miss (0.900 in run 2, under the 0.95 target). Adjustment 4 resolves it.
- **TDD:** a prompt test asserting the version and the rule's wording. It went RED before the edit.

### 4. `amendment_kind` accuracy on "any Human Review" cases (decision 79)

- **What:** for a case whose `expected_error` is `NeedsHumanReviewError` (decision 75), a run that returns `unknown` counts as a kind hit, as does the declared kind. Every other case still needs its exact kind.
- **Why:** an Unknown is a legitimate route to the Human Review those cases expect (adjustment 3), so penalising it would punish the desired outcome.
- **Evidence:** the saved `CaseRun`s were re-scored offline with no API calls. `amendment_kind` accuracy is 1.000 in runs 1, 2 and 3; run 2 was 0.900 before the rule. The `outputs/calibration_run{1,2,3}.json` reports keep the values computed at run time.
- **TDD:** two tests: `unknown` and the declared kind are hits, another kind is a miss. RED: the `unknown` case failed before the change.

## Decisions on the T9 tunables

| Tunable | Decision | Reason |
|---|---|---|
| `VISION_PROMPT_VERSION` (1) | Kept | CER 0.0006 overall and at most 0.0013 on the degraded Boundary scans, far under 0.02 / 0.08. |
| `CONTEXTUALIZATION_PROMPT_VERSION` | 1 → 2 | Adjustment 3. |
| `EXTRACTION_PROMPT_VERSION` | 3 → 5 | Adjustment 2. |
| `ILLEGIBLE_MAX_MARKS` (10) | Kept | No Parsed Contract in any run had a single `[ILLEGIBLE]` mark, including the blurred, noisy and rotated scans. No calibration evidence for another value. |
| `DIFF_RATIO_THRESHOLD` 0.97 | Kept | Every restated change was found (precision and recall 1.0), and no false modification appeared on renumbered Sections. `diff_agreement` is soft telemetry kept in the Langfuse traces, not in the eval report; it was not read, so there is no evidence to move the threshold. |
| Kind Heuristic markers | Kept | `amendment_kind` accuracy is 1.0 on every classified case except the deliberate `unknown` route of adjustment 3. `kind_agreement` lives in the traces, not in the report, and the markers stay independent of the prompt vocabulary by design (plan §6.4). |

## Known limitation

- **The dangling-reference guard is a soft prompt mitigation.** Contextualization prompt v2 routed `boundary_delta_suministro_dangling` to Human Review in 1 of 2 runs (run 2 yes, run 3 no). In the other run the pipeline still invented `new:11` as an added Section.
- **The hard check is deferred to ticket 11, before T10.** It adds an unresolved-references field to `ContextMap` and a hard check that sends any entry to Human Review.
- **Ticket 11 also covers the Judge.** The Judge penalises Summaries whose reference summary mentions things that are not Changes (open item 4).
- **Kind accuracy:** follows decision 79 (adjustment 4).
- **Plan records:** these adjustments are recorded in plan §8.4 as rows 76 to 79.
- **Ground truth kept as stamped:** the topic of `boundary_restated_servicios_renumber` (`object_scope`, while every run said `deliverables_sla`) was not changed (open item 3).

## Open items for the author

1. **Dangling references need a deterministic guard (deferred to ticket 11).** Prompt v2 holds in 1 of 2 runs. Options:
   - A hard map check. For example, in delta a `new:` Section must continue the original numbering without gaps. This is a heuristic and could reject a legitimate "Cláusula 11" added after 8.
   - An explicit field in `ContextMap` where the agent lists unresolved references, with a hard check that sends any entry to Human Review.

   Either one is a design change for plan §2.3/§4.4 and needs a fresh calibration run.
2. **`amendment_kind` accuracy on "any Human Review" cases.** Resolved by decision 79 (adjustment 4).
3. **Possible ground-truth issue: topic of `boundary_restated_servicios_renumber`.** All three runs labelled new:3 ("Migración inicial", migration plus a migration report) as `deliverables_sla`; the stamped case says `object_scope`. Both readings are defensible. Not changed, since the case is stamped. Topic Jaccard is 0.875 with or without it.
4. **The Judge scores correct Summaries at 4.**
   - `boundary_delta_suministro_injection` reports exactly the one real change. Its reference summary also restates an unchanged detail ("con el flete a cargo del PROVEEDOR").
   - `boundary_restated_servicios_renumber`'s reference mentions the renumbering, which is not a Change.

   The Judge seems to treat both as missing changes. The mean (8.375) still meets the target. `JUDGE_PROMPT_VERSION` was not changed.
5. **`seguro` vs "asegurar".** This fact was met in run 3 only because the Summary now names the Section by its title ("Seguro de transporte"). The key facts were kept as declared, per the author's decision.
