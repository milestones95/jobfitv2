"""Find a company's careers page by searching its website footer with Playwright."""

import re
from typing import Callable
from urllib.parse import urljoin

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

FOOTER_SELECTOR = "footer, [role=contentinfo], [class*=footer i], [id*=footer i]"

STRONG_TEXT = re.compile(
    r"\b(careers?|jobs|join (us|our team)|work (with|at|for) us|we'?re hiring|open (roles|positions))\b",
    re.I,
)
STRONG_HREF = re.compile(r"(careers?|jobs|join-?us|greenhouse\.io|lever\.co|myworkdayjobs|ashbyhq)", re.I)
SKIP_HREF = re.compile(r"^(mailto:|tel:|javascript:|#)", re.I)

COOKIE_BUTTONS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "#onetrust-accept-btn-handler",
]

FALLBACK_PATHS = ["/careers", "/jobs", "/company/careers", "/about/careers"]

EXTRACT_LINKS_JS = """
els => els.map(a => ({
    text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim(),
    href: a.getAttribute('href') || ''
}))
"""


def score_link(text: str, href: str) -> int:
    if not href or SKIP_HREF.match(href):
        return 0
    score = 0
    if STRONG_TEXT.search(text):
        score += 10
        # Short link text like "Careers" beats long sentences that merely mention jobs.
        if len(text) <= 20:
            score += 3
    if STRONG_HREF.search(href):
        score += 5
    return score


def best_link(
    links: list[dict], base_url: str, scorer: Callable[[str, str], int] = score_link
) -> str | None:
    scored = [(scorer(l["text"], l["href"]), l) for l in links]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    return urljoin(base_url, scored[0][1]["href"])


async def settle(page: Page, timeout: int = 5000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        pass


async def dismiss_cookie_banner(page: Page) -> None:
    for selector in COOKIE_BUTTONS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=1000)
                return
        except Exception:
            continue


async def collect_links(page: Page, selector: str) -> list[dict]:
    # Footer selectors can match nested containers, so dedupe by href.
    links = await page.eval_on_selector_all(f":is({selector}) a[href]", EXTRACT_LINKS_JS)
    seen, unique = set(), []
    for link in links:
        if link["href"] not in seen:
            seen.add(link["href"])
            unique.append(link)
    return unique


async def try_fallback_paths(page: Page, base_url: str) -> str | None:
    for path in FALLBACK_PATHS:
        url = urljoin(base_url, path)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except PlaywrightTimeout:
            continue
        if response and response.ok:
            return page.url
    return None


async def find_careers_url(page: Page, url: str, log: Callable[..., None] = print) -> str | None:
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await settle(page)
    await dismiss_cookie_banner(page)

    # Scroll to the bottom so lazy-loaded footers render.
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(1500)

    base_url = page.url
    footer_links = await collect_links(page, FOOTER_SELECTOR)
    log(f"Found {len(footer_links)} footer links")
    target = best_link(footer_links, base_url)

    if target:
        log(f"Careers link in footer: {target}")
    else:
        all_links = await collect_links(page, "body")
        target = best_link(all_links, base_url)
        if target:
            log(f"Careers link elsewhere on page: {target}")

    if target:
        await page.goto(target, wait_until="domcontentloaded", timeout=30000)
        await settle(page)
        return page.url

    log("No careers link found; trying common paths")
    return await try_fallback_paths(page, base_url)
