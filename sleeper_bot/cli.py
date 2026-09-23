"""Command line entry point: `python -m sleeper_bot {login,check,run}`."""

from __future__ import annotations

import argparse
import functools
import logging
from pathlib import Path

from .api import PlayerLookupError, SleeperAPI, SleeperAPIError
from .browser import NotLoggedIn, open_site, save_login
from .config import ConfigError, load_config
from .planner import decide
from .runner import prepare, roster_snapshot, run

log = logging.getLogger("sleeper_bot")


def cmd_login(args: argparse.Namespace) -> int:
    auth_file = args.auth_file
    if auth_file is None:
        try:
            auth_file = load_config(args.config).settings.auth_file
        except ConfigError:
            auth_file = ".auth/state.json"
    save_login(auth_file)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    api = SleeperAPI(config.settings.cache_dir)
    prepared = prepare(config, api)
    mine, others = roster_snapshot(api, config.league_id, prepared.user_id)
    print(f"Your roster has {len(mine)} players. Planned moves:")
    for i, move in enumerate(prepared.moves, start=1):
        print(f"  {i}. {move.describe():<60} -> {decide(move, mine, others).value}")
    if not Path(config.settings.auth_file).exists():
        print(f"\nNo saved login at {config.settings.auth_file}; run `python -m sleeper_bot login` before `run`.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.headed:
        config.settings.headless = False
    if args.retry_minutes is not None:
        config.settings.retry_minutes = args.retry_minutes
    api = SleeperAPI(config.settings.cache_dir)
    factory = functools.partial(
        open_site,
        config.league_id,
        config.settings.auth_file,
        headless=config.settings.headless,
        selectors=config.selectors,
        screenshot_dir=Path("screenshots") if config.settings.screenshots else None,
    )
    return run(config, api, factory, dry_run=args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sleeper_bot", description="Auto add/drop free agents on Sleeper.")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="open a browser, log in by hand, and save the session")
    login.add_argument("--auth-file", help="where to save the session (default: from config)")
    login.set_defaults(func=cmd_login)

    check = sub.add_parser("check", help="validate config and show what would happen (no browser)")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="perform the add/drops")
    run.add_argument("--dry-run", action="store_true", help="go through the site but stop before confirming")
    run.add_argument("--headed", action="store_true", help="show the browser window")
    run.add_argument("--retry-minutes", type=float, help="override settings.retry_minutes")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        return args.func(args)
    except (ConfigError, PlayerLookupError, SleeperAPIError) as exc:
        log.error("%s", exc)
        return 2
    except NotLoggedIn as exc:
        log.error("%s", exc)
        return 3
    except Exception:  # noqa: BLE001 -- we run unattended; log everything
        log.exception("Unexpected error")
        return 1
