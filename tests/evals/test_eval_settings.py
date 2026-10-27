"""EvalSettings: the runtime Settings plus a Judge model that never grades its own extraction."""

from typing import Any

import pytest
from pydantic import ValidationError

from config import Settings
from errors import ConfigurationError
from settings import EvalSettings, load_eval_settings

REQUIRED = {
    "openai_api_key": "sk-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_secret_key": "sk-lf-test",
}


@pytest.fixture(autouse=True)
def no_judge_model_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest only clears the runtime Settings names; JUDGE_MODEL is eval-only.
    monkeypatch.delenv("JUDGE_MODEL", raising=False)


def make_eval_settings(**overrides: Any) -> EvalSettings:
    return EvalSettings(_env_file=None, **{**REQUIRED, **overrides})


def set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.upper(), value)


def test_judge_model_defaults_to_gpt_4_1_next_to_the_runtime_settings() -> None:
    settings = make_eval_settings()

    assert settings.judge_model == "gpt-4.1"
    assert settings.extraction_model == "gpt-4o"
    assert isinstance(settings, Settings)


def test_judge_model_equal_to_extraction_model_is_rejected() -> None:
    with pytest.raises(ValidationError, match="JUDGE_MODEL must differ from EXTRACTION_MODEL"):
        make_eval_settings(judge_model="gpt-4o")


def test_the_comparison_ignores_case_and_surrounding_spaces() -> None:
    with pytest.raises(ValidationError, match="JUDGE_MODEL must differ"):
        make_eval_settings(judge_model=" GPT-4.1 ", extraction_model="gpt-4.1")


def test_a_judge_different_from_a_custom_extraction_model_is_accepted() -> None:
    settings = make_eval_settings(judge_model="gpt-4o", extraction_model="gpt-4.1")

    assert settings.judge_model == "gpt-4o"


def test_blank_judge_model_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_eval_settings(judge_model="")


def test_judge_model_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("JUDGE_MODEL", "gpt-4.1-mini")

    assert load_eval_settings(env_file=None).judge_model == "gpt-4.1-mini"


def test_invalid_eval_settings_become_a_configuration_error_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("JUDGE_MODEL", "gpt-4o")

    with pytest.raises(ConfigurationError) as caught:
        load_eval_settings(env_file=None)

    assert "JUDGE_MODEL must differ from EXTRACTION_MODEL" in str(caught.value)
    assert "sk-test" not in str(caught.value)
    assert caught.value.exit_code == 3
