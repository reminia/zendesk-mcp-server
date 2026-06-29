# Zendesk MCP Server — Agent Guide

## Quick start

```sh
uv venv && uv pip install -e .
cp .env.example .env   # fill in ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_KEY
zendesk                 # runs via stdin/stdout (stdio transport)
```

## Commands

| Command | What it does |
|---|---|
| `uv build` | Build (CI only — no lint, test, or typecheck exists) |
| `uv run zendesk` | Run the server locally |
| `docker build -t zendesk-mcp-server .` | Containerized build (uses `requirements.lock`) |

No test runner, linter, formatter, or type checker is configured. CI only runs `uv build`.

## Architecture

- **Single package** at `src/zendesk_mcp_server/` (3 files: `__init__.py`, `server.py`, `zendesk_client.py`)
- **Entrypoint**: console script `zendesk` → `zendesk_mcp_server:main`
- **Framework**: MCP Python SDK 1.1.2 (`stdio_server()` transport)
- **Zendesk lib**: `zenpy` 2.0.56 for most API calls; direct `urllib`/`requests` for `get_tickets` (zenpy lacks offset pagination) and `get_ticket_attachment`
- **Tools (8)**: get_ticket, get_tickets, get_ticket_comments, get_ticket_attachment, get_ticket_fields, create_ticket, create_ticket_comment, update_ticket
- **Prompts (2)**: analyze-ticket, draft-ticket-response
- **Resources (1)**: `zendesk://knowledge-base` (Help Center articles, 1hr TTL cache)

## Quirks & gotchas

- `post_comment` accepts **HTML** via `html_body`, not plain text.
- `update_ticket` loads the ticket, mutates attrs, calls `client.tickets.update()`, then re-fetches — the update call returns a `TicketAudit`, not a `Ticket`.
- `get_tickets` uses Zendesk offset-pagination (`page`/`per_page`), **not** cursor. Capped at 100 per page.
- Attachment fetch validates MIME type against an allowlist + magic bytes + 10 MB cap. SVG is explicitly excluded.
- Server patches MCP SDK's `_receive_loop` to suppress `ValidationError` from `notifications/cancelled` messages (a crash fix).
- Knowledge base data is cached at module level with `@ttl_cache(ttl=3600)`. No manual invalidation.
- Environment: Python ≥3.12, `.env` loaded via `python-dotenv` at import time.
- `.gitignore` excludes `AGENT.md` (singular) — `AGENTS.md` (plural) is not git-ignored.
