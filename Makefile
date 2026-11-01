# Local shortcuts for the HTTP API and the web front end. Both talk to OpenAI and Langfuse.
.PHONY: api web dev

# API on http://127.0.0.1:8000; needs API_KEYS and the usual keys in .env.
api:
	uv run uvicorn --app-dir src api:app

# Front end on http://localhost:5173; needs VITE_API_KEY in web/.env.local.
web:
	cd web && pnpm install --frozen-lockfile && pnpm dev

# Both at once; Ctrl-C stops both.
dev:
	@trap 'kill 0' INT TERM; \
	$(MAKE) --no-print-directory api & \
	$(MAKE) --no-print-directory web & \
	wait
