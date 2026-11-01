"""StageName: the five stage names the pipeline traces, and no literal escapes them (plan §4.5)."""

import ast
from pathlib import Path

from models import DocumentRole
from stages import StageName, parse_stage_for

SRC = Path(__file__).resolve().parent.parent / "src"
TRACER_NAMES = {"tracer", "_tracer"}


def test_stage_name_has_the_five_stages_of_the_pipeline() -> None:
    assert {stage.value for stage in StageName} == {
        "parse_original_contract",
        "parse_amendment_contract",
        "contextualization_agent",
        "extraction_agent",
        "output_validation",
    }


def test_each_document_role_has_its_parse_stage() -> None:
    assert parse_stage_for(DocumentRole.ORIGINAL) is StageName.PARSE_ORIGINAL_CONTRACT
    assert parse_stage_for(DocumentRole.AMENDMENT) is StageName.PARSE_AMENDMENT_CONTRACT


def tracer_stage_calls() -> list[tuple[str, ast.Call]]:
    """Every `<...>tracer.stage(...)` call in src/, with the file it lives in."""
    calls: list[tuple[str, ast.Call]] = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if is_tracer_stage_call(node):
                assert isinstance(node, ast.Call)
                calls.append((path.name, node))
    return calls


def is_tracer_stage_call(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    owner = node.func.value
    owner_name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
    return node.func.attr == "stage" and owner_name in TRACER_NAMES


def is_stage_name(expression: ast.expr) -> bool:
    source = ast.unparse(expression)
    return source.startswith(("StageName.", "parse_stage_for("))


def test_the_ast_scan_finds_the_stages_the_pipeline_emits() -> None:
    found = {name for name, _ in tracer_stage_calls()}

    assert {"image_parser.py", "contextualization_agent.py", "extraction_agent.py"} <= found
    assert "pipeline.py" in found


def test_every_tracer_stage_call_receives_a_stage_name() -> None:
    offenders = [
        f"{name}:{call.lineno} {ast.unparse(call.args[0])}"
        for name, call in tracer_stage_calls()
        if not is_stage_name(call.args[0])
    ]

    assert offenders == []


def test_the_ast_check_rejects_a_literal_and_an_f_string() -> None:
    literal = ast.parse('tracer.stage("extraction_agent", {})', mode="eval").body
    formatted = ast.parse('self._tracer.stage(f"parse_{x}", {})', mode="eval").body
    enum_member = ast.parse("self._tracer.stage(StageName.EXTRACTION_AGENT, {})", mode="eval").body
    assert isinstance(literal, ast.Call)
    assert isinstance(formatted, ast.Call)
    assert isinstance(enum_member, ast.Call)

    assert not is_stage_name(literal.args[0])
    assert not is_stage_name(formatted.args[0])
    assert is_stage_name(enum_member.args[0])
