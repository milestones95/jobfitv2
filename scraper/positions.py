"""Step 1: from the careers landing page, follow an "open positions" link if there is one."""

import re
from typing import Callable
from urllib.parse import urlsplit

from playwright.async_api import Page

from scraper.careers_finder import SKIP_HREF, best_link, collect_links, settle

POSITIONS_TEXT = re.compile(
    r"\b(open (positions|roles|jobs)|view (all )?(open )?(jobs|openings|roles|positions)"
    r"|see (all )?(open )?(jobs|roles|openings|positions)|search (all )?jobs|current openings"
    r"|job openings|browse (all )?jobs|explore (all )?(open )?(jobs|roles|opportunities)|all jobs)\b",
    re.I,
)
POSITIONS_HREF = re.compile(
    r"(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|smartrecruiters|workable"
    r"|/jobs\b|/openings|/positions|/search|/open-roles)",
    re.I,
)


URL_ONLY_SCORE = 5


def score_positions_link(text: str, href: str) -> int:
    if not href or SKIP_HREF.match(href):
        return 0
    score = 0
    if POSITIONS_TEXT.search(text):
        score += 10
    if POSITIONS_HREF.search(href):
        score += URL_ONLY_SCORE
    return score


def same_page(a: str, b: str) -> bool:
    """True if two URLs differ only by query string or #fragment."""
    strip = lambda u: urlsplit(u)._replace(query="", fragment="").geturl().rstrip("/")
    return strip(a) == strip(b)


async def scroll_to_bottom(page: Page, rounds: int = 3) -> None:
    for _ in range(rounds):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)


async def follow_positions_link(page: Page, log: Callable[..., None] = print) -> str:
    """Navigate to the open-positions page if the careers page links to one. Returns the current URL."""
    links = await collect_links(page, "body")
    target = best_link(links, page.url, scorer=score_positions_link)
    # Several links that match only by URL (e.g. an "Apply" link per job to jobs.ashbyhq.com)
    # mean the roles are listed on this page, so stay rather than follow one of them.
    url_only = {l["href"] for l in links if score_positions_link(l["text"], l["href"]) == URL_ONLY_SCORE}
    if target and len(url_only) > 1 and not any(
        score_positions_link(l["text"], l["href"]) > URL_ONLY_SCORE for l in links
    ):
        target = None
    if target and not same_page(target, page.url):
        log(f"Open positions link: {target}")
        await page.goto(target, wait_until="domcontentloaded", timeout=30000)
        await settle(page)
    else:
        log("No separate open positions link; staying on careers page")
    await scroll_to_bottom(page)
    await settle(page, timeout=3000)
    return page.url
