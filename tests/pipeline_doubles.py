"""Stub Stages for pipeline and CLI tests: scripted results, spans and one shared call log."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from models import (
    AmendmentKind,
    ContextMap,
    DocumentRole,
    ExtractionResult,
    ParsedContract,
    SectionMapping,
)
from observability import PipelineTracer
from pipeline import PipelineStages

ORIGINAL_TEXT = "CONTRATO\nCláusula Primera — Precio. $ 1.500.000."
AMENDMENT_TEXT = "ADENDA\nCláusula Primera — Precio. $ 1.850.000."


def parsed(role: DocumentRole, path: Path) -> ParsedContract:
    text = ORIGINAL_TEXT if role is DocumentRole.ORIGINAL else AMENDMENT_TEXT
    sha = "a" * 64 if role is DocumentRole.ORIGINAL else "b" * 64
    return ParsedContract(role=role, source_path=str(path), image_sha256=sha, text=text)


def context_map() -> ContextMap:
    return ContextMap(
        reasoning="Reproduce el contrato completo.",
        amendment_kind=AmendmentKind.RESTATED,
        sections=[
            SectionMapping(
                section_id="1",
                title="Precio",
                original_ref="Cláusula Primera",
                amendment_ref="Cláusula Primera",
                purpose="Fija el precio.",
            )
        ],
    )


def extraction() -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "reasoning": "Cambia el precio.",
            "changes": [
                {
                    "section_id": "1",
                    "change_type": "modified",
                    "topic": "price_payment",
                    "description": "El precio pasa de $ 1.500.000 a $ 1.850.000",
                    "original_quote": "$ 1.500.000",
                    "amendment_quote": "$ 1.850.000",
                }
            ],
            "sections_changed": ["1"],
            "topics_touched": ["price_payment"],
            "summary_of_the_change": "Resumen propuesto por el agente.",
        }
    )


@dataclass
class StubStages:
    """Each stub opens the span its real adapter opens, so the tracer sees the same Stages."""

    tracer: PipelineTracer
    calls: list[str] = field(default_factory=list)
    failures: dict[str, BaseException] = field(default_factory=dict)
    extraction_result: Callable[[], ExtractionResult] = extraction
    on_call: Callable[[str], None] = lambda _: None

    def ports(self) -> PipelineStages:
        return PipelineStages(
            parse_contract_image=self.parse_contract_image,
            contextualize=self.contextualize,
            extract=self.extract,
        )

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        self.on_call(name)
        if name in self.failures:
            raise self.failures[name]

    def parse_contract_image(self, path: Path, role: DocumentRole) -> ParsedContract:
        name = f"parse_{role.value}_contract"
        with self.tracer.stage(name, {"path": path.name}):
            self._enter(name)
            return parsed(role, path)

    def contextualize(self, original: ParsedContract, amendment: ParsedContract) -> ContextMap:
        assert (original.role, amendment.role) == (DocumentRole.ORIGINAL, DocumentRole.AMENDMENT)
        with self.tracer.stage("contextualization_agent", {}):
            self._enter("contextualization_agent")
            return context_map()

    def extract(
        self, mapped: ContextMap, original: ParsedContract, amendment: ParsedContract
    ) -> ExtractionResult:
        assert mapped == context_map()
        assert (original.text, amendment.text) == (ORIGINAL_TEXT, AMENDMENT_TEXT)
        with self.tracer.stage("extraction_agent", {}):
            self._enter("extraction_agent")
            return self.extraction_result()
