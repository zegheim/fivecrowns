from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import websockets as ws
from sixnimmt import Card, Game, Player

SESSIONS: dict[str, Game] = {}


def broadcast(game: Game, payload: dict[str, Any]):
    ws.broadcast((player.connection for player in game.players), json.dumps(payload))


def leave(game: Game, player: Player):
    game.players.remove(player)
    broadcast(game, {"type": "leave", "player": str(player.connection.id)})
    if len(game.players) == 0:
        del SESSIONS[game.session_id]


async def play(game: Game, player: Player, card: Card):
    if not game.play(player, card):
        await error(player, f"Cannot play card {card.value}")

    broadcast(game, {"type": "play", "player": str(player.connection.id)})

    if not game.should_progress:
        return

    for player, card in game.cards_to_play.items():
        broadcast(game, {"type": "play", "player": str(player.connection.id), "card": card.value})

    if game.lowest_card_player is not None:
        broadcast(game, {"type": "select", "player": str(game.lowest_card_player.connection.id)})
    else:
        await progress(game, player)


async def select(game: Game, player: Player, row: int):
    if not game.select(player, row):
        await error(player, f"Cannot select row {row}")

    broadcast(game, {"type": "select", "player": str(player.connection.id), "row": row})

    await progress(game, player)


async def progress(game: Game, player: Player):
    if not game.progress():
        return

    for player, (card, position) in game.cards_played.items():
        broadcast(
            game,
            {
                "type": "play",
                "player": str(player.connection.id),
                "row": position.row,
                "column": position.col,
                "card": card.value,
            },
        )

    if game.should_end:
        broadcast(
            game,
            {"type": "end", "scores": {str(player.connection.id): player.score for player in game.players}},
        )
        for p in game.players:
            await p.connection.close(reason=f"Game {game.session_id} has ended.")
    else:
        game.reset()


async def send(player: Player, payload: dict[str, Any]):
    await player.connection.send(json.dumps(payload))


async def error(player: Player, message: str):
    await send(player, {"type": "error", "message": message})


async def start(game: Game, player: Player):
    if not game.start():
        await error(player, f"Cannot start game {game.session_id}.")
        return

    for idx, row in enumerate(game.board.board):
        broadcast(game, {"type": "init", "row": idx, "column": 0, "card": row[0].value})

    for player in game.players:
        await send(player, {"type": "deal", "cards": [card.value for card in player.hand]})


async def handle(game: Game, player: Player):
    async for message in player.connection:
        try:
            event = json.loads(message)
        except json.JSONDecodeError as err:
            await error(player, err.msg)
            continue

        match event:
            case {"type": "start"}:
                await start(game, player)
            case {"type": "play", "card": card}:
                await play(game, player, Card(card))
            case {"type": "select", "row": row}:
                await select(game, player, row)
            case _:
                await error(player, f"Invalid payload: {message}")

        if game.should_end:
            break


async def host(player: Player):
    session_id = secrets.token_urlsafe(6)
    try:
        game = Game(session_id)
    except AssertionError:
        await error(player, "Invalid game configuration.")
        return

    game.add(player)

    SESSIONS[session_id] = game

    try:
        await send(player, {"type": "info", "sessionId": session_id, "players": [str(p.connection.id) for p in game.players]})
        await handle(game, player)
    finally:
        leave(game, player)


async def join(player: Player, session_id: str):
    try:
        game = SESSIONS[session_id]
    except KeyError:
        await error(player, "Game not found.")
        return

    if not game.add(player):
        await error(player, f"Cannot join game {session_id}.")
        return

    await send(player, {"type": "info", "sessionId": session_id, "players": [str(p.connection.id) for p in game.players]})

    broadcast(game, {"type": "join", "player": str(player.connection.id)})

    try:
        await handle(game, player)
    finally:
        leave(game, player)


async def handler(websocket: ws.WebSocketServerProtocol):
    player = Player(websocket)

    try:
        message = await player.connection.recv()
    except ws.ConnectionClosedOK:
        return

    try:
        event = json.loads(message)
    except json.JSONDecodeError:
        await error(player, f"Not a valid JSON: {message}")
        return

    match event:
        case {"type": "host"}:
            await host(player)
        case {"type": "join", "sessionId": session_id}:
            await join(player, session_id)
        case _:
            await error(player, f"Invalid payload: {message}")


async def main():
    async with ws.serve(handler, host="", port=8001):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
