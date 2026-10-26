"""Shared fixtures: unit tests never see the developer's real environment or .env."""

import pytest

from config import Settings


def settings_env_names() -> list[str]:
    """Every environment name Settings reads, derived so a new field cannot be forgotten."""
    names: list[str] = []
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias if isinstance(field.validation_alias, str) else None
        names.append((alias or field.alias or name).upper())
    return names


@pytest.fixture(autouse=True)
def isolated_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Integration tests read real keys from .env or the shell, so they keep the environment.
    if request.node.get_closest_marker("integration"):
        return
    for name in settings_env_names():
        monkeypatch.delenv(name, raising=False)
