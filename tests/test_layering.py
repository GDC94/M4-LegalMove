"""AST architecture tests: dependencies only point inward (plan §1 Arquitectura, §6.2)."""

import ast
from pathlib import Path

import pytest

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
