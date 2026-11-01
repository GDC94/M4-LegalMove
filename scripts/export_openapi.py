"""Writes the API's OpenAPI to `web/openapi.json`, offline: no `.env`, no Langfuse (ticket 16c)."""

import argparse
import json
from pathlib import Path

from api import ApiRuntime, create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "web/openapi.json"


def refuse_runtime() -> ApiRuntime:
    """The schema never runs the lifespan; reaching this would mean reading settings."""
    raise RuntimeError("exporting the OpenAPI must not build the API runtime")


def render_openapi() -> str:
    """Stable bytes (fixed indent, trailing newline) so CI can `git diff --exit-code` them."""
    document = create_app(build_runtime=refuse_runtime).openapi()
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    output: Path = parser.parse_args().output
    output.write_text(render_openapi(), encoding="utf-8")


if __name__ == "__main__":
    main()
