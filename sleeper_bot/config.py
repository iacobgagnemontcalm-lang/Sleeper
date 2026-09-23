"""Load and validate the YAML config that lists the add/drop moves."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class PlayerRef:
    """A player as written in the config: a name, optionally narrowed by team/position, or a raw Sleeper id."""

    name: Optional[str] = None
    team: Optional[str] = None
    position: Optional[str] = None
    player_id: Optional[str] = None

    @classmethod
    def parse(cls, raw: Any, where: str) -> "PlayerRef":
        if isinstance(raw, str) and raw.strip():
            return cls(name=raw.strip())
        if isinstance(raw, dict):
            ref = cls(
                name=_opt_str(raw.get("name")),
                team=_opt_str(raw.get("team")),
                position=_opt_str(raw.get("position")),
                player_id=_opt_str(raw.get("id") or raw.get("player_id")),
            )
            if ref.name or ref.player_id:
                return ref
        raise ConfigError(f"{where}: expected a player name or a mapping with 'name' or 'id', got {raw!r}")

    def __str__(self) -> str:
        label = self.name or f"id {self.player_id}"
        extra = "/".join(x for x in (self.position, self.team) if x)
        return f"{label} ({extra})" if extra else label


@dataclass
class Move:
    add: PlayerRef
    drop: Optional[PlayerRef] = None


@dataclass
class Settings:
    headless: bool = True
    # Keep retrying pending moves for this long (e.g. start just before waivers clear).
    retry_minutes: float = 0.0
    retry_interval_seconds: float = 30.0
    # How long to poll the public API for the roster change after submitting.
    verify_timeout_seconds: float = 60.0
    screenshots: bool = True
    auth_file: str = ".auth/state.json"
    cache_dir: str = ".cache"


@dataclass
class Config:
    sleeper_username: str
    league_id: str
    moves: list[Move]
    settings: Settings = field(default_factory=Settings)
    selectors: dict[str, str] = field(default_factory=dict)


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_config(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config must be a YAML mapping")

    # May be left empty and supplied via the SLEEPER_USERNAME environment variable instead.
    username = _opt_str(data.get("sleeper_username")) or ""
    league_id = _opt_str(data.get("league_id"))
    if not league_id:
        raise ConfigError("'league_id' is required (quote it in YAML so it stays a string)")

    raw_moves = data.get("moves") or []
    if not isinstance(raw_moves, list):
        raise ConfigError("'moves' must be a list")
    moves = []
    for i, raw in enumerate(raw_moves, start=1):
        if not isinstance(raw, dict) or "add" not in raw:
            raise ConfigError(f"moves[{i}]: each move needs an 'add' key")
        add = PlayerRef.parse(raw["add"], f"moves[{i}].add")
        drop = PlayerRef.parse(raw["drop"], f"moves[{i}].drop") if raw.get("drop") is not None else None
        moves.append(Move(add=add, drop=drop))

    settings = Settings()
    raw_settings = data.get("settings") or {}
    if not isinstance(raw_settings, dict):
        raise ConfigError("'settings' must be a mapping")
    for key, value in raw_settings.items():
        if not hasattr(settings, key):
            raise ConfigError(f"unknown setting {key!r}")
        default = getattr(settings, key)
        try:
            if isinstance(default, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            else:
                value = type(default)(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"setting {key!r}: {exc}") from exc
        setattr(settings, key, value)

    selectors = data.get("selectors") or {}
    if not isinstance(selectors, dict):
        raise ConfigError("'selectors' must be a mapping")

    return Config(
        sleeper_username=username,
        league_id=league_id,
        moves=moves,
        settings=settings,
        selectors={str(k): str(v) for k, v in selectors.items()},
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found -- create it (see config.yaml in the repo for the format)")
    with path.open(encoding="utf-8") as fh:
        return parse_config(yaml.safe_load(fh))
