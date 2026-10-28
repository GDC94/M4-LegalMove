"""Golden Case text: apply Mutations to a base contract and phrase restated or delta Amendments."""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from golden_case import (
    BaseContract,
    GoldenCaseError,
    Mutation,
    MutationOp,
    Numbering,
    UnrelatedDocument,
)
from models import parse_section_id

FEMININE_ORDINALS = (
    "Primera", "Segunda", "Tercera", "Cuarta", "Quinta", "Sexta",
    "Séptima", "Octava", "Novena", "Décima", "Undécima", "Duodécima",
)  # fmt: skip
ROMAN_NUMERALS = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")
INSTRUCTION_LEADS = (
    "PRIMERO", "SEGUNDO", "TERCERO", "CUARTO", "QUINTO",
    "SEXTO", "SÉPTIMO", "OCTAVO", "NOVENO", "DÉCIMO",
)  # fmt: skip

# Delta instruction phrasings. Each op mixes Kind Heuristic markers with phrasings the heuristic
# does not know, so the golden set never grades the heuristic with its own vocabulary (plan §6.4).
MODIFY_PHRASINGS = {
    "modificase": 'Modifícase {ref} del Contrato con el siguiente texto: "{quote}"',
    "pase_a_decir": 'Las partes convienen en que {ref} del Contrato pase a decir: "{quote}"',
    "dira_en_adelante": 'Desde la firma de esta adenda, {ref} del Contrato dirá: "{quote}"',
    "reformulase": 'Reformúlase {ref} del Contrato en estos términos: "{quote}"',
}
PHRASINGS: dict[MutationOp, dict[str, str]] = {
    MutationOp.MODIFY: MODIFY_PHRASINGS,
    MutationOp.ADD: {
        "incorporase": 'Incorpórase al Contrato, como {name}, el siguiente texto: "{quote}"',
        "agregase": 'Agrégase al Contrato, como {name}, el siguiente texto: "{quote}"',
        "suman": 'Las partes suman al Contrato, como {name}, el texto que sigue: "{quote}"',
    },
    MutationOp.REMOVE: {
        "deja_sin_efecto": "Déjase sin efecto {ref} ({title}) del Contrato.",
        "suprimese": "Suprímese {ref} ({title}) del Contrato, que no regirá en adelante.",
        "concluida": "Las partes dan por concluida la vigencia de {ref} ({title}) del Contrato.",
    },
    MutationOp.RENUMBER: {
        "pasa_a_numerarse": (
            "Sin cambios en su texto, {ref} ({title}) del Contrato pasa a numerarse como {name}."
        ),
    },
    MutationOp.NOOP: {
        "ratifican": (
            'Las partes ratifican {ref} del Contrato, que conserva el siguiente texto: "{quote}"'
        ),
        # Plan §2.6: the same wording as a modification, quoting the unchanged text.
        "reformulase": MODIFY_PHRASINGS["reformulase"],
    },
    # A dangling reference reads exactly like a modification of a Section that does not exist.
    MutationOp.DANGLING: MODIFY_PHRASINGS,
}


@dataclass(frozen=True)
class Block:
    """One paragraph of a document; `lead` is a Section heading or an instruction number."""

    lead: str | None
    body: str


@dataclass(frozen=True)
class Document:
    title: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class AmendedSection:
    """A Section as the Amendment shows it; `origin` is its original Section ID, if any."""

    origin: str | None
    number: int
    title: str
    body: str


def section_name(numbering: Numbering, number: int) -> str:
    if numbering is Numbering.ARTICLE:
        return f"Artículo {ROMAN_NUMERALS[number - 1]}"
    if numbering is Numbering.CLAUSE:
        return f"Cláusula {FEMININE_ORDINALS[number - 1]}"
    return f"cláusula {number}"


def section_ref(numbering: Numbering, number: int) -> str:
    article = "el" if numbering is Numbering.ARTICLE else "la"
    return f"{article} {section_name(numbering, number)}"


def section_heading(numbering: Numbering, number: int, title: str) -> str:
    if numbering is Numbering.NUMERIC:
        return f"{number}. {title}."
    return f"{section_name(numbering, number)} — {title}."


def block_text(block: Block) -> str:
    return block.body if block.lead is None else f"{block.lead} {block.body}"


def document_text(document: Document) -> str:
    return "\n\n".join([document.title, *(block_text(block) for block in document.blocks)])


def section_blocks(numbering: Numbering, sections: Sequence[AmendedSection]) -> list[Block]:
    return [Block(section_heading(numbering, s.number, s.title), s.body) for s in sections]


def original_sections(base: BaseContract) -> list[AmendedSection]:
    return [AmendedSection(str(s.number), s.number, s.title, s.body) for s in base.sections]


def build_original_document(base: BaseContract) -> Document:
    blocks = section_blocks(base.numbering, original_sections(base))
    return Document(base.title, (Block(None, base.preamble), *blocks, Block(None, base.closing)))


def render_original_text(base: BaseContract) -> str:
    return document_text(build_original_document(base))


def find_section(sections: Sequence[AmendedSection], section_id: str) -> int:
    for index, section in enumerate(sections):
        if section.origin == section_id:
            return index
    raise GoldenCaseError(f"section {section_id} does not exist in the base contract")


Amend = Callable[[list[AmendedSection], Mutation], list[AmendedSection]]


def amend_modify(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    index = find_section(sections, mutation.section)
    amended = replace(
        sections[index], title=mutation.title or sections[index].title, body=mutation.text or ""
    )
    if amended == sections[index]:
        # Plan §2.6: rewriting a clause with the same text is no real change.
        raise GoldenCaseError(
            f"modify leaves section {mutation.section} unchanged; declare a noop instead"
        )
    sections[index] = amended
    return sections


def amend_remove(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    del sections[find_section(sections, mutation.section)]
    return sections


def amend_renumber(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    index = find_section(sections, mutation.section)
    sections[index] = replace(sections[index], number=mutation.number or 0)
    return sections


def amend_noop(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    find_section(sections, mutation.section)  # a noop must still name a real Section
    return sections


def amend_dangling(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    if any(section.origin == mutation.section for section in sections):
        raise GoldenCaseError(
            f"dangling section {mutation.section} exists in the base contract; declare a modify"
        )
    return sections


def amend_add(sections: list[AmendedSection], mutation: Mutation) -> list[AmendedSection]:
    position = len(sections)
    if mutation.after is not None:
        position = find_section(sections, mutation.after) + 1
    number = parse_section_id(mutation.section).numbers[0]
    added = AmendedSection(None, number, mutation.title or "", mutation.text or "")
    return [*sections[:position], added, *sections[position:]]


AMENDERS: dict[MutationOp, Amend] = {
    MutationOp.MODIFY: amend_modify,
    MutationOp.ADD: amend_add,
    MutationOp.REMOVE: amend_remove,
    MutationOp.RENUMBER: amend_renumber,
    MutationOp.NOOP: amend_noop,
    MutationOp.DANGLING: amend_dangling,
}


def amend_sections(base: BaseContract, mutations: Sequence[Mutation]) -> list[AmendedSection]:
    sections = original_sections(base)
    for mutation in mutations:
        sections = AMENDERS[mutation.op](sections, mutation)
    numbers = [section.number for section in sections]
    if any(left >= right for left, right in pairwise(numbers)):
        raise GoldenCaseError(f"amendment section numbers collide or go backwards: {numbers}")
    return sections


def build_restated_document(base: BaseContract, mutations: Sequence[Mutation]) -> Document:
    blocks = section_blocks(base.numbering, amend_sections(base, mutations))
    preamble, closing = Block(None, base.restated_preamble), Block(None, base.closing)
    return Document(base.restated_heading, (preamble, *blocks, closing))


def render_restated_text(base: BaseContract, mutations: Sequence[Mutation]) -> str:
    return document_text(build_restated_document(base, mutations))


def choose_phrasing(mutation: Mutation, index: int, seed: int) -> str:
    phrasings = PHRASINGS[mutation.op]
    if mutation.phrasing is None:
        return phrasings[random.Random(seed * 1000 + index).choice(sorted(phrasings))]
    if mutation.phrasing not in phrasings:
        raise GoldenCaseError(f"unknown phrasing {mutation.phrasing} for {mutation.op}")
    return phrasings[mutation.phrasing]


def declared_section_fields(numbering: Numbering, mutation: Mutation) -> dict[str, str]:
    """Fields of a Section the original lacks: its number comes from the declared ID."""
    number = parse_section_id(mutation.section).numbers[0]
    title, body = mutation.title or "", mutation.text or ""
    return {
        "ref": section_ref(numbering, number),
        "name": section_name(numbering, number),
        "title": title,
        "quote": f"{section_heading(numbering, number, title)} {body}",
    }


def instruction_fields(base: BaseContract, mutation: Mutation) -> dict[str, str]:
    """The placeholders a phrasing may use: ref, name, title and the quoted Section text."""
    numbering = base.numbering
    if mutation.op in {MutationOp.ADD, MutationOp.DANGLING}:
        return declared_section_fields(numbering, mutation)
    original = base.sections[find_section(original_sections(base), mutation.section)]
    number = mutation.number or original.number
    title = mutation.title or original.title
    body = mutation.text or original.body
    return {
        "ref": section_ref(numbering, original.number),
        "name": section_name(numbering, number),
        "title": original.title,
        "quote": f"{section_heading(numbering, original.number, title)} {body}",
    }


def build_delta_instructions(
    base: BaseContract, mutations: Sequence[Mutation], seed: int
) -> list[Block]:
    return [
        Block(
            f"{INSTRUCTION_LEADS[index]}.",
            choose_phrasing(mutation, index, seed).format_map(instruction_fields(base, mutation)),
        )
        for index, mutation in enumerate(mutations)
    ]


def render_delta_instructions(
    base: BaseContract, mutations: Sequence[Mutation], seed: int
) -> list[str]:
    return [block_text(block) for block in build_delta_instructions(base, mutations, seed)]


def build_delta_document(base: BaseContract, mutations: Sequence[Mutation], seed: int) -> Document:
    amend_sections(base, mutations)  # the same structural checks as restated
    instructions = build_delta_instructions(base, mutations, seed)
    preamble = Block(None, base.delta_preamble)
    closing = (Block(None, base.delta_closing), Block(None, base.closing))
    return Document(base.delta_heading, (preamble, *instructions, *closing))


def render_delta_text(base: BaseContract, mutations: Sequence[Mutation], seed: int) -> str:
    return document_text(build_delta_document(base, mutations, seed))


def insert_after_preamble(document: Document, body: str) -> Document:
    """An unnumbered paragraph between the preamble and the first Section or instruction."""
    preamble, *rest = document.blocks
    return replace(document, blocks=(preamble, Block(None, body), *rest))


def build_unrelated_document(unrelated: UnrelatedDocument) -> Document:
    return Document(unrelated.title, tuple(Block(None, body) for body in unrelated.paragraphs))
