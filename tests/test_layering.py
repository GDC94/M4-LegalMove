"""AST architecture tests: dependencies only point inward (plan §1 Arquitectura, §6.2)."""

import ast
import re
from pathlib import Path

import pytest

from golden_text import PHRASINGS

SRC = Path(__file__).resolve().parent.parent / "src"
DOMAIN_MODULES = ["models.py", "errors.py", "checks.py", "prompt_blocks.py", "budget.py"]
INFRASTRUCTURE = {"langchain", "langchain_core", "langchain_openai", "openai", "langfuse"}
LANGFUSE_FREE = ["image_parser.py", "models.py", "checks.py", "pipeline.py", "errors.py"]


def runtime_imports(path: Path) -> set[str]:
    """Top-level modules imported at runtime; `if TYPE_CHECKING:` blocks are type-only."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    pending: list[ast.AST] = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            pending.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
        pending.extend(ast.iter_child_nodes(node))
    return imported


def calls_function(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
        for node in ast.walk(tree)
    )


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("module", LANGFUSE_FREE)
def test_business_modules_do_not_import_langfuse(module: str) -> None:
    path = SRC / module
    if not path.exists():
        pytest.skip(f"{module} does not exist yet")

    assert "langfuse" not in runtime_imports(path)


def test_agents_do_not_import_langfuse() -> None:
    for path in sorted((SRC / "agents").glob("*.py")) if (SRC / "agents").exists() else []:
        assert "langfuse" not in runtime_imports(path), path.name


@pytest.mark.parametrize("module", DOMAIN_MODULES)
def test_domain_modules_import_no_infrastructure_or_config(module: str) -> None:
    path = SRC / module
    if not path.exists():
        pytest.skip(f"{module} does not exist yet")

    assert runtime_imports(path).isdisjoint(INFRASTRUCTURE | {"config", "observability"})


def adapter_modules() -> list[Path]:
    return [
        SRC / "image_parser.py",
        SRC / "budgeted_model.py",
        *sorted((SRC / "agents").glob("*.py")),
    ]


@pytest.mark.parametrize("path", adapter_modules(), ids=lambda path: path.name)
def test_adapters_do_not_load_the_langfuse_adapter_at_runtime(path: Path) -> None:
    assert "observability" not in runtime_imports(path)


def test_only_main_and_observability_import_langfuse() -> None:
    importers = {path.name for path in source_files() if "langfuse" in runtime_imports(path)}

    assert importers <= {"main.py", "observability.py"}


def test_only_main_calls_load_settings() -> None:
    callers = {path.name for path in source_files() if calls_function(path, "load_settings")}

    assert callers <= {"main.py"}


def test_runtime_imports_ignores_type_checking_blocks(tmp_path: Path) -> None:
    module = tmp_path / "sample.py"
    module.write_text(
        "from typing import TYPE_CHECKING\n"
        "import os\n"
        "if TYPE_CHECKING:\n"
        "    import langfuse\n"
        "def f():\n"
        "    from openai import APIError\n",
        encoding="utf-8",
    )

    assert runtime_imports(module) == {"typing", "os", "openai"}


ADAPTERS_AND_ROOT = {"agents", "image_parser", "budgeted_model", "observability", "config", "main"}


def test_pipeline_depends_only_on_domain_and_ports() -> None:
    imported = runtime_imports(SRC / "pipeline.py")

    assert imported.isdisjoint(INFRASTRUCTURE | ADAPTERS_AND_ROOT)


def test_pipeline_never_flushes_or_owns_the_trace_lifecycle() -> None:
    tree = ast.parse((SRC / "pipeline.py").read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert attributes.isdisjoint({"flush", "open_root", "shutdown"})


ROOT = SRC.parent
# Deliverables of the assignment (plan §1 layout, marked *).
DELIVERABLES = [
    "src/main.py",
    "src/image_parser.py",
    "src/models.py",
    "src/agents/contextualization_agent.py",
    "src/agents/extraction_agent.py",
    "requirements.txt",
    ".env.example",
    "data/test_contracts/README.md",
    "README.md",
]


@pytest.mark.parametrize("relative_path", DELIVERABLES)
def test_deliverable_exists_at_its_path(relative_path: str) -> None:
    assert (ROOT / relative_path).is_file()


def test_test_contracts_hold_at_least_two_complete_pairs() -> None:
    pairs = [
        pair
        for pair in sorted((ROOT / "data/test_contracts").iterdir())
        if pair.is_dir()
        and all(
            (pair / name).is_file()
            for name in (
                "original.png",
                "amendment.png",
                "source_original.txt",
                "source_amendment.txt",
            )
        )
    ]

    assert len(pairs) >= 2


GOLDEN = ROOT / "data/golden"
SENTENCE_END = re.compile(r"(?<=[.:;])\s+|\n+")
MIN_FRAGMENT_CHARS = 30
YAML_KEY = re.compile(r"^[a-z_]+:\s*")


def normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def golden_fragments(golden: Path) -> set[str]:
    """Every long sentence of the base contracts and case declarations, normalized."""
    sources = [*golden.glob("base/*.md"), *golden.glob("cases/*/case.yaml")]
    sentences = (
        normalize_text(piece)
        for path in sources
        for piece in SENTENCE_END.split(path.read_text(encoding="utf-8"))
    )
    sentences = (YAML_KEY.sub("", sentence) for sentence in sentences)
    return {sentence for sentence in sentences if len(sentence) >= MIN_FRAGMENT_CHARS}


def phrasing_fragments() -> set[str]:
    """The fixed words of each delta phrasing, without its placeholders."""
    pieces = (
        normalize_text(piece)
        for phrasings in PHRASINGS.values()
        for template in phrasings.values()
        for piece in re.split(r"\{[a-z]+\}", template)
    )
    return {piece for piece in pieces if len(piece) >= 15}


def string_constants(src: Path) -> str:
    """All string literals of the source tree (docstrings and prompts included), normalized."""
    return "\n".join(
        normalize_text(node.value)
        for path in sorted(src.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def leaked_fragments(fragments: set[str], src: Path) -> list[str]:
    literals = string_constants(src)
    return sorted(fragment for fragment in fragments if fragment in literals)


def test_no_golden_text_appears_in_src() -> None:
    fragments = golden_fragments(GOLDEN) | phrasing_fragments()

    assert len(fragments) > 50  # never pass vacuously on a missing golden set
    assert leaked_fragments(fragments, SRC) == []


def test_leak_detection_finds_a_golden_sentence_inside_a_prompt(tmp_path: Path) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "base/prueba.md").write_text(
        "## 1. Precio\nEl CLIENTE abonará la suma mensual de $ 1.850.000 más IVA.\n",
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "prompt.py").write_text(
        'PROMPT = """Ejemplo:\n  el cliente abonará la suma mensual de $ 1.850.000 más IVA."""\n',
        encoding="utf-8",
    )

    assert leaked_fragments(golden_fragments(tmp_path), src) == [
        "el cliente abonará la suma mensual de $ 1.850.000 más iva."
    ]


def test_leak_detection_ignores_yaml_keys_in_front_matter(tmp_path: Path) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "base/prueba.md").write_text(
        "---\nclosing: En prueba de conformidad se firman dos ejemplares.\n---\n",
        encoding="utf-8",
    )

    assert golden_fragments(tmp_path) == {"en prueba de conformidad se firman dos ejemplares."}


EVALS_MODULES = {
    "generate_golden",
    "golden_case",
    "golden_text",
    "golden_render",
    "golden_degrade",
    "render_plain",
    "playwright",
}


def test_src_never_imports_evals_modules_or_playwright() -> None:
    importers = {
        path.relative_to(SRC).as_posix(): sorted(runtime_imports(path) & EVALS_MODULES)
        for path in source_files()
    }

    assert {name: found for name, found in importers.items() if found} == {}
