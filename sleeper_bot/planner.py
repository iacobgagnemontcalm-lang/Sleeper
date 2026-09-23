"""Pure decision logic: given current rosters, what should happen with each move?"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    ATTEMPT = "attempt"  # Player looks available and the drop is on our roster: go for it.
    ALREADY_MINE = "already_mine"  # Nothing to do.
    TAKEN = "taken"  # Someone else owns the player now.
    DROP_MISSING = "drop_missing"  # The player we planned to drop is no longer on our roster.


@dataclass
class ResolvedMove:
    add_id: str
    add_label: str
    drop_id: Optional[str] = None
    drop_label: Optional[str] = None

    def describe(self) -> str:
        if self.drop_id:
            return f"add {self.add_label}, drop {self.drop_label}"
        return f"add {self.add_label}"


def decide(move: ResolvedMove, my_players: set[str], owned_by_others: set[str]) -> Decision:
    if move.add_id in my_players:
        return Decision.ALREADY_MINE
    if move.add_id in owned_by_others:
        return Decision.TAKEN
    if move.drop_id and move.drop_id not in my_players:
        return Decision.DROP_MISSING
    return Decision.ATTEMPT


def move_landed(move: ResolvedMove, my_players: set[str]) -> bool:
    """True once the roster reflects the add (and the drop, if any)."""
    return move.add_id in my_players and (move.drop_id is None or move.drop_id not in my_players)
