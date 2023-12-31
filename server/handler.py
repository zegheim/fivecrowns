from __future__ import annotations

import json
import logging
import secrets
from typing import Any

import websockets as ws

from .sixnimmt import Card, Game, Player

SESSIONS: dict[str, Game] = {}

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _broadcast(game: Game, payload: dict[str, Any], stacklevel: int = 1):
    ws.broadcast((player.connection for player in game.players), json.dumps(payload))
    logger.info("BROADCAST: %s", payload, stacklevel=stacklevel + 1)


async def _send(player: Player, payload: dict[str, Any], stacklevel: int = 1):
    await player.connection.send(json.dumps(payload))
    logger.info("SEND (%s): %s", player.connection.id, payload, stacklevel=stacklevel + 1)


async def _error(player: Player, message: str):
    await _send(player, {"type": "error", "message": message}, stacklevel=2)


def leave(game: Game, player: Player):
    game.players.remove(player)
    _broadcast(game, {"type": "leave", "player": str(player.connection.id)})
    if len(game.players) == 0:
        session_id = game.session_id
        del SESSIONS[session_id]
        logger.info("Deleted %s as it is empty", session_id)


async def play(game: Game, player: Player, card: Card):
    if not game.play(player, card):
        await _error(player, f"Cannot play card {card.value}")
        return

    _broadcast(game, {"type": "play", "player": str(player.connection.id)})

    if not game.should_progress:
        return

    for player, card in game.cards_to_play.items():
        _broadcast(game, {"type": "play", "player": str(player.connection.id), "card": card.value})

    if game.lowest_card_player is not None:
        _broadcast(game, {"type": "select", "player": str(game.lowest_card_player.connection.id)})
    else:
        await progress(game, player)


async def select(game: Game, player: Player, row: int):
    if not game.select(player, row):
        await _error(player, f"Cannot select row {row}")
        return

    _broadcast(game, {"type": "select", "player": str(player.connection.id), "row": row})

    await progress(game, player)


async def progress(game: Game, player: Player):
    if not game.progress():
        return

    for player, (card, position) in game.cards_played.items():
        _broadcast(
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
        _broadcast(
            game,
            {"type": "end", "scores": {str(player.connection.id): player.score for player in game.players}},
        )
        for p in game.players:
            await p.connection.close(reason=f"Game {game.session_id} has ended.")
    else:
        game.reset()


async def start(game: Game, player: Player):
    if not game.start():
        await _error(player, f"Cannot start game {game.session_id}.")
        return

    for idx, row in enumerate(game.board.board):
        _broadcast(game, {"type": "init", "row": idx, "column": 0, "card": row[0].value})

    for player in game.players:
        await _send(player, {"type": "deal", "cards": [card.value for card in player.hand]})


async def handle(game: Game, player: Player):
    async for message in player.connection:
        try:
            event = json.loads(message)
        except json.JSONDecodeError as err:
            await _error(player, err.msg)
            continue

        logger.info("RECEIVE (%s): %s", player.connection.id, event)

        match event:
            case {"type": "start"}:
                await start(game, player)
            case {"type": "play", "card": card}:
                await play(game, player, Card(card))
            case {"type": "select", "row": row}:
                await select(game, player, row)
            case _:
                await _error(player, f"Invalid payload: {message}")

        if game.should_end:
            break


async def host(player: Player):
    session_id = secrets.token_urlsafe(6)
    try:
        game = Game(session_id)
    except AssertionError:
        await _error(player, "Invalid game configuration.")
        return

    game.add(player)

    SESSIONS[session_id] = game

    try:
        await _send(player, {"type": "info", "sessionId": session_id, "players": [str(p.connection.id) for p in game.players]})
        await handle(game, player)
    finally:
        leave(game, player)


async def join(player: Player, session_id: str):
    try:
        game = SESSIONS[session_id]
    except KeyError:
        await _error(player, "Game not found.")
        return

    if not game.add(player):
        await _error(player, f"Cannot join game {session_id}.")
        return

    await _send(player, {"type": "info", "sessionId": session_id, "players": [str(p.connection.id) for p in game.players]})

    _broadcast(game, {"type": "join", "player": str(player.connection.id)})

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
        await _error(player, f"Not a valid JSON: {message}")
        return

    logger.info("RECEIVE (%s): %s", player.connection.id, event)

    match event:
        case {"type": "host"}:
            await host(player)
        case {"type": "join", "sessionId": session_id}:
            await join(player, session_id)
        case _:
            await _error(player, f"Invalid payload: {message}")
