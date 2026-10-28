"""Generate Golden Cases from case.yaml: texts, rendered and degraded PNGs, and expected.json."""

import hashlib
import io
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from PIL import Image
from pydantic import ValidationError

from golden_case import (
    BaseContract,
    CaseSpec,
    GoldenCaseError,
    MutationOp,
    assign_split,
    load_base_contract,
    load_case_spec,
)
from golden_degrade import degrade_image, png_bytes
from golden_render import PageRenderer, PlaywrightPageRenderer, render_html
from golden_text import (
    Document,
    build_delta_document,
    build_original_document,
    build_restated_document,
    build_unrelated_document,
    document_text,
    insert_after_preamble,
)
from models import AmendmentKind, ChangeType, ContractChangeOutput, parse_section_id

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_ROOT = ROOT / "data" / "golden"
TEST_CONTRACTS = ROOT / "data" / "test_contracts"
# Decision 14: two Standard cases replace the phase 1 pairs A (restated) and B (delta).
TEST_PAIRS = {"pair_a": "standard_restated_servicios", "pair_b": "standard_delta_servicios"}
PAIR_FILES = ("original.png", "amendment.png", "source_original.txt", "source_amendment.txt")
CHANGE_TYPES = {
    MutationOp.MODIFY: ChangeType.MODIFIED,
    MutationOp.ADD: ChangeType.ADDED,
    MutationOp.REMOVE: ChangeType.REMOVED,
}


class ReviewedCaseError(GoldenCaseError):
    """The case's expected.json carries reviewed_at; regenerating would discard a human review."""


def canonical_key(section_id: str) -> tuple[bool, tuple[int, ...]]:
    """Original Sections first in document order, then new: Sections in Amendment order."""
    parts = parse_section_id(section_id)
    return parts.is_new, parts.numbers


def derive_changes(case: CaseSpec) -> list[dict[str, str]]:
    changes = [
        {
            "section_id": mutation.section,
            "change_type": CHANGE_TYPES[mutation.op].value,
            "topic": str(mutation.topic),
        }
        for mutation in case.mutations
        if mutation.op in CHANGE_TYPES
    ]
    return sorted(changes, key=lambda change: canonical_key(change["section_id"]))


def build_output(changes: list[dict[str, str]], summary: str) -> dict[str, Any]:
    declared = {
        "sections_changed": [change["section_id"] for change in changes],
        "topics_touched": list(dict.fromkeys(change["topic"] for change in changes)),
        "summary_of_the_change": summary,
    }
    try:
        return ContractChangeOutput.model_validate(declared).model_dump(mode="json")
    except ValidationError as error:
        raise GoldenCaseError(f"expected output breaks the public contract: {error}") from error


def derive_expected(case: CaseSpec, base: BaseContract, declaration_sha256: str) -> dict[str, Any]:
    """The draft truth a person reviews; review fields always start empty.

    Only `output` is the public ContractChangeOutput payload; every other key is case metadata.
    """
    changes = derive_changes(case)
    output = None if case.expected_error else build_output(changes, case.reference_summary)
    return {
        "base": base.family,
        "split": assign_split(case, base).value,
        "category": case.category,
        "amendment_kind": case.amendment_kind.value,
        "declaration_sha256": declaration_sha256,
        "expected_error": case.expected_error,
        "output": output,
        "changes": changes,
        "key_facts": list(case.key_facts),
        "reviewed_by": None,
        "reviewed_at": None,
    }


def derive_case_expected(case_dir: Path, golden_root: Path) -> dict[str, Any]:
    """Load a case directory and derive its expected.json, hashing the exact case.yaml bytes."""
    declaration = (case_dir / "case.yaml").read_bytes()
    case = load_case_spec(case_dir / "case.yaml")
    base = load_base_contract(golden_root / "base" / f"{case.base}.md")
    return derive_expected(case, base, hashlib.sha256(declaration).hexdigest())


def read_reviewed_at(case_dir: Path) -> str | None:
    expected = case_dir / "expected.json"
    if not expected.is_file():
        return None
    reviewed_at = json.loads(expected.read_text(encoding="utf-8")).get("reviewed_at")
    return str(reviewed_at) if reviewed_at is not None else None


def is_reviewed(case_dir: Path) -> bool:
    """A null, empty or blank reviewed_at means not reviewed; any other value protects the case."""
    reviewed_at = read_reviewed_at(case_dir)
    return reviewed_at is not None and bool(reviewed_at.strip())


def ensure_not_reviewed(case_dir: Path) -> None:
    if is_reviewed(case_dir):
        raise ReviewedCaseError(
            f"{case_dir.name} was reviewed at {read_reviewed_at(case_dir)}; "
            "clear reviewed_at to regenerate it"
        )


def build_amendment_document(case: CaseSpec, base: BaseContract) -> Document:
    """The page the pipeline reads as the Amendment, with its injection if the case declares one."""
    if case.unrelated_document is not None:
        return build_unrelated_document(case.unrelated_document)
    document = build_changing_document(case, base)
    if case.injection is None:
        return document
    return insert_after_preamble(document, case.injection)


def build_changing_document(case: CaseSpec, base: BaseContract) -> Document:
    if case.amendment_kind is AmendmentKind.RESTATED:
        return build_restated_document(base, case.mutations)
    return build_delta_document(base, case.mutations, case.seed)


def write_page(case_dir: Path, role: str, page: Image.Image) -> None:
    (case_dir / f"{role}.png").write_bytes(png_bytes(page))


def render_scan(renderer: PageRenderer, html: str, case: CaseSpec, seed: int) -> Image.Image:
    with Image.open(io.BytesIO(renderer.render_png(html))) as rendered:
        return degrade_image(rendered, case.degradations, seed)


def generate_case(case_dir: Path, golden_root: Path, renderer: PageRenderer) -> Path:
    """Write one case's artifacts; refuses before touching anything if it was reviewed."""
    ensure_not_reviewed(case_dir)
    expected = derive_case_expected(case_dir, golden_root)
    case = load_case_spec(case_dir / "case.yaml")
    base = load_base_contract(golden_root / "base" / f"{case.base}.md")
    template = (golden_root / "templates" / f"{case.template}.html").read_text(encoding="utf-8")
    documents = {
        "original": build_original_document(base),
        "amendment": build_amendment_document(case, base),
    }
    for offset, (role, document) in enumerate(documents.items()):
        page = render_scan(renderer, render_html(document, template), case, case.seed + offset)
        write_page(case_dir, role, page)
        (case_dir / f"source_{role}.txt").write_text(document_text(document) + "\n", "utf-8")
    payload = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    (case_dir / "expected.json").write_text(payload, encoding="utf-8")
    return case_dir


def has_pair_files(case_dir: Path) -> bool:
    return all((case_dir / name).is_file() for name in PAIR_FILES)


def publish_test_pairs(
    golden_root: Path, test_contracts: Path, pairs: Mapping[str, str]
) -> list[str]:
    """Copy each pair's case files into data/test_contracts; a case not yet generated is skipped."""
    published: list[str] = []
    for pair, case_id in pairs.items():
        case_dir = golden_root / "cases" / case_id
        if not has_pair_files(case_dir):
            continue
        (test_contracts / pair).mkdir(parents=True, exist_ok=True)
        for name in PAIR_FILES:
            shutil.copyfile(case_dir / name, test_contracts / pair / name)
        published.append(pair)
    return published


def selected_cases(golden_root: Path, case_ids: Sequence[str]) -> list[Path]:
    cases = sorted(path.parent for path in (golden_root / "cases").glob("*/case.yaml"))
    if unknown := sorted(set(case_ids) - {case.name for case in cases}):
        raise GoldenCaseError(f"unknown cases: {', '.join(unknown)}")
    return [case for case in cases if not case_ids or case.name in case_ids]


def generate_cases(
    golden_root: Path, case_ids: Sequence[str], renderer: PageRenderer, notes: TextIO
) -> list[Path]:
    """Without names, skip reviewed cases with a note; a named reviewed case refuses loudly."""
    generated: list[Path] = []
    for case_dir in selected_cases(golden_root, case_ids):
        if not case_ids and is_reviewed(case_dir):
            notes.write(f"skipped {case_dir.name}: already reviewed\n")
            continue
        generated.append(generate_case(case_dir, golden_root, renderer))
    return generated


def main(case_ids: list[str]) -> None:
    generated = generate_cases(GOLDEN_ROOT, case_ids, PlaywrightPageRenderer(), sys.stderr)
    for case_dir in generated:
        sys.stdout.write(f"{case_dir.relative_to(ROOT)}\n")
    for pair in publish_test_pairs(GOLDEN_ROOT, TEST_CONTRACTS, TEST_PAIRS):
        sys.stdout.write(f"published {pair}\n")


if __name__ == "__main__":
    main(sys.argv[1:])
