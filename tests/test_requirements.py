"""Dependency pins declared in pyproject.toml and exported to requirements.txt."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"

RUNTIME_PACKAGES = {
    "openai",
    "langchain",
    "langchain-core",
    "langchain-openai",
    "pydantic",
    "langfuse",
    "python-dotenv",
    "pydantic-settings",
    "pyyaml",
    "pillow",
}
DEV_PACKAGES = {"pytest", "playwright", "jiwer", "ruff", "mypy"}

PINNED = re.compile(r"^([A-Za-z0-9._-]+)(\[[^\]]+\])?==([0-9][A-Za-z0-9.!+-]*)$")
REQUIREMENT_LINE = re.compile(r"^([A-Za-z0-9._-]+)==(\S+)")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def read_runtime_specs() -> list[str]:
    project = read_pyproject()["project"]
    assert isinstance(project, dict)
    return list(project["dependencies"])


def read_dev_specs() -> list[str]:
    groups = read_pyproject()["dependency-groups"]
    assert isinstance(groups, dict)
    return list(groups["dev"])


def declared_names(specs: list[str]) -> set[str]:
    return {normalize_name(re.split(r"[\[=<>!~; ]", spec, maxsplit=1)[0]) for spec in specs}


def split_spec(spec: str) -> tuple[str, str]:
    match = PINNED.match(spec)
    assert match, f"not an exact pin: {spec}"
    return normalize_name(match.group(1)), match.group(3)


def read_exported_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        match = REQUIREMENT_LINE.match(line)
        if match:
            pins[normalize_name(match.group(1))] = match.group(2)
    return pins


def test_runtime_dependencies_are_declared() -> None:
    assert declared_names(read_runtime_specs()) == RUNTIME_PACKAGES


def test_dev_group_is_declared() -> None:
    assert declared_names(read_dev_specs()) == DEV_PACKAGES


@pytest.mark.parametrize("group", ["runtime", "dev"])
def test_every_declared_dependency_is_pinned_with_double_equals(group: str) -> None:
    specs = read_runtime_specs() if group == "runtime" else read_dev_specs()

    unpinned = [spec for spec in specs if not PINNED.match(spec)]

    assert unpinned == []


def test_requirements_txt_exists() -> None:
    assert REQUIREMENTS.is_file()


def test_requirements_txt_pins_every_package() -> None:
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    unpinned = [line for line in lines if not REQUIREMENT_LINE.match(line)]

    assert lines
    assert unpinned == []


def test_requirements_txt_excludes_dev_group() -> None:
    assert DEV_PACKAGES.isdisjoint(read_exported_pins())


def test_requirements_txt_matches_runtime_pins() -> None:
    exported = read_exported_pins()
    declared = dict(split_spec(spec) for spec in read_runtime_specs())

    assert {name: exported.get(name) for name in declared} == declared
