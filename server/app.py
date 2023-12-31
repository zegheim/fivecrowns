import asyncio
import json
import logging.config
from pathlib import Path

import websockets as ws

from .handler import handler

with open(Path(__file__).parent / "config/logging.json") as config:
    logging.config.dictConfig(json.load(config))

logger = logging.getLogger(__name__)


async def main():
    logger.info("Starting websockets server localhost:8001")
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


def run():
    asyncio.run(main())
