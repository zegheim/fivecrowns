from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from functools import cached_property, total_ordering

import websockets

DECK = range(1, 105)
TURNS = 10  # No. of cards dealt to each player
ROWS = 4
COLS = 5  # Maximum no. of cards in a given row
MIN_PLAYERS = 2
MAX_PLAYERS = 10


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
    connection: websockets.WebSocketServerProtocol
    hand: set[Card] = field(default_factory=set, init=False, compare=False)
    stack: set[Card] = field(default_factory=set, init=False, compare=False)

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
    board_id: uuid.UUID = field(default_factory=uuid.uuid4, init=False)
    rows: int = field(default=ROWS, compare=False, kw_only=True)
    cols: int = field(default=COLS, compare=False, kw_only=True)
    board: list[list[Card]] = field(init=False, compare=False)

    def __post_init__(self):
        self.board = [[] for _ in range(self.rows)]

    @property
    def smallest_card(self) -> Card:
        return min(row[-1] for row in self.board)

    def is_valid(self, row: int):
        return 0 <= row <= self.rows - 1

    def where(self, card: Card) -> int:
        min_idx = self.rows
        min_diff = DECK[-1]

        for idx, row in enumerate(self.board):
            if card < row[-1]:
                continue
            if (diff := card.value - row[-1].value) < min_diff:
                min_idx = idx
                min_diff = diff

        return min_idx

    def place(self, card: Card, row: int | None = None) -> tuple[Position, set[Card]]:
        row = row or self.where(card)

        if (col := len(self.board[row])) >= self.cols:
            stack = self.board[row]
            self.board[row] = [card]
            return (Position(row, 0), set(stack))
        else:
            self.board[row].append(card)
            return (Position(row, col), set())


@dataclass
class Game:
    # Session-related attributes
    session_id: str
    players: set[Player] = field(default_factory=set, init=False)
    board: Board = field(default_factory=Board, compare=False)
    min_players: int = field(default=MIN_PLAYERS, compare=False, kw_only=True)
    max_players: int = field(default=MAX_PLAYERS, compare=False, kw_only=True)
    started: bool = field(default=False, init=False, compare=False)

    # Turn-related attributes
    lowest_card_player: Player | None = field(default=None, init=False, compare=False)
    selected_row: int | None = field(default=None, init=False, compare=False)
    cards_to_play: dict[Player, Card] = field(default_factory=dict, init=False, compare=False)
    played_cards: dict[Player, tuple[Card, Position]] = field(default_factory=dict, init=False, compare=False)
    progressed: bool = field(default=False, init=False, compare=False)

    def __post_init__(self):
        assert MIN_PLAYERS <= self.min_players <= self.max_players
        assert self.min_players <= self.max_players <= MAX_PLAYERS

    @property
    def should_start(self):
        return (self.min_players <= len(self.players) <= self.max_players) and not self.started

    @property
    def should_progress(self):
        return (
            self.started
            and not self.progressed
            and set(self.cards_to_play.values()) == set(self.players)  # everyone has chosen to play a card
            and len(set(len(player.hand) for player in self.players)) == 1  # everyone has same number of cards left
        )

    @property
    def should_end(self):
        return all(len(player.hand) == 0 for player in self.players)

    def add(self, player: Player) -> bool:
        if len(self.players) >= self.max_players:
            return False

        self.players.add(player)

        return True

    def deal(self) -> bool:
        if self.started:
            return False

        if any(len(player.hand) > 0 for player in self.players):
            return False

        if any(len(row) > 0 for row in self.board.board):
            return False

        deck = [Card(value) for value in random.sample(DECK, len(DECK))]

        for _ in range(TURNS):
            for player in self.players:
                player.hand.add(deck.pop())

        for row in self.board.board:
            row.append(deck.pop())

        return True

    def play(self, player: Player, card: Card) -> bool:
        if not self.started:
            return False

        if player in self.cards_to_play:
            return False

        if not player.play(card):
            return False

        self.cards_to_play[player] = card

        if (self.lowest_card_player is None) and (card < self.board.smallest_card):
            # First player to play a card lower than the smallest card
            self.lowest_card_player = player

        if (self.lowest_card_player is not None) and (card < self.cards_to_play[self.lowest_card_player]):
            # Player plays a card lower than the current lowest card
            self.lowest_card_player = player

        return True

    def select(self, player: Player, row: int) -> bool:
        if not self.should_progress:
            return False

        if player != self.lowest_card_player:
            return False

        if self.selected_row is not None:
            return False

        if not self.board.is_valid(row):
            return False

        self.selected_row = row

        return True

    def progress(self) -> bool:
        if not self.should_progress:
            return False

        if (self.lowest_card_player is not None) and (self.selected_row is None):
            return False

        for idx, (player, card) in enumerate(sorted(self.cards_to_play.items(), key=lambda m: m[1])):
            row = self.selected_row if idx == 0 else None
            position, stack = self.board.place(card, row=row)
            player.stack |= stack
            self.played_cards[player] = (card, position)

        self.progressed = True

        return True

    def start(self) -> bool:
        if self.should_start:
            self.started = self.deal()

        return self.started

    def reset(self) -> bool:
        if not self.progressed:
            return False

        self.lowest_card_player = None
        self.selected_row = None
        self.cards_to_play = {}
        self.played_cards = {}
        self.progressed = False

        return True
