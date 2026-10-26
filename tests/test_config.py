"""Settings defaults, ranges and cross-field rules (plan §5.1)."""

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from config import APP_VERSION, Settings, load_settings
from conftest import settings_env_names
from errors import ConfigurationError

REQUIRED = {
    "openai_api_key": "sk-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_secret_key": "sk-lf-test",
}


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**REQUIRED, **overrides})


def test_defaults_match_the_plan() -> None:
    settings = make_settings()

    assert settings.langfuse_host == "https://cloud.langfuse.com"
    assert settings.app_env == "development"
    assert settings.vision_model == "gpt-4o"
    assert settings.vision_detail == "high"
    assert settings.llm_temperature == 0.0
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_max_retries == 2
    assert settings.pipeline_timeout_seconds == 300
    assert settings.max_image_bytes == 10_000_000
    assert settings.min_image_side_px == 600
    assert settings.max_image_side_px == 4096
    assert settings.max_image_pixels == 16_777_216
    assert settings.illegible_max_marks == 10
    assert settings.parse_cache_dir == Path(".cache/parsed")
    assert settings.parse_cache_ttl_hours == 24
    assert settings.log_level == "INFO"


def test_secrets_are_not_revealed_in_repr() -> None:
    assert "sk-test" not in repr(make_settings())


def test_app_version_is_a_constant() -> None:
    assert isinstance(APP_VERSION, str)
    assert APP_VERSION


@pytest.mark.parametrize(
    "missing", ["openai_api_key", "langfuse_public_key", "langfuse_secret_key"]
)
def test_required_keys_are_mandatory(missing: str) -> None:
    values = {key: value for key, value in REQUIRED.items() if key != missing}

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_temperature", 2.1),
        ("llm_temperature", -0.1),
        ("llm_timeout_seconds", 0),
        ("llm_max_retries", 11),
        ("max_image_bytes", 0),
        ("min_image_side_px", 0),
        ("max_image_pixels", 0),
        ("illegible_max_marks", -1),
        ("parse_cache_ttl_hours", 0),
        ("vision_detail", "medium"),
        ("app_env", "test"),
        ("vision_model", ""),
        ("log_level", "VERBOSE"),
        ("langfuse_host", "not-a-url"),
    ],
)
def test_out_of_range_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_pipeline_timeout_must_cover_one_llm_call() -> None:
    with pytest.raises(ValidationError, match="PIPELINE_TIMEOUT_SECONDS"):
        make_settings(llm_timeout_seconds=120, pipeline_timeout_seconds=90)


def test_pipeline_timeout_equal_to_llm_timeout_is_allowed() -> None:
    assert make_settings(llm_timeout_seconds=90, pipeline_timeout_seconds=90)


def test_max_image_side_must_not_be_below_min_side() -> None:
    with pytest.raises(ValidationError, match="MAX_IMAGE_SIDE_PX"):
        make_settings(min_image_side_px=800, max_image_side_px=700)


@pytest.mark.parametrize(
    "host",
    [
        "https://cloud.langfuse.com",
        "https://us.cloud.langfuse.com",
        "https://cloud.langfuse.com/",
        "https://hipaa.cloud.langfuse.com",
        "https://new-region.cloud.langfuse.com:443",
        "HTTPS://CLOUD.LANGFUSE.COM",
    ],
)
def test_production_rejects_public_langfuse_cloud(host: str) -> None:
    with pytest.raises(ValidationError, match="LANGFUSE_HOST"):
        make_settings(app_env="production", langfuse_host=host)


def test_production_does_not_confuse_lookalike_hosts_with_langfuse_cloud() -> None:
    settings = make_settings(app_env="production", langfuse_host="https://mycloud.langfuse.com.ar")

    assert settings.app_env == "production"


def test_production_accepts_an_explicit_private_host() -> None:
    settings = make_settings(
        app_env="production", langfuse_host="https://langfuse.legalmove.internal"
    )

    assert settings.langfuse_host == "https://langfuse.legalmove.internal"


@pytest.mark.parametrize(
    ("app_env", "explicit", "expected"),
    [
        ("development", None, True),
        ("staging", None, False),
        ("production", None, False),
        ("production", True, True),
        ("development", False, False),
    ],
)
def test_parse_cache_default_depends_on_app_env(
    app_env: str, explicit: bool | None, expected: bool
) -> None:
    host = {"langfuse_host": "https://langfuse.legalmove.internal"}
    settings = make_settings(app_env=app_env, parse_cache_enabled=explicit, **host)

    assert settings.is_parse_cache_enabled is expected


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-env")
    monkeypatch.setenv("VISION_DETAIL", "low")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key.get_secret_value() == "sk-env"
    assert settings.vision_detail == "low"


def test_load_settings_wraps_validation_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as raised:
        load_settings(env_file=tmp_path / "missing.env")

    assert raised.value.exit_code == 3
    assert raised.value.stage == "composition"


def test_configuration_error_does_not_echo_secret_values(tmp_path: Path) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text("OPENAI_API_KEY=sk-leak\nLLM_TEMPERATURE=9\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as raised:
        load_settings(env_file=env_file)

    assert "sk-leak" not in str(raised.value)


def test_unit_tests_cannot_see_shell_settings() -> None:
    assert "OPENAI_API_KEY" in settings_env_names()
    assert "PARSE_CACHE_TTL_HOURS" in settings_env_names()
    assert not [name for name in settings_env_names() if name in os.environ]
