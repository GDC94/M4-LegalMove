"""The API's documented contract: response models, refusal details and their OpenAPI entries."""

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from change_details import ChangeDetail
from errors import ValidationIssue
from jobs import JobState
from models import AmendmentKind, ContractChangeOutput


class AnalysisCreated(BaseModel):
    """`202` body of `POST /analyses`."""

    model_config = ConfigDict(extra="forbid")

    id: str


class ApiProblem(BaseModel):
    """Body of every refusal: one sentence that never quotes a document."""

    model_config = ConfigDict(extra="forbid")

    detail: str


class ErrorEnvelope(BaseModel):
    """The CLI error envelope of plan §4.7; `issues` only on a Human Review."""

    model_config = ConfigDict(extra="forbid")

    error: str
    stage: str
    message: str
    run_id: str
    trace_id: str | None
    issues: list[ValidationIssue] | None = None


class AnalysisBody(BaseModel):
    """`GET /analyses/{id}`: the result fields only when `done`, `error` on the other two ends."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: JobState
    created_at: str = Field(json_schema_extra={"format": "date-time"})
    trace_id: str | None
    output: ContractChangeOutput | None = None
    changes: list[ChangeDetail] | None = None
    amendment_kind: AmendmentKind | None = None
    error: ErrorEnvelope | None = None


# The runtime `detail` of each refusal; the OpenAPI descriptions are built from the same strings.
UNAUTHORIZED_DETAIL = "missing or invalid API key"
NOT_FOUND_DETAIL = "analysis not found"
LENGTH_REQUIRED_DETAIL = "Content-Length is required"
TOO_LARGE_DETAIL = "each image must be at most {limit} bytes"
MISSING_IMAGE_DETAIL = "{name}: an image file is required"
RATE_LIMITED_DETAIL = "rate limit exceeded"
QUEUE_FULL_DETAIL = "the analysis queue is full; retry later"

# HTTPStatus is an IntEnum, so `REFUSALS[401]` finds these keys.
REFUSALS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: UNAUTHORIZED_DETAIL,
    HTTPStatus.NOT_FOUND: f"{NOT_FOUND_DETAIL} (unknown, expired or another key's)",
    HTTPStatus.LENGTH_REQUIRED: LENGTH_REQUIRED_DETAIL,
    HTTPStatus.CONTENT_TOO_LARGE: TOO_LARGE_DETAIL.format(limit="<limit>"),
    HTTPStatus.UNPROCESSABLE_CONTENT: (
        f"{MISSING_IMAGE_DETAIL.format(name='<field>')}, or `<field>: <why the image is invalid>`"
    ),
    HTTPStatus.TOO_MANY_REQUESTS: f"{RATE_LIMITED_DETAIL}; see Retry-After",
    HTTPStatus.SERVICE_UNAVAILABLE: f"{QUEUE_FULL_DETAIL}; see Retry-After",
}


# Any: FastAPI types `responses` as dict[int | str, dict[str, Any]], and dict is invariant.
def document_problems(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Documentation only: each refusal carries an ApiProblem, so the web client can parse it."""
    return {code: {"model": ApiProblem, "description": REFUSALS[code]} for code in codes}
