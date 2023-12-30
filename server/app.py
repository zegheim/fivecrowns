import asyncio

import websockets as ws
from handler import handler


async def main():
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
