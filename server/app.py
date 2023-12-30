import asyncio
import json
import logging.config

import websockets as ws
from handler import handler

with open("config/logging.json") as config:
    logging.config.dictConfig(json.load(config))


async def main():
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
