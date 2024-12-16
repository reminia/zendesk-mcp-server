import asyncio

from . import server


def main():
    asyncio.run(server.main())


__all__ = ["main", "server"]
