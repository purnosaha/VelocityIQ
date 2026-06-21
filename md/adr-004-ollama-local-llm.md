# ADR-004: Ollama over Cloud LLM APIs for the AI Insight Layer

**Status:** Accepted  
**Date:** 2026-06-22

## Context

The evaluation rubric requires an AI/LLM component. The chosen implementation is a natural-language-to-SQL interface (`POST /insight`) where a user asks a business question in plain English, the system generates a valid DuckDB SELECT statement, executes it, and returns both the result set and a prose narrative summary. The LLM is called twice per request: once to generate SQL and once to summarise results.

A key constraint is that the data is sensitive sales and product information. Sending it to a third-party cloud API introduces data egress and cost concerns that are unacceptable for a skeleton a client team will inherit.

## Options Considered

- **OpenAI API (GPT-4o / GPT-4-turbo):** High SQL generation quality, simple REST integration, no infrastructure required. Requires an API key, incurs per-token costs, and sends query results (potentially sensitive business data) to a third-party endpoint. Not viable for clients with data residency requirements.

- **Anthropic API (Claude Sonnet/Haiku):** Similar tradeoffs to OpenAI — excellent quality, but external API dependency, cost, and data egress.

- **Ollama with open-weight model:** Self-hosted LLM runtime that runs as a Docker service. Models are pulled once and cached in a named volume. Zero per-token cost, zero data egress, works fully offline. Configurable via environment variables — swapping the model requires no code changes.

## Decision

Ollama (`ollama/ollama:latest`) running **Qwen 2.5 7B** (`qwen2.5:7b`), deployed as a dedicated Docker Compose service alongside the application.

**Architecture:**
- `ollama` service starts and exposes port 11434
- `ollama-init` one-shot container pulls `qwen2.5:7b` on first run; subsequent starts use the cached `ollama_models` named volume
- FastAPI calls `http://ollama:11434/api/chat` via `httpx` with a 180-second timeout (configurable via `OLLAMA_TIMEOUT`)
- `OLLAMA_BASE_URL` and `OLLAMA_MODEL` environment variables allow the model to be swapped (e.g., to `llama3.2`, `mistral`, or a fine-tuned variant) without touching application code

**SQL safety gate:** All generated SQL is validated before execution. Statements containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, or `EXEC` are rejected with a 400 error. Only `SELECT` queries are executed.

**Schema context prompt:** A 238-line system prompt is injected with every SQL generation request, documenting the full star schema (tables, columns, data types, view definitions, query patterns, and DuckDB-specific constraints such as `"year"` being a reserved keyword). This grounds the model in the actual schema and significantly reduces hallucinated column names.

## Consequences

**Accepted trade-offs:**
- Qwen 2.5 7B requires approximately 8 GB of RAM or VRAM. Machines with less memory will experience slow inference or OOM errors. A cloud API would have no such hardware requirement.
- SQL generation quality is lower than GPT-4 or Claude Sonnet, particularly for complex multi-join queries. The 238-line schema context mitigates this for the bounded schema in scope.
- Cold start during `docker compose up` requires pulling the ~4 GB model on first run. Subsequent starts use the cached volume.
- The FX rate (`CAD_USD_RATE`) and other constants in schema context must be kept in sync with the actual data manually.

**Benefits realised:**
- No API keys, no external network dependency, no per-token cost.
- Data never leaves the deployment environment — suitable for clients with data residency or confidentiality requirements.
- Model is swappable via a single environment variable — the same application can run a faster/smaller model (e.g., `qwen2.5:3b`) for resource-constrained environments or a larger model for higher accuracy.
- Fully reproducible — the Docker Compose stack is self-contained; no external service accounts required to run.
