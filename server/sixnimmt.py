from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from functools import cached_property, total_ordering

import structlog
from websockets import WebSocketServerProtocol

DECK = range(1, 105)
MIN_PLAYERS, MAX_PLAYERS = 2, 10
CARDS_PER_PLAYER = 10
ROWS, COLS = 4, 5

logger = structlog.stdlib.get_logger()


@dataclass
class Position:
    row: int
    col: int


@dataclass
@total_ordering
class Card:
    value: int

    def __post_init__(self):
        assert DECK[0] <= self.value <= DECK[-1]

    def __hash__(self):
        return hash(self.value)

    def __lt__(self, other: Card):
        return self.value < other.value

    @cached_property
    def score(self) -> int:
        if self.value == 55:
            return 7

        if not (self.value % 11):
            return 5

        if not (self.value % 10):
            return 3

        if not (self.value % 5):
            return 2

        return 1


@dataclass
class Player:
    connection: WebSocketServerProtocol = field(repr=False)
    hand: set[Card] = field(default_factory=set, init=False, compare=False)
    stack: set[Card] = field(default_factory=set, init=False, compare=False)

    def __post_init__(self):
        self.logger = logger.bind(player_id=self.player_id)

    def __hash__(self):
        return hash(self.player_id)

    @property
    def player_id(self):
        return self.connection.id

    @property
    def score(self):
        return sum(card.score for card in self.stack)

    def play(self, card: Card):
        log = self.logger.bind(card=card)

        try:
            self.hand.remove(card)
        except KeyError:
            log.info("player.play", succeeded=False, reason="Card does not exist")
            return False

        log.debug("player.play", succeeded=True)

        return True


@dataclass
class Board:
    rows: int = field(default=ROWS, kw_only=True)
    cols: int = field(default=COLS, kw_only=True)
    board: list[list[Card]] = field(init=False)

    def __post_init__(self):
        self.board = [[] for _ in range(self.rows)]
        self.logger = logger.bind()

    @property
    def smallest_card(self) -> Card:
        return min(row[-1] for row in self.board)

    def _sweep(self, row: int, card: Card) -> tuple[Position, set[Card]]:
        stack = set(self.board[row])
        position = Position(row, 0)
        self.board[row] = [card]

        self.logger.info("board.place", card=card, position=position, cards=stack)

        return position, stack

    def is_valid(self, row: int) -> bool:
        return 0 <= row <= self.rows - 1

    def where(self, card: Card) -> int:
        min_idx = sys.maxsize
        min_diff = sys.maxsize

        for idx, row in enumerate(self.board):
            if card <= row[-1]:
                continue
            if (diff := card.value - row[-1].value) < min_diff:
                min_idx = idx
                min_diff = diff

        return min_idx

    def place(self, card: Card, row: int | None = None) -> tuple[Position, set[Card]]:
        if row is not None:
            return self._sweep(row, card)

        row = self.where(card)

        if (col := len(self.board[row])) >= self.cols:
            return self._sweep(row, card)

        position = Position(row, col)
        self.board[row].append(card)
        self.logger.info("board.place", card=card, position=position)

        return position, set()


@dataclass
class Session:
    # Session-related attributes
    session_id: str
    players: set[Player] = field(default_factory=set, init=False)
    board: Board = field(default_factory=Board, kw_only=True)
    cards_per_player: int = field(default=CARDS_PER_PLAYER, kw_only=True)
    min_players: int = field(default=MIN_PLAYERS, kw_only=True)
    max_players: int = field(default=MAX_PLAYERS, kw_only=True)
    started: bool = field(default=False, init=False)

    # Turn-related attributes
    smallest_card_player: Player | None = field(default=None, init=False)
    selected_row: int | None = field(default=None, init=False)
    cards_to_play: dict[Player, Card] = field(default_factory=dict, init=False)
    cards_played: dict[Player, tuple[Card, Position]] = field(default_factory=dict, init=False)
    progressed: bool = field(default=False, init=False)

    def __post_init__(self):
        assert MIN_PLAYERS <= self.min_players <= self.max_players <= MAX_PLAYERS
        assert self.max_players * self.cards_per_player + self.board.rows <= DECK[-1]

        self.logger = logger.bind(session_id=self.session_id)

    @property
    def should_start(self):
        if len(self.players) < self.min_players:
            self.logger.debug("session.should_start", succeeded=False, reason="Not enough players")
            return False

        if len(self.players) > self.max_players:
            self.logger.debug("session.should_start", succeeded=False, reason="Too many players")
            return False

        if any(len(player.hand) > 0 for player in self.players):
            self.logger.debug("session.should_start", succeeded=False, reason="There are already players with cards")
            return False

        if any(len(row) > 0 for row in self.board.board):
            self.logger.debug("session.should_start", succeeded=False, reason="There are already cards on the board")
            return False

        if self.started:
            self.logger.debug("session.should_start", succeeded=False, reason="Session has already started")
            return False

        self.logger.debug("session.should_start", succeeded=True)

        return True

    @property
    def should_progress(self):
        if not self.started:
            self.logger.debug("session.should_start", succeeded=False, reason="Session has not started")
            return False

        if self.progressed:
            self.logger.debug("session.should_start", succeeded=False, reason="Turn has already progressed")
            return False

        if self.players != set(self.cards_to_play):
            self.logger.debug("session.should_start", succeeded=False, reason="Not everyone has played")
            return False

        if len(set(len(player.hand) for player in self.players)) > 1:
            self.logger.debug("session.should_start", succeeded=False, reason="Not everyone has same number of cards left")
            return False

        if self.smallest_card_player is not None and self.selected_row is None:
            self.logger.debug(
                "session.should_start", succeeded=False, player=self.smallest_card_player.player_id, reason="A row has not been selected"
            )
            return False

        self.logger.debug("session.should_start", succeeded=True)

        return True

    @property
    def should_end(self):
        if not self.started:
            self.logger.debug("session.should_end", succeeded=False, reason="Session has not started")
            return False

        if any(len(player.hand) > 0 for player in self.players):
            self.logger.debug("session.should_end", succeeded=False, reason="There are players with cards left")
            return False

        self.logger.debug("session.should_end", succeeded=True)

        return True

    def _deal(self):
        deck = [Card(value) for value in random.sample(DECK, len(DECK))]

        for _ in range(self.cards_per_player):
            for player in self.players:
                card = deck.pop()
                player.hand.add(card)
                self.logger.debug("session.deal", player_id=player.player_id, card=card)

        for row in range(self.board.rows):
            card = deck.pop()
            position, _ = self.board.place(card, row=row)
            self.logger.debug("session.deal", card=card, position=position)

        self.logger.debug("session.deal", succeeded=True)

    def add(self, player: Player) -> bool:
        log = self.logger.bind(player_id=player.player_id)

        if self.started:
            log.warning("session.add_player", succeeded=False, reason="Session has started")
            return False

        if len(self.players) >= self.max_players:
            self.logger.warning("session.add_player", succeeded=False, reason="Session is full")
            return False

        self.players.add(player)

        self.logger.info("session.add_player", succeeded=True)

        return True

    def remove(self, player: Player) -> bool:
        log = self.logger.bind(player_id=player.player_id)

        try:
            self.players.remove(player)
        except KeyError:
            log.warning("session.remove_player", succeeded=False, reason="Player does not exist")
            return False

        # TODO: Cleanup turn-related states

        log.info("session.remove_player", succeeded=True)

        return True

    def play(self, player: Player, card: Card) -> bool:
        log = self.logger.bind(player_id=player.player_id, card=card)

        if not self.started:
            log.warning("session.play", succeeded=False, reason="Session has not started")
            return False

        if self.progressed:
            log.warning("session.play", succeeded=False, reason="Turn has already progressed")
            return False

        if player in self.cards_to_play:
            log.warning("session.play", succeeded=False, reason="Player has already played this turn")
            return False

        if not player.play(card):
            return False

        self.cards_to_play[player] = card

        if (self.smallest_card_player is None) and (card < self.board.smallest_card):
            self.smallest_card_player = player

        if (self.smallest_card_player is not None) and (card < self.cards_to_play[self.smallest_card_player]):
            self.smallest_card_player = player

        log.info("session.play", succeeded=True)

        return True

    def select(self, player: Player, row: int) -> bool:
        log = self.logger.bind(player_id=player.player_id, row=row)

        if not self.should_progress:
            log.warning("session.select", succeeded=False, reason="Turn is not ready to progress")
            return False

        if player != self.smallest_card_player:
            log.warning("session.select", succeeded=False, reason="Not the smallest card player")
            return False

        if self.selected_row is not None:
            log.warning("session.select", succeeded=False, reason="Already selected a row")
            return False

        if not self.board.is_valid(row):
            log.warning("session.select", succeeded=False, reason="Not a valid row")
            return False

        self.selected_row = row

        log.info("session.select", succeeded=True)

        return True

    def progress(self) -> bool:
        if not self.should_progress:
            self.logger.warning("session.progress", succeeded=False, reason="Turn is not ready to progress")
            return False

        for idx, (player, card) in enumerate(sorted(self.cards_to_play.items(), key=lambda m: m[1])):
            row = self.selected_row if idx == 0 else None
            position, stack = self.board.place(card, row=row)
            player.stack |= stack
            self.cards_played[player] = (card, position)

        self.progressed = True

        self.logger.info("session.progress", succeeded=True)

        return True

    def start(self) -> bool:
        if not self.should_start:
            self.logger.warning("session.start", succeeded=False, reason="Session is not ready to start")
            return False

        self._deal()
        self.started = True

        self.logger.info("session.start", succeeded=True)

        return True

    def reset(self) -> bool:
        if not self.progressed:
            self.logger.warning("session.reset", succeeded=False, reason="Turn has not progressed")
            return False

        self.smallest_card_player = None
        self.selected_row = None
        self.cards_to_play = {}
        self.cards_played = {}
        self.progressed = False

        self.logger.info("session.reset", succeeded=True)

        return True
