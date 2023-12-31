import asyncio

import structlog
import websockets as ws

from .handler import handler

logger = structlog.stdlib.get_logger()


async def main():
    logger.info("server.init")
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


def run():
    asyncio.run(main())
