"""API-only settings: `ApiSettings`, its loader and the sha256 key digests."""

import hashlib
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator

from config import Settings, describe_problems
from errors import ConfigurationError


class ApiSettings(Settings):
    """Runtime Settings plus the API-only knobs; API_KEYS does not exist for the CLI."""

    api_keys: SecretStr
    api_max_concurrent_jobs: int = Field(default=2, ge=1)
    api_max_pending_jobs: int = Field(default=10, ge=1)
    api_rate_limit_requests: int = Field(default=10, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    api_max_upload_bytes: int | None = Field(default=None, ge=1)
    api_job_ttl_seconds: int = Field(default=3600, ge=1)

    @field_validator("api_keys")
    @classmethod
    def check_there_is_a_key(cls, value: SecretStr) -> SecretStr:
        if not split_keys(value.get_secret_value()):
            raise ValueError("API_KEYS must hold at least one key")
        return value

    @property
    def key_hashes(self) -> tuple[str, ...]:
        """Keys are only ever kept as sha256 digests."""
        return tuple(hash_api_key(key) for key in split_keys(self.api_keys.get_secret_value()))

    @property
    def max_upload_bytes(self) -> int:
        """Capped at the image limit, so an oversized image is always a 413, never a 422."""
        return min(self.api_max_upload_bytes or self.max_image_bytes, self.max_image_bytes)


def split_keys(raw: str) -> list[str]:
    return [key.strip() for key in raw.split(",") if key.strip()]


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_api_settings(env_file: Path | None = Path(".env")) -> ApiSettings:
    """Build ApiSettings or raise ConfigurationError naming only the offending fields."""
    try:
        # pyright synthesizes __init__ from the fields and misses pydantic-settings' _env_file.
        return ApiSettings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        # Drop the cause: pydantic errors echo input values, which may be secrets.
        raise ConfigurationError(f"invalid API settings: {describe_problems(error)}") from None
