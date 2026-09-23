"""Read-only client for Sleeper's public API (https://docs.sleeper.com).

The public API cannot make transactions, but it tells us who owns whom. We use it to
resolve player names to ids, skip moves that can no longer work, and confirm that a
submitted add/drop actually landed on the roster.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

import requests

from .config import PlayerRef

BASE_URL = "https://api.sleeper.app/v1"
PLAYERS_CACHE_TTL = 24 * 60 * 60  # Sleeper asks clients to fetch /players/nfl at most once a day.

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


class SleeperAPIError(RuntimeError):
    pass


class PlayerLookupError(LookupError):
    pass


def normalize_name(name: str) -> str:
    """'Odell Beckham Jr.' -> 'odell beckham'; 'D'Andre Swift' -> 'dandre swift'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[.'`]", "", text.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w]
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def player_label(player: dict) -> str:
    name = player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
    extra = "/".join(x for x in (player.get("position"), player.get("team") or "FA") if x)
    return f"{name} ({extra})"


def resolve_player(ref: PlayerRef, players: dict[str, dict]) -> str:
    """Return the Sleeper player_id for a config entry, or raise PlayerLookupError."""
    if ref.player_id:
        if ref.player_id not in players:
            raise PlayerLookupError(f"no Sleeper player with id {ref.player_id}")
        return ref.player_id

    wanted = normalize_name(ref.name or "")
    matches = []
    for pid, p in players.items():
        full = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}"
        if normalize_name(full) != wanted:
            continue
        if ref.team and (p.get("team") or "").upper() != ref.team.upper():
            continue
        if ref.position and (p.get("position") or "").upper() != ref.position.upper():
            continue
        matches.append(pid)

    if len(matches) > 1:
        # Prefer active players on an NFL team over retired namesakes.
        active = [pid for pid in matches if players[pid].get("active") and players[pid].get("team")]
        if len(active) == 1:
            return active[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PlayerLookupError(f"no player named {ref}")
    options = ", ".join(f"{player_label(players[pid])} id={pid}" for pid in matches[:5])
    raise PlayerLookupError(f"{ref} is ambiguous: {options}. Add 'team', 'position' or 'id' in the config.")


def roster_players(roster: dict) -> set[str]:
    ids: set[str] = set()
    for key in ("players", "reserve", "taxi"):
        ids.update(roster.get(key) or [])
    return ids


def find_my_roster(rosters: list[dict], user_id: str) -> dict:
    for roster in rosters:
        if roster.get("owner_id") == user_id or user_id in (roster.get("co_owners") or []):
            return roster
    raise SleeperAPIError("could not find your roster in this league -- check sleeper_username and league_id")


class SleeperAPI:
    def __init__(self, cache_dir: str | Path = ".cache", session: Optional[requests.Session] = None):
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()

    def _get(self, path: str) -> Any:
        url = f"{BASE_URL}{path}"
        try:
            resp = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            raise SleeperAPIError(f"GET {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise SleeperAPIError(f"GET {url} returned HTTP {resp.status_code}")
        data = resp.json()
        if data is None:
            raise SleeperAPIError(f"GET {url} returned nothing (bad username or league id?)")
        return data

    def user(self, username: str) -> dict:
        return self._get(f"/user/{username}")

    def league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters")

    def players(self) -> dict[str, dict]:
        cache = self.cache_dir / "players_nfl.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < PLAYERS_CACHE_TTL:
            with cache.open(encoding="utf-8") as fh:
                return json.load(fh)
        data = self._get("/players/nfl")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with cache.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return data
