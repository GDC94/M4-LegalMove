# T13 Key Facts rule

Ticket T13 (`.scratch/legalmove/issues/13-metrica-key-facts.md`), plan §4.4, §6.4, §6.6–6.7: `key_facts_coverage` should measure facts, not wording, and the literal number rule of decision 95 is enforced in code. Everything is decided on the `calibration` split only. The frozen baseline (`outputs/baseline.json`, decision 101) is not rescored and no `validation` case is touched.

## Matcher rule

`score_key_facts` (`evals/metrics.py`) still checks substring containment after `normalize_fact_text` (NFKC, casefold, collapsed whitespace, no space hugging `$` or `%`, decision 76). On top of that:

- Both the fact and the summary drop the whole-word tokens in `FACT_STOPWORDS`: `el`, `la`, `los`, `las`, `lo`, `de`, `del`, `al`. Removal is word-wise, never a substring replace, so "desde" keeps its "de".
- `un`, `una` and `por` are deliberately left out: they are part of numbers ("un (1) mes", "veinte por ciento").
- A fact made only of stopwords falls back to its plain normalized form; otherwise the empty string would be found in any summary.
- The fact must start at a word boundary of the summary, so "el 20 %" (content "20%") is not found in "sube al 120 %". The right side stays open, so "seguro" is found in "seguros". The stopword-only fallback uses the same left boundary.
- Numbers, units, currency and percent signs are never removed or changed. There is no fuzzy matching: no edit distance, similarity or embeddings.

With this rule, three of the four T12 misses are found: "informe de las ventas" in "informe de ventas", "electricidad y de pintura" in "electricidad y pintura", and the atomic "dieciocho (18)" in "camiones … hasta dieciocho (18)". "dos (2) trimestres" against "dos trimestres" is still a miss on purpose: it was a real violation of the literal rule.

## Literal number check

New hard check `number_parenthetical_dropped` (`src/checks.py`). On the normalized text of both documents it reads each number written in words as the maximal run of words from a closed lexicon (`CARDINAL_WORDS`), joined directly or by the connectors `y` and `por` and starting and ending with a cardinal word: "cuarenta y cinco", "veinte por ciento", "un mil". If the run is followed by a parenthesised numeral (OCR spacing and `%` tolerated) and a next word, it gives a key such as "cuarenta y cinco días" with the numeral "cuarenta y cinco (45)". The original contract is scanned too, because restated summaries describe its numbers.

The summary and each change description are read the same way. If one of them writes a key's run and next word without the numeral, the attempt fails and goes to the single retry, and the retry detail asks to keep "cuarenta y cinco (45)". Because whole runs are compared, "cuarenta y cinco (45) días" and "treinta y cinco (35) días" stay apart, "cinco por ciento del total" does not fire against "veinte por ciento (20 %) del canon", and "cinco mil pesos" does not fire against "un mil (1.000) pesos".

It does not fire when:
- the documents themselves also write the same run and word without the numeral ("dos meses de gracia" next to "dos (2) meses de alquiler"). Another run such as "treinta y cinco días hábiles" does not suppress "cuarenta y cinco días";
- the run is only "un", "una" or "uno". These are usually articles ("una (1) vez por año" against "una vez firmada la adenda"). A compound run such as "treinta y un (31) días" still counts;
- the word is not a cardinal ("Anexo (1) del");
- no word follows the parenthesis ("(18)." or "(18) [ILLEGIBLE]").

## Authoring guideline for future Key Facts

Each Key Fact is the minimal fact:

- A number goes with exactly the words the amendment writes next to it: "dieciocho (18)", not "dieciocho (18) camiones" when the noun may come first.
- A phrase fact keeps its content words and neither starts nor ends with a stopword. `test_calibration_key_facts_keep_content_once_stopwords_are_dropped` enforces this on `calibration`; every current calibration fact already passes it, so none was rewritten.

## Limitations

- Substring matching keeps the right side open: "$465" is found inside "$4650".
- The number check skips "un (1)", "una (1)" and "uno (1)" on their own, so dropping that numeral is not caught.
- Word order is not normalized: a non-atomic fact such as "dieciocho (18) camiones" still misses "camiones … hasta dieciocho (18)". Only the authoring guideline avoids it.
- A summary that writes a stopword where the fact has another one ("de la" vs "del") now matches; this is accepted as wording, not as a different fact.

## Calibration run (2026-10-28)

One authorized run on `calibration` (25 cases, report kept locally as `outputs/calibration_t13_run1.json`), no 429s. Every §6.6 target is met:

| Metric | Value | Target |
|---|---|---|
| CER clean / degraded | 0.0003 / 0.0008 | ≤ 0.02 / ≤ 0.08 |
| amendment_kind_accuracy | 1.000 | ≥ 0.95 |
| change_precision / recall | 0.952 / 0.976 | ≥ 0.90 |
| change_type_accuracy | 1.000 | ≥ 0.90 |
| topic_jaccard | 0.826 | ≥ 0.80 |
| key_facts_coverage | 0.9375 | ≥ 0.90 |
| judge_score | 9.71 | ≥ 7 |
| human_review | 1.000 | 1.0 |

`number_parenthetical_dropped` caused no exit 10. Two cases fell below T12 run 4:

- `boundary_restated_servicios_renumber` ended in `InvalidExtractionError` (`unresolved_section_reference`). Its trace shows the contextualization agent mapped only the 7 renumbered sections and missed the added "Cláusula Tercera — Migración inicial", so neither `new:3` nor `new:8` resolved. T13 does not touch contextualization; the same case passed in T12 run 4, so this is model variance.
- `standard_restated_licencia` scored 2/3 key facts: "ochenta (80) usuarios" is not atomic and the summary writes "de cincuenta (50) a ochenta (80)". Rewriting the fact as "ochenta (80)" needs the author's review and is left pending.

## Open questions

- Which holdout validates the new rule. It needs either raising the cap of 8 base contracts or a new split; it is not decided here.
- How a future run compares against a baseline scored with the previous rule, since the regression gate (decision 88) would compare different `key_facts_coverage` definitions.
