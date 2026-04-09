# Zendesk MCP Server

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A [Model Context Protocol](https://modelcontextprotocol.io/) server for Zendesk, maintained by [Alternative Payments](https://github.com/getalternative). Security-patched fork of [reminia/zendesk-mcp-server](https://github.com/reminia/zendesk-mcp-server).

## What It Does

Connects Claude (or any MCP client) to your Zendesk instance with **24 tools**, prompt templates, and knowledge base access.

## Setup

### 1. Install

```bash
uv venv && uv pip install -e .
```

### 2. Configure credentials

Copy `.env.example` to `.env` and configure one of the auth methods:

**Option A -- API token (recommended for team use):**
```env
ZENDESK_SUBDOMAIN=yourcompany
ZENDESK_EMAIL=you@company.com
ZENDESK_API_KEY=your_api_token
```

**Option B -- OAuth:**
```env
ZENDESK_SUBDOMAIN=yourcompany
```
Then run `zendesk-auth` to authenticate via browser. The token is saved to `.zendesk_token`.

**Option C -- Session cookie:**
```env
ZENDESK_SUBDOMAIN=yourcompany
ZENDESK_SESSION_COOKIE=your_session_cookie
```

### 3. Configure MCP client

**Claude Code** (`settings.json`):
```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/zendesk-mcp-server",
        "run",
        "zendesk"
      ]
    }
  }
}
```

### Docker

```bash
docker build -t zendesk-mcp-server .
docker run --rm -i --env-file /path/to/.env zendesk-mcp-server
```

The image uses `python:3.12-slim`, installs from pinned `requirements.lock`, and runs as a non-root user.

## Tools (24)

### Tickets
| Tool | Description |
|---|---|
| `get_ticket` | Get a ticket by ID |
| `get_tickets` | List tickets with pagination and sorting |
| `get_tickets_bulk` | Fetch up to 100 tickets by IDs |
| `create_ticket` | Create a new ticket |
| `update_ticket` | Update ticket fields (status, priority, assignee, etc.) |
| `delete_ticket` | Permanently delete a ticket |
| `merge_tickets` | Merge source tickets into a target |
| `get_ticket_comments` | Get all comments on a ticket |
| `create_ticket_comment` | Add a comment to a ticket |
| `get_ticket_attachment` | Fetch an image attachment as base64 |

### Search
| Tool | Description |
|---|---|
| `search` | Full-text search via Zendesk Query Language (ZQL) |

### Users
| Tool | Description |
|---|---|
| `get_user` | Get user by ID (resolve requester/assignee) |
| `get_current_user` | Get the authenticated user |
| `search_users` | Search users by name/email |
| `get_user_tickets` | Get tickets by user role (requested/assigned/ccd) |

### Organizations
| Tool | Description |
|---|---|
| `get_organization` | Get org by ID |
| `search_organizations` | Search orgs by name |

### Views & Fields
| Tool | Description |
|---|---|
| `list_views` | List saved ticket queues |
| `execute_view` | Run a view and get its tickets |
| `list_ticket_fields` | List all ticket fields with valid options |
| `list_ticket_forms` | List ticket forms and field mappings |

### Groups & Macros
| Tool | Description |
|---|---|
| `list_groups` | List assignable groups |
| `list_macros` | List available macros |
| `apply_macro` | Preview macro effect on a ticket |

## Resources

- `zendesk://knowledge-base` -- full Help Center articles, cached for 1 hour

## Prompts

- **analyze-ticket** -- analyze a ticket with summary, timeline, and insights
- **draft-ticket-response** -- draft a professional response to a ticket

## Security Patches

This fork includes the following security fixes not present in the upstream repo:

- **SSRF/credential exfiltration fix**: `get_ticket_attachment` validates URLs against an allowlist (`*.zendesk.com`, `*.zdusercontent.com`) and requires HTTPS before sending auth headers
- **Input validation**: `sort_by`, `sort_order`, and `role` parameters validated against strict allowlists
- **Error sanitization**: Tool errors are logged server-side; only generic messages returned to MCP clients
- **XSS hardening**: OAuth callback page uses safe DOM manipulation (`textContent`) instead of `innerHTML`
- **Command injection prevention**: Windows browser launch uses direct executable paths instead of `cmd /c start`

## License

Apache 2.0
