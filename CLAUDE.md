# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Model Context Protocol (MCP) server that provides AI assistants with access to Zendesk Support tickets and Help Center articles. The server exposes tools, prompts, and resources through the MCP protocol.

The server supports two modes of operation:
- **Local Mode**: Uses stdio transport with API key authentication (for development/single-user)
- **Remote Mode**: Uses HTTP transport with OAuth 2.0 authentication (for production/multi-user)

## Build and Development Commands

The project uses `uv` for Python package management:

- **Install dependencies**: `uv venv && uv pip install -e .`
- **Build package**: `uv build`
- **Run local server**: `uv run zendesk` (API key auth, stdio transport)
- **Run remote server**: `uv run zendesk-remote` (OAuth auth, HTTP transport)

## Configuration

### Local Mode (API Key Authentication)

For single-user or development use. Configure in `.env`:
```
ZENDESK_SUBDOMAIN=xxx
ZENDESK_EMAIL=xxx
ZENDESK_API_KEY=xxx
```

### Remote Mode (OAuth 2.0 Authentication)

For multi-user production deployment. Configure in `.env`:
```
# OAuth App (register at Admin Center > Apps > APIs > OAuth clients)
ZENDESK_OAUTH_CLIENT_ID=xxx
ZENDESK_OAUTH_CLIENT_SECRET=xxx
ZENDESK_OAUTH_REDIRECT_URI=https://your-server.com/oauth/callback
ZENDESK_OAUTH_SCOPES=read,write

# Server Configuration
MCP_SERVER_HOST=0.0.0.0
MCP_SERVER_PORT=8080
MCP_SERVER_BASE_URL=https://your-server.com

# Security (required for production)
TOKEN_ENCRYPTION_KEY=xxx  # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DATABASE_URL=sqlite+aiosqlite:///zendesk_mcp_tokens.db
```

See `.env.example` for the complete template.

## Architecture

### Project Structure

```
src/zendesk_mcp_server/
├── __init__.py          # Entry points: main() and main_remote()
├── config.py            # Configuration management (API key vs OAuth)
├── server.py            # Local MCP server (stdio transport)
├── server_http.py       # Remote MCP server (HTTP transport + OAuth)
├── zendesk_client.py    # Zendesk API wrapper (supports both auth modes)
├── oauth/
│   ├── __init__.py
│   ├── provider.py      # Zendesk OAuth 2.0 flow with PKCE
│   ├── storage.py       # Token storage (SQLite with encryption)
│   └── discovery.py     # MCP OAuth discovery endpoints (RFC 9728)
└── auth/
    ├── __init__.py
    └── middleware.py    # FastAPI auth middleware for HTTP mode
```

### Three-Layer Structure (Local Mode)

1. **server.py** - Main MCP server implementation
   - Defines MCP handlers for tools, prompts, and resources
   - Routes requests to the Zendesk client
   - Implements caching strategy with TTL-based decorators

2. **zendesk_client.py** - Zendesk API wrapper
   - Thin wrapper around the `zenpy` library
   - Supports both API token and OAuth authentication
   - Handles all direct communication with Zendesk API
   - Returns dictionaries for serialization

3. **__init__.py** - Entry point
   - `main()` - Local stdio server with API key auth
   - `main_remote()` - HTTP server with OAuth auth

### OAuth Architecture (Remote Mode)

The remote server implements OAuth 2.0 with the following components:

1. **ZendeskOAuthProvider** (`oauth/provider.py`)
   - Implements Authorization Code flow with PKCE (required by Zendesk Feb 2025)
   - Manages authorization state and CSRF protection
   - Handles token exchange and refresh

2. **TokenStorage** (`oauth/storage.py`)
   - SQLite-based storage with Fernet encryption
   - Stores access/refresh tokens per user session
   - Supports token refresh and cleanup

3. **OAuth Discovery** (`oauth/discovery.py`)
   - `/.well-known/oauth-protected-resource` (RFC 9728)
   - `/.well-known/oauth-authorization-server` (RFC 8414)

4. **AuthMiddleware** (`auth/middleware.py`)
   - Extracts Bearer tokens from requests
   - Validates sessions and injects into request context
   - Handles 401 responses with WWW-Authenticate headers

### OAuth Flow

```
1. Client → GET /oauth/authorize?subdomain=xxx
2. Server → Redirect to Zendesk authorization
3. User authorizes in Zendesk
4. Zendesk → Redirect to /oauth/callback?code=xxx&state=xxx
5. Server exchanges code for tokens (with PKCE verifier)
6. Server stores encrypted tokens, returns session_id
7. Client uses session_id as Bearer token for MCP requests
```

### MCP Protocol Implementation

The server implements three MCP primitives:

- **Tools**: Direct API operations including:
  - Tickets: `get_ticket`, `get_multiple_tickets`, `get_ticket_comments`, `create_ticket_comment`
  - Knowledge Base: `search_kb_articles`, `get_kb_article`, `list_kb_sections`, `get_section_articles`
  - Attachments: `get_attachment`
  - Macros: `search_macros`, `get_macro`, `apply_macro_to_ticket`
- **Prompts**: Templated workflows (analyze-ticket, draft-ticket-response)
- **Resources**: URI-based access to knowledge base metadata (zendesk://knowledge-base)

### Caching Strategy

Knowledge base operations use TTL caching to reduce API calls:
- Sections list: 2 hours (`@ttl_cache(ttl=7200)`)
- Individual articles: 1 hour (`@ttl_cache(ttl=3600)`)
- Search results: 15 minutes (`@ttl_cache(ttl=900)`)

Ticket operations are not cached as they need real-time data.

### Search-First Knowledge Base Design

The knowledge base resource returns only metadata (section list). Users are directed to use `search_kb_articles` tool for article discovery. This prevents loading excessive content into context and improves performance.

## Key Design Patterns

- All Zendesk client methods return dictionaries (not zenpy objects) for JSON serialization
- Long article bodies are truncated to 1000 characters in list views; full content available via `get_kb_article`
- Error handling returns user-friendly error messages through MCP TextContent
- Logging uses standard Python logging with INFO level for operational visibility

### Attachment Handling

The server supports ticket attachments (images, PDFs, documents):

- **Metadata in comments**: Attachment info (ID, filename, type, size, URL) is included in `get_ticket_comments` responses
- **On-demand fetching**: Use `get_attachment` tool to download specific attachments
- **Native image support**: Images returned as MCP `ImageContent` for direct viewing by multimodal AI
- **Non-image files**: PDFs/documents returned as base64-encoded data in JSON
- **Inline images**: Optional `include_inline_images` parameter to fetch inline attachments

**Workflow:**

**Option 1: Manual (token-efficient)**
1. Call `get_ticket_comments(ticket_id)` to see attachment metadata
2. Identify relevant attachments (check `is_image` flag and `content_type`)
3. Call `get_attachment(attachment_id)` to view/download specific files

**Option 2: Automatic inline images**
1. Call `get_ticket_comments(ticket_id, include_inline_images=true)`
2. All image attachments are automatically fetched and returned as `ImageContent`
3. Images display natively in multimodal AI clients

**Token efficiency**:
- Manual mode: Only download what you need (~99% savings for large tickets)
- Automatic mode: Best for tickets with few images (<10) where visual context is essential

### Macros Support

The server supports Zendesk macros for automated ticket actions:

- **Search macros**: Use `search_macros(query, limit)` to find macros by title/keyword
- **Get macro details**: Use `get_macro(macro_id)` to retrieve full macro configuration
- **Apply to tickets**: Use `apply_macro_to_ticket(ticket_id, macro_id)` to apply macro actions

**Important Implementation Notes:**

1. **Zendesk API requires non-empty query** for macro search - empty strings return 400 Bad Request
2. **Direct HTTP requests pattern**: Some Zendesk operations don't work well with zenpy's abstraction layer. For these cases, use direct HTTP requests via the authenticated session:
   ```python
   url = f"https://{self.client.macros.base_url}/api/v2/macros/search.json?query={encoded_query}"
   response = self.client.macros.session.get(url, timeout=self.client.macros.timeout)
   response.raise_for_status()
   data = response.json()
   ```
   This pattern is used in: `search_macros()`, `get_macro()`

3. **TicketAudit objects**: `client.tickets.update()` returns a `TicketAudit` object (not a Ticket). Access the ticket via `ticket_audit.ticket`

4. **Macro application process**: Uses Zendesk's two-step pattern:
   - Step 1: Preview changes with `show_macro_effect(ticket_id, macro_id)` → returns `MacroResult`
   - Step 2: Apply changes with `tickets.update(macro_result.ticket)` → returns `TicketAudit`

**Testing:**
- `test_search_macros.py` - Tests search and get operations
- `test_apply_macro.py` - Interactive script for applying macros to tickets

### Bulk Ticket Retrieval

The server supports retrieving multiple tickets in a single API call:

- **Get multiple tickets**: Use `get_multiple_tickets(ticket_ids, include_comments)` to fetch up to 100 tickets at once
- **Efficient for batch operations**: Customer service staff can review multiple tickets simultaneously
- **Optional comments inclusion**: Set `include_comments=true` to fetch full conversation history in one call

**Important Implementation Notes:**

1. **Accepts array of ticket IDs**: Pass a list of integers `[123, 456, 789]` instead of calling single ticket endpoint multiple times
2. **Maximum 100 tickets per call**: Zendesk API limitation - requests exceeding 100 tickets will be rejected
3. **Direct HTTP requests pattern**: Uses the `/api/v2/tickets/show_many.json?ids={ids}` endpoint via authenticated session:
   ```python
   ids_param = ','.join(str(tid) for tid in ticket_ids)
   url = f"https://{self.client.tickets.base_url}/api/v2/tickets/show_many.json?ids={ids_param}"
   response = self.client.tickets.session.get(url, timeout=self.client.tickets.timeout)
   ```
4. **Parallel comments fetching**: When `include_comments=true`, the method uses `ThreadPoolExecutor` with max 10 workers to fetch comments in parallel for better performance. Failed comment fetches log a warning but don't fail the entire request.
   ```python
   with ThreadPoolExecutor(max_workers=10) as executor:
       future_to_ticket = {executor.submit(fetch_comments_for_ticket, ticket): ticket for ticket in tickets}
       for future in as_completed(future_to_ticket):
           ticket_with_comments = future.result()
   ```

**Use Cases:**
- Batch ticket review workflows for customer service teams (use `include_comments=true`)
- Bulk ticket analysis and reporting (summaries only, `include_comments=false`)
- Multi-ticket operations when Claude Desktop doesn't support parallel tool calls

**Testing:**
- `test_get_multiple_tickets.py` - Interactive script for testing bulk ticket retrieval

## Common Pitfalls & Solutions

### Zenpy Abstraction Issues

**Problem**: Using zenpy's high-level methods (e.g., `self.client.macros(id=X)`) may fail with cryptic errors like "'str' object has no attribute 'scheme'" or pagination issues.

**Solution**: Use direct HTTP requests via the authenticated session:
```python
url = f"https://{self.client.macros.base_url}/api/v2/endpoint.json"
response = self.client.macros.session.get(url, timeout=self.client.macros.timeout)
response.raise_for_status()
data = response.json()
```

The session object is available on any zenpy API object (e.g., `self.client.macros.session`, `self.client.tickets.session`) and is already authenticated.

### TicketAudit vs Ticket Objects

**Problem**: After updating a ticket with `client.tickets.update(ticket)`, attempting to access ticket attributes directly (e.g., `result.id`) fails.

**Solution**: The `update()` method returns a `TicketAudit` object, not a `Ticket`. Extract the ticket:
```python
ticket_audit = self.client.tickets.update(ticket)
actual_ticket = ticket_audit.ticket
# Now access actual_ticket.id, actual_ticket.status, etc.
```

### Empty Query Strings

**Problem**: Zendesk's macro search endpoint returns 400 Bad Request with empty query strings.

**Solution**: Always validate that search queries are non-empty before making API calls. Provide a default query or handle empty queries at the application level.
