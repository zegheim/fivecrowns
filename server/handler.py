from __future__ import annotations

import json
import secrets
from typing import Any

import structlog
import websockets as ws

from .sixnimmt import Card, Player, Session

SESSIONS: dict[str, Session] = {}

logger = structlog.stdlib.get_logger()


def _broadcast(session: Session, payload: dict[str, Any]):
    ws.broadcast((player.connection for player in session.players), json.dumps(payload))
    logger.info("server.broadcast", session=session.session_id, **payload)


async def _send(player: Player, payload: dict[str, Any]):
    await player.connection.send(json.dumps(payload))
    logger.info("server.send", player_id=player.player_id, **payload)


async def _error(player: Player, message: str):
    await _send(player, {"type": "error", "message": message})


def leave(session: Session, player: Player):
    if not session.remove(player):
        return

    _broadcast(session, {"type": "leave", "player": str(player.player_id)})

    if len(session.players) == 0:
        del SESSIONS[session.session_id]
        logger.info("session.delete", session_id=session.session_id, player_id=player.player_id)


async def play(session: Session, player: Player, card: Card):
    if not session.play(player, card):
        await _error(player, f"Cannot play card {card.value}")
        return

    _broadcast(session, {"type": "play", "player": str(player.player_id)})

    if not session.should_progress:
        return

    for player, card in session.cards_to_play.items():
        _broadcast(session, {"type": "play", "player": str(player.player_id), "card": card.value})

    if session.smallest_card_player is not None:
        _broadcast(session, {"type": "select", "player": str(session.smallest_card_player.player_id)})
    else:
        await progress(session, player)


async def select(session: Session, player: Player, row: int):
    if not session.select(player, row):
        await _error(player, f"Cannot select row {row}")
        return

    _broadcast(session, {"type": "select", "player": str(player.player_id), "row": row})

    await progress(session, player)


async def progress(session: Session, player: Player):
    if not session.progress():
        return

    for player, (card, position) in session.cards_played.items():
        _broadcast(
            session,
            {
                "type": "play",
                "player": str(player.player_id),
                "row": position.row,
                "column": position.col,
                "card": card.value,
            },
        )

    if session.should_end:
        _broadcast(
            session,
            {"type": "end", "scores": {str(player.player_id): player.score for player in session.players}},
        )
    else:
        session.reset()


async def start(session: Session, player: Player):
    if not session.start():
        await _error(player, f"Cannot start game {session.session_id}.")
        return

    for idx, row in enumerate(session.board.board):
        _broadcast(session, {"type": "init", "row": idx, "column": 0, "card": row[0].value})

    for player in session.players:
        await _send(player, {"type": "deal", "cards": [card.value for card in player.hand]})


async def handle(session: Session, player: Player):
    async for message in player.connection:
        try:
            event = json.loads(message)
        except json.JSONDecodeError as err:
            await _error(player, err.msg)
            continue

        logger.info("server.receive", session_id=session.session_id, player_id=player.player_id, payload=event)

        match event:
            case {"type": "start"}:
                await start(session, player)
            case {"type": "play", "card": card}:
                await play(session, player, Card(card))
            case {"type": "select", "row": row}:
                await select(session, player, row)
            case _:
                await _error(player, f"Invalid payload: {message}")

        if session.should_end:
            break


async def host(player: Player):
    session_id = secrets.token_urlsafe(6)
    try:
        game = Session(session_id)
    except AssertionError:
        await _error(player, "Invalid game configuration.")
        return

    game.add(player)

    SESSIONS[session_id] = game

    try:
        await _send(player, {"type": "info", "sessionId": session_id, "players": [str(p.player_id) for p in game.players]})
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

    await _send(player, {"type": "info", "sessionId": session_id, "players": [str(p.player_id) for p in game.players]})

    _broadcast(game, {"type": "join", "player": str(player.player_id)})

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

    logger.info("server.receive", player_id=player.player_id, payload=event)

    match event:
        case {"type": "host"}:
            await host(player)
        case {"type": "join", "sessionId": session_id}:
            await join(player, session_id)
        case _:
            await _error(player, f"Invalid payload: {message}")
