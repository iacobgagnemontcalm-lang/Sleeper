"""Run the configured moves: check rosters, click through Sleeper, confirm via the API, retry."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, ContextManager, Optional

from .api import PlayerLookupError, SleeperAPI, find_my_roster, player_label, resolve_player, roster_players
from .browser import Outcome
from .config import Config
from .planner import Decision, ResolvedMove, decide, move_landed

log = logging.getLogger("sleeper_bot")

# Signature of browser.open_site bound to the config: returns a context manager yielding a SleeperSite.
SiteFactory = Callable[[], ContextManager]


@dataclass
class Prepared:
    user_id: str
    moves: list[ResolvedMove]
    players: dict[str, dict]


def prepare(config: Config, api: SleeperAPI) -> Prepared:
    """Look up the user and resolve every player name up front, so typos fail before game time."""
    user = api.user(config.sleeper_username)
    league = api.league(config.league_id)
    log.info("League: %s (%s season)", league.get("name", config.league_id), league.get("season", "?"))
    players = api.players()

    moves, errors = [], []
    for i, move in enumerate(config.moves, start=1):
        try:
            add_id = resolve_player(move.add, players)
            drop_id = resolve_player(move.drop, players) if move.drop else None
        except PlayerLookupError as exc:
            errors.append(f"move {i}: {exc}")
            continue
        moves.append(ResolvedMove(
            add_id=add_id,
            add_label=player_label(players[add_id]),
            drop_id=drop_id,
            drop_label=player_label(players[drop_id]) if drop_id else None,
        ))
    if errors:
        raise PlayerLookupError("\n".join(errors))

    # Make sure we can find the user's team now rather than at 4 AM.
    find_my_roster(api.rosters(config.league_id), user["user_id"])
    return Prepared(user_id=user["user_id"], moves=moves, players=players)


def roster_snapshot(api: SleeperAPI, league_id: str, user_id: str) -> tuple[set[str], set[str]]:
    rosters = api.rosters(league_id)
    mine = find_my_roster(rosters, user_id)
    others: set[str] = set()
    for roster in rosters:
        if roster is not mine:
            others |= roster_players(roster)
    return roster_players(mine), others


def site_name(player: dict) -> str:
    return player.get("full_name") or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()


def wait_for_landing(api: SleeperAPI, config: Config, user_id: str, move: ResolvedMove,
                     sleep: Callable[[float], None], clock: Callable[[], float]) -> bool:
    deadline = clock() + config.settings.verify_timeout_seconds
    while True:
        mine, _ = roster_snapshot(api, config.league_id, user_id)
        if move_landed(move, mine):
            return True
        if clock() >= deadline:
            return False
        sleep(5)


def run(config: Config, api: SleeperAPI, site_factory: SiteFactory, dry_run: bool = False,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
        prepared: Optional[Prepared] = None) -> int:
    prepared = prepared or prepare(config, api)
    players, user_id = prepared.players, prepared.user_id
    pending = list(prepared.moves)
    succeeded: list[ResolvedMove] = []
    given_up: list[tuple[ResolvedMove, str]] = []
    deadline = clock() + config.settings.retry_minutes * 60
    attempt = 0

    while pending:
        attempt += 1
        log.info("Round %d: %d move(s) pending", attempt, len(pending))
        still_pending = []
        with site_factory() as site:
            for move in pending:
                mine, others = roster_snapshot(api, config.league_id, user_id)
                decision = decide(move, mine, others)
                if decision is Decision.ALREADY_MINE:
                    log.info("%s is already on your roster", move.add_label)
                    succeeded.append(move)
                    continue
                if decision is Decision.TAKEN:
                    log.warning("Sorry, someone else got %s", move.add_label)
                    given_up.append((move, "taken by another team"))
                    continue
                if decision is Decision.DROP_MISSING:
                    log.warning("Skipping %s: %s is no longer on your roster", move.describe(), move.drop_label)
                    given_up.append((move, "drop player not on roster"))
                    continue

                log.info("Trying to %s", move.describe())
                drop_name = site_name(players[move.drop_id]) if move.drop_id else None
                outcome, detail = site.add_drop(site_name(players[move.add_id]), drop_name, dry_run=dry_run)
                log.info("  -> %s: %s", outcome.value, detail)

                if outcome is Outcome.SUBMITTED and dry_run:
                    succeeded.append(move)
                elif outcome is Outcome.SUBMITTED:
                    if wait_for_landing(api, config, user_id, move, sleep, clock):
                        log.info("Congratulations! %s was added.", move.add_label)
                        succeeded.append(move)
                    else:
                        log.warning("Submitted, but the roster has not changed yet; will re-check")
                        still_pending.append(move)
                else:
                    still_pending.append(move)

        pending = still_pending
        if not pending:
            break
        if clock() + config.settings.retry_interval_seconds > deadline:
            for move in pending:
                given_up.append((move, "out of retry time (still on waivers or UI not recognized)"))
            break
        log.info("Retrying in %.0fs", config.settings.retry_interval_seconds)
        sleep(config.settings.retry_interval_seconds)

    log.info("Done: %d succeeded, %d not completed", len(succeeded), len(given_up))
    for move, why in given_up:
        log.info("  not completed: %s (%s)", move.describe(), why)
    return 0 if not given_up else 1
