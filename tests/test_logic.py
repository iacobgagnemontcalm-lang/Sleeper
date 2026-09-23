import contextlib

import pytest

from sleeper_bot.api import PlayerLookupError, find_my_roster, normalize_name, resolve_player
from sleeper_bot.browser import Outcome
from sleeper_bot.config import ConfigError, PlayerRef, parse_config
from sleeper_bot.planner import Decision, ResolvedMove, decide
from sleeper_bot.runner import run

PLAYERS = {
    "1": {"full_name": "Joe Flacco", "position": "QB", "team": "CLE", "active": True},
    "2": {"full_name": "Deshaun Watson", "position": "QB", "team": "CLE", "active": True},
    "3": {"full_name": "Josh Allen", "position": "QB", "team": "BUF", "active": True},
    "4": {"full_name": "Josh Allen", "position": "LB", "team": "JAX", "active": True},
    "5": {"full_name": "Odell Beckham Jr.", "position": "WR", "team": None, "active": True},
    "6": {"full_name": "Tank Bigsby", "position": "RB", "team": "JAX", "active": True},
}


def test_normalize_name():
    assert normalize_name("Odell Beckham Jr.") == "odell beckham"
    assert normalize_name("D'Andre Swift") == "dandre swift"
    assert normalize_name("  JOE   flacco ") == "joe flacco"


def test_resolve_player():
    assert resolve_player(PlayerRef(name="joe flacco"), PLAYERS) == "1"
    assert resolve_player(PlayerRef(name="Odell Beckham"), PLAYERS) == "5"
    assert resolve_player(PlayerRef(name="Josh Allen", position="qb"), PLAYERS) == "3"
    assert resolve_player(PlayerRef(player_id="4"), PLAYERS) == "4"
    with pytest.raises(PlayerLookupError, match="ambiguous"):
        resolve_player(PlayerRef(name="Josh Allen"), PLAYERS)
    with pytest.raises(PlayerLookupError, match="no player"):
        resolve_player(PlayerRef(name="Nobody Here"), PLAYERS)


def test_parse_config():
    cfg = parse_config({
        "sleeper_username": "me",
        "league_id": 123,
        "moves": [{"add": "Joe Flacco", "drop": "Deshaun Watson"}, {"add": {"name": "Josh Allen", "team": "BUF"}}],
        "settings": {"retry_minutes": "5", "headless": "false"},
    })
    assert cfg.league_id == "123"
    assert cfg.moves[1].drop is None and cfg.moves[1].add.team == "BUF"
    assert cfg.settings.retry_minutes == 5.0 and cfg.settings.headless is False
    with pytest.raises(ConfigError):
        parse_config({"sleeper_username": "me", "league_id": "1", "moves": []})
    with pytest.raises(ConfigError, match="unknown setting"):
        parse_config({"sleeper_username": "me", "league_id": "1", "moves": [{"add": "x"}], "settings": {"bogus": 1}})


def test_decide():
    move = ResolvedMove("1", "Joe", "2", "Deshaun")
    assert decide(move, {"2"}, set()) is Decision.ATTEMPT
    assert decide(move, {"1", "2"}, set()) is Decision.ALREADY_MINE
    assert decide(move, {"2"}, {"1"}) is Decision.TAKEN
    assert decide(move, set(), set()) is Decision.DROP_MISSING
    assert decide(ResolvedMove("1", "Joe"), set(), set()) is Decision.ATTEMPT


def test_find_my_roster_co_owner():
    rosters = [{"roster_id": 1, "owner_id": "a"}, {"roster_id": 2, "owner_id": "b", "co_owners": ["me"]}]
    assert find_my_roster(rosters, "me")["roster_id"] == 2


class FakeAPI:
    def __init__(self, rosters):
        self._rosters = rosters

    def user(self, username):
        return {"user_id": "me"}

    def league(self, league_id):
        return {"name": "Test League", "season": "2026"}

    def rosters(self, league_id):
        return [dict(r, players=list(r["players"])) for r in self._rosters]

    def players(self):
        return PLAYERS


class FakeSite:
    """Pretends to be sleeper.com: stays 'on waivers' for `waiver_rounds` calls, then applies the move."""

    def __init__(self, api, waiver_rounds=0):
        self.api, self.waiver_rounds, self.calls = api, waiver_rounds, []

    def add_drop(self, add_name, drop_name, dry_run=False):
        self.calls.append((add_name, drop_name))
        if self.waiver_rounds:
            self.waiver_rounds -= 1
            return Outcome.ON_WAIVERS, "on waivers"
        mine = self.api._rosters[0]["players"]
        name_to_id = {p["full_name"]: pid for pid, p in PLAYERS.items()}
        mine.append(name_to_id[add_name])
        if drop_name:
            mine.remove(name_to_id[drop_name])
        return Outcome.SUBMITTED, "ok"


def make(moves, rosters, waiver_rounds=0, retry_minutes=0):
    cfg = parse_config({
        "sleeper_username": "me", "league_id": "L", "moves": moves,
        "settings": {"retry_minutes": retry_minutes, "retry_interval_seconds": 30},
    })
    api = FakeAPI(rosters)
    site = FakeSite(api, waiver_rounds)
    now = [0.0]
    code = run(cfg, api, lambda: contextlib.nullcontext(site),
               sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    return code, site, api


def test_run_adds_and_drops():
    code, site, api = make(
        [{"add": "Joe Flacco", "drop": "Deshaun Watson"}],
        [{"owner_id": "me", "players": ["2"]}, {"owner_id": "x", "players": []}],
    )
    assert code == 0
    assert site.calls == [("Joe Flacco", "Deshaun Watson")]
    assert api._rosters[0]["players"] == ["1"]


def test_run_skips_taken_and_missing_drop():
    code, site, _ = make(
        [{"add": "Joe Flacco", "drop": "Deshaun Watson"}, {"add": "Tank Bigsby", "drop": "Deshaun Watson"}],
        [{"owner_id": "me", "players": ["2"]}, {"owner_id": "x", "players": ["1"]}],
    )
    # Flacco is taken; Bigsby proceeds because Watson was never dropped.
    assert code == 1
    assert site.calls == [("Tank Bigsby", "Deshaun Watson")]


def test_run_retries_until_waivers_clear():
    code, site, _ = make(
        [{"add": "Joe Flacco", "drop": "Deshaun Watson"}],
        [{"owner_id": "me", "players": ["2"]}],
        waiver_rounds=2, retry_minutes=5,
    )
    assert code == 0
    assert len(site.calls) == 3


def test_run_gives_up_after_retry_window():
    code, site, _ = make(
        [{"add": "Joe Flacco"}],
        [{"owner_id": "me", "players": []}],
        waiver_rounds=100, retry_minutes=2,
    )
    assert code == 1
    assert len(site.calls) == 5  # t=0, 30, 60, 90, 120; the next try would be past the deadline
