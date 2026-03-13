"""
Chess game logic for Discord Atrium bots.
Wraps python-chess for board management, move validation, and rendering.

Supports two modes:
  - Human vs AI:  one side is a human Discord user, the other is a persona
  - AI vs AI:     both sides are personas (game runs autonomously)
"""

import chess
import random
from dataclasses import dataclass, field
from datetime import datetime


# Unicode piece symbols
PIECE_SYMBOLS = {
    (chess.PAWN,   chess.WHITE): '♟',
    (chess.ROOK,   chess.WHITE): '♜',
    (chess.KNIGHT, chess.WHITE): '♞',
    (chess.BISHOP, chess.WHITE): '♝',
    (chess.QUEEN,  chess.WHITE): '♛',
    (chess.KING,   chess.WHITE): '♚',
    (chess.PAWN,   chess.BLACK): '♙',
    (chess.ROOK,   chess.BLACK): '♖',
    (chess.KNIGHT, chess.BLACK): '♘',
    (chess.BISHOP, chess.BLACK): '♗',
    (chess.QUEEN,  chess.BLACK): '♕',
    (chess.KING,   chess.BLACK): '♔',
}


@dataclass
class ChessGame:
    """
    A chess game where each side is either a human or an AI persona.

    white_persona_key  — persona key for white, or None if a human plays white
    black_persona_key  — persona key for black, or None if a human plays black
    white_name         — display name for white (human display name or persona name)
    black_name         — display name for black
    human_name         — Discord display name of the human player; None for AI vs AI
    """
    white_name: str
    black_name: str
    white_persona_key: str | None   # None = human plays white
    black_persona_key: str | None   # None = human plays black
    human_name: str | None          # None for AI vs AI
    started_at: datetime = field(default_factory=datetime.now)
    board: chess.Board = field(default_factory=chess.Board)
    move_count: int = 0

    # ── derived properties ────────────────────────────────────────────────────

    @property
    def is_ai_vs_ai(self) -> bool:
        return self.white_persona_key is not None and self.black_persona_key is not None

    @property
    def current_persona_key(self) -> str | None:
        """Persona key of whoever is to move, or None if it's the human's turn."""
        if self.board.turn == chess.WHITE:
            return self.white_persona_key
        return self.black_persona_key

    @property
    def current_name(self) -> str:
        return self.white_name if self.board.turn == chess.WHITE else self.black_name

    @property
    def is_human_turn(self) -> bool:
        return self.current_persona_key is None

    @property
    def is_ai_turn(self) -> bool:
        return self.current_persona_key is not None

    @property
    def current_color_name(self) -> str:
        return "White" if self.board.turn == chess.WHITE else "Black"

    @property
    def current_turn_label(self) -> str:
        return f"{self.current_color_name} ({self.current_name})"

    # ── move helpers ──────────────────────────────────────────────────────────

    def get_legal_moves_san(self) -> list[str]:
        return [self.board.san(m) for m in self.board.legal_moves]

    def try_move(self, move_str: str) -> tuple[bool, str]:
        """
        Apply a move given as SAN or UCI. Returns (success, san_or_error).
        """
        move = None

        try:
            move = self.board.parse_san(move_str)
        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            pass

        if move is None:
            try:
                candidate = chess.Move.from_uci(move_str.lower().replace("-", ""))
                if candidate in self.board.legal_moves:
                    move = candidate
            except (ValueError, chess.InvalidMoveError):
                pass

        if move is None:
            return False, f"Invalid or illegal move: `{move_str}`"

        san = self.board.san(move)
        self.board.push(move)
        self.move_count += 1
        return True, san

    def pick_random_move(self) -> str:
        move = random.choice(list(self.board.legal_moves))
        return self.board.san(move)

    # ── display ───────────────────────────────────────────────────────────────

    def render_board(self, flip: bool = False) -> str:
        """
        Unicode board inside a code block.
        flip=True shows Black at the bottom.

        Each cell is 3 visual columns wide:
          piece glyph (~2 wide) + 1 space  = 3
          '.' (1 wide) + 2 spaces          = 3
        Column headers are spaced to match.
        """
        col_labels = 'hgfedcba' if flip else 'abcdefgh'
        header = '    ' + '  '.join(col_labels)  # 4-space prefix, cols 3 wide each

        lines = ['```']

        rank_range = range(0, 8) if flip else range(7, -1, -1)
        file_range = range(7, -1, -1) if flip else range(0, 8)

        for rank in rank_range:
            row = f'{rank + 1}   '  # rank number + 3 spaces = 4-char prefix
            for file in file_range:
                sq = chess.square(file, rank)
                piece = self.board.piece_at(sq)
                if piece:
                    row += PIECE_SYMBOLS[(piece.piece_type, piece.color)] + ' '  # glyph + 1 sp
                else:
                    row += '.  '  # dot + 2 spaces
            lines.append(row)

        lines.append(header)
        lines.append('```')
        return '\n'.join(lines)

    # ── state ─────────────────────────────────────────────────────────────────

    def status(self) -> str:
        if self.board.is_checkmate():
            return 'checkmate'
        if self.board.is_stalemate():
            return 'stalemate'
        if (self.board.is_insufficient_material()
                or self.board.is_seventyfive_moves()
                or self.board.is_fivefold_repetition()):
            return 'draw'
        if self.board.is_check():
            return 'check'
        return 'ongoing'

    def is_over(self) -> bool:
        return self.board.is_game_over()

    def result_description(self) -> str:
        s = self.status()
        if s == 'checkmate':
            # board.turn is the side that was checkmated (they are to move but have no moves)
            loser_name  = self.white_name if self.board.turn == chess.WHITE else self.black_name
            winner_name = self.black_name if self.board.turn == chess.WHITE else self.white_name
            return f"Checkmate! **{winner_name}** defeats {loser_name}."
        if s == 'stalemate':
            return "Stalemate — it's a draw!"
        if s == 'draw':
            return "The game is a draw."
        return "Game over."
