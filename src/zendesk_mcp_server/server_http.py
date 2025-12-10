"""
HTTP-based MCP Server with OAuth Authentication.

This module implements a remote MCP server using Streamable HTTP transport
with Zendesk OAuth 2.0 authentication.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from pydantic import AnyUrl
from sse_starlette.sse import EventSourceResponse

from zendesk_mcp_server.config import Config, get_config
from zendesk_mcp_server.oauth import ZendeskOAuthProvider, SQLiteTokenStorage
from zendesk_mcp_server.oauth.discovery import (
    create_protected_resource_metadata,
    create_authorization_server_metadata,
)
from zendesk_mcp_server.auth import AuthMiddleware, get_current_session
from zendesk_mcp_server.zendesk_client import ZendeskClient

# Import MCP types
from mcp.server import InitializationOptions, NotificationOptions
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("zendesk-mcp-server-http")

# Global instances
config: Optional[Config] = None
oauth_provider: Optional[ZendeskOAuthProvider] = None
token_storage: Optional[SQLiteTokenStorage] = None


def get_zendesk_client_for_session() -> ZendeskClient:
    """
    Get a ZendeskClient for the current authenticated session.

    Returns:
        ZendeskClient configured with the session's OAuth token

    Raises:
        HTTPException: If no valid session is available
    """
    session = get_current_session()
    if session is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return ZendeskClient.from_oauth_token(
        subdomain=session.zendesk_subdomain,
        access_token=session.access_token,
        timeout=config.timeout if config else 30,
    )


# Create MCP server instance
mcp_server = Server("Zendesk Server")


# ============================================================================
# MCP Handlers (reused from server.py with per-request client)
# ============================================================================

TICKET_ANALYSIS_TEMPLATE = """
You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

Please fetch the ticket info and comments to analyze it and provide:
1. A summary of the issue
2. The current status and timeline
3. Key points of interaction
4. Any attachments (images, files) that provide context

If comments contain image attachments, use the get_attachment tool to view them.

Remember to be professional and focus on actionable insights.
"""

COMMENT_DRAFT_TEMPLATE = """
You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

Please:
1. Fetch the ticket info and comments to understand the issue
2. Review any image attachments using the get_attachment tool if they provide relevant context
3. Search the knowledge base for relevant articles using the search_kb_articles tool
4. Draft a professional and helpful response that:
   - Acknowledges the customer's concern
   - Addresses the specific issues raised (including any issues shown in attachments)
   - Provides clear next steps or ask for specific details need to proceed
   - Maintains a friendly and professional tone
5. Ask for confirmation before commenting on the ticket

The response should be formatted well and ready to be posted as a comment.
"""


@mcp_server.list_prompts()
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


@mcp_server.get_prompt()
async def handle_get_prompt(name: str, arguments: Dict[str, str] | None) -> types.GetPromptResult:
    """Handle prompt requests"""
    if not arguments or "ticket_id" not in arguments:
        raise ValueError("Missing required argument: ticket_id")

    ticket_id = int(arguments["ticket_id"])
    try:
        if name == "analyze-ticket":
            prompt = TICKET_ANALYSIS_TEMPLATE.format(ticket_id=ticket_id)
            description = f"Analysis prompt for ticket #{ticket_id}"
        elif name == "draft-ticket-response":
            prompt = COMMENT_DRAFT_TEMPLATE.format(ticket_id=ticket_id)
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


@mcp_server.list_tools()
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
            name="get_multiple_tickets",
            description="Retrieve multiple Zendesk tickets by their IDs (up to 100 tickets in one call)",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Array of ticket IDs to retrieve (max 100)",
                        "minItems": 1,
                        "maxItems": 100
                    },
                    "include_comments": {
                        "type": "boolean",
                        "description": "Whether to include comments for each ticket",
                        "default": False
                    }
                },
                "required": ["ticket_ids"]
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
                    },
                    "include_inline_images": {
                        "type": "boolean",
                        "description": "Whether to include inline image attachments",
                        "default": False
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
            name="search_kb_articles",
            description="Search Zendesk Help Center articles by query",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant articles"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of articles to return",
                        "default": 10
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language locale for articles",
                        "default": "en-us"
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_kb_article",
            description="Get a specific Zendesk Help Center article by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "integer",
                        "description": "The ID of the article to retrieve"
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language locale for the article",
                        "default": "en-us"
                    }
                },
                "required": ["article_id"]
            }
        ),
        types.Tool(
            name="list_kb_sections",
            description="List all Zendesk Help Center sections",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="get_section_articles",
            description="Get articles from a specific Zendesk Help Center section",
            inputSchema={
                "type": "object",
                "properties": {
                    "section_id": {
                        "type": "integer",
                        "description": "The ID of the section"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of articles to return",
                        "default": 20
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language locale for articles",
                        "default": "en-us"
                    }
                },
                "required": ["section_id"]
            }
        ),
        types.Tool(
            name="get_attachment",
            description="Download and view a Zendesk ticket attachment",
            inputSchema={
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": "The ID of the attachment to download"
                    }
                },
                "required": ["attachment_id"]
            }
        ),
        types.Tool(
            name="search_macros",
            description="Search Zendesk macros by query string",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant macros"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of macros to return",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_macro",
            description="Get a specific Zendesk macro by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "macro_id": {
                        "type": "integer",
                        "description": "The ID of the macro to retrieve"
                    }
                },
                "required": ["macro_id"]
            }
        ),
        types.Tool(
            name="apply_macro_to_ticket",
            description="Apply a Zendesk macro to a ticket",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_id": {
                        "type": "integer",
                        "description": "The ID of the ticket to apply the macro to"
                    },
                    "macro_id": {
                        "type": "integer",
                        "description": "The ID of the macro to apply"
                    }
                },
                "required": ["ticket_id", "macro_id"]
            }
        )
    ]


@mcp_server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle Zendesk tool execution requests"""
    try:
        if not arguments:
            raise ValueError("Missing arguments")

        # Get client for current session
        zendesk_client = get_zendesk_client_for_session()

        if name == "get_ticket":
            ticket = zendesk_client.get_ticket(arguments["ticket_id"])
            return [types.TextContent(type="text", text=json.dumps(ticket))]

        elif name == "get_multiple_tickets":
            include_comments = arguments.get("include_comments", False)
            tickets = zendesk_client.get_multiple_tickets(
                ticket_ids=arguments["ticket_ids"],
                include_comments=include_comments
            )
            return [types.TextContent(type="text", text=json.dumps(tickets, indent=2))]

        elif name == "get_ticket_comments":
            include_inline = arguments.get("include_inline_images", False)
            comments = zendesk_client.get_ticket_comments(
                ticket_id=arguments["ticket_id"],
                include_inline_images=include_inline
            )
            response_content = [types.TextContent(type="text", text=json.dumps(comments))]

            if include_inline:
                for comment in comments:
                    for attachment in comment.get('attachments', []):
                        if attachment.get('is_image', False):
                            try:
                                attachment_data = zendesk_client.get_attachment(attachment['id'])
                                response_content.append(types.ImageContent(
                                    type="image",
                                    data=attachment_data['data'],
                                    mimeType=attachment_data['content_type']
                                ))
                            except Exception as e:
                                logger.error(f"Failed to fetch attachment {attachment['id']}: {e}")
            return response_content

        elif name == "create_ticket_comment":
            public = arguments.get("public", True)
            result = zendesk_client.post_comment(
                ticket_id=arguments["ticket_id"],
                comment=arguments["comment"],
                public=public
            )
            return [types.TextContent(type="text", text=f"Comment created successfully: {result}")]

        elif name == "search_kb_articles":
            articles = zendesk_client.search_articles(
                query=arguments["query"],
                limit=arguments.get("limit", 10),
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(type="text", text=json.dumps(articles, indent=2))]

        elif name == "get_kb_article":
            article = zendesk_client.get_article(
                article_id=arguments["article_id"],
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(type="text", text=json.dumps(article, indent=2))]

        elif name == "list_kb_sections":
            sections = zendesk_client.list_sections()
            return [types.TextContent(type="text", text=json.dumps(sections, indent=2))]

        elif name == "get_section_articles":
            articles = zendesk_client.get_section_articles(
                section_id=arguments["section_id"],
                limit=arguments.get("limit", 20),
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(type="text", text=json.dumps(articles, indent=2))]

        elif name == "get_attachment":
            attachment_data = zendesk_client.get_attachment(int(arguments["attachment_id"]))
            if attachment_data['content_type'].startswith('image/'):
                return [types.ImageContent(
                    type="image",
                    data=attachment_data['data'],
                    mimeType=attachment_data['content_type']
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        'file_name': attachment_data['file_name'],
                        'content_type': attachment_data['content_type'],
                        'size': attachment_data['size'],
                        'base64_data': attachment_data['data'],
                        'note': 'Base64-encoded file content.'
                    }, indent=2)
                )]

        elif name == "search_macros":
            macros = zendesk_client.search_macros(
                query=arguments["query"],
                limit=arguments.get("limit", 10)
            )
            return [types.TextContent(type="text", text=json.dumps(macros, indent=2))]

        elif name == "get_macro":
            macro = zendesk_client.get_macro(arguments["macro_id"])
            return [types.TextContent(type="text", text=json.dumps(macro, indent=2))]

        elif name == "apply_macro_to_ticket":
            result = zendesk_client.apply_macro_to_ticket(
                ticket_id=arguments["ticket_id"],
                macro_id=arguments["macro_id"]
            )
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


@mcp_server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri=AnyUrl("zendesk://knowledge-base"),
            name="Zendesk Knowledge Base",
            description="Access to Zendesk Help Center articles and sections",
            mimeType="application/json",
        )
    ]


@mcp_server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    if uri.scheme != "zendesk":
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    path = str(uri).replace("zendesk://", "")
    if path != "knowledge-base":
        raise ValueError(f"Unknown resource path: {path}")

    try:
        zendesk_client = get_zendesk_client_for_session()
        sections = zendesk_client.list_sections()
        return json.dumps({
            "metadata": {
                "total_sections": len(sections),
                "sections": sections,
                "note": "Use the search_kb_articles tool to find specific articles"
            }
        }, indent=2)
    except Exception as e:
        logger.error(f"Error fetching knowledge base metadata: {e}")
        raise


# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global config, oauth_provider, token_storage

    logger.info("Starting Zendesk MCP Server (HTTP mode)...")

    # Load configuration
    config = get_config()

    if not config.is_oauth_mode:
        raise RuntimeError(
            "HTTP server requires OAuth configuration. "
            "Set ZENDESK_OAUTH_CLIENT_ID and related environment variables."
        )

    # Initialize token storage
    token_storage = SQLiteTokenStorage(
        db_path=config.server_config.database_url,
        encryption_key=config.server_config.token_encryption_key,
    )
    await token_storage.initialize()

    # Initialize OAuth provider
    oauth_provider = ZendeskOAuthProvider(
        config=config.oauth_config,
        token_storage=token_storage,
    )

    logger.info(f"Server initialized at {config.server_config.base_url}")

    yield

    # Cleanup
    if oauth_provider:
        await oauth_provider.close()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Zendesk MCP Server",
    description="Model Context Protocol server for Zendesk with OAuth authentication",
    version="0.2.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on your needs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# OAuth Endpoints
# ============================================================================

@app.get("/oauth/authorize")
async def oauth_authorize(
    subdomain: str = Query(..., description="Zendesk subdomain to authenticate"),
):
    """
    Start OAuth authorization flow.

    Redirects user to Zendesk authorization page.
    """
    if not oauth_provider:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    auth_url, _ = oauth_provider.generate_authorization_url(subdomain)
    return RedirectResponse(url=auth_url)


@app.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Zendesk"),
    state: str = Query(..., description="State parameter for CSRF protection"),
):
    """
    OAuth callback endpoint.

    Exchanges authorization code for access tokens.
    """
    if not oauth_provider:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    # Validate and consume state
    auth_state = oauth_provider.consume_state(state)
    if auth_state is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state parameter"
        )

    try:
        # Exchange code for tokens
        session = await oauth_provider.exchange_code_for_tokens(code, auth_state)

        # Return session ID to client
        return JSONResponse({
            "session_id": session.session_id,
            "subdomain": session.zendesk_subdomain,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "message": "Authentication successful. Use session_id as Bearer token.",
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/oauth/revoke")
async def oauth_revoke(request: Request):
    """Revoke an OAuth session."""
    if not oauth_provider:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    # Get session from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    session_id = auth_header.replace("Bearer ", "")
    success = await oauth_provider.revoke_session(session_id)

    if success:
        return JSONResponse({"message": "Session revoked successfully"})
    else:
        raise HTTPException(status_code=404, detail="Session not found")


# ============================================================================
# MCP Discovery Endpoints
# ============================================================================

@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource():
    """OAuth 2.0 Protected Resource Metadata (RFC 9728)."""
    if not config:
        raise HTTPException(status_code=500, detail="Server not configured")

    metadata = create_protected_resource_metadata(
        base_url=config.server_config.base_url,
        scopes=config.oauth_config.scopes,
    )
    return JSONResponse(metadata.to_dict())


@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server():
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)."""
    if not config:
        raise HTTPException(status_code=500, detail="Server not configured")

    metadata = create_authorization_server_metadata(
        base_url=config.server_config.base_url,
        scopes=config.oauth_config.scopes,
    )
    return JSONResponse(metadata.to_dict())


# ============================================================================
# MCP Transport Endpoints (Streamable HTTP with SSE)
# ============================================================================

@app.get("/mcp")
async def mcp_sse(request: Request):
    """
    MCP Server-Sent Events endpoint.

    This endpoint establishes an SSE connection for receiving server messages.
    """
    session = get_current_session()
    if session is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Authentication required"},
            headers={"WWW-Authenticate": 'Bearer realm="zendesk-mcp"'},
        )

    transport = SseServerTransport("/mcp")

    async def event_generator():
        async with transport.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as streams:
            await mcp_server.run(
                streams[0],
                streams[1],
                InitializationOptions(
                    server_name="Zendesk",
                    server_version="0.2.0",
                    capabilities=mcp_server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    return EventSourceResponse(event_generator())


@app.post("/mcp")
async def mcp_post(request: Request):
    """
    MCP HTTP POST endpoint for client messages.

    Receives MCP protocol messages from clients.
    """
    session = get_current_session()
    if session is None:
        return JSONResponse(
            status_code=401,
            content={"error": "Authentication required"},
            headers={"WWW-Authenticate": 'Bearer realm="zendesk-mcp"'},
        )

    transport = SseServerTransport("/mcp")
    return await transport.handle_post_message(
        request.scope,
        request.receive,
        request._send,
    )


# ============================================================================
# Health and Info Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Server info endpoint."""
    return JSONResponse({
        "name": "Zendesk MCP Server",
        "version": "0.2.0",
        "protocol": "MCP",
        "transport": "Streamable HTTP",
        "auth": "OAuth 2.0",
        "docs": "/docs",
    })


@app.get("/health")
async def health():
    """Health check endpoint."""
    return JSONResponse({"status": "healthy"})


# Add auth middleware after routes are defined
app.add_middleware(AuthMiddleware, oauth_provider=oauth_provider)


def run_server():
    """Run the HTTP server."""
    import uvicorn

    cfg = get_config()
    if not cfg.is_oauth_mode:
        raise RuntimeError("HTTP server requires OAuth configuration")

    uvicorn.run(
        app,
        host=cfg.server_config.host,
        port=cfg.server_config.port,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()
