# LegalMove

CLI that reads two scanned contract images (original and amendment) and returns a validated JSON with the legal changes introduced by the amendment. `plan.md` is the source of truth for the design; `requirements.md` is the primary source of requirements. Tickets live in `.scratch/legalmove/issues/`; if a ticket and the plan differ, the ticket wins.

## Commands

- `uv run pytest` runs unit tests; integration tests are excluded by default.
- `uv run pytest -m integration` runs integration tests against real OpenAI and Langfuse. Only on the author's explicit request.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy src` is the style gate; must be green before every commit.
- `uv run python src/main.py <original.png> <amendment.png>` runs the pipeline.
- `uv add <pkg>` or `uv add --dev <pkg>`, never `pip`. Pin with `==` (`uv add 'pkg==1.2.3'`). Then `uv export --format requirements-txt --no-hashes --no-emit-project --no-dev > requirements.txt`.

## Way of working

- One ticket at a time. Read `plan.md` and the ticket before writing code.
- Strict TDD: show the failing test before the implementation, then the minimal code, then refactor.
- A ticket is closed when unit tests, the style gate and every acceptance criterion are green, with one conventional commit on `main`.
- The author reviews each closed ticket before the next one starts.
- Changes to `plan.md` are discussed in chat before they are written.
- Never call OpenAI or Langfuse (integration tests, evals, manual runs) unless the author asks for it in that turn. Unit tests and the style gate run without asking.
- CI (`.github/workflows/ci.yml`, from T4) runs the style gate and unit tests on every push. It has no API secrets and never runs integration tests or evals.

## Architecture

**Pattern: lightweight Ports & Adapters (hexagonal) on a flat `src/` layout.** We adopt the dependency direction, ports as `Protocol`, and a single composition root. We do not adopt `domain/application/infrastructure` folders: the assignment fixes flat paths and the flow is a single linear four-stage use case.

| Layer | Modules | Rule |
|---|---|---|
| Domain | `models.py`, `checks.py`, `errors.py`, `prompt_blocks.py` | Pydantic and deterministic logic. Never import LangChain, OpenAI, Langfuse or `config.py` |
| Application | `pipeline.py` (`run_analysis()`) | Orchestrates the stages through the ports. Knows nothing about Langfuse or settings |
| Ports | `PipelineTracer` and `StageSpan` (`Protocol`, plan §5.2); the `chat_model` each `build_*` factory receives | Business modules depend on the abstraction, never on the provider |
| Adapters | `observability.py` (Langfuse), `image_parser.py` and `agents/` (OpenAI via LangChain) | The only modules that touch external infrastructure |
| Composition root | `main.py` | The only module that calls `load_settings()`, builds the frozen dataclass container and wires adapters to ports |

Dependency rule: domain ← application ← adapters ← `main.py`. Arrows only point inward; a module never imports from a more outer layer. AST-based architecture tests (plan §6.2) enforce these rules in CI.

Practical consequences when implementing a ticket:

- A new external dependency (API, SDK, filesystem cache) goes behind a port or inside an existing adapter, never into `models.py`, `checks.py` or `pipeline.py`.
- Every module exposes a `build_*(chat_model, ...)` factory; only `main.py` calls them.
- Tests replace adapters with recording doubles (`RecordingTracer`, recording chat models); unit tests run without network or `.env`.

## Code style

Style is enforced by tools, not by review. This command must be green before every commit:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

- Full modern typing: `X | None`, `Literal`, `StrEnum`, `Protocol`. `mypy --strict` with the `pydantic.mypy` plugin; no `Any` or `type: ignore` without a comment saying why.
- Functions are short and do one thing. `ruff` fails the build above these limits: cyclomatic complexity 8 (`C901`), 25 statements (`PLR0915`), 5 arguments (`PLR0913`), 8 branches (`PLR0912`), 4 returns (`PLR0911`). Do not raise the limits; split the function.
- Names are verb + object for functions (`parse_contract_image`, `render_summary`), `PascalCase` for classes and enums, `snake_case` elsewhere. If a function name needs "and", it is two functions. Use the exact vocabulary of `CONTEXT.md`.
- One-line docstring per module (`D100`). Comments explain why, never what.
- Small modules with one responsibility. A new provider is a new adapter behind the existing port; domain and `pipeline.py` do not change (open/closed). Doubles must satisfy the same `Protocol` as the real adapter (Liskov). Ports stay minimal and separate (`PipelineTracer` vs `StageSpan`).
- Tests may ignore `PLR2004` (magic values); nothing else is ignored per file.

## Known traps

- `uv export` includes the `dev` group by default, which would put playwright, jiwer, pytest, ruff and mypy into `requirements.txt`. Always pass `--no-dev`; `tests/test_requirements.py` fails otherwise.
- `uv add` writes `>=` constraints by default. Pass the version explicitly (`'pkg==1.2.3'`); the requirements test rejects any declared dependency not pinned with `==`.
- LangChain's fake chat models do not support `with_structured_output`. Put the injection seam above that call and cover structured behavior with integration tests (plan §11).
- The project is not installable (no `[build-system]`). Imports are absolute from `src/` (`from models import ...`); pytest finds them through `pythonpath = ["src"]`, never through relative imports or `sys.path` hacks.
- `ruff check .` also lints `tests/`: every test module needs a one-line module docstring (`D100`).
- The real `.env` holds secrets. Never print, stage or commit it; `.gitignore` covers it and `tests/test_gitignore.py` guards that.

## Git and GitHub

- Remote: `https://github.com/GDC94/M4-LegalMove` (public, created by the author on 2026-10-25). `origin` is added in T0.
- Always push as the GitHub account **GDC94**. The machine has a second `gh` account; before any push run `gh auth status` and confirm `GDC94` is the active account. If it is not, run `gh auth switch --user GDC94` first. Never push from another account.
- Branch `main`, conventional commits, one commit per closed ticket, pushed when the ticket closes.
- No AI attribution lines in commits.

## Language

- English: code, identifiers, comments, tests, prompts, `CONTEXT.md` and `README.md`.
- Spanish: `plan.md`, domain documents in `data/`, and explanations to the author.
