"""Eval-only settings: the runtime Settings plus the Judge model (plan §5.1, decision 32)."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator

from config import Settings, describe_problems
from errors import ConfigurationError


class EvalSettings(Settings):
    """Runtime Settings plus JUDGE_MODEL; the Judge never grades a Summary its own model wrote."""

    judge_model: str = Field(default="gpt-4.1", min_length=1)
    # The second Judge is optional: without a key the eval runs with the OpenAI Judge alone.
    anthropic_api_key: SecretStr | None = None
    claude_judge_model: str = Field(default="claude-opus-5-5", min_length=1)
    claude_judge_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    @model_validator(mode="after")
    def check_judge_differs_from_extraction(self) -> Self:
        if normalize_model_name(self.judge_model) == normalize_model_name(self.extraction_model):
            raise ValueError("JUDGE_MODEL must differ from EXTRACTION_MODEL")
        return self


def normalize_model_name(name: str) -> str:
    return name.strip().casefold()


def load_eval_settings(env_file: Path | None = Path(".env")) -> EvalSettings:
    """Build EvalSettings or raise ConfigurationError naming only the offending fields."""
    try:
        return EvalSettings(_env_file=env_file)
    except ValidationError as error:
        # Drop the cause: pydantic errors echo input values, which may be secrets.
        raise ConfigurationError(f"invalid eval settings: {describe_problems(error)}") from None
