"""Shared fixtures: unit tests never see the developer's real environment or .env."""

import pytest
from pydantic_settings import BaseSettings

from api_settings import ApiSettings
from config import Settings
from settings import EvalSettings

# Every settings class the tests build; the subclasses add names Settings does not read.
SETTINGS_CLASSES: tuple[type[BaseSettings], ...] = (Settings, ApiSettings, EvalSettings)


def settings_env_names() -> list[str]:
    """Every environment name the settings classes read, derived so none can be forgotten."""
    names: set[str] = set()
    for settings_class in SETTINGS_CLASSES:
        for name, field in settings_class.model_fields.items():
            alias = field.validation_alias if isinstance(field.validation_alias, str) else None
            names.add((alias or field.alias or name).upper())
    return sorted(names)


@pytest.fixture(autouse=True)
def isolated_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Integration tests read real keys from .env or the shell, so they keep the environment.
    if request.node.get_closest_marker("integration"):
        return
    for name in settings_env_names():
        monkeypatch.delenv(name, raising=False)
