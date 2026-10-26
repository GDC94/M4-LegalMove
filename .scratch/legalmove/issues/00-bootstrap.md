# 00: Bootstrap — repo, dependencias fijadas y documentación que gobierna el trabajo

**Status:** ready-for-agent

**Fase:** 1 — entrega completa de la consigna

**Bloqueado por:** Nada

## Resumen

Se arma la casa antes de meter muebles. Carpeta limpia, lista de piezas con número exacto de versión. Cuaderno de reglas para quien trabaje después. Llaves secretas quedan fuera del repositorio. Quien baje el proyecto lo prueba y todo sale en verde.

## Resultado vertical

Un clon limpio hace `uv sync && uv run pytest` en verde y `requirements.txt` está fijado y exportado desde `uv.lock`. Existen `CLAUDE.md` y `CONTEXT.md`.

## Cobertura de la consigna

- Requisitos: R21 (parte: `.env` ignorado)
- Entregables: E08, E09 (template; se completa en T1)

## Secciones de plan.md que gobiernan el trabajo

No se duplica la arquitectura aquí: la fuente de verdad es `plan.md`; `requirements.md` es la fuente primaria de requisitos.

- §1 (layout, imports, arquitectura, fases)
- §5.1 (lista de variables para `.env.example`)
- §6.9
- §8.1 (contenido literal de `CONTEXT.md`)
- §8.3 (`CLAUDE.md`)
- §10
- §11

Decisiones del registro §8.4: 1, 27, 33, 35, 61, 62, 63, 66

## Criterios de aceptación

- [ ] `pyproject.toml` no tiene `[build-system]` y define `pythonpath = ["src"]`.
- [ ] `pyproject.toml` define `addopts = "--strict-markers -m 'not integration'"` y el marker `integration`.
- [ ] Las dependencias de runtime de §10 y el grupo `dev` (pytest, playwright, jiwer, ruff, mypy) están declarados.
- [ ] `pyproject.toml` configura `ruff` (conjuntos de reglas y límites por función de §11) y `mypy --strict` con el plugin `pydantic.mypy`; `uv run ruff check . && uv run ruff format --check . && uv run mypy src` está en verde.
- [ ] Existe `src/main.py` mínimo: solo el docstring de módulo de una línea (`D100`), sin lógica. Es el único archivo de `src/` en T0 y existe para que `uv run mypy src` tenga qué verificar; T1 lo completa como composition root.
- [ ] `requirements.txt` existe, está fijado y no incluye playwright.
- [ ] `.gitignore` sigue §6.9 e incluye además `.cache/`.
- [ ] `.env.example` contiene todas las variables de §5.1 y la sección opcional "evals".
- [ ] `git init` hecho, rama `main`.
- [ ] `git log` muestra un commit convencional.
- [ ] `origin` apunta a `https://github.com/GDC94/M4-LegalMove` y el commit está pusheado con la cuenta GDC94 (`gh auth status` la muestra activa antes del push).
- [ ] Existen `CLAUDE.md` y `CONTEXT.md` (este último con el contenido literal de §8.1). `CLAUDE.md` ya existe con la sección "Architecture" (§1); T0 la conserva y completa el resto según §8.3.

## Tests requeridos (TDD estricto)

**RED (se escriben primero y fallan):** `tests/test_requirements.py` (cada dependencia declarada fijada con `==`, `requirements.txt` existe), `tests/test_gitignore.py` (`.env`, `.cache/` ignorados).

**GREEN (lo mínimo que los hace pasar):** `uv add` + `uv export --format requirements-txt --no-hashes --no-emit-project --no-dev`.

## Fuera de alcance

- Cualquier archivo en `src/` salvo el `src/main.py` mínimo (mypy falla si `src/` no existe o está vacía).
- README.

## Comments
