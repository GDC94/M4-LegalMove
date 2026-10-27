"""Golden Case factory: declared truth first, then text, images and expected.json (plan §6.4)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from checks import KindHeuristic, detect_kind_heuristic
from generate_golden import (
    ReviewedCaseError,
    derive_expected,
    generate_case,
    generate_cases,
    is_reviewed,
    publish_test_pairs,
)
from golden_case import (
    BaseContract,
    CaseSpec,
    Degradation,
    GoldenCaseError,
    Mutation,
    MutationOp,
    SplitLeakError,
    SplitName,
    TemplateName,
    assign_split,
    parse_base_contract,
)
from golden_degrade import degrade_image, png_bytes
from golden_render import PlaywrightPageRenderer, render_html
from golden_text import (
    PHRASINGS,
    Block,
    Document,
    render_delta_instructions,
    render_delta_text,
    render_original_text,
    render_restated_text,
)
from models import ContractChangeOutput
from render_plain import PAGE_SIZE, FontNotFoundError, PageOverflowError, load_page_font

BASE_MARKDOWN = """---
split: calibration
numbering: clausula
title: CONTRATO DE PRUEBA
preamble: Entre ALFA S.A. y BETA S.R.L. se celebra el presente contrato.
closing: En prueba de conformidad, se firman dos ejemplares.
restated_heading: ADENDA N° 1 — TEXTO ORDENADO DEL CONTRATO DE PRUEBA
restated_preamble: Las partes reemplazan el contrato por el siguiente texto ordenado.
delta_heading: ADENDA N° 1 AL CONTRATO DE PRUEBA
delta_preamble: Las partes convienen la presente adenda.
delta_closing: Las restantes cláusulas del Contrato conservan plena vigencia.
reviewed_by:
reviewed_at:
---

## 1. Objeto
ALFA prestará servicios de limpieza a BETA.

## 2. Plazo
El contrato dura doce (12) meses.

## 3. Precio
BETA pagará $ 100.000 por mes.

## 4. Penalidades
Cada día de demora genera una multa del uno por ciento (1 %).
"""


SHA = "0" * 64


def make_base(split: str = "calibration", family: str = "prueba") -> BaseContract:
    return parse_base_contract(BASE_MARKDOWN.replace("calibration", split), family)


def make_case(**overrides: Any) -> CaseSpec:
    declared: dict[str, Any] = {
        "base": "prueba",
        "amendment_kind": "restated",
        "category": "standard",
        "split": "calibration",
        "template": "one_column",
        "seed": 7,
        "mutations": [
            {
                "op": "modify",
                "section": "3",
                "text": "BETA pagará $ 150.000.",
                "topic": "price_payment",
            }
        ],
        "degradations": [],
        "key_facts": ["$ 150.000"],
        "reference_summary": "El precio mensual sube a $ 150.000.",
    }
    declared.update(overrides)
    return CaseSpec.model_validate(declared)


# --- declaration ---


def test_base_contract_parses_front_matter_and_numbered_sections() -> None:
    base = make_base()

    assert base.family == "prueba"
    assert base.split is SplitName.CALIBRATION
    assert [(s.number, s.title) for s in base.sections] == [
        (1, "Objeto"),
        (2, "Plazo"),
        (3, "Precio"),
        (4, "Penalidades"),
    ]
    assert base.sections[2].body == "BETA pagará $ 100.000 por mes."
    assert base.reviewed_at is None


@pytest.mark.parametrize("op", ["modify", "add", "remove", "renumber", "noop"])
def test_the_five_mutation_ops_are_declarable(op: str) -> None:
    declared = {
        "modify": {"section": "3", "text": "Nuevo texto.", "topic": "price_payment"},
        "add": {"section": "new:5", "title": "Seguros", "text": "Texto.", "topic": "insurance"},
        "remove": {"section": "4", "topic": "penalties"},
        "renumber": {"section": "4", "number": 5},
        "noop": {"section": "2"},
    }[op]

    assert Mutation.model_validate({"op": op, **declared}).op.value == op


@pytest.mark.parametrize(
    "declared",
    [
        {"op": "modify", "section": "3", "topic": "price_payment"},  # no text
        {"op": "modify", "section": "3", "text": "Texto."},  # no topic
        {"op": "add", "section": "5", "title": "T", "text": "Texto.", "topic": "other"},  # not new:
        {"op": "add", "section": "new:5", "text": "Texto.", "topic": "other"},  # no title
        {"op": "remove", "section": "4"},  # no topic
        {"op": "renumber", "section": "4"},  # no number
        {"op": "noop", "section": "new:2"},  # new: is never an original Section
        {"op": "rewrite", "section": "2"},  # unknown op
        {"op": "remove", "section": "new:05", "topic": "other"},  # breaks the ID grammar
        {"op": "add", "section": "new:05", "title": "T", "text": "Texto.", "topic": "other"},
        {"op": "modify", "section": "3", "text": "T.", "topic": "other", "number": 4},
        {"op": "noop", "section": "2", "topic": "other"},  # noop declares no truth
    ],
)
def test_an_incomplete_or_unknown_mutation_is_rejected(declared: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Mutation.model_validate(declared)


def test_a_case_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError):
        make_case(expected_output={"sections_changed": []})


# --- split by family ---


def test_split_comes_from_the_base_contract_family() -> None:
    assert assign_split(make_case(split="validation"), make_base("validation")) == "validation"


def test_a_case_declaring_another_split_than_its_family_is_a_leak() -> None:
    with pytest.raises(SplitLeakError, match="prueba"):
        assign_split(make_case(split="validation"), make_base("calibration"))


def test_a_case_of_another_family_cannot_borrow_this_base() -> None:
    with pytest.raises(SplitLeakError):
        assign_split(make_case(base="otra"), make_base())


def write_case_dir(root: Path, case_id: str, yaml_text: str) -> Path:
    case_dir = root / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "case.yaml").write_text(yaml_text, encoding="utf-8")
    return case_dir


# --- mutations applied to the text ---


def section_lines(text: str) -> list[str]:
    return [line for line in text.split("\n\n") if line.startswith("Cláusula")]


def test_original_text_renders_every_section_with_its_heading() -> None:
    text = render_original_text(make_base())

    assert text.startswith("CONTRATO DE PRUEBA\n\nEntre ALFA S.A.")
    assert section_lines(text)[2] == "Cláusula Tercera — Precio. BETA pagará $ 100.000 por mes."
    assert text.endswith("En prueba de conformidad, se firman dos ejemplares.")


def test_restated_modify_replaces_the_body_and_keeps_the_rest() -> None:
    case = make_case()

    lines = section_lines(render_restated_text(make_base(), case.mutations))

    assert lines[2] == "Cláusula Tercera — Precio. BETA pagará $ 150.000."
    assert lines[1] == "Cláusula Segunda — Plazo. El contrato dura doce (12) meses."


def test_restated_add_appends_a_new_section_with_its_amendment_number() -> None:
    add = Mutation(
        op="add",
        section="new:5",
        title="Seguros",
        text="ALFA contratará un seguro.",
        topic="insurance",
    )

    lines = section_lines(render_restated_text(make_base(), (add,)))

    assert lines[-1] == "Cláusula Quinta — Seguros. ALFA contratará un seguro."


def test_restated_remove_drops_the_section() -> None:
    remove = Mutation(op="remove", section="4", topic="penalties")

    lines = section_lines(render_restated_text(make_base(), (remove,)))

    assert len(lines) == 3
    assert not any("Penalidades" in line for line in lines)


def test_restated_add_after_with_renumber_shifts_the_following_sections() -> None:
    mutations = (
        Mutation(
            op="add",
            section="new:3",
            after="2",
            title="Garantía",
            text="ALFA garantiza.",
            topic="warranties",
        ),
        Mutation(op="renumber", section="3", number=4),
        Mutation(op="renumber", section="4", number=5),
    )

    lines = section_lines(render_restated_text(make_base(), mutations))

    assert [line.split(" — ")[0] for line in lines] == [
        "Cláusula Primera",
        "Cláusula Segunda",
        "Cláusula Tercera",
        "Cláusula Cuarta",
        "Cláusula Quinta",
    ]
    assert lines[2].startswith("Cláusula Tercera — Garantía.")
    assert lines[3] == "Cláusula Cuarta — Precio. BETA pagará $ 100.000 por mes."


def test_restated_noop_leaves_the_text_unchanged() -> None:
    noop = Mutation(op="noop", section="2")
    base = make_base()

    assert section_lines(render_restated_text(base, (noop,))) == section_lines(
        render_original_text(base)
    )


def test_colliding_section_numbers_are_rejected() -> None:
    add = Mutation(
        op="add", section="new:3", after="2", title="Garantía", text="Texto.", topic="warranties"
    )

    with pytest.raises(GoldenCaseError, match="3"):
        render_restated_text(make_base(), (add,))


def test_a_mutation_on_a_missing_section_is_rejected() -> None:
    with pytest.raises(GoldenCaseError, match="9"):
        render_restated_text(make_base(), (Mutation(op="remove", section="9", topic="other"),))


# --- delta instructions ---


DELTA_MUTATIONS = (
    Mutation(op="modify", section="3", text="BETA pagará $ 150.000.", topic="price_payment"),
    Mutation(op="remove", section="4", topic="penalties"),
    Mutation(
        op="add",
        section="new:5",
        title="Seguros",
        text="ALFA contratará un seguro.",
        topic="insurance",
    ),
)


def test_delta_instructions_quote_the_full_new_text_and_name_the_section() -> None:
    instructions = render_delta_instructions(make_base(), DELTA_MUTATIONS, seed=1)

    assert instructions[0].startswith("PRIMERO. ")
    assert "Cláusula Tercera" in instructions[0]
    assert '"Cláusula Tercera — Precio. BETA pagará $ 150.000."' in instructions[0]
    assert (
        instructions[1].startswith("SEGUNDO. ")
        and "Cláusula Cuarta (Penalidades)" in instructions[1]
    )
    assert '"Cláusula Quinta — Seguros. ALFA contratará un seguro."' in instructions[2]


def test_delta_text_wraps_instructions_with_heading_preamble_and_closing() -> None:
    text = render_delta_text(make_base(), DELTA_MUTATIONS, seed=1)

    assert text.startswith("ADENDA N° 1 AL CONTRATO DE PRUEBA\n\nLas partes convienen")
    assert "Las restantes cláusulas del Contrato conservan plena vigencia." in text
    assert text.endswith("En prueba de conformidad, se firman dos ejemplares.")


def test_delta_renumber_and_noop_produce_instructions_without_new_content() -> None:
    mutations = (
        Mutation(op="renumber", section="4", number=5),
        Mutation(op="noop", section="2"),
    )

    renumber, noop = render_delta_instructions(make_base(), mutations, seed=1)

    assert "Cláusula Cuarta" in renumber and "Cláusula Quinta" in renumber
    assert '"Cláusula Segunda — Plazo. El contrato dura doce (12) meses."' in noop


@pytest.mark.parametrize("op", list(MutationOp))
def test_every_op_has_a_phrasing_the_kind_heuristic_does_not_know(op: MutationOp) -> None:
    unknown = [
        key
        for key in PHRASINGS[op]
        if detect_kind_heuristic(phrase_instruction(make_base(), op, key))
        is KindHeuristic.INCONCLUSIVE
    ]

    assert unknown, f"every {op} phrasing is a Kind Heuristic marker"


def phrase_instruction(base: BaseContract, op: MutationOp, key: str) -> str:
    declared = {
        MutationOp.MODIFY: {"section": "3", "text": "BETA pagará $ 1.", "topic": "price_payment"},
        MutationOp.ADD: {
            "section": "new:5",
            "title": "Seguros",
            "text": "Texto.",
            "topic": "insurance",
        },
        MutationOp.REMOVE: {"section": "4", "topic": "penalties"},
        MutationOp.RENUMBER: {"section": "4", "number": 5},
        MutationOp.NOOP: {"section": "2"},
    }[op]
    mutation = Mutation.model_validate({"op": op, "phrasing": key, **declared})
    [instruction] = render_delta_instructions(base, (mutation,), seed=0)
    return instruction


def test_a_declared_phrasing_is_used_verbatim() -> None:
    known = phrase_instruction(make_base(), MutationOp.REMOVE, "deja_sin_efecto")

    assert detect_kind_heuristic(known) is KindHeuristic.DELTA


def test_an_unknown_phrasing_key_is_rejected() -> None:
    with pytest.raises(GoldenCaseError, match="nope"):
        phrase_instruction(make_base(), MutationOp.REMOVE, "nope")


def test_undeclared_phrasing_is_chosen_deterministically_by_seed() -> None:
    first = render_delta_instructions(make_base(), DELTA_MUTATIONS, seed=11)

    assert render_delta_instructions(make_base(), DELTA_MUTATIONS, seed=11) == first
    assert any(
        render_delta_instructions(make_base(), DELTA_MUTATIONS, seed=seed) != first
        for seed in range(12, 30)
    )


def test_restated_text_never_carries_a_kind_heuristic_marker() -> None:
    text = render_restated_text(make_base(), DELTA_MUTATIONS)

    assert detect_kind_heuristic(text) is KindHeuristic.INCONCLUSIVE


# --- expected.json derived from the declared truth ---


def test_expected_lists_follow_the_canonical_order_not_the_mutation_order() -> None:
    case = make_case(
        mutations=[
            {
                "op": "add",
                "section": "new:5",
                "title": "Seguros",
                "text": "Texto.",
                "topic": "insurance",
            },
            {"op": "remove", "section": "4", "topic": "penalties"},
            {"op": "modify", "section": "3", "text": "BETA pagará $ 1.", "topic": "price_payment"},
            {"op": "noop", "section": "2"},
        ]
    )

    expected = derive_expected(case, make_base(), SHA)

    assert expected["output"]["sections_changed"] == ["3", "4", "new:5"]
    assert expected["output"]["topics_touched"] == ["price_payment", "penalties", "insurance"]
    assert expected["changes"] == [
        {"section_id": "3", "change_type": "modified", "topic": "price_payment"},
        {"section_id": "4", "change_type": "removed", "topic": "penalties"},
        {"section_id": "new:5", "change_type": "added", "topic": "insurance"},
    ]


def test_expected_output_is_a_valid_public_contract_with_the_reference_summary() -> None:
    expected = derive_expected(make_case(), make_base(), SHA)

    output = ContractChangeOutput.model_validate(expected["output"])
    assert output.summary_of_the_change == "El precio mensual sube a $ 150.000."
    assert expected["amendment_kind"] == "restated"
    assert expected["key_facts"] == ["$ 150.000"]
    assert expected["expected_error"] is None


def test_renumber_and_noop_are_not_changes() -> None:
    case = make_case(
        mutations=[{"op": "renumber", "section": "4", "number": 5}, {"op": "noop", "section": "2"}],
        reference_summary="No se identificaron cambios.",
        key_facts=[],
    )

    output = derive_expected(case, make_base(), SHA)["output"]

    assert output == {
        "sections_changed": [],
        "topics_touched": [],
        "summary_of_the_change": "No se identificaron cambios.",
    }


def test_a_reference_summary_that_breaks_the_public_contract_is_rejected() -> None:
    case = make_case(mutations=[{"op": "noop", "section": "2"}], reference_summary="Cambió algo.")

    with pytest.raises(GoldenCaseError, match="expected output"):
        derive_expected(case, make_base(), SHA)


def test_an_expected_error_case_has_no_output() -> None:
    expected = derive_expected(make_case(expected_error="InvalidExtractionError"), make_base(), SHA)

    assert expected["output"] is None
    assert expected["expected_error"] == "InvalidExtractionError"


def test_review_fields_are_derived_empty() -> None:
    expected = derive_expected(make_case(), make_base(), SHA)

    assert expected["reviewed_by"] is None
    assert expected["reviewed_at"] is None


# --- HTML templates and the renderer seam ---


def test_html_fills_the_template_and_escapes_document_text() -> None:
    document = Document("A & B", (Block("Cláusula Primera — Objeto.", "x < y"), Block(None, "fin")))

    html = render_html(document, "<h1>$title</h1><main>$blocks</main>")

    assert "<h1>A &amp; B</h1>" in html
    assert '<span class="lead">Cláusula Primera — Objeto.</span>' in html
    assert "x &lt; y" in html
    assert '<p class="block"><span class="lead"></span><span class="body">fin</span></p>' in html


class RecordingRenderer:
    """Renderer double: a blank A4 page, recording each HTML it was given."""

    def __init__(self) -> None:
        self.pages: list[str] = []

    def render_png(self, html: str) -> bytes:
        self.pages.append(html)
        return png_bytes(Image.new("RGB", PAGE_SIZE, "white"))


def make_golden_root(tmp_path: Path, case_yaml: str) -> Path:
    root = tmp_path / "golden"
    (root / "base").mkdir(parents=True)
    (root / "base/prueba.md").write_text(BASE_MARKDOWN, encoding="utf-8")
    (root / "templates").mkdir()
    for name in TemplateName:
        (root / "templates" / f"{name}.html").write_text(
            f"<!-- {name} --><h1>$title</h1>$blocks", encoding="utf-8"
        )
    write_case_dir(root / "cases", "caso", case_yaml)
    return root


CASE_YAML = """
base: prueba
amendment_kind: delta
category: standard
split: calibration
template: table
seed: 3
mutations:
  - {op: modify, section: "3", text: "BETA pagará $ 150.000.", topic: price_payment}
degradations:
  - {op: rotate, amount: 0.5}
key_facts: ["$ 150.000"]
reference_summary: El precio mensual sube a $ 150.000.
"""


def test_generate_case_writes_every_artifact_from_the_declaration(tmp_path: Path) -> None:
    root = make_golden_root(tmp_path, CASE_YAML)
    renderer = RecordingRenderer()

    case_dir = generate_case(root / "cases/caso", root, renderer)

    assert sorted(p.name for p in case_dir.iterdir()) == [
        "amendment.png", "case.yaml", "expected.json", "original.png",
        "source_amendment.txt", "source_original.txt",
    ]  # fmt: skip
    original, amendment = renderer.pages
    assert "<!-- table -->" in original and "CONTRATO DE PRUEBA" in original
    assert "ADENDA N° 1 AL CONTRATO DE PRUEBA" in amendment
    source = (case_dir / "source_amendment.txt").read_text(encoding="utf-8")
    assert source.startswith("ADENDA N° 1 AL CONTRATO DE PRUEBA")
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    assert expected["output"]["sections_changed"] == ["3"]
    with Image.open(case_dir / "amendment.png") as image:
        assert image.format == "PNG"
        assert image.size == PAGE_SIZE


def test_generate_case_rejects_a_split_leak(tmp_path: Path) -> None:
    root = make_golden_root(tmp_path, CASE_YAML.replace("calibration", "validation"))

    with pytest.raises(SplitLeakError):
        generate_case(root / "cases/caso", root, RecordingRenderer())


# --- no overwrite of a reviewed expected.json ---


def reviewed(case_dir: Path, reviewed_at: str | None) -> None:
    stamp = {"reviewed_by": "autor" if reviewed_at else None, "reviewed_at": reviewed_at}
    (case_dir / "expected.json").write_text(json.dumps(stamp), encoding="utf-8")


def test_generator_refuses_to_overwrite_a_reviewed_case(tmp_path: Path) -> None:
    root = make_golden_root(tmp_path, CASE_YAML)
    case_dir = root / "cases/caso"
    reviewed(case_dir, "2026-10-27")
    renderer = RecordingRenderer()

    with pytest.raises(ReviewedCaseError, match="caso"):
        generate_case(case_dir, root, renderer)

    assert json.loads((case_dir / "expected.json").read_text())["reviewed_at"] == "2026-10-27"
    assert renderer.pages == []
    assert not (case_dir / "original.png").exists()


def test_generator_regenerates_a_case_that_is_not_reviewed_yet(tmp_path: Path) -> None:
    root = make_golden_root(tmp_path, CASE_YAML)
    case_dir = root / "cases/caso"
    reviewed(case_dir, None)

    generate_case(case_dir, root, RecordingRenderer())

    assert "output" in json.loads((case_dir / "expected.json").read_text())


# --- seeded Degradations ---


def sample_page() -> Image.Image:
    page = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(page).rectangle((50, 50, 350, 120), fill="black")
    return page


ALL_DEGRADATIONS = (
    Degradation(op="rotate", amount=1.5),
    Degradation(op="noise", amount=0.08),
    Degradation(op="blur", amount=1.0),
    Degradation(op="jpeg", amount=40),
    Degradation(op="downscale", amount=0.6),
)


def test_same_seed_gives_identical_bytes() -> None:
    first = png_bytes(degrade_image(sample_page(), ALL_DEGRADATIONS, seed=5))

    assert png_bytes(degrade_image(sample_page(), ALL_DEGRADATIONS, seed=5)) == first


def test_a_different_seed_gives_a_different_scan() -> None:
    first = png_bytes(degrade_image(sample_page(), ALL_DEGRADATIONS, seed=5))

    assert png_bytes(degrade_image(sample_page(), ALL_DEGRADATIONS, seed=6)) != first


def test_no_degradations_keep_the_page_clean() -> None:
    assert png_bytes(degrade_image(sample_page(), (), seed=5)) == png_bytes(sample_page())


@pytest.mark.parametrize("degradation", ALL_DEGRADATIONS, ids=lambda d: d.op.value)
def test_each_degradation_changes_the_page_and_keeps_its_size(degradation: Degradation) -> None:
    degraded = degrade_image(sample_page(), (degradation,), seed=5)

    assert degraded.size == (400, 300)
    assert png_bytes(degraded) != png_bytes(sample_page())


def test_stamp_draws_its_text_over_the_page() -> None:
    try:
        load_page_font()
    except FontNotFoundError:
        pytest.skip("the stamp needs DejaVu Sans or Arial; none is installed on this machine")
    stamp = Degradation(op="stamp", amount=0.5, text="COPIA")

    degraded = degrade_image(sample_page(), (stamp,), seed=5)

    assert png_bytes(degraded) != png_bytes(sample_page())


# --- the real Playwright adapter (skipped when Chromium is not installed) ---


@pytest.fixture(scope="module")
def chromium_renderer() -> PlaywrightPageRenderer:
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
    except PlaywrightError:
        pytest.skip("Chromium for Playwright is not installed: uv run playwright install chromium")
    return PlaywrightPageRenderer()


def test_playwright_renders_one_a4_page(chromium_renderer: PlaywrightPageRenderer) -> None:
    png = chromium_renderer.render_png("<p>Cláusula Séptima — ñ</p>")

    with Image.open(io.BytesIO(png)) as image:
        assert image.size == PAGE_SIZE


def test_playwright_refuses_a_page_that_overflows(
    chromium_renderer: PlaywrightPageRenderer,
) -> None:
    with pytest.raises(PageOverflowError):
        chromium_renderer.render_png("<p>línea</p>" * 400)


def test_a_modify_that_rewrites_the_section_identically_is_rejected() -> None:
    same = Mutation(
        op="modify", section="3", text="BETA pagará $ 100.000 por mes.", topic="price_payment"
    )

    with pytest.raises(GoldenCaseError, match="noop"):
        render_restated_text(make_base(), (same,))
    with pytest.raises(GoldenCaseError, match="noop"):
        render_delta_text(make_base(), (same,), seed=1)


def test_expected_records_the_declaration_hash_outside_the_public_output() -> None:
    expected = derive_expected(make_case(), make_base(), "ab" * 32)

    assert expected["declaration_sha256"] == "ab" * 32
    assert "declaration_sha256" not in expected["output"]


@pytest.mark.parametrize(
    ("reviewed_at", "verdict"),
    [("2026-10-27", True), (None, False), ("", False), ("   ", False)],
)
def test_only_a_non_empty_reviewed_at_counts_as_reviewed(
    tmp_path: Path, reviewed_at: str | None, verdict: bool
) -> None:
    reviewed(tmp_path, reviewed_at)

    assert is_reviewed(tmp_path) is verdict


def test_is_reviewed_is_false_without_expected_json(tmp_path: Path) -> None:
    assert is_reviewed(tmp_path) is False


# --- batch generation and the pairs ---


def make_two_case_root(tmp_path: Path) -> Path:
    root = make_golden_root(tmp_path, CASE_YAML)
    write_case_dir(root / "cases", "otro", CASE_YAML)
    return root


def test_batch_skips_reviewed_cases_and_notes_them_on_stderr(tmp_path: Path) -> None:
    root = make_two_case_root(tmp_path)
    reviewed(root / "cases/caso", "2026-10-27")
    notes = io.StringIO()

    generated = generate_cases(root, [], RecordingRenderer(), notes)

    assert [path.name for path in generated] == ["otro"]
    assert "caso" in notes.getvalue() and "reviewed" in notes.getvalue()
    assert not (root / "cases/caso/original.png").exists()


def test_naming_a_reviewed_case_refuses_loudly(tmp_path: Path) -> None:
    root = make_two_case_root(tmp_path)
    reviewed(root / "cases/caso", "2026-10-27")

    with pytest.raises(ReviewedCaseError, match="caso"):
        generate_cases(root, ["caso"], RecordingRenderer(), io.StringIO())


def test_naming_a_case_generates_only_that_case(tmp_path: Path) -> None:
    root = make_two_case_root(tmp_path)

    generated = generate_cases(root, ["otro"], RecordingRenderer(), io.StringIO())

    assert [path.name for path in generated] == ["otro"]
    assert not (root / "cases/caso/original.png").exists()


def test_naming_an_unknown_case_is_an_error(tmp_path: Path) -> None:
    root = make_two_case_root(tmp_path)

    with pytest.raises(GoldenCaseError, match="nada"):
        generate_cases(root, ["nada"], RecordingRenderer(), io.StringIO())


def test_pairs_are_published_only_from_cases_with_artifacts(tmp_path: Path) -> None:
    root = make_two_case_root(tmp_path)
    generate_case(root / "cases/caso", root, RecordingRenderer())
    contracts = tmp_path / "test_contracts"

    published = publish_test_pairs(root, contracts, {"pair_a": "caso", "pair_b": "otro"})

    assert published == ["pair_a"]
    assert sorted(path.name for path in (contracts / "pair_a").iterdir()) == [
        "amendment.png", "original.png", "source_amendment.txt", "source_original.txt",
    ]  # fmt: skip
    assert not (contracts / "pair_b").exists()
