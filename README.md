# Zendesk MCP Server

![ci](https://github.com/reminia/zendesk-mcp-server/actions/workflows/ci.yml/badge.svg)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A Model Context Protocol server for Zendesk.

This server provides a comprehensive integration with Zendesk. It offers:

- Tools for retrieving, filtering, and summarizing Zendesk tickets
- Grounded filter discovery using real tags, organizations, and groups
- Priority and customer-support sentiment report prompts
- Zendesk Help Center and filter-vocabulary resources

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

### Docker

You can containerize the server if you prefer an isolated runtime:

1. Copy `.env.example` to `.env` and fill in your Zendesk credentials. Keep this file outside version control.
2. Build the image:

   ```bash
   docker build -t zendesk-mcp-server .
   ```

3. Run the server, providing the environment file:

   ```bash
   docker run --rm --env-file /path/to/.env zendesk-mcp-server
   ```

   Add `-i` when wiring the container to MCP clients over STDIN/STDOUT (Claude Code uses this mode). For daemonized runs, add `-d --name zendesk-mcp`.

The image installs dependencies from `requirements.lock`, drops privileges to a non-root user, and expects configuration exclusively via environment variables.

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

## Resources

- zendesk://knowledge-base, get access to the whole help center articles.
- zendesk://filter-vocabulary, get the top 200 recent tags with ticket counts,
  active groups, valid priorities/statuses, and detected severity conventions.

## Prompts

### analyze-ticket

Analyze a Zendesk ticket and provide a detailed analysis of the ticket.

### find-tickets

Turn an ad-hoc request into grounded Zendesk filters. The prompt discovers real
account values, asks one consolidated clarification question when necessary,
previews the result count, and reports the final query.

Example: `Find urgent database tickets for Acme this month.` If `database` or
`Acme` has multiple real matches, the client presents those candidates before
searching rather than inventing a tag.

### analyze-ticket-sentiment

Fetch a clean public ticket transcript and classify the customer's sentiment
toward support. The rubric weights agent communication and helpfulness at 70%
and perceived issue progress at 30%.

### ticket-report

Create a priority-grouped report for a topic, date range, and group. Each ticket
shows the original report, current state, age, owner, latest interactions, and
recommended next action.

Example: `Use ticket-report for Kafka, this_week, and the Platform Support group.`

### unhappy-customers-report

Triage tickets using objective service metrics, fetch full transcripts only for
the riskiest candidates, and report customers whose negative sentiment is aimed
at the support experience rather than only the product problem.

Example: `Use unhappy-customers-report for Postgres over last_30_days.`

## Tools

### get_tickets

Fetch the latest tickets with pagination support

- Input:
  - `page` (integer, optional): Page number (defaults to 1)
  - `per_page` (integer, optional): Number of tickets per page, max 100 (defaults to 25)
  - `sort_by` (string, optional): Field to sort by - created_at, updated_at, priority, or status (defaults to created_at)
  - `sort_order` (string, optional): Sort order - asc or desc (defaults to desc)

- Output: Returns a list of tickets with essential fields including id, subject, status, priority, description, timestamps, and assignee information, along with pagination metadata

### get_ticket

Retrieve a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to retrieve

### get_ticket_comments

Retrieve all comments for a Zendesk ticket by its ID

- Input:
  - `ticket_id` (integer): The ID of the ticket to get comments for

### get_ticket_attachment

Fetch a Zendesk ticket attachment by its content_url and return the file as base64-encoded data. Use the attachment URLs returned by `get_ticket_comments`.

- Input:
  - `content_url` (string): The content_url of the attachment from `get_ticket_comments`

### discover_filters

Discover real tags (with ticket counts), organizations, groups, and severity
tags for a partial term. Call this before filtering on a value that is not
already known exactly.

- Input:
  - `term` (string): Partial service, tag, organization, or group name
  - `dimensions` (array, optional): `tags`, `organizations`, `groups`,
    `severity`, `priority`, and/or `status`
  - `limit` (integer, optional): Maximum candidates per dimension

### search_tickets

Search through Zendesk's native Search API by tags, created/updated dates,
priority, status, group, assignee, organization, satisfaction, or text.
Relative ranges include `this_week`, `last_7_days`, `last_30_days`, and
`this_month`.

Use `count_only=true` to validate a filter before retrieving ticket data. The
response always includes the resolved Zendesk query for auditability. Zendesk's
standard Search API exposes at most 1,000 results; narrow broad filters before
paginating beyond that boundary.

### get_ticket_digests

Return compact factual summaries for up to 25 ticket IDs. `detail=summary`
includes the customer report, current state, latest interactions, metrics, and
objective service-risk signals. `detail=transcript` supports up to 10 tickets
and adds a cleaned, role-labelled public conversation for sentiment analysis.
Internal notes are excluded by default.
