# Sleeper Free Agent Bot

Automatically add and drop free agents in your [Sleeper](https://sleeper.com) fantasy football league, so you
don't have to be awake when waivers clear.

Inspired by [philipfong/free-agent-sleeper](https://github.com/philipfong/free-agent-sleeper) (ESPN/Yahoo, Ruby),
rebuilt for Sleeper in Python + [Playwright](https://playwright.dev/python/).

## How it works

1. Sleeper's **public read API** is used to turn player names into ids, find your roster, and check whether
   the player you want is still available (and whether the player you're dropping is still on your team).
2. A **headless browser** logs in with your saved session and clicks through the league's Players page:
   search → `+` → pick the player to drop → confirm. (Sleeper has no public API for making transactions.)
3. The bot then **checks the public API again** to confirm the roster actually changed, rather than trusting
   what the page said.
4. If the player is still on waivers, it can **keep retrying** for a set time. That way you can start it a few
   minutes before waivers clear.

## Setup

Requires Python 3.9+.

```bash
git clone <this repo> && cd Sleeper
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml   # then edit it
```

Edit `config.yaml`:

```yaml
sleeper_username: your_sleeper_username
league_id: "123456789012345678"      # from https://sleeper.com/leagues/<league_id>/...
moves:
  - add: Joe Flacco
    drop: Deshaun Watson
  - add: { name: Josh Allen, position: QB }   # disambiguate duplicate names
    drop: { id: "4034" }                       # or use a Sleeper player id
settings:
  retry_minutes: 30
```

Moves run in order, and rosters are re-checked between moves.

## Usage

```bash
# 1. Log in once. A browser opens; sign in (including any 2FA), then press Enter in the terminal.
#    The session is saved to .auth/state.json (git-ignored, so keep it private).
python -m sleeper_bot login

# 2. Check your config: names resolve, and each move shows attempt / taken / already_mine / drop_missing.
python -m sleeper_bot check

# 3. Practice run: go through the site with the browser visible, but stop before clicking confirm.
python -m sleeper_bot run --dry-run --headed

# 4. For real.
python -m sleeper_bot run
```

Exit codes: `0` all moves done, `1` some moves not completed, `2` config/API error, `3` login expired.
Screenshots of each step are saved to `screenshots/` (turn this off with `settings.screenshots: false`).

## Scheduling

The bot does not schedule itself. Run it from cron or Windows Task Scheduler a few minutes before your
league's waivers clear, with `retry_minutes` set so it keeps trying through the clear time. The computer has
to be on and awake at that time.

**macOS / Linux (cron)**: Wednesdays at 02:55 local time, retrying for 30 minutes:

```cron
55 2 * * 3 cd /path/to/Sleeper && .venv/bin/python -m sleeper_bot run --retry-minutes 30 >> sleeper_bot.log 2>&1
```

**Windows Task Scheduler**
- Program: `C:\path\to\Sleeper\.venv\Scripts\python.exe`
- Arguments: `-m sleeper_bot run --retry-minutes 30`
- Start in: `C:\path\to\Sleeper`

The saved login lasts a long time but not forever. If a run exits with code 3, run `login` again.

## When Sleeper changes its website

The browser steps rely on Sleeper's page layout, which can change without warning. If a run stops finding
the search box, the `+` button or the confirm button:

1. Run `python -m sleeper_bot run --dry-run --headed` and look at the screenshots in `screenshots/`.
2. Override the relevant entry under `selectors:` in `config.yaml`. The defaults and what each one matches
   are in `DEFAULT_SELECTORS` in [`sleeper_bot/browser.py`](sleeper_bot/browser.py).

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## Disclaimer

Unofficial and not affiliated with Sleeper. Automating the website may go against Sleeper's terms of
service. Use at your own risk and follow your league's rules.
