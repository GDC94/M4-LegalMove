# Contract Change Analysis

Compares a scanned Original Contract with its scanned Amendment and reports which Sections changed, which Topics they touch and what changed, as a validated JSON that LegalMove's Compliance systems can process automatically. One context: parsing, contextualization, extraction and validation share the same language.

## Language

### Documents

**Original Contract**:
The version of a contract in force before an Amendment.
_Avoid_: base document, source contract, documento madre, contrato base

**Amendment**:
A document that modifies an Original Contract. Exactly one Amendment is compared with one Original Contract per run.
_Avoid_: enmienda, addendum, annex, anexo (an annex adds material; it does not modify), revision

**Amendment Kind**:
How an Amendment expresses its changes: Restated, Delta or Unknown.
_Avoid_: amendment type, format, tipo de adenda

**Restated Amendment**:
An Amendment that reproduces the whole contract with its changes already applied.
_Avoid_: consolidated version, full amendment, versión consolidada

**Delta Amendment**:
An Amendment that only lists change instructions, such as "clause five shall read as follows".
_Avoid_: partial amendment, change letter, diff (reserved for the deterministic comparison)

**Unknown Amendment**:
An Amendment whose kind cannot be determined with confidence. It always goes to Human Review and is never guessed.
_Avoid_: other, unclassified, invalid amendment

**Parsed Contract**:
The verbatim text read from one document image by the vision model, together with its image hash and Illegible Mark count.
_Avoid_: OCR output, transcription, extracted text

**Illegible Mark**:
The literal `[ILLEGIBLE]` placeholder the vision model writes where it cannot read the image.
_Avoid_: unreadable flag, gap, blank

### Structure

**Section**:
A unit of a contract, whatever the document calls it ("cláusula", "artículo", "punto"). The unit in which Changes are reported.
_Avoid_: clause (as a generic term), paragraph, block, chunk

**Section ID**:
The canonical identifier of a Section, independent of how the document writes it: "Cláusula Quinta", "5ª" and "Artículo V" are all `5`. It always follows the Original Contract's numbering. Numbered IDs are positive integers separated by dots (`5`, `5.2`); unnumbered Sections use a positional ID such as `§7`; Amendment-only Sections prepend `new:` (`new:5`, `new:5.2`, `new:§7`). Titles are stored separately and never form part of the ID.
_Avoid_: clause number, section number, label

**Context Map**:
The ContextualizationAgent's result: the Amendment Kind and one Section Mapping per Section. It describes structure only, never Changes.
_Avoid_: context, structural summary, outline, mapa

**Section Mapping**:
One row of the Context Map: a Section ID, the Instruction that targets it, how the Section appears in each document, and its purpose.
_Avoid_: alignment, correspondence row, pair

**Instruction**:
What a Delta Amendment's instruction does to one Section: adds it, rewrites it or removes it. `none` when no instruction names the Section, and always `none` in a Restated Amendment.
_Avoid_: action, operation, change type (reserved for Changes)

**Unresolved Reference**:
A Delta instruction that rewrites or removes a Section the Original Contract does not have. The Section cannot be invented as an Addition: the run goes straight to Human Review, without a retry.
_Avoid_: dangling clause, missing section, broken reference

### Changes

**Change**:
One concrete modification introduced by the Amendment in one Section, with its Change Type, Topic and Quotes.
_Avoid_: difference, diff, delta, edit, modificación (as a generic term)

**Change Type**:
Whether a Change is an Addition, a Removal or a Modification.
_Avoid_: action, operation, kind (reserved for Amendment Kind)

**Addition**:
A Change that introduces a complete Section absent from the Original Contract. Adding content inside an existing Section is a Modification.
_Avoid_: insertion, new clause

**Removal**:
A Change that leaves a complete Section of the Original Contract without effect. Removing content inside a retained Section is a Modification.
_Avoid_: deletion, derogation

**Modification**:
A Change that alters the content of a Section present in both documents. A renumbered Section with the same content is not a Modification.
_Avoid_: update, edit, amendment (reserved for the document)

**Topic**:
The legal or commercial category of a Change, taken from a closed taxonomy. `other` is allowed and monitored.
_Avoid_: category, tag, tópico, subject

**Quote**:
Text copied literally from a Parsed Contract as evidence for a Change.
_Avoid_: excerpt, snippet, fragment, citation

**Summary**:
The public Spanish description rendered deterministically from the ordered, validated Changes. The ExtractionAgent also proposes a Summary for requirement compliance and analysis, but that proposal is not published.
_Avoid_: description (reserved for one Change), abstract, report

### Validation

**Stage**:
One step of the pipeline with its own span: parsing, contextualization, extraction or output validation.
_Avoid_: phase, step, node

**Check**:
A deterministic comparison that produces a score or a pass/fail result, such as Grounding or Consistency. A Check never calls a model.
_Avoid_: evaluator, assertion, guardrail

**Grounding**:
The Check that every Quote exists in its Parsed Contract after normalization: whitespace, Unicode NFKC, `casefold()` and unified quotes and dashes. There is no fuzzy matching.
_Avoid_: citation check, fact check, verification

**Consistency**:
The Check that the reported Sections and Topics equal the Sections and Topics of the Changes.
_Avoid_: coherence, agreement

**Kind Heuristic**:
The deterministic marker search that returns Delta or Inconclusive, used as a second signal against the ContextualizationAgent. Absence of a Delta marker is never evidence of Restated.
_Avoid_: classifier, rule engine, detector

**Human Review**:
The outcome where the system declines to answer and hands the case to a person (exit code 10).
_Avoid_: manual fallback, escalation, rejection, error

### Testing

**Golden Case**:
A pair of document images with an expected result declared before the images are generated and reviewed by a person.
_Avoid_: test case, fixture, sample, example

**Mutation**:
A declared edit applied to a base contract text to produce a Golden Case's Amendment.
_Avoid_: perturbation, transformation, edit

**Degradation**:
A declared image effect, such as rotation, noise or blur, that simulates a real scan.
_Avoid_: augmentation, noise, distortion

**Key Fact**:
A fact the Summary of a Golden Case must mention, such as an amount or a deadline.
_Avoid_: keyword, must-have, expected phrase

**Split**:
The partition a Golden Case belongs to: Calibration, used to tune prompts and thresholds, or Validation, never used to tune. Assignment is by base-contract family so variants of one contract never cross the boundary.
_Avoid_: fold, set, test set

**Judge**:
The model, different from the one being evaluated, that rates a Summary against its Golden Case.
_Avoid_: evaluator, grader, LLM-as-judge
