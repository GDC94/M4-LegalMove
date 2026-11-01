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


def test_the_claude_judge_settings_default_to_no_key_opus_and_medium_effort() -> None:
    settings = make_eval_settings()

    assert settings.anthropic_api_key is None
    assert settings.claude_judge_model == "claude-opus-5-5"
    assert settings.claude_judge_effort == "medium"


def test_the_claude_judge_settings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_required_environment(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CLAUDE_JUDGE_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("CLAUDE_JUDGE_EFFORT", "high")

    settings = load_eval_settings(env_file=None)

    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert settings.claude_judge_model == "claude-opus-4-8"
    assert settings.claude_judge_effort == "high"


def test_an_effort_outside_the_known_levels_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_eval_settings(claude_judge_effort="extreme")


def test_the_anthropic_key_does_not_show_in_its_string_form() -> None:
    settings = make_eval_settings(anthropic_api_key="sk-ant-secret")

    assert "sk-ant-secret" not in str(settings.anthropic_api_key)
    assert "sk-ant-secret" not in repr(settings)
