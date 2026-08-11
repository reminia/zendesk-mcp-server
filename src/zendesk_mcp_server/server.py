import asyncio
import json
import logging
import os
from typing import Any, Dict

from cachetools.func import ttl_cache
from dotenv import load_dotenv
from mcp.server import InitializationOptions, NotificationOptions
from mcp.server import Server, types
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl

from zendesk_mcp_server.zendesk_client import ZendeskClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("zendesk-mcp-server")
logger.info("zendesk mcp server started")

load_dotenv()
zendesk_client = ZendeskClient(
    subdomain=os.getenv("ZENDESK_SUBDOMAIN"),
    email=os.getenv("ZENDESK_EMAIL"),
    token=os.getenv("ZENDESK_API_KEY")
)

server = Server("Zendesk Server")

TICKET_ANALYSIS_TEMPLATE = """
You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

Please fetch the ticket info and comments to analyze it and provide:
1. A summary of the issue
2. The current status and timeline
3. Key points of interaction

Remember to be professional and focus on actionable insights.
"""

SENTIMENT_RUBRIC = """
<INSTRUCTION>
Analyze the sentiment of the following ticket conversation. The primary focus
(70%) should be on the customer's expressed emotion and attitude towards the
support agents' interactions, irrespective of whether the underlying
issue/request is fully resolved. Account for nuances like sarcasm, mixed
sentiments (positive and negative expressions co-existing), and changes in
sentiment throughout the conversation. Consider soft skills employed by the
support agents and their impact on customer sentiment.

The remaining 30% of the analysis should consider the customer's perception of
progress towards resolving their issue. Even if the issue is not fully solved,
has the customer acknowledged any improvements, effort, or clearer
understanding of the situation due to the agent's involvement?

Classify the overall customer conversation sentiment as Positive, Negative, or
Neutral. Focus on sentiment expressed towards the support agents'
communication and helpfulness, but briefly acknowledge the customer's
perception of the issue's status.
</INSTRUCTION>

<QUERY>
Ticket Conversation:
{ticket_content}
</QUERY>

<OUTPUT_FORMAT>
Overall Sentiment: [Positive/Negative/Neutral]
Summary: [Brief explanation of the sentiment analysis. Describe how the
customer's sentiment evolves throughout the ticket conversation, highlighting
instances of sarcasm, mixed emotions, or shifts in attitude towards the support
agents. Note any specific interactions (positive or negative) that heavily
influenced the overall sentiment, even if the underlying technical issue was
not fully resolved. Briefly include any observations about the customer's
perception of progress on their underlying issue/request, even if incomplete.]
</OUTPUT_FORMAT>
"""

FIND_TICKETS_TEMPLATE = """
Find Zendesk tickets for this request:

{request}

Follow this workflow:
1. Decompose the request into topic/service, organization, severity, date
   range, status, and team scope.
2. For each vague value, call discover_filters. Never invent a tag. Resolve an
   exact or clearly dominant match automatically; otherwise ask one
   consolidated clarification question listing real candidates and counts.
3. Call search_tickets with count_only=true first. If it returns zero, widen
   the narrowest filter or try discovered sibling tags and explain the change.
   If it is very broad, ask the user to narrow it.
4. Fetch results only after the filter is useful.
5. State the resolved Zendesk query and filters in the answer so the search is
   auditable.
"""

TICKET_REPORT_TEMPLATE = """
Create a priority-focused Zendesk report for topic "{topic}", date range
"{date_range}", and group "{group}". Return at most {limit} tickets.

First discover the real topic tag and group when either is ambiguous. Preview
the match count, then search sorted by priority descending and fetch summary
digests for the selected tickets. Present a concise human-readable report
grouped Urgent, High, Normal, Low. For every ticket include:
- linked ticket ID and subject
- what the customer originally reported
- where the ticket is now
- age, owner, and latest customer/agent interaction
- a concrete next action

State the resolved query. Do not perform sentiment analysis for this report.
"""

UNHAPPY_CUSTOMERS_TEMPLATE = """
Create a support-experience sentiment report for topic "{topic}", date range
"{date_range}", and group "{group}". Return at most {limit} negative tickets
from a candidate pool of at most {candidate_pool}.

Use this two-stage workflow:
1. Discover real topic tags and group values if ambiguous. Preview the count,
   then search open and recently solved tickets in the requested window.
2. Fetch summary digests for up to {candidate_pool} matches. Rank candidates by
   service_signals.risk_score, then priority and age. This score is only a
   factual triage signal and is never a sentiment verdict.
3. Fetch transcript digests only for the top {limit} candidates.
4. Apply the rubric below independently to each transcript.
5. Report only tickets classified Negative, worst first. Quote the customer's
   own words and explain which part of the support experience failed, where the
   ticket stands, and a recommended recovery action.

Exclude tickets where negativity is aimed only at the product or technical
problem rather than agent communication or support helpfulness. State the
resolved Zendesk query.

Sentiment rubric:
{sentiment_rubric}
"""


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """List available prompts"""
    return [
        types.Prompt(
            name="analyze-ticket",
            description="Analyze a Zendesk ticket and provide insights",
            arguments=[
                types.PromptArgument(
                    name="ticket_id",
                    description="The ID of the ticket to analyze",
                    required=True,
                )
            ],
        ),
        types.Prompt(
            name="find-tickets",
            description=(
                "Turn a natural-language request into grounded Zendesk filters, "
                "asking one consolidated clarification question when needed"
            ),
            arguments=[
                types.PromptArgument(
                    name="request",
                    description="The ticket search request in plain English",
                    required=True,
                )
            ],
        ),
        types.Prompt(
            name="analyze-ticket-sentiment",
            description=(
                "Analyze customer sentiment toward support interactions and "
                "perceived issue progress"
            ),
            arguments=[
                types.PromptArgument(
                    name="ticket_id",
                    description="The ticket ID to analyze",
                    required=True,
                )
            ],
        ),
        types.Prompt(
            name="ticket-report",
            description="Create a priority-focused ticket report",
            arguments=[
                types.PromptArgument(
                    name="topic",
                    description="Service, product, or topic",
                    required=True,
                ),
                types.PromptArgument(
                    name="date_range",
                    description="Relative range such as this_week",
                    required=False,
                ),
                types.PromptArgument(
                    name="group",
                    description="Zendesk group name or ID",
                    required=False,
                ),
                types.PromptArgument(
                    name="limit",
                    description="Maximum tickets to report",
                    required=False,
                ),
            ],
        ),
        types.Prompt(
            name="unhappy-customers-report",
            description=(
                "Find tickets with negative sentiment toward the support "
                "experience using a two-stage, token-efficient workflow"
            ),
            arguments=[
                types.PromptArgument(
                    name="topic",
                    description="Service, product, or topic",
                    required=True,
                ),
                types.PromptArgument(
                    name="date_range",
                    description="Relative range such as last_30_days",
                    required=False,
                ),
                types.PromptArgument(
                    name="group",
                    description="Zendesk group name or ID",
                    required=False,
                ),
                types.PromptArgument(
                    name="limit",
                    description="Maximum negative tickets to report",
                    required=False,
                ),
                types.PromptArgument(
                    name="candidate_pool",
                    description="Maximum summary candidates to triage",
                    required=False,
                ),
            ],
        ),
    ]


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: Dict[str, str] | None) -> types.GetPromptResult:
    """Handle prompt requests"""
    arguments = arguments or {}
    try:
        if name == "analyze-ticket":
            if "ticket_id" not in arguments:
                raise ValueError("Missing required argument: ticket_id")
            ticket_id = int(arguments["ticket_id"])
            prompt = TICKET_ANALYSIS_TEMPLATE.format(
                ticket_id=ticket_id
            )
            description = f"Analysis prompt for ticket #{ticket_id}"

        elif name == "find-tickets":
            if "request" not in arguments:
                raise ValueError("Missing required argument: request")
            prompt = FIND_TICKETS_TEMPLATE.format(request=arguments["request"])
            description = "Grounded Zendesk ticket search"

        elif name == "analyze-ticket-sentiment":
            if "ticket_id" not in arguments:
                raise ValueError("Missing required argument: ticket_id")
            ticket_id = int(arguments["ticket_id"])
            rubric = SENTIMENT_RUBRIC.format(
                ticket_content=(
                    "Use the transcript returned by get_ticket_digests for "
                    f"ticket #{ticket_id}."
                )
            )
            prompt = (
                f"Call get_ticket_digests for ticket #{ticket_id} with "
                'detail="transcript", max_chars=12000, and '
                "include_internal_notes=false. Then apply this rubric to the "
                f"returned transcript:\n\n{rubric}"
            )
            description = f"Sentiment analysis prompt for ticket #{ticket_id}"

        elif name == "ticket-report":
            if "topic" not in arguments:
                raise ValueError("Missing required argument: topic")
            prompt = TICKET_REPORT_TEMPLATE.format(
                topic=arguments["topic"],
                date_range=arguments.get("date_range", "this_week"),
                group=arguments.get(
                    "group", os.getenv("ZENDESK_DEFAULT_GROUP", "default group")
                ),
                limit=int(arguments.get("limit", 10)),
            )
            description = f"Priority ticket report for {arguments['topic']}"

        elif name == "unhappy-customers-report":
            if "topic" not in arguments:
                raise ValueError("Missing required argument: topic")
            sentiment_rubric = SENTIMENT_RUBRIC.format(
                ticket_content="Apply to each transcript independently."
            )
            prompt = UNHAPPY_CUSTOMERS_TEMPLATE.format(
                topic=arguments["topic"],
                date_range=arguments.get("date_range", "last_30_days"),
                group=arguments.get(
                    "group", os.getenv("ZENDESK_DEFAULT_GROUP", "default group")
                ),
                limit=min(int(arguments.get("limit", 10)), 10),
                candidate_pool=min(int(arguments.get("candidate_pool", 25)), 25),
                sentiment_rubric=sentiment_rubric,
            )
            description = (
                f"Unhappy customer support report for {arguments['topic']}"
            )

        else:
            raise ValueError(f"Unknown prompt: {name}")

        return types.GetPromptResult(
            description=description,
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt.strip()),
                )
            ],
        )

    except Exception as e:
        logger.error(f"Error generating prompt: {e}")
        raise


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available Zendesk tools"""
    return [
        types.Tool(
            name="get_ticket",
            description="Retrieve a Zendesk ticket by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to retrieve"
                    }
                },
                "required": ["ticket_id"]
            }
        ),
        types.Tool(
            name="get_tickets",
            description="Fetch the latest tickets with pagination support",
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Page number",
                        "default": 1
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Number of tickets per page (max 100)",
                        "default": 25
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Field to sort by (created_at, updated_at, priority, status)",
                        "default": "created_at"
                    },
                    "sort_order": {
                        "type": "string",
                        "description": "Sort order (asc or desc)",
                        "default": "desc"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_ticket_comments",
            description="Retrieve all comments for a Zendesk ticket by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to get comments for"
                    }
                },
                "required": ["ticket_id"]
            }
        ),
        types.Tool(
            name="discover_filters",
            description=(
                "Discover real Zendesk tags, organizations, groups, and severity "
                "conventions for a vague term. Use this before search_tickets "
                "whenever a requested filter is not an exact known value. Never "
                "invent tag names; use values returned by this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "minLength": 2,
                        "description": "Partial service, tag, organization, or group name"
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "tags", "organizations", "groups",
                                "severity", "priority", "status"
                            ]
                        },
                        "description": "Dimensions to search; defaults to all"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "default": 10
                    }
                },
                "required": ["term"]
            }
        ),
        types.Tool(
            name="search_tickets",
            description=(
                "Search Zendesk tickets with account-backed filters. For vague "
                "tags, organizations, groups, or severity, call discover_filters "
                "first. Use count_only before broad result fetches. Relative date "
                "ranges are this_week, last_7_days, last_30_days, and this_month."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "match_all_tags": {"type": "boolean", "default": True},
                    "created_after": {"type": "string"},
                    "created_before": {"type": "string"},
                    "updated_after": {"type": "string"},
                    "updated_before": {"type": "string"},
                    "date_range": {
                        "type": "string",
                        "enum": [
                            "this_week", "last_7_days",
                            "last_30_days", "this_month"
                        ]
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "normal", "high", "urgent"]
                    },
                    "priority_operator": {
                        "type": "string",
                        "enum": [":", ">", "<", ">=", "<="],
                        "default": ":"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["new", "open", "pending", "hold", "solved", "closed"]
                    },
                    "status_operator": {
                        "type": "string",
                        "enum": [":", ">", "<", ">=", "<="],
                        "default": ":"
                    },
                    "group": {
                        "description": "Exact group name or ID returned by discover_filters",
                        "anyOf": [{"type": "string"}, {"type": "integer"}]
                    },
                    "assignee": {
                        "description": "Assignee name, ID, or 'me'",
                        "anyOf": [{"type": "string"}, {"type": "integer"}]
                    },
                    "organization": {
                        "description": "Exact organization name or ID from discover_filters",
                        "anyOf": [{"type": "string"}, {"type": "integer"}]
                    },
                    "satisfaction": {
                        "type": "string",
                        "enum": [
                            "bad", "badwithcomment", "good",
                            "goodwithcomment", "offered"
                        ]
                    },
                    "text": {"type": "string"},
                    "raw_query": {
                        "type": "string",
                        "description": "Advanced Zendesk search syntax appended as-is"
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": [
                            "updated_at", "created_at", "priority",
                            "status", "ticket_type"
                        ],
                        "default": "created_at"
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "desc"
                    },
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "per_page": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 25
                    },
                    "count_only": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return only the resolved query and match count"
                    }
                },
                "required": []
            }
        ),
        types.Tool(
            name="get_ticket_digests",
            description=(
                "Build factual ticket digests. summary detail returns reported "
                "issue, current state, and objective service-risk signals for up "
                "to 25 tickets. transcript detail adds cleaned role-labelled "
                "public conversations for sentiment analysis, up to 10 tickets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "maxItems": 25
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "transcript"],
                        "default": "summary"
                    },
                    "max_chars": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 50000,
                        "default": 800
                    },
                    "max_comments": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 30
                    },
                    "include_internal_notes": {
                        "type": "boolean",
                        "default": False
                    }
                },
                "required": ["ticket_ids"]
            }
        ),
        types.Tool(
            name="get_ticket_attachment",
            description="Fetch a Zendesk ticket attachment by its content_url and return the file as base64-encoded data. Use the attachment URLs returned by get_ticket_comments.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content_url": {
                        "type": "string",
                        "description": "The content_url of the attachment from get_ticket_comments"
                    }
                },
                "required": ["content_url"]
            }
        )
    ]


@server.call_tool()
async def handle_call_tool(
        name: str,
        arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle Zendesk tool execution requests"""
    try:
        if name == "get_ticket":
            if not arguments:
                raise ValueError("Missing arguments")
            ticket = zendesk_client.get_ticket(arguments["ticket_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(ticket)
            )]

        elif name == "get_tickets":
            page = arguments.get("page", 1) if arguments else 1
            per_page = arguments.get("per_page", 25) if arguments else 25
            sort_by = arguments.get("sort_by", "created_at") if arguments else "created_at"
            sort_order = arguments.get("sort_order", "desc") if arguments else "desc"

            tickets = zendesk_client.get_tickets(
                page=page,
                per_page=per_page,
                sort_by=sort_by,
                sort_order=sort_order
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(tickets, indent=2)
            )]

        elif name == "get_ticket_comments":
            if not arguments:
                raise ValueError("Missing arguments")
            comments = zendesk_client.get_ticket_comments(
                arguments["ticket_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(comments)
            )]

        elif name == "discover_filters":
            if not arguments or not arguments.get("term"):
                raise ValueError("Missing required argument: term")
            filters = zendesk_client.discover_filters(
                term=arguments["term"],
                dimensions=arguments.get("dimensions"),
                limit=arguments.get("limit", 10),
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(filters, indent=2)
            )]

        elif name == "search_tickets":
            search_arguments = dict(arguments or {})
            if (
                "group" not in search_arguments
                and os.getenv("ZENDESK_DEFAULT_GROUP")
            ):
                search_arguments["group"] = os.getenv("ZENDESK_DEFAULT_GROUP")
            tickets = zendesk_client.search_tickets(**search_arguments)
            return [types.TextContent(
                type="text",
                text=json.dumps(tickets, indent=2)
            )]

        elif name == "get_ticket_digests":
            if not arguments or not arguments.get("ticket_ids"):
                raise ValueError("Missing required argument: ticket_ids")
            detail = arguments.get("detail", "summary")
            default_max_chars = 12000 if detail == "transcript" else 800
            digests = zendesk_client.get_ticket_digests(
                ticket_ids=arguments["ticket_ids"],
                detail=detail,
                max_chars=arguments.get("max_chars", default_max_chars),
                max_comments=arguments.get("max_comments", 30),
                include_internal_notes=arguments.get(
                    "include_internal_notes", False
                ),
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(digests, indent=2)
            )]

        elif name == "get_ticket_attachment":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.get_ticket_attachment(arguments["content_url"])
            content_type = result["content_type"]
            if content_type.startswith("image/"):
                return [types.ImageContent(
                    type="image",
                    data=result["data"],
                    mimeType=content_type,
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({"content_type": content_type, "data_base64": result["data"]})
                )]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    logger.debug("Handling list_resources request")
    return [
        types.Resource(
            uri=AnyUrl("zendesk://knowledge-base"),
            name="Zendesk Knowledge Base",
            description="Access to Zendesk Help Center articles and sections",
            mimeType="application/json",
        ),
        types.Resource(
            uri=AnyUrl("zendesk://filter-vocabulary"),
            name="Zendesk Filter Vocabulary",
            description=(
                "Popular tags with ticket counts, groups, priorities, statuses, "
                "and detected severity tags for grounded ticket filtering"
            ),
            mimeType="application/json",
        )
    ]


@ttl_cache(ttl=3600)
def get_cached_kb():
    return zendesk_client.get_all_articles()


@ttl_cache(ttl=3600)
def get_cached_filter_vocabulary():
    return zendesk_client.get_filter_vocabulary()


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    logger.debug(f"Handling read_resource request for URI: {uri}")
    if uri.scheme != "zendesk":
        logger.error(f"Unsupported URI scheme: {uri.scheme}")
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    path = str(uri).replace("zendesk://", "")
    try:
        if path == "knowledge-base":
            kb_data = get_cached_kb()
            return json.dumps({
                "knowledge_base": kb_data,
                "metadata": {
                    "sections": len(kb_data),
                    "total_articles": sum(
                        len(section['articles']) for section in kb_data.values()
                    ),
                }
            }, indent=2)
        if path == "filter-vocabulary":
            return json.dumps({
                "filter_vocabulary": get_cached_filter_vocabulary(),
                "metadata": {
                    "cache_ttl_seconds": 3600,
                    "tag_window": "most-used tags from the last 60 days",
                }
            }, indent=2)
        logger.error(f"Unknown resource path: {path}")
        raise ValueError(f"Unknown resource path: {path}")
    except Exception as e:
        logger.error(f"Error fetching Zendesk resource: {e}")
        raise


async def main():
    # Run the server using stdin/stdout streams
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream=read_stream,
            write_stream=write_stream,
            initialization_options=InitializationOptions(
                server_name="Zendesk",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
