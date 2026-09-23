# Sleeper Free Agent Bot

Automatically add and drop free agents in your [Sleeper](https://sleeper.com) fantasy football league, so you
don't have to be awake when waivers clear.

Inspired by [philipfong/free-agent-sleeper](https://github.com/philipfong/free-agent-sleeper) (ESPN/Yahoo, Ruby),
rebuilt for Sleeper in Python + [Playwright](https://playwright.dev/python/).

## How it works

1. Sleeper's **public read API** is used to turn player names into ids, find your roster, and check whether
   the player you want is still available (and whether the player you're dropping is still on your team).
2. A **headless browser** logs in (with a saved session, or your password from GitHub Secrets) and clicks
   through the league's Players page: search → `+` → pick the player to drop → confirm. (Sleeper has no public API for making transactions.)
3. The bot then **checks the public API again** to confirm the roster actually changed, rather than trusting
   what the page said.
4. If the player is still on waivers, it can **keep retrying** for a set time. That way you can start it a few
   minutes before waivers clear.

## Run it on GitHub (no computer needed)

Everything runs in GitHub Actions: on a schedule when waivers clear, or when you press a button.

### 1. Add your login as secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `SLEEPER_USERNAME` | Your Sleeper username (shown on your profile), used to find your team |
| `SLEEPER_LOGIN` | What you type in the login box: email, phone or username |
| `SLEEPER_PASSWORD` | Your Sleeper password |

Secrets are encrypted and hidden in logs. Because the bot logs in automatically, it won't work if your
account asks for a text/email verification code at every login.

### 2. Test it with a dry run

**Actions → Sleeper bot → Run workflow**. To only check that the bot can log in, leave **add** empty and
mode on **dry-run**. To test a full move, put a free agent in **add** (and, optionally, one of your players
in **drop**), leave mode on **dry-run**, and run it. A dry run goes all the way to the final confirm button
and stops there. When it succeeds, the log ends with `dry run: would click '...'`. If it fails, download the
**screenshots** artifact at the bottom of the run page to see where it got stuck.

The log also prints your league's waiver settings (`Waivers: ...`), which you need for step 4.

### 3. List your moves

Edit [`config.yaml`](config.yaml) on github.com (pencil icon) and fill in `moves:`:

```yaml
moves:
  - add: Joe Flacco
    drop: Deshaun Watson
  - add: { name: Josh Allen, position: QB }   # disambiguate duplicate names
```

Moves run in order, and rosters are re-checked between moves. Once they're done, clear the list
(`moves: []`); an empty list makes the scheduled run do nothing. Moves that are already done are
skipped, not repeated.

For a one-off move you can also use **Run workflow** with mode **for-real**.

### 4. Scheduling

The schedule is at the top of [`.github/workflows/sleeper-bot.yml`](.github/workflows/sleeper-bot.yml):
by default **Wednesday 06:45 UTC (2:45 AM Eastern)**, retrying for 75 minutes. Change the `cron:` line to
start a little before your league's waivers clear. GitHub cron uses **UTC**.

Keep in mind:
- **GitHub can start scheduled runs late**, sometimes by 15+ minutes when it's busy. Start early and let
  `retry_minutes` cover the gap.
- A run that doesn't complete every move **fails**, and GitHub emails you. That happens if someone else got
  the player or it's still on waivers after the retry window.
- GitHub pauses schedules in repos with no commits for 60 days. Editing `config.yaml` counts as activity.
- This repo is **public**, so anyone can see its Actions logs and screenshots. Your password isn't in them,
  but your league, roster and moves are. You can make the repo private (Settings → General →
  Danger Zone); the free Actions minutes are plenty for this.

## Run it on your own computer

Requires Python 3.9+.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

python -m sleeper_bot login                    # sign in once in a real browser window; session saved to .auth/
python -m sleeper_bot check                    # names resolve? each move: attempt / taken / already_mine / drop_missing
python -m sleeper_bot run --dry-run --headed   # watch it; stops before confirming
python -m sleeper_bot run                      # for real
python -m sleeper_bot run --add "Joe Flacco" --drop "Deshaun Watson"   # one-off move, ignores config moves
```

Instead of `login`, you can set the `SLEEPER_LOGIN` and `SLEEPER_PASSWORD` environment variables.
Exit codes: `0` all moves done, `1` some moves not completed, `2` config/API error, `3` login problem.

## When Sleeper changes its website

The browser steps rely on Sleeper's page layout, which can change without warning. If a run stops finding
the search box, the `+` button or the confirm button:

1. Look at the screenshots (the **screenshots** artifact on GitHub, or `screenshots/` locally).
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
