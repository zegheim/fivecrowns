from __future__ import annotations

import logging
import random
import sys
from dataclasses import dataclass, field
from functools import cached_property, total_ordering

from websockets import WebSocketServerProtocol

DECK = range(1, 105)
MIN_PLAYERS, MAX_PLAYERS = 2, 10
CARDS_PER_PLAYER = 10
ROWS, COLS = 4, 5

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


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

    def __hash__(self):
        return hash(self.connection.id)

    @property
    def score(self) -> int:
        return sum(card.score for card in self.stack)

    def play(self, card: Card) -> bool:
        try:
            self.hand.remove(card)
        except KeyError:
            return False

        return True


@dataclass
class Board:
    rows: int = field(default=ROWS, kw_only=True)
    cols: int = field(default=COLS, kw_only=True)
    board: list[list[Card]] = field(init=False)
    logger: logging.Logger = field(default=logger.getChild(__qualname__), init=False, repr=False)

    def __post_init__(self):
        self.board = [[] for _ in range(self.rows)]

    @property
    def smallest_card(self) -> Card:
        return min(row[-1] for row in self.board)

    def _sweep(self, row: int, card: Card) -> tuple[Position, set[Card]]:
        self.logger.info("Sweeping row %d", row)

        stack = set(self.board[row])
        position = Position(row, 0)
        self.board[row] = [card]

        self.logger.info("Sweeped %s from row %d", stack, row)
        self.logger.info("Placed %s on %s", card, position)

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

        self.logger.info("Placed %s on %s", card, position)

        return position, set()


@dataclass
class Game:
    _class_logger: logging.Logger = field(default=logger.getChild(__qualname__), init=False, repr=False)

    # Session-related attributes
    session_id: str
    players: set[Player] = field(default_factory=set, init=False)
    board: Board = field(default_factory=Board, kw_only=True)
    cards_per_player: int = field(default=CARDS_PER_PLAYER, kw_only=True)
    min_players: int = field(default=MIN_PLAYERS, kw_only=True)
    max_players: int = field(default=MAX_PLAYERS, kw_only=True)
    started: bool = field(default=False, init=False)

    # Turn-related attributes
    lowest_card_player: Player | None = field(default=None, init=False)
    selected_row: int | None = field(default=None, init=False)
    cards_to_play: dict[Player, Card] = field(default_factory=dict, init=False)
    cards_played: dict[Player, tuple[Card, Position]] = field(default_factory=dict, init=False)
    progressed: bool = field(default=False, init=False)

    def __post_init__(self):
        assert MIN_PLAYERS <= self.min_players <= self.max_players <= MAX_PLAYERS
        assert self.max_players * self.cards_per_player + self.board.rows <= DECK[-1]
        self.logger = self._class_logger.getChild(self.session_id)

    @property
    def should_start(self):
        self.logger.debug("Game has started = %s | No. of players = %d", self.started, len(self.players))
        return (self.min_players <= len(self.players) <= self.max_players) and not self.started

    @property
    def should_progress(self):
        have_all_played = self.players == set(self.cards_to_play)
        have_all_same_number_of_cards_left = len(set(len(player.hand) for player in self.players)) == 1
        have_selected_a_row = self.lowest_card_player is None or self.selected_row is not None

        self.logger.debug(
            "Game has started = %s | Turn has not progressed = %s | Everyone has played a card = %s | Everyone has same number of cards left = %s | A row has been selected (if necessary) = %s",
            self.started,
            not self.progressed,
            have_all_played,
            have_all_same_number_of_cards_left,
            have_selected_a_row,
        )

        return self.started and not self.progressed and have_all_played and have_all_same_number_of_cards_left

    @property
    def should_end(self):
        have_no_cards_left = all(len(player.hand) == 0 for player in self.players)
        self.logger.debug("Game has started = %s | Everyone has no cards left = %s", have_no_cards_left)
        return self.started and have_no_cards_left

    def _deal(self) -> bool:
        if any(len(player.hand) > 0 for player in self.players):
            self.logger.warning("There are player(s) with non-empty hands, will not deal")
            return False

        if any(len(row) > 0 for row in self.board.board):
            self.logger.warning("There are non-empty row(s) in the board, will not deal")
            return False

        deck = [Card(value) for value in random.sample(DECK, len(DECK))]

        for _ in range(self.cards_per_player):
            for player in self.players:
                card = deck.pop()
                player.hand.add(card)
                self.logger.debug("Dealt %s to %s", card, player.connection.id)

        for row in range(self.board.rows):
            card = deck.pop()
            position, _ = self.board.place(deck.pop(), row=row)
            self.logger.debug("Dealt %s to %s on the board", card, position)

        return True

    def add(self, player: Player) -> bool:
        if self.started:
            self.logger.warning("Cannot add %s as game has already started", player.connection.id, self.session_id)
            return False

        if len(self.players) >= self.max_players:
            self.logger.warning("Cannot add %s as game is already full", player.connection.id)
            return False

        self.players.add(player)

        self.logger.info("Added %s to game", player.connection.id)

        return True

    def play(self, player: Player, card: Card) -> bool:
        if not self.started:
            self.logger.warning("%s cannot play %s as game has not started yet", player.connection.id, card)
            return False

        if self.progressed:
            self.logger.warning("%s cannot play %s as turn has already progressed", player.connection.id, card)
            return False

        if player in self.cards_to_play:
            self.logger.warning("%s cannot play %s as they have already played %s this turn", player.connection.id, card, self.cards_to_play[player])
            return False

        if not player.play(card):
            self.logger.warning("%s cannot play %s as it does not exist in their hand", player.connection.id, card)
            return False

        self.cards_to_play[player] = card

        if (self.lowest_card_player is None) and (card < self.board.smallest_card):
            self.logger.debug("%s played %s, which is lower than the lowest card in play %s", player.connection.id, card, self.board.smallest_card)
            self.lowest_card_player = player

        if (self.lowest_card_player is not None) and (card < self.cards_to_play[self.lowest_card_player]):
            self.logger.debug(
                "%s played %s, lower than %s (%s)",
                player.connection.id,
                card,
                self.cards_to_play[self.lowest_card_player],
                self.lowest_card_player,
            )
            self.lowest_card_player = player

        self.logger.info("%s played %s", player.connection.id, card)

        return True

    def select(self, player: Player, row: int) -> bool:
        if not self.should_progress:
            self.logger.warning("%s cannot select %d as turn should not progress yet", player.connection.id, row)
            return False

        if player != self.lowest_card_player:
            self.logger.warning("%s cannot select %d as they are not the lowest card player (%s)", player.connection.id, row, self.lowest_card_player)
            return False

        if self.selected_row is not None:
            self.logger.warning("%s cannot select %d as they have already selected %d this turn", player.connection.id, row, self.selected_row)
            return False

        if not self.board.is_valid(row):
            self.logger.warning("%s cannot select %d as it is not a valid row", player.connection.id, row)
            return False

        self.selected_row = row

        self.logger.info("%s selected row %d", player.connection.id, row)

        return True

    def progress(self) -> bool:
        if not self.should_progress:
            self.logger.warning("Game cannot progress as it should not progress yet")
            return False

        for idx, (player, card) in enumerate(sorted(self.cards_to_play.items(), key=lambda m: m[1])):
            row = self.selected_row if idx == 0 else None
            position, stack = self.board.place(card, row=row)
            player.stack |= stack
            self.cards_played[player] = (card, position)

            if len(stack):
                self.logger.debug("%s played %s on %s, sweeping %s", player.connection.id, card, position, stack)
            else:
                self.logger.debug("%s played %s on %s", player.connection.id, card, position)

        self.progressed = True

        self.logger.info("Turn has progressed")

        return True

    def start(self) -> bool:
        if self.should_start:
            self.started = self._deal()

        return self.started

    def reset(self) -> bool:
        if not self.progressed:
            self.logger.warning("Cannot reset turn as turn has not progressed yet")
            return False

        self.lowest_card_player = None
        self.selected_row = None
        self.cards_to_play = {}
        self.cards_played = {}
        self.progressed = False

        self.logger.info("Resetted turn")

        return True
