from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import websockets as ws
from sixnimmt import Board, Card, Game, Player

SESSIONS: dict[str, Game] = {}


def broadcast(game: Game, payload: dict[str, Any]):
    ws.broadcast((player.connection for player in game.players), json.dumps(payload))


def play(game: Game, player: Player, card: Card):
    if not game.play(player, card):
        return

    broadcast(game, {"type": "play", "player": player.connection.id})

    if not game.should_progress:
        return

    for player, card in game.cards_to_play.items():
        broadcast(game, {"type": "play", "player": player.connection.id, "card": card.value})

    if game.lowest_card_player is not None:
        broadcast(game, {"type": "select", "player": player.connection.id})
    else:
        progress(game, player)


def select(game: Game, player: Player, row: int):
    if not game.select(player, row):
        return

    broadcast(game, {"type": "select", "player": player.connection.id, "row": row})
    progress(game, player)


def progress(game: Game, player: Player):
    if not game.progress():
        return

    for player, (card, position) in game.played_cards.items():
        broadcast(
            game,
            {
                "type": "play",
                "player": player.connection.id,
                "row": position.row,
                "column": position.col,
                "card": card.value,
            },
        )

    if game.should_end:
        broadcast(
            game,
            {"type": "end", "scores": {player.connection.id: player.score for player in game.players}},
        )
    else:
        game.reset()


async def send(player: Player, payload: dict[str, Any]):
    await player.connection.send(json.dumps(payload))


async def error(player: Player, message: str):
    await send(player, {"type": "error", "message": message})


async def start(game: Game):
    if game.started:
        return

    game.start()

    for idx, row in enumerate(game.board.board):
        broadcast(game, {"type": "init", "row": idx, "column": 0, "card": row[0]})

    for player in game.players:
        await send(player, {"type": "deal", "cards": [card.value for card in player.hand]})


async def handle(player: Player, game: Game):
    async for message in player.connection:
        event = json.loads(message)

        match event:
            case {"type": "start"}:
                await start(game)
            case {"type": "play", "card": card}:
                play(game, player, Card(card))
            case {"type": "select", "row": row}:
                select(game, player, row)
            case _:
                raise NotImplementedError


async def host(websocket: ws.WebSocketServerProtocol):
    player = Player(websocket)
    game = Game(set([player]), Board())
    session_id = secrets.token_urlsafe(6)
    SESSIONS[session_id] = game

    try:
        await send(player, {"type": "host", "sessionId": session_id})
        await handle(player, game)
    finally:
        del SESSIONS[session_id]


async def join(websocket: ws.WebSocketServerProtocol, session_id: str):
    player = Player(websocket)

    try:
        game = SESSIONS[session_id]
    except KeyError:
        await error(player, "Game not found.")
        return

    if game.started:
        await error(player, "Game has already started.")
        return

    game.players.add(player)

    try:
        await handle(player, game)
    finally:
        game.players.remove(player)


async def handler(websocket: ws.WebSocketServerProtocol):
    message = await websocket.recv()
    event = json.loads(message)

    match event:
        case {"type": "host"}:
            await host(websocket)
        case {"type": "join", "sessionId": session_id}:
            await join(websocket, session_id)
        case _:
            raise NotImplementedError


async def main():
    async with ws.serve(handler, "", 8001):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
