"""Runtime settings loaded from the environment and validated before any API call."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from errors import ConfigurationError
from models import VisionDetail

APP_VERSION = "0.1.0"

# Public Langfuse Cloud, in any region, is only acceptable with synthetic data (decision 49).
PUBLIC_LANGFUSE_DOMAIN = "cloud.langfuse.com"

AppEnv = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Every runtime knob of plan §5.1; secrets stay as SecretStr."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr
    langfuse_public_key: SecretStr
    langfuse_secret_key: SecretStr
    langfuse_host: str = "https://cloud.langfuse.com"
    app_env: AppEnv = "development"
    vision_model: str = Field(default="gpt-4o", min_length=1)
    contextualization_model: str = Field(default="gpt-4o", min_length=1)
    extraction_model: str = Field(default="gpt-4o", min_length=1)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    vision_detail: VisionDetail = "high"
    llm_timeout_seconds: int = Field(default=60, ge=1)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    pipeline_timeout_seconds: int = Field(default=300, ge=1)
    max_image_bytes: int = Field(default=10_000_000, ge=1)
    min_image_side_px: int = Field(default=600, ge=1)
    max_image_side_px: int = Field(default=4096, ge=1)
    max_image_pixels: int = Field(default=16_777_216, ge=1)
    illegible_max_marks: int = Field(default=10, ge=0)
    parse_cache_dir: Path = Path(".cache/parsed")
    parse_cache_enabled: bool | None = None
    parse_cache_ttl_hours: int = Field(default=24, ge=1)
    log_level: LogLevel = "INFO"

    @field_validator("langfuse_host")
    @classmethod
    def check_host_is_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LANGFUSE_HOST must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def check_pipeline_budget(self) -> Self:
        if self.pipeline_timeout_seconds < self.llm_timeout_seconds:
            raise ValueError("PIPELINE_TIMEOUT_SECONDS must be >= LLM_TIMEOUT_SECONDS")
        return self

    @model_validator(mode="after")
    def check_image_sides(self) -> Self:
        if self.max_image_side_px < self.min_image_side_px:
            raise ValueError("MAX_IMAGE_SIDE_PX must be >= MIN_IMAGE_SIDE_PX")
        return self

    @model_validator(mode="after")
    def check_production_host(self) -> Self:
        if self.app_env == "production" and is_public_langfuse_cloud(self.langfuse_host):
            raise ValueError("LANGFUSE_HOST must be an approved private host in production")
        return self

    @property
    def is_parse_cache_enabled(self) -> bool:
        """Explicit choice wins; otherwise only development caches sensitive parsed text."""
        if self.parse_cache_enabled is not None:
            return self.parse_cache_enabled
        return self.app_env == "development"


def is_public_langfuse_cloud(url: str) -> bool:
    host = (urlparse(url).hostname or "").rstrip(".")  # hostname is already lower-cased
    return host == PUBLIC_LANGFUSE_DOMAIN or host.endswith(f".{PUBLIC_LANGFUSE_DOMAIN}")


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Build Settings or raise ConfigurationError naming only the offending fields."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        # Drop the cause: pydantic errors echo input values, which may be secrets.
        raise ConfigurationError(f"invalid settings: {describe_problems(error)}") from None


def describe_problems(error: ValidationError) -> str:
    """Name offending fields, or the cross-field rule message, never the values."""
    problems = {
        str(item["loc"][0]).upper() if item["loc"] else item["msg"] for item in error.errors()
    }
    return "; ".join(sorted(problems))
