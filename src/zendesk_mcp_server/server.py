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


def _init_client() -> ZendeskClient:
    """
    Initialize the Zendesk client with the best available auth method.

    Priority:
    1. Env var ZENDESK_OAUTH_TOKEN or ZENDESK_API_KEY (explicit config)
    2. Saved OAuth token from .zendesk_token file
    3. Browser-based OAuth flow (opens browser for login)
    """
    from zendesk_mcp_server.auth import ensure_auth, load_token

    subdomain = os.getenv("ZENDESK_SUBDOMAIN")
    email = os.getenv("ZENDESK_EMAIL")
    api_key = os.getenv("ZENDESK_API_KEY")
    oauth_token = os.getenv("ZENDESK_OAUTH_TOKEN")
    session_cookie = os.getenv("ZENDESK_SESSION_COOKIE")

    # If explicit credentials are set, use them directly
    if oauth_token or (email and api_key) or session_cookie:
        oauth_from_file = None
        if not oauth_token:
            token_data = load_token()
            if token_data:
                oauth_from_file = token_data.get("access_token")
                subdomain = subdomain or token_data.get("subdomain")
        return ZendeskClient(
            subdomain=subdomain,
            email=email,
            token=api_key,
            session_cookie=session_cookie,
            oauth_access_token=oauth_token or oauth_from_file,
        )

    # No explicit credentials - try token file, then browser auth
    if not subdomain:
        # Check if token file has a subdomain
        token_data = load_token()
        if token_data:
            subdomain = token_data.get("subdomain")

    if not subdomain:
        raise ValueError(
            "ZENDESK_SUBDOMAIN is required. Set it in .env or run 'zendesk-auth'."
        )

    # ensure_auth will check existing token, verify it, or open browser
    token_data = ensure_auth(subdomain)
    return ZendeskClient(
        subdomain=subdomain,
        oauth_access_token=token_data["access_token"],
    )


zendesk_client = _init_client()

server = Server("Zendesk Server")

TICKET_ANALYSIS_TEMPLATE = """
You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

Please fetch the ticket info and comments to analyze it and provide:
1. A summary of the issue
2. The current status and timeline
3. Key points of interaction

Remember to be professional and focus on actionable insights.
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

Please fetch the ticket info, comments and knowledge base to draft a professional and helpful response that:
1. Acknowledges the customer's concern
2. Addresses the specific issues raised
3. Provides clear next steps or ask for specific details need to proceed
4. Maintains a friendly and professional tone
5. Ask for confirmation before commenting on the ticket

The response should be formatted well and ready to be posted as a comment.
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
            name="draft-ticket-response",
            description="Draft a professional response to a Zendesk ticket",
            arguments=[
                types.PromptArgument(
                    name="ticket_id",
                    description="The ID of the ticket to respond to",
                    required=True,
                )
            ],
        )
    ]


@server.get_prompt()
async def handle_get_prompt(name: str, arguments: Dict[str, str] | None) -> types.GetPromptResult:
    """Handle prompt requests"""
    if not arguments or "ticket_id" not in arguments:
        raise ValueError("Missing required argument: ticket_id")

    ticket_id = int(arguments["ticket_id"])
    try:
        if name == "analyze-ticket":
            prompt = TICKET_ANALYSIS_TEMPLATE.format(
                ticket_id=ticket_id
            )
            description = f"Analysis prompt for ticket #{ticket_id}"

        elif name == "draft-ticket-response":
            prompt = COMMENT_DRAFT_TEMPLATE.format(
                ticket_id=ticket_id
            )
            description = f"Response draft prompt for ticket #{ticket_id}"

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
            name="create_ticket",
            description="Create a new Zendesk ticket",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Ticket subject"},
                    "description": {"type": "string", "description": "Ticket description"},
                    "requester_id": {"type": "integer"},
                    "assignee_id": {"type": "integer"},
                    "priority": {"type": "string", "description": "low, normal, high, urgent"},
                    "type": {"type": "string", "description": "problem, incident, question, task"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "custom_fields": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["subject", "description"],
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
            name="create_ticket_comment",
            description="Create a new comment on an existing Zendesk ticket",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to comment on"
                    },
                    "comment": {
                        "type": "string",
                        "description": "The comment text/content to add"
                    },
                    "public": {
                        "type": "boolean",
                        "description": "Whether the comment should be public",
                        "default": True
                    }
                },
                "required": ["ticket_id", "comment"]
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
        ),
        types.Tool(
            name="update_ticket",
            description="Update fields on an existing Zendesk ticket (e.g., status, priority, assignee_id)",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer", "description": "The ID of the ticket to update"},
                    "subject": {"type": "string"},
                    "status": {"type": "string", "description": "new, open, pending, on-hold, solved, closed"},
                    "priority": {"type": "string", "description": "low, normal, high, urgent"},
                    "type": {"type": "string"},
                    "assignee_id": {"type": "integer"},
                    "requester_id": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "custom_fields": {"type": "array", "items": {"type": "object"}},
                    "due_at": {"type": "string", "description": "ISO8601 datetime"}
                },
                "required": ["ticket_id"]
            }
        ),
        # ── P0: Search & Users ──────────────────────────────────────
        types.Tool(
            name="search",
            description="Search Zendesk using Zendesk Query Language (ZQL). Searches tickets, users, and organizations. Example queries: 'type:ticket status:open priority:urgent', 'type:ticket assignee:me', 'type:user email:john@example.com'",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "ZQL search query"},
                    "page": {"type": "integer", "default": 1},
                    "per_page": {"type": "integer", "default": 25, "description": "Results per page (max 100)"},
                    "sort_by": {"type": "string", "default": "relevance", "description": "relevance, updated_at, created_at, priority, status, ticket_type"},
                    "sort_order": {"type": "string", "default": "desc", "description": "asc or desc"},
                },
                "required": ["query"],
            }
        ),
        types.Tool(
            name="get_user",
            description="Get a Zendesk user by their ID. Use this to resolve requester_id or assignee_id from tickets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "The user ID"}
                },
                "required": ["user_id"],
            }
        ),
        types.Tool(
            name="get_current_user",
            description="Get the currently authenticated Zendesk user",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="search_users",
            description="Search Zendesk users by name, email, or external_id",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name, email, or external_id to search for"}
                },
                "required": ["query"],
            }
        ),
        # ── P1: Views, Fields, Orgs, Bulk ───────────────────────────
        types.Tool(
            name="list_views",
            description="List all available Zendesk views (saved ticket queues)",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="execute_view",
            description="Execute a Zendesk view and return its tickets",
            inputSchema={
                "type": "object",
                "properties": {
                    "view_id": {"type": "integer", "description": "The view ID to execute"},
                    "page": {"type": "integer", "default": 1},
                    "per_page": {"type": "integer", "default": 25},
                },
                "required": ["view_id"],
            }
        ),
        types.Tool(
            name="list_ticket_fields",
            description="List all ticket fields (system + custom) with their types and valid options",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="get_organization",
            description="Get a Zendesk organization by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "organization_id": {"type": "integer", "description": "The organization ID"}
                },
                "required": ["organization_id"],
            }
        ),
        types.Tool(
            name="search_organizations",
            description="Search Zendesk organizations by name",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Organization name to search for"}
                },
                "required": ["query"],
            }
        ),
        types.Tool(
            name="get_tickets_bulk",
            description="Fetch multiple tickets by IDs in a single request (max 100)",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_ids": {"type": "array", "items": {"type": "integer"}, "description": "List of ticket IDs"}
                },
                "required": ["ticket_ids"],
            }
        ),
        # ── P2: Groups, Merge, Macros ───────────────────────────────
        types.Tool(
            name="list_groups",
            description="List assignable Zendesk groups for ticket routing",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="merge_tickets",
            description="Merge source tickets into a target ticket",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_id": {"type": "integer", "description": "The ticket to merge into"},
                    "source_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tickets to merge from"},
                    "target_comment": {"type": "string", "default": "Merged from related tickets."},
                    "source_comment": {"type": "string", "default": "This ticket has been merged."},
                },
                "required": ["target_id", "source_ids"],
            }
        ),
        types.Tool(
            name="list_macros",
            description="List available Zendesk macros (canned responses and actions)",
            inputSchema={
                "type": "object",
                "properties": {
                    "active_only": {"type": "boolean", "default": True}
                },
            }
        ),
        types.Tool(
            name="apply_macro",
            description="Preview the result of applying a macro to a ticket (does not save changes)",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer", "description": "The ticket to apply the macro to"},
                    "macro_id": {"type": "integer", "description": "The macro to apply"},
                },
                "required": ["ticket_id", "macro_id"],
            }
        ),
        # ── P3: User Tickets, Forms, Delete ─────────────────────────
        types.Tool(
            name="get_user_tickets",
            description="Get tickets for a specific user by role (requested, assigned, or ccd)",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "The user ID"},
                    "role": {"type": "string", "default": "requested", "description": "requested, assigned, or ccd"},
                    "page": {"type": "integer", "default": 1},
                    "per_page": {"type": "integer", "default": 25},
                },
                "required": ["user_id"],
            }
        ),
        types.Tool(
            name="list_ticket_forms",
            description="List all ticket forms and their associated field IDs",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="delete_ticket",
            description="Permanently delete a Zendesk ticket. Use with caution.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer", "description": "The ticket ID to delete"}
                },
                "required": ["ticket_id"],
            }
        ),
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

        elif name == "create_ticket":
            if not arguments:
                raise ValueError("Missing arguments")
            created = zendesk_client.create_ticket(
                subject=arguments.get("subject"),
                description=arguments.get("description"),
                requester_id=arguments.get("requester_id"),
                assignee_id=arguments.get("assignee_id"),
                priority=arguments.get("priority"),
                type=arguments.get("type"),
                tags=arguments.get("tags"),
                custom_fields=arguments.get("custom_fields"),
            )
            return [types.TextContent(
                type="text",
                text=json.dumps({"message": "Ticket created successfully", "ticket": created}, indent=2)
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

        elif name == "create_ticket_comment":
            if not arguments:
                raise ValueError("Missing arguments")
            public = arguments.get("public", True)
            result = zendesk_client.post_comment(
                ticket_id=arguments["ticket_id"],
                comment=arguments["comment"],
                public=public
            )
            return [types.TextContent(
                type="text",
                text=f"Comment created successfully: {result}"
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

        elif name == "update_ticket":
            if not arguments:
                raise ValueError("Missing arguments")
            ticket_id = arguments.get("ticket_id")
            if ticket_id is None:
                raise ValueError("ticket_id is required")
            update_fields = {k: v for k, v in arguments.items() if k != "ticket_id"}
            updated = zendesk_client.update_ticket(ticket_id=int(ticket_id), **update_fields)
            return [types.TextContent(
                type="text",
                text=json.dumps({"message": "Ticket updated successfully", "ticket": updated}, indent=2)
            )]

        # ── P0: Search & Users ──────────────────────────────────────
        elif name == "search":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.search(
                query=arguments["query"],
                page=arguments.get("page", 1),
                per_page=arguments.get("per_page", 25),
                sort_by=arguments.get("sort_by", "relevance"),
                sort_order=arguments.get("sort_order", "desc"),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_user":
            if not arguments:
                raise ValueError("Missing arguments")
            user = zendesk_client.get_user(arguments["user_id"])
            return [types.TextContent(type="text", text=json.dumps(user, indent=2))]

        elif name == "get_current_user":
            user = zendesk_client.get_current_user()
            return [types.TextContent(type="text", text=json.dumps(user, indent=2))]

        elif name == "search_users":
            if not arguments:
                raise ValueError("Missing arguments")
            users = zendesk_client.search_users(arguments["query"])
            return [types.TextContent(type="text", text=json.dumps(users, indent=2))]

        # ── P1: Views, Fields, Orgs, Bulk ───────────────────────────
        elif name == "list_views":
            views = zendesk_client.list_views()
            return [types.TextContent(type="text", text=json.dumps(views, indent=2))]

        elif name == "execute_view":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.execute_view(
                view_id=arguments["view_id"],
                page=arguments.get("page", 1),
                per_page=arguments.get("per_page", 25),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_ticket_fields":
            fields = zendesk_client.list_ticket_fields()
            return [types.TextContent(type="text", text=json.dumps(fields, indent=2))]

        elif name == "get_organization":
            if not arguments:
                raise ValueError("Missing arguments")
            org = zendesk_client.get_organization(arguments["organization_id"])
            return [types.TextContent(type="text", text=json.dumps(org, indent=2))]

        elif name == "search_organizations":
            if not arguments:
                raise ValueError("Missing arguments")
            orgs = zendesk_client.search_organizations(arguments["query"])
            return [types.TextContent(type="text", text=json.dumps(orgs, indent=2))]

        elif name == "get_tickets_bulk":
            if not arguments:
                raise ValueError("Missing arguments")
            tickets = zendesk_client.get_tickets_bulk(arguments["ticket_ids"])
            return [types.TextContent(type="text", text=json.dumps(tickets, indent=2))]

        # ── P2: Groups, Merge, Macros ───────────────────────────────
        elif name == "list_groups":
            groups = zendesk_client.list_groups()
            return [types.TextContent(type="text", text=json.dumps(groups, indent=2))]

        elif name == "merge_tickets":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.merge_tickets(
                target_id=arguments["target_id"],
                source_ids=arguments["source_ids"],
                target_comment=arguments.get("target_comment", "Merged from related tickets."),
                source_comment=arguments.get("source_comment", "This ticket has been merged."),
            )
            return [types.TextContent(type="text", text=json.dumps({"message": "Tickets merged successfully", "result": result}, indent=2))]

        elif name == "list_macros":
            active_only = arguments.get("active_only", True) if arguments else True
            macros = zendesk_client.list_macros(active_only=active_only)
            return [types.TextContent(type="text", text=json.dumps(macros, indent=2))]

        elif name == "apply_macro":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.apply_macro(
                ticket_id=arguments["ticket_id"],
                macro_id=arguments["macro_id"],
            )
            return [types.TextContent(type="text", text=json.dumps({"message": "Macro preview (not saved)", "result": result}, indent=2))]

        # ── P3: User Tickets, Forms, Delete ─────────────────────────
        elif name == "get_user_tickets":
            if not arguments:
                raise ValueError("Missing arguments")
            result = zendesk_client.get_user_tickets(
                user_id=arguments["user_id"],
                role=arguments.get("role", "requested"),
                page=arguments.get("page", 1),
                per_page=arguments.get("per_page", 25),
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_ticket_forms":
            forms = zendesk_client.list_ticket_forms()
            return [types.TextContent(type="text", text=json.dumps(forms, indent=2))]

        elif name == "delete_ticket":
            if not arguments:
                raise ValueError("Missing arguments")
            zendesk_client.delete_ticket(arguments["ticket_id"])
            return [types.TextContent(type="text", text=json.dumps({"message": f"Ticket {arguments['ticket_id']} deleted successfully"}))]

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
        )
    ]


@ttl_cache(ttl=3600)
def get_cached_kb():
    return zendesk_client.get_all_articles()


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    logger.debug(f"Handling read_resource request for URI: {uri}")
    if uri.scheme != "zendesk":
        logger.error(f"Unsupported URI scheme: {uri.scheme}")
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    path = str(uri).replace("zendesk://", "")
    if path != "knowledge-base":
        logger.error(f"Unknown resource path: {path}")
        raise ValueError(f"Unknown resource path: {path}")

    try:
        kb_data = get_cached_kb()
        return json.dumps({
            "knowledge_base": kb_data,
            "metadata": {
                "sections": len(kb_data),
                "total_articles": sum(len(section['articles']) for section in kb_data.values()),
            }
        }, indent=2)
    except Exception as e:
        logger.error(f"Error fetching knowledge base: {e}")
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
