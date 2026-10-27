"""The README carries the sections, tables and references plan §8.2 requires (ticket T6)."""

import re
from pathlib import Path

import pytest

import errors
from settings import EvalSettings
from test_layering import DELIVERABLES

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CONTRACTS_README = ROOT / "data/test_contracts/README.md"
ENV_EXAMPLE = ROOT / ".env.example"

# Plan §8.2, in order; the README is written in English (CLAUDE.md, Language).
REQUIRED_SECTIONS = [
    "What it is",
    "Diagram",
    "Architecture",
    "Setup",
    "Configuration",
    "Usage",
    "Tracing",
    "Testing and evals",
    "Technical decisions",
    "Known limitations",
    "Deliverables",
    "Vocabulary",
]

# Plan §13.2 deliverables with a repository path: the architecture list plus the two that
# are a directory or a module rather than a single required file.
DELIVERABLE_PATHS = [*DELIVERABLES, "data/test_contracts/", "src/observability.py"]
DELIVERABLE_IDS = [f"E{number:02d}" for number in range(1, 12)]

TRACE_CAPTURE = "docs/images/langfuse-trace.png"
ANIMATED_DIAGRAM = "docs/diagrams/legalmove-pipeline.html"


def read_readme() -> str:
    return README.read_text(encoding="utf-8")


def section_titles(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def read_section(title: str) -> str:
    """Body of one `## ` section, up to the next `## ` heading."""
    match = re.search(
        rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", read_readme(), flags=re.M | re.S
    )
    assert match, f"missing section: {title}"
    return match.group(1)


def leaf_errors() -> list[type[errors.LegalMoveError]]:
    """Concrete domain errors: the ones main.py can turn into an exit code."""
    found: list[type[errors.LegalMoveError]] = []
    pending = list(errors.LegalMoveError.__subclasses__())
    while pending:
        error = pending.pop()
        children = error.__subclasses__()
        pending.extend(children)
        if not children:
            found.append(error)
    return sorted(found, key=lambda error: error.__name__)


def test_readme_has_the_twelve_sections_in_plan_order() -> None:
    titles = section_titles(read_readme())

    assert [title for title in titles if title in REQUIRED_SECTIONS] == REQUIRED_SECTIONS


def test_diagram_section_has_mermaid_and_links_the_animated_version() -> None:
    body = read_section("Diagram")

    assert "```mermaid" in body
    assert f"]({ANIMATED_DIAGRAM})" in body
    assert (ROOT / ANIMATED_DIAGRAM).is_file()


# EvalSettings extends Settings, so this also covers the eval-only JUDGE_MODEL.
@pytest.mark.parametrize("field", sorted(EvalSettings.model_fields))
def test_configuration_table_lists_every_setting(field: str) -> None:
    assert f"| `{field.upper()}` |" in read_section("Configuration")


def test_configuration_never_documents_secret_values() -> None:
    body = read_section("Configuration")

    assert not re.search(r"sk-[A-Za-z0-9]|pk-lf-|sk-lf-", body)


@pytest.mark.parametrize("error", leaf_errors(), ids=lambda error: error.__name__)
def test_usage_exit_code_table_matches_the_error_hierarchy(
    error: type[errors.LegalMoveError],
) -> None:
    row = re.compile(rf"^\|.*`{error.__name__}`.*\|\s*`{error.exit_code}`\s*\|$", flags=re.M)

    assert row.search(read_section("Usage"))


@pytest.mark.parametrize("code", [0, 1])
def test_usage_exit_code_table_covers_success_and_unexpected_failure(code: int) -> None:
    assert re.search(rf"\|\s*`{code}`\s*\|$", read_section("Usage"), flags=re.M)


def test_usage_documents_the_session_id_option() -> None:
    assert "--session-id" in read_section("Usage")


def test_tracing_section_references_a_real_trace_capture() -> None:
    assert f"]({TRACE_CAPTURE})" in read_section("Tracing")
    assert (ROOT / TRACE_CAPTURE).is_file()


def test_testing_section_has_the_measured_results_subsection() -> None:
    assert re.search(r"^### Measured results$", read_section("Testing and evals"), flags=re.M)


# EvalSettings extends Settings, so this also covers the eval-only JUDGE_MODEL.
@pytest.mark.parametrize("field", sorted(EvalSettings.model_fields))
def test_env_example_lists_every_setting(field: str) -> None:
    """E09: the template names every Settings variable, set or commented out."""
    pattern = rf"^(# )?{field.upper()}="

    assert re.search(pattern, ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.M)


@pytest.mark.parametrize("deliverable_id", DELIVERABLE_IDS)
def test_deliverables_table_lists_every_requirement(deliverable_id: str) -> None:
    assert f"| {deliverable_id} |" in read_section("Deliverables")


@pytest.mark.parametrize("path", DELIVERABLE_PATHS)
def test_deliverables_table_names_every_path(path: str) -> None:
    assert f"`{path}`" in read_section("Deliverables")


@pytest.mark.parametrize("pair", ["pair_a", "pair_b"])
def test_contracts_readme_describes_each_final_pair(pair: str) -> None:
    text = CONTRACTS_README.read_text(encoding="utf-8")
    row = re.search(rf"^\| `{pair}` \|.*$", text, flags=re.M)

    assert row, f"no table row for {pair}"
    assert re.search(r"`(restated|delta)`", row.group(0))
    assert "sections_changed" in text
    assert "topics_touched" in text
    assert "Degradaciones" in text
