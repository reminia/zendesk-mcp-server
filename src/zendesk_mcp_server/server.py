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
    token=os.getenv("ZENDESK_API_KEY"),
    timeout=30,
)

server = Server("Zendesk Server")

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
            name="get_multiple_tickets",
            description="Retrieve multiple Zendesk tickets by their IDs (up to 100 tickets in one call). Optionally include comments for batch review workflows.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticket_ids": {
                        "type": "array",
                        "items": {
                            "type": "integer"
                        },
                        "description": "Array of ticket IDs to retrieve (max 100)",
                        "minItems": 1,
                        "maxItems": 100
                    },
                    "include_comments": {
                        "type": "boolean",
                        "description": "Whether to include comments for each ticket (default: false). Set to true for full batch review workflows.",
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
                        "description": "Whether to include inline image attachments (default: false)",
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
            description="Search Zendesk Help Center articles by query,  check locale argument base on user language",
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
                        "description": "Language locale for articles (default: 'en-us'). Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'",
                        "default": "en-us"
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="get_kb_article",
            description="Get a specific Zendesk Help Center article by ID, check locale argument base on user language",
            inputSchema={
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "integer",
                        "description": "The ID of the article to retrieve"
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language locale for the article (default: 'en-us'). Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'",
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
            description="Get articles from a specific Zendesk Help Center section with locale support",
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
                        "description": "Language locale for articles (default: 'en-us'). Examples: 'en-us', 'zh-cn', 'zh-tw', 'ja', 'ko', 'de', 'es', 'fr', 'it', 'ru', 'tr'",
                        "default": "en-us"
                    }
                },
                "required": ["section_id"]
            }
        ),
        types.Tool(
            name="get_attachment",
            description="Download and view a Zendesk ticket attachment (image, document, etc.)",
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


@server.call_tool()
async def handle_call_tool(
        name: str,
        arguments: dict[str, Any] | None
) -> list[types.TextContent]:
    """Handle Zendesk tool execution requests"""
    try:
        if not arguments:
            raise ValueError("Missing arguments")

        if name == "get_ticket":
            ticket = zendesk_client.get_ticket(arguments["ticket_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(ticket)
            )]

        elif name == "get_multiple_tickets":
            include_comments = arguments.get("include_comments", False)
            tickets = zendesk_client.get_multiple_tickets(
                ticket_ids=arguments["ticket_ids"],
                include_comments=include_comments
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(tickets, indent=2)
            )]

        elif name == "get_ticket_comments":
            include_inline = arguments.get("include_inline_images", False)
            comments = zendesk_client.get_ticket_comments(
                ticket_id=arguments["ticket_id"],
                include_inline_images=include_inline
            )

            # Build response content list
            response_content = [types.TextContent(
                type="text",
                text=json.dumps(comments)
            )]

            # If include_inline_images is True, fetch and append image attachments
            if include_inline:
                for comment in comments:
                    for attachment in comment.get('attachments', []):
                        if attachment.get('is_image', False):
                            try:
                                logger.info(f"Fetching inline image: {attachment['file_name']} (ID: {attachment['id']})")
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
            return [types.TextContent(
                type="text",
                text=f"Comment created successfully: {result}"
            )]

        elif name == "search_kb_articles":
            articles = zendesk_client.search_articles(
                query=arguments["query"],
                limit=arguments.get("limit", 10),
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(articles, indent=2)
            )]

        elif name == "get_kb_article":
            article = zendesk_client.get_article(
                article_id=arguments["article_id"],
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(article, indent=2)
            )]

        elif name == "list_kb_sections":
            sections = zendesk_client.list_sections()
            return [types.TextContent(
                type="text",
                text=json.dumps(sections, indent=2)
            )]

        elif name == "get_section_articles":
            articles = zendesk_client.get_section_articles(
                section_id=arguments["section_id"],
                limit=arguments.get("limit", 20),
                locale=arguments.get("locale", "en-us")
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(articles, indent=2)
            )]

        elif name == "get_attachment":
            logger.info(f"Downloading attachment {arguments}")

            attachment_data = zendesk_client.get_attachment(int(arguments["attachment_id"]))


            # If it's an image, return as ImageContent for native viewing
            if attachment_data['content_type'].startswith('image/'):
                return [types.ImageContent(
                    type="image",
                    data=attachment_data['data'],
                    mimeType=attachment_data['content_type']
                )]
            else:
                # For non-images (PDFs, docs, etc.), return metadata + base64
                return [types.TextContent(
                    type="text",
                    text=json.dumps({
                        'file_name': attachment_data['file_name'],
                        'content_type': attachment_data['content_type'],
                        'size': attachment_data['size'],
                        'base64_data': attachment_data['data'],
                        'note': 'Base64-encoded file content. Decode to access the file.'
                    }, indent=2)
                )]

        elif name == "search_macros":
            macros = zendesk_client.search_macros(
                query=arguments["query"],
                limit=arguments.get("limit", 10)
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(macros, indent=2)
            )]

        elif name == "get_macro":
            macro = zendesk_client.get_macro(arguments["macro_id"])
            return [types.TextContent(
                type="text",
                text=json.dumps(macro, indent=2)
            )]

        elif name == "apply_macro_to_ticket":
            result = zendesk_client.apply_macro_to_ticket(
                ticket_id=arguments["ticket_id"],
                macro_id=arguments["macro_id"]
            )
            return [types.TextContent(
                type="text",
                text=json.dumps(result, indent=2)
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
        )
    ]


@ttl_cache(ttl=7200)
def get_cached_sections():
    """Cache section list for 2 hours"""
    return zendesk_client.list_sections()


@ttl_cache(ttl=3600)
def get_cached_article(article_id: int, locale: str = 'en-us'):
    """Cache individual articles for 1 hour (per locale)"""
    return zendesk_client.get_article(article_id, locale)


@ttl_cache(ttl=900)
def search_cached_articles(query: str, limit: int = 10, locale: str = 'en-us'):
    """Cache search results for 15 minutes (per locale)"""
    return zendesk_client.search_articles(query, limit, locale)


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
        # Return lightweight metadata only
        sections = get_cached_sections()
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
