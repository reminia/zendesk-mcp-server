import asyncio


def main():
    # Imported here rather than at module scope so that `zendesk-auth` does not
    # pull in the MCP server, which logs and loads configuration on import.
    from . import server

    asyncio.run(server.main())


__all__ = ["main"]
