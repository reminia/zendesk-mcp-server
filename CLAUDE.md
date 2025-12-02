# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Model Context Protocol (MCP) server that provides AI assistants with access to Zendesk Support tickets and Help Center articles. The server exposes tools, prompts, and resources through the MCP protocol.

## Build and Development Commands

The project uses `uv` for Python package management:

- **Install dependencies**: `uv venv && uv pip install -e .`
- **Build package**: `uv build`
- **Run server**: `uv run zendesk` (requires `.env` configuration)

## Configuration

The server requires Zendesk credentials in a `.env` file:
```
ZENDESK_SUBDOMAIN=xxx
ZENDESK_EMAIL=xxx
ZENDESK_API_KEY=xxx
```

See `.env.example` for the template.

## Architecture

### Three-Layer Structure

1. **server.py** - Main MCP server implementation
   - Defines MCP handlers for tools, prompts, and resources
   - Routes requests to the Zendesk client
   - Implements caching strategy with TTL-based decorators

2. **zendesk_client.py** - Zendesk API wrapper
   - Thin wrapper around the `zenpy` library
   - Handles all direct communication with Zendesk API
   - Returns dictionaries for serialization

3. **__init__.py** - Entry point
   - Provides the `main()` function that starts the asyncio server

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

- **Get multiple tickets**: Use `get_multiple_tickets(ticket_ids)` to fetch up to 100 tickets at once
- **Efficient for batch operations**: Customer service staff can review multiple tickets simultaneously
- **Full ticket data**: Returns complete ticket information including custom fields, tags, and metadata

**Important Implementation Notes:**

1. **Accepts array of ticket IDs**: Pass a list of integers `[123, 456, 789]` instead of calling single ticket endpoint multiple times
2. **Maximum 100 tickets per call**: Zendesk API limitation - requests exceeding 100 tickets will be rejected
3. **Direct HTTP requests pattern**: Uses the `/api/v2/tickets/show_many.json?ids={ids}` endpoint via authenticated session:
   ```python
   ids_param = ','.join(str(tid) for tid in ticket_ids)
   url = f"https://{self.client.tickets.base_url}/api/v2/tickets/show_many.json?ids={ids_param}"
   response = self.client.tickets.session.get(url, timeout=self.client.tickets.timeout)
   ```
   
**Use Cases:**
- Batch ticket review workflows for customer service teams
- Bulk ticket analysis and reporting
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
