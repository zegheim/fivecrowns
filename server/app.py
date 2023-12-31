import asyncio
import logging.config

import websockets as ws

from .handler import handler

logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting websockets server localhost:8001")
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


def run():
    asyncio.run(main())
