# Zendesk MCP Server

![ci](https://github.com/reminia/zendesk-mcp-server/actions/workflows/ci.yml/badge.svg)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Model Context Protocol server for Zendesk.

This server provides a comprehensive integration with Zendesk. It offers:

- Tools for retrieving and managing Zendesk tickets and comments
- Support for ticket attachments (images, PDFs, documents) with native image viewing
- Macro management and application for automated ticket actions
- Specialized prompts for ticket analysis and response drafting
- Intelligent search-based access to Zendesk Help Center articles
- Multi-language support with locale selection for knowledge base content
- Efficient knowledge base tools with caching and pagination

![demo](https://res.cloudinary.com/leecy-me/image/upload/v1736410626/open/zendesk_yunczu.gif)

## Setup

- build: `uv venv && uv pip install -e .` or `uv build` in short.
- setup zendesk credentials in `.env` file, refer to [.env.example](.env.example).
- configure in Claude desktop:

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

#### Claude MCP Integration

To use the Dockerized server from Claude Code/Desktop, add an entry to Claude Code's `settings.json` similar to:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/usr/local/bin/docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env-file",
        "/path/to/zendesk-mcp-server/.env",
        "zendesk-mcp-server"
      ]
    }
  }
}
```

Adjust the paths to match your environment. After saving the file, restart Claude for the new MCP server to be detected.

## Features

### Multi-Language Support

**Features:**
- All knowledge base tools (`search_kb_articles`, `get_kb_article`, `get_section_articles`) support locale parameter
- Article URLs are automatically updated to match the requested locale (e.g., `/hc/en-us/articles/123` → `/hc/zh-cn/articles/123`)
- Article responses include a `locale` field showing the content language
- Cache is locale-aware to prevent cross-language cache pollution

**Example:**
```python
# Search articles in Chinese
search_kb_articles(query="密码重置", locale="zh-cn")

# Get article in English
get_kb_article(article_id=123456, locale="en-us")
```

### Attachment Handling

The server provides flexible attachment handling with two modes:

**Option 1: Manual (token-efficient)**
1. Call `get_ticket_comments(ticket_id)` to see attachment metadata
2. Identify relevant attachments by checking `is_image` flag and `content_type`
3. Call `get_attachment(attachment_id)` to view/download specific files

**Option 2: Automatic inline images**
1. Call `get_ticket_comments(ticket_id, include_inline_images=true)`
2. All image attachments are automatically fetched and displayed

The manual mode is recommended for tickets with many attachments, while automatic mode is best for tickets with few images where visual context is essential.

### Macros

The server supports Zendesk macros for automated ticket workflows:

- **Search and discover macros** by keywords or title
- **View complete macro configurations** including all actions and restrictions
- **Apply macros to tickets** to automate common actions (status changes, canned responses, field updates, etc.)

Macros are useful for:
- Standardizing responses to common issues
- Automating repetitive ticket workflows
- Applying consistent ticket categorization and routing
- Ensuring compliance with team procedures

## Resources

- zendesk://knowledge-base - Returns metadata about the help center (sections list). Use the search tools below to find specific articles.

## Prompts

### analyze-ticket

Analyze a Zendesk ticket and provide a detailed analysis of the ticket.

### draft-ticket-response

Draft a response to a Zendesk ticket.

## Tools

### get_ticket

Retrieve a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to retrieve

### get_ticket_comments

Retrieve all comments for a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to get comments for
  - `include_inline_images` (boolean, optional): Whether to include inline image attachments (defaults to false)

### create_ticket_comment

Create a new comment on an existing Zendesk ticket

- Input:
  - `ticket_id` (integer): The ID of the ticket to comment on
  - `comment` (string): The comment text/content to add
  - `public` (boolean, optional): Whether the comment should be public (defaults to true)

### get_attachment

Download and view a Zendesk ticket attachment (image, document, etc.)

- Input:
  - `attachment_id` (string): The ID of the attachment to download
- Returns:
  - Images: Returned as native ImageContent for direct viewing in multimodal clients
  - Other files: Returned as base64-encoded data with metadata

### search_kb_articles

Search Zendesk Help Center articles by query

- Input:
  - `query` (string): Search query to find relevant articles
  - `limit` (integer, optional): Maximum number of articles to return (defaults to 10)
  - `locale` (string, optional): Language locale for articles (defaults to `en-us`)

### get_kb_article

Get a specific Zendesk Help Center article by ID

- Input:
  - `article_id` (integer): The ID of the article to retrieve
  - `locale` (string, optional): Language locale for the article (defaults to `en-us`)

### list_kb_sections

List all Zendesk Help Center sections

- Input: None

### get_section_articles

Get articles from a specific Zendesk Help Center section

- Input:
  - `section_id` (integer): The ID of the section
  - `limit` (integer, optional): Maximum number of articles to return (defaults to 20)
  - `locale` (string, optional): Language locale for articles (defaults to `en-us`)

### search_macros

Search Zendesk macros by query string

- Input:
  - `query` (string): Search query to find relevant macros (required, cannot be empty)
  - `limit` (integer, optional): Maximum number of macros to return (defaults to 10)
- Returns:
  - List of macros with ID, title, description, actions, active status, and metadata

### get_macro

Get a specific Zendesk macro by ID

- Input:
  - `macro_id` (integer): The ID of the macro to retrieve
- Returns:
  - Complete macro configuration including all actions, restrictions, and settings

### apply_macro_to_ticket

Apply a Zendesk macro to a ticket

- Input:
  - `ticket_id` (integer): The ID of the ticket to apply the macro to
  - `macro_id` (integer): The ID of the macro to apply
- Returns:
  - Success status and updated ticket information
- Note: This operation modifies ticket data. The macro's actions (status changes, comments, field updates, etc.) are applied to the ticket.

## License

Apache License 2.0
