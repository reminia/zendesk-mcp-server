# Zendesk MCP Server

A Model Context Protocol server for Zendesk.

This server provides a comprehensive integration with Zendesk. It offers:

- Tools for retrieving and managing Zendesk tickets and comments
- Specialized prompts for ticket analysis and response drafting
- Full access to the Zendesk Help Center articles as knowledge base

## Setup

- build: `uv pip install -e .`
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

## Resources

- zendesk://knowledge-base, get access to the whole help center articles.

## Prompts

### analyze-ticket

Analyze a Zendesk ticket and provide a detailed analysis of the ticket.

### draft-ticket-respons

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

### create_ticket_comment

Create a new comment on an existing Zendesk ticket

- Input:
  - `ticket_id` (integer): The ID of the ticket to comment on
  - `comment` (string): The comment text/content to add
  - `public` (boolean, optional): Whether the comment should be public (defaults to true)
