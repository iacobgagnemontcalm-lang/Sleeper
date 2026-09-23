"""Drive sleeper.com with Playwright to submit add/drop transactions.

Sleeper has no public write API, so we click through the website the same way you
would. Sleeper changes its markup from time to time; every selector lives in
DEFAULT_SELECTORS and can be overridden under `selectors:` in config.yaml.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import Browser, Locator, Page, TimeoutError as PWTimeout, sync_playwright

SITE = "https://sleeper.com"

log = logging.getLogger("sleeper_bot")

DEFAULT_SELECTORS = {
    # Search box on the league's Players tab.
    "search_input": 'input[placeholder*="find" i], input[placeholder*="search" i], input[type="search"]',
    # The "+" button next to a free agent.
    "add_button": 'button:has-text("+"), [aria-label*="add" i], [class*="add-button" i], [class*="add-player" i]',
    # The popup that opens after pressing "+".
    "dialog": '[role="dialog"], [class*="modal" i]',
    # Control next to a rostered player in that popup that marks them to be dropped.
    "drop_button": 'button:has-text("-"), button:has-text("Drop"), [aria-label*="drop" i], [class*="drop" i]',
    # Final confirmation button inside the popup (matched against the button's text).
    "confirm_text": r"^\s*(confirm|submit|add|add\s*&\s*drop|drop\s*&\s*add|add\s+player)\b",
    # A button with this text means the player is still on waivers (a claim, not a free-agent add).
    "waiver_text": r"claim|waiver|bid",
    # Login page (used for unattended login, e.g. on GitHub Actions).
    "login_identifier": 'input[aria-label*="email" i], input[aria-label*="phone" i], input[type="email"], '
                        'input[type="tel"], input[placeholder*="email" i], input[placeholder*="phone" i]',
    "login_password": 'input[type="password"]',
    # A code box means Sleeper wants a texted/emailed verification code, which the bot can't answer.
    "login_code": 'input[autocomplete="one-time-code"], input[aria-label*="code" i]',
    "login_submit_text": r"^\s*(continue|next|sign\s*in|log\s*in)\s*$",
}


class Outcome(str, Enum):
    SUBMITTED = "submitted"
    ON_WAIVERS = "on_waivers"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class NotLoggedIn(RuntimeError):
    pass


def save_login(auth_file: str | Path) -> None:
    """Open a visible browser, let the user log in by hand, and save the session."""
    auth_file = Path(auth_file)
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{SITE}/login")
        input("Log in to Sleeper in the browser window, then press Enter here... ")
        context.storage_state(path=str(auth_file))
        browser.close()
    print(f"Saved login session to {auth_file}. Keep this file private.")


def name_pattern(full_name: str) -> re.Pattern:
    """Match 'Joe Flacco' as well as the abbreviated 'J. Flacco' Sleeper uses in tight layouts."""
    parts = full_name.split()
    options = [re.escape(full_name)]
    if len(parts) >= 2:
        options.append(rf"{re.escape(parts[0][0])}\.?\s*{re.escape(' '.join(parts[1:]))}")
    return re.compile(rf"^\s*(?:{'|'.join(options)})\s*$", re.I)


def describe_secret(value: str) -> str:
    """Shape of a secret for debugging -- never the value itself."""
    return (f"{len(value)} chars, {sum(c.isdigit() for c in value)} digits, "
            f"{sum(c.isalpha() for c in value)} letters, '@': {'@' in value}, '+': {'+' in value}, "
            f"spaces inside: {' ' in value.strip()}, extra spaces/newlines at ends: {value != value.strip()}")


def identifier_variants(identifier: str) -> list[str]:
    """The login as given, plus international forms if it looks like a North American phone number."""
    variants = [identifier]
    digits = re.sub(r"\D", "", identifier)
    if re.fullmatch(r"[\d\s().+-]+", identifier.strip()):
        if len(digits) == 10:
            variants += [f"+1{digits}", f"1{digits}"]
        elif len(digits) == 11 and digits.startswith("1"):
            variants += [f"+{digits}", digits[1:]]
    return list(dict.fromkeys(variants))


class SleeperSite:
    def __init__(self, page: Page, league_id: str, selectors: Optional[dict] = None,
                 screenshot_dir: Optional[Path] = None, credentials: Optional[tuple[str, str]] = None):
        self.page = page
        self.league_id = league_id
        self.sel = {**DEFAULT_SELECTORS, **(selectors or {})}
        self.screenshot_dir = screenshot_dir
        self.credentials = credentials

    def snap(self, label: str) -> None:
        if not self.screenshot_dir:
            return
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        self.page.screenshot(path=str(self.screenshot_dir / f"{stamp}-{slug}.png"), full_page=True)

    def _goto_players(self) -> bool:
        self.page.goto(f"{SITE}/leagues/{self.league_id}/players", wait_until="domcontentloaded")
        try:
            self.page.locator(self.sel["search_input"]).first.wait_for(state="visible", timeout=20_000)
            return True
        except PWTimeout:
            return False

    def open_players(self) -> None:
        if self._goto_players():
            return
        if not self.credentials:
            self.snap("not-logged-in")
            raise NotLoggedIn("could not open the Players page -- run `python -m sleeper_bot login` again, "
                              "or set SLEEPER_LOGIN / SLEEPER_PASSWORD")
        self.login(*self.credentials)
        if not self._goto_players():
            self.snap("players-page-missing")
            self.describe_page()
            raise NotLoggedIn("logged in, but the Players page still did not load (see screenshots)")

    def _visible(self, selector: str) -> Locator:
        return self.page.locator(selector).filter(visible=True).first

    def _click_submit(self) -> None:
        submit = re.compile(self.sel["login_submit_text"], re.I)
        # .last: the header's "LOG IN" button comes before the dialog's buttons.
        self.page.get_by_role("button", name=submit).filter(visible=True).last.click()

    def login(self, identifier: str, password: str) -> None:
        """Log in through Sleeper's login dialog (identifier -> CONTINUE -> password -> sign in)."""
        page, sel = self.page, self.sel
        log.info("Login value: %s", describe_secret(identifier))
        identifier, password = identifier.strip(), password.strip("\r\n")
        ident, pw = self._visible(sel["login_identifier"]), self._visible(sel["login_password"])
        not_found = page.get_by_text(re.compile(r"unable to find anyone", re.I)).filter(visible=True).first
        # Opening a league page while logged out shows the login dialog and returns there afterwards.
        page.goto(f"{SITE}/leagues/{self.league_id}/players", wait_until="domcontentloaded")
        try:
            try:
                ident.wait_for(timeout=15_000)
            except PWTimeout:
                page.get_by_role("button", name=re.compile(r"^\s*log\s*in\s*$", re.I)).filter(visible=True).first.click()
                ident.wait_for(timeout=10_000)
            for attempt in identifier_variants(identifier):
                ident.fill(attempt)
                log.info("Typed login matches: %s", ident.input_value() == attempt)
                if pw.is_visible():
                    break
                self._click_submit()
                try:
                    pw.or_(not_found).first.wait_for(timeout=10_000)
                except PWTimeout:
                    ident.press("Enter")
                if pw.is_visible() or not not_found.is_visible():
                    break
                log.info("Sleeper didn't recognize that login format")
            pw.wait_for(timeout=10_000)
            pw.fill(password)
            self._click_submit()
            pw.wait_for(state="hidden", timeout=30_000)
        except PWTimeout:
            self.snap("login-failed")
            self.describe_page()
            if not_found.is_visible():
                raise NotLoggedIn("Sleeper couldn't find an account for SLEEPER_LOGIN -- use your Sleeper "
                                  "username or email there instead")
            raise NotLoggedIn("login failed -- wrong login/password, or the login dialog changed (see log above)")
        page.wait_for_timeout(2_000)
        if self._visible(sel["login_code"]).is_visible():
            self.snap("login-code")
            raise NotLoggedIn("Sleeper asked for a verification code, which the bot can't answer")
        log.info("Logged in to Sleeper")

    def describe_page(self) -> None:
        """Log what's on the page (labels only, never typed values) so selector problems can be fixed from the log."""
        info = self.page.evaluate("""() => {
            const vis = el => el.checkVisibility ? el.checkVisibility({visibilityProperty: true, opacityProperty: true})
                                                 : !!(el.offsetWidth || el.offsetHeight);
            const txt = el => (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
            return {
                url: location.href,
                title: document.title,
                inputs: [...document.querySelectorAll('input, textarea')].filter(vis).map(el =>
                    `${el.tagName.toLowerCase()} type=${el.type} placeholder=${el.placeholder} aria=${el.getAttribute('aria-label')}`),
                buttons: [...new Set([...document.querySelectorAll('button, [role=button]')].filter(vis).map(txt).filter(Boolean))].slice(0, 40),
                dialogs: [...document.querySelectorAll('[role=dialog]')].filter(vis)
                    .map(d => d.innerText.replace(/\\s+/g, ' ').slice(0, 400)),
                links: [...new Set([...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => h.includes('/leagues/')))].slice(0, 30),
            };
        }""")
        log.info("Page: %s | title: %s", info["url"], info["title"])
        log.info("Inputs: %s", info["inputs"] or "none")
        log.info("Buttons: %s", info["buttons"])
        log.info("League links: %s", info["links"])
        # Mask long digit runs so a phone number shown in the dialog never lands in a public log.
        for text in info["dialogs"]:
            log.info("Dialog text: %s", re.sub(r"\d[\d\s().-]{5,}\d", "#######", text))

    def _container_with(self, anchor: Locator, inner_selector: str, max_depth: int = 8) -> Optional[Locator]:
        """Walk up from `anchor` to the nearest ancestor that contains exactly one `inner_selector`."""
        for depth in range(1, max_depth + 1):
            ancestor = anchor.locator(f"xpath=ancestor::*[{depth}]")
            if ancestor.locator(inner_selector).count() == 1:
                return ancestor
        return None

    def add_drop(self, add_name: str, drop_name: Optional[str], dry_run: bool = False) -> tuple[Outcome, str]:
        page, sel = self.page, self.sel
        self.open_players()

        search = page.locator(sel["search_input"]).first
        search.fill(add_name)
        name_el = page.get_by_text(name_pattern(add_name)).first
        try:
            name_el.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            self.snap(f"not-found-{add_name}")
            return Outcome.NOT_FOUND, f"{add_name} did not show up in the Players search"

        row = self._container_with(name_el, sel["add_button"])
        if row is None:
            self.snap(f"no-add-button-{add_name}")
            return Outcome.NOT_FOUND, f"no add button next to {add_name} (already rostered?)"
        row.locator(sel["add_button"]).first.click()

        dialog = page.locator(sel["dialog"]).last
        try:
            dialog.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            self.snap(f"no-dialog-{add_name}")
            return Outcome.FAILED, "add dialog did not open"
        self.snap(f"dialog-{add_name}")

        if dialog.get_by_role("button", name=re.compile(sel["waiver_text"], re.I)).count():
            return Outcome.ON_WAIVERS, f"{add_name} is still on waivers"

        if drop_name:
            drop_el = dialog.get_by_text(name_pattern(drop_name)).first
            try:
                drop_el.wait_for(state="visible", timeout=10_000)
            except PWTimeout:
                self.snap(f"drop-missing-{drop_name}")
                return Outcome.FAILED, f"{drop_name} not listed in the drop dialog"
            drop_row = self._container_with(drop_el, sel["drop_button"])
            (drop_row.locator(sel["drop_button"]).first if drop_row else drop_el).click()
            self.snap(f"drop-selected-{drop_name}")

        confirm = dialog.get_by_role("button", name=re.compile(sel["confirm_text"], re.I)).last
        try:
            confirm.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            self.snap(f"no-confirm-{add_name}")
            return Outcome.FAILED, "could not find the confirm button"

        if dry_run:
            return Outcome.SUBMITTED, f"dry run: would click '{confirm.inner_text().strip()}'"
        confirm.click()
        time.sleep(2)
        self.snap(f"after-confirm-{add_name}")
        return Outcome.SUBMITTED, "transaction submitted"


@contextmanager
def open_site(league_id: str, auth_file: str | Path, headless: bool = True,
              selectors: Optional[dict] = None, screenshot_dir: Optional[Path] = None,
              credentials: Optional[tuple[str, str]] = None) -> Iterator[SleeperSite]:
    auth_file = Path(auth_file)
    if not auth_file.exists() and not credentials:
        raise NotLoggedIn(f"{auth_file} not found -- run `python -m sleeper_bot login` first, "
                          "or set SLEEPER_LOGIN / SLEEPER_PASSWORD")
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                storage_state=str(auth_file) if auth_file.exists() else None,
                viewport={"width": 1400, "height": 1000},
            )
            page = context.new_page()
            yield SleeperSite(page, league_id, selectors, screenshot_dir, credentials)
            # Sleeper may refresh its token while we browse; keep the newest one.
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(auth_file))
        finally:
            browser.close()
