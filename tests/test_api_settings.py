"""ApiSettings: the runtime Settings plus the API-only knobs, as EvalSettings does (decision 32)."""

import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

from api_settings import ApiSettings, load_api_settings
from config import Settings
from conftest import settings_env_names
from errors import ConfigurationError

REQUIRED = {
    "openai_api_key": "sk-test",
    "langfuse_public_key": "pk-lf-test",
    "langfuse_secret_key": "sk-lf-test",
    "api_keys": "key-one",
}
API_ENV_NAMES = (
    "API_KEYS",
    "API_MAX_CONCURRENT_JOBS",
    "API_MAX_PENDING_JOBS",
    "API_RATE_LIMIT_REQUESTS",
    "API_RATE_LIMIT_WINDOW_SECONDS",
    "API_MAX_UPLOAD_BYTES",
    "API_JOB_TTL_SECONDS",
)


def make_api_settings(**overrides: Any) -> ApiSettings:
    return ApiSettings(_env_file=None, **{**REQUIRED, **overrides})


def test_defaults_match_the_ticket() -> None:
    settings = make_api_settings()

    assert isinstance(settings, Settings)
    assert settings.api_max_concurrent_jobs == 2
    assert settings.api_max_pending_jobs == 10
    assert settings.api_rate_limit_requests == 10
    assert settings.api_rate_limit_window_seconds == 60
    assert settings.api_job_ttl_seconds == 3600


def test_the_upload_limit_defaults_to_the_image_limit_and_can_be_overridden() -> None:
    assert make_api_settings(max_image_bytes=5_000).max_upload_bytes == 5_000
    assert make_api_settings(api_max_upload_bytes=7_000).max_upload_bytes == 7_000


def test_the_upload_limit_never_exceeds_the_image_limit() -> None:
    # Above the image limit an upload would pass the 413 check and fail later as a 422.
    settings = make_api_settings(max_image_bytes=5_000, api_max_upload_bytes=7_000)

    assert settings.max_upload_bytes == 5_000


def test_keys_are_split_on_commas_and_stored_as_sha256() -> None:
    settings = make_api_settings(api_keys=" key-one , key-two ,,")

    assert settings.key_hashes == (
        hashlib.sha256(b"key-one").hexdigest(),
        hashlib.sha256(b"key-two").hexdigest(),
    )


def test_keys_are_not_revealed_in_repr() -> None:
    assert "key-one" not in repr(make_api_settings())


def test_without_api_keys_the_settings_do_not_build() -> None:
    values = {name: value for name, value in REQUIRED.items() if name != "api_keys"}

    with pytest.raises(ValidationError, match="api_keys"):
        ApiSettings(_env_file=None, **values)


@pytest.mark.parametrize("blank", ["", "  ", " , ,"])
def test_blank_keys_are_rejected(blank: str) -> None:
    with pytest.raises(ValidationError, match="API_KEYS"):
        make_api_settings(api_keys=blank)


@pytest.mark.parametrize(
    "name",
    [
        "api_max_concurrent_jobs",
        "api_max_pending_jobs",
        "api_rate_limit_requests",
        "api_rate_limit_window_seconds",
        "api_max_upload_bytes",
        "api_job_ttl_seconds",
    ],
)
def test_the_integer_knobs_must_be_positive(name: str) -> None:
    with pytest.raises(ValidationError):
        make_api_settings(**{name: 0})


def test_the_runtime_settings_do_not_know_the_api_keys() -> None:
    assert "api_keys" not in Settings.model_fields


def test_load_api_settings_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("API_MAX_CONCURRENT_JOBS", "4")

    settings = load_api_settings(env_file=None)

    assert settings.api_max_concurrent_jobs == 4


def test_load_api_settings_names_only_the_missing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        if name != "api_keys":
            monkeypatch.setenv(name.upper(), value)

    with pytest.raises(ConfigurationError) as caught:
        load_api_settings(env_file=None)

    assert "API_KEYS" in str(caught.value)
    assert "sk-test" not in str(caught.value)


def test_the_shared_isolation_clears_every_api_and_eval_variable() -> None:
    # An exported API_RATE_LIMIT_REQUESTS=1 once leaked into the API tests through ApiSettings.
    assert {*API_ENV_NAMES, "JUDGE_MODEL"} <= set(settings_env_names())
