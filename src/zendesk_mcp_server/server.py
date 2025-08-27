import asyncio
import json
import logging
import os
from cachetools.func import ttl_cache
from dotenv import load_dotenv
from fastmcp import FastMCP

from zendesk_client import ZendeskClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("zendesk-mcp-server")
logger.info("Zendesk MCP server started")

# -------------------------------
# Setup Zendesk client
# -------------------------------
load_dotenv()
zendesk_client = ZendeskClient(
    subdomain=os.getenv("ZENDESK_SUBDOMAIN"),
    email=os.getenv("ZENDESK_EMAIL"),
    token=os.getenv("ZENDESK_API_KEY"),
)

# Initialize FastMCP server
server = FastMCP("Zendesk Server")


# -------------------------------
# Prompts
# -------------------------------
@server.prompt
def analyze_ticket(ticket_id: int):
    """Analyze a Zendesk ticket and provide insights"""
    
    return f"""
    You are a helpful Zendesk support analyst. You've been asked to analyze ticket #{ticket_id}.

    Please fetch the ticket info and comments to analyze it and provide:
    1. A summary of the issue
    2. The current status and timeline
    3. Key points of interaction

    Remember to be professional and focus on actionable insights.
"""


@server.prompt
def draft_ticket_response(ticket_id: int):
    """Draft a professional response to a Zendesk ticket"""

    return f"""
    You are a helpful Zendesk support agent. You need to draft a response to ticket #{ticket_id}.

    Please fetch the ticket info, comments and knowledge base to draft a professional and helpful response that:
    1. Acknowledges the customer's concern
    2. Addresses the specific issues raised
    3. Provides clear next steps or ask for specific details need to proceed
    4. Maintains a friendly and professional tone
    5. Ask for confirmation before commenting on the ticket

    The response should be formatted well and ready to be posted as a comment.
    """


# -------------------------------
# Tools
# -------------------------------
@server.tool
def get_ticket(ticket_id: int) -> str:
    """Retrieve a Zendesk ticket by its ID"""
    ticket = zendesk_client.get_ticket(ticket_id)
    return json.dumps(ticket)


@server.tool
def get_ticket_comments(ticket_id: int) -> str:
    """Retrieve all comments for a Zendesk ticket by its ID"""
    comments = zendesk_client.get_ticket_comments(ticket_id)
    return json.dumps(comments)


@server.tool
def create_ticket_comment(ticket_id: int, comment: str, public: bool = True) -> str:
    """Create a new comment on an existing Zendesk ticket"""
    result = zendesk_client.post_comment(ticket_id=ticket_id, comment=comment, public=public)
    return f"Comment created successfully: {result}"


# -------------------------------
# Resources
# -------------------------------
@ttl_cache(ttl=3600)
def get_cached_kb():
    return zendesk_client.get_all_articles()


@server.resource("zendesk://knowledge-base", name="Zendesk Knowledge Base")
def read_knowledge_base() -> str:
    """Access Zendesk Help Center articles and sections"""
    kb_data = get_cached_kb()
    return json.dumps(
        {
            "knowledge_base": kb_data,
            "metadata": {
                "sections": len(kb_data),
                "total_articles": sum(len(section["articles"]) for section in kb_data.values()),
            },
        },
        indent=2,
    )


if __name__ == "__main__":
    # Run over HTTP instead of stdio
    server.run(
        # transport="http",   # "stdio" is default, change to "http"
        # host="0.0.0.0",     # bind all interfaces (good for Docker/K8s)
        # port=8000           # pick your service port
    )
