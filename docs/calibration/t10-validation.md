# T10 blind validation report

Ticket T10 (`.scratch/legalmove/issues/10-validacion-ciega.md`), plan §6.6–6.7: the `validation` split runs once, blind, after prompts and thresholds were frozen on `calibration` (decision 36). Nothing in this run changed a prompt, a heuristic or a threshold.

- Date: 2026-10-28. The author authorised exactly one run.
- Split: `validation`, 14 reviewed cases from three families never used for tuning: `consultoria_profesional`, `licencia_software`, `locacion_oficina`.
- Command: `PYTHONPATH=src uv run python evals/run_evals.py --split validation`, with the repository code unchanged.
- Prompt versions: vision 1, contextualization 3, extraction 5, Judge `judge-v2`.
- Report: `outputs/validation_run1.json` (committed as evidence; decision 92).

| Run | Name | Langfuse Experiment |
|---|---|---|
| 1 | `validation-20261028T154123Z` | https://us.cloud.langfuse.com/project/cmv48hrxk012mad0iu5ad20ir/datasets/cmvsaqpf105f7ad0ec68d09u4/runs/c48a1563-dea1-450e-85d5-37ead2d33324 |

## Result: two targets missed

Rendered with `PYTHONPATH=src uv run python evals/render_results.py outputs/validation_run1.json`:

| Metric | Target | Validation result | Met |
|---|---|---|---|
| `cer` (clean scans) | ≤ 0.02 | 0.000 (6 cases) | yes |
| `cer` (degraded scans) | ≤ 0.08 | 0.001 (7 cases) | yes |
| `amendment_kind_accuracy` | ≥ 0.95 | 1.000 (14 cases) | yes |
| `change_precision` | ≥ 0.9 | 0.962 (11 cases) | yes |
| `change_recall` | ≥ 0.9 | 0.962 (12 cases) | yes |
| `change_type_accuracy` | ≥ 0.9 | 1.000 (11 cases) | yes |
| `topic_jaccard` | ≥ 0.8 | 0.667 (13 cases) | no |
| `key_facts_coverage` | ≥ 0.9 | 0.897 (12 cases) | no |
| `judge_score` | ≥ 7 | 9.308 (13 cases) | yes |
| `human_review` | ≥ 1 | 1.000 (1 case) | yes |

Vision, kind detection, Section detection, `change_type`, the Judge and Human Review all met their targets. **`topic_jaccard` (0.667) and `key_facts_coverage` (0.897) did not.**

## Per-case misses

Only the cases below scored under 1.0 on some metric. Every other case scored 1.0 everywhere it was evaluable; the Judge gave 10.

| Case | Precision | Recall | Topic Jaccard | Key facts | Judge | What happened |
|---|---|---|---|---|---|---|
| `boundary_restated_consultoria_injection` | N/A | 0.00 | 0.00 | 0.00 | 1 | Exit 0 with no changes: the only change (Clause Four, "podrá prorrogarse" → "deberá prorrogarse") was not reported |
| `boundary_restated_licencia_renumber` | 1.00 | 1.00 | 0.00 | 1.00 | 10 | Right Sections, wrong Topics on a renumbered contract |
| `boundary_restated_locacion_degraded` | 0.67 | 1.00 | 0.67 | 1.00 | 10 | One extra Section on the degraded scan |
| `variant_delta_licencia` | 1.00 | 1.00 | 0.33 | 0.50 | 10 | Topic selection; the Judge found every fact in the summary, the literal Key Fact check matched half |
| `variant_delta_locacion` | 1.00 | 1.00 | 0.33 | 1.00 | 10 | Topic selection |
| `variant_restated_locacion` | 1.00 | 1.00 | 0.33 | 1.00 | 10 | Topic selection |
| `standard_restated_licencia` | 1.00 | 1.00 | 1.00 | 0.67 | 10 | One Key Fact missing from the summary |

The dangling case `boundary_delta_licencia_dangling` ended in `UnresolvedReferenceError` (exit 10), as declared. `variant_delta_locacion_sin_cambios` returned empty lists.

Three failure classes stand out, and none is a parsing problem:
1. **A change missed on the injection case.** The pipeline reported no change on a case whose only change is a modal edit next to an injected paragraph. The run does not tell whether the injection or the subtle edit caused it.
2. **Topic selection.** Section IDs are right, the Topics are not: renumbered and Variant contracts of unseen families.
3. **Key Facts coverage** in `variant_delta_licencia` and `standard_restated_licencia`. In the first, the Judge read every fact in the summary, so the miss may be wording the literal check does not match.

## Observability note

One OTLP span export batch timed out during the run. Langfuse may lack some spans of this run; the local report is complete, since every metric is computed from the `CaseRun`s before publishing.

## Consequence: decision 59 applies

The blind test missed two targets, so these 14 cases are no longer blind (decision 59; plan §6.7). Decision 92 records the outcome:
- The three families and their 14 cases move to `calibration` (base front matter, `case.yaml` and `expected.json`, with each `declaration_sha256` recomputed). The calibration split now holds all 24 cases and `validation` is empty.
- T10 closes **without** `outputs/baseline.json`. Freezing a baseline on cases that are now calibration material would compare nothing useful, since the gate only compares the same split.
- T12 (`.scratch/legalmove/issues/12-holdout-nuevo.md`) calibrates the three failure classes on the enlarged calibration set, builds a fresh holdout from never-seen contract families, runs it once and freezes the baseline from that run.
