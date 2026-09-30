"""Company name -> careers page -> job listings -> engineering roles -> ranked by fit.

Each step opens its own browser so they can also run separately (see scraper/mcp_server.py).
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from playwright.async_api import BrowserContext, async_playwright

from scraper.careers_finder import USER_AGENT, find_careers_url, settle
from scraper.descriptions import fill_descriptions
from scraper.domain_lookup import get_company_domain
from scraper.filters import filter_jobs
from scraper.jobs_api import ResponseRecorder, jobs_from_ats, jobs_from_responses
from scraper.jobs_html import jobs_from_html
from scraper.models import Job, JobListing, ScrapeResult
from scraper.positions import follow_positions_link, scroll_to_bottom
from scraper.ranking import DEFAULT_IDEAL_ROLE, DEFAULT_MIN_MATCH_PCT, rank_jobs, suggestions


@asynccontextmanager
async def open_browser(headless: bool = True) -> AsyncIterator[BrowserContext]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            yield await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
        finally:
            await browser.close()


async def find_careers(company: str, headless: bool = True, log: Callable[..., None] = print) -> ScrapeResult:
    """Step 1: the company's domain, careers page, and open-positions page (if separate)."""
    # The OpenAI client is synchronous, so keep it off the event loop.
    domain = await asyncio.to_thread(get_company_domain, company)
    log(f"Domain: {domain.domain} (confidence: {domain.confidence})")
    result = ScrapeResult(company=company, domain=domain.domain, confidence=domain.confidence)

    async with open_browser(headless) as context:
        page = await context.new_page()
        result.careers_url = await find_careers_url(page, f"https://{domain.domain}", log)
        if not result.careers_url:
            return result
        log(f"Careers page: {result.careers_url}")
        result.jobs_url = await follow_positions_link(page, log)
    return result


async def list_jobs(
    url: str,
    company: str,
    follow_positions: bool = True,
    headless: bool = True,
    log: Callable[..., None] = print,
) -> JobListing:
    """Step 2: every job listed at url. Set follow_positions=False when url is already the jobs page."""
    async with open_browser(headless) as context:
        recorder = ResponseRecorder(context)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await settle(page)
        if follow_positions:
            await follow_positions_link(page, log)
        else:
            await scroll_to_bottom(page)
            await settle(page, timeout=3000)

        # Prefer the roles the careers site itself shows; a job board's public API can list stale
        # postings, so it's only a fallback when the site yields nothing.
        jobs = await jobs_from_responses(context, recorder.responses, company, page.url, log)
        strategy = "api" if jobs else None
        if not jobs:
            jobs, strategy = await jobs_from_html(page, log)
            strategy = strategy if jobs else None
        if not jobs:
            ats = await jobs_from_ats(context, page, recorder.responses, log)
            if ats and ats[1]:
                strategy, jobs = ats

        log(f"Strategy: {strategy}; {len(jobs)} jobs")
        return JobListing(url=page.url, strategy=strategy, jobs=jobs, screenshot=await page.screenshot())


async def rank(
    jobs: list[Job], ideal_role: str, headless: bool = True, log: Callable[..., None] = print
) -> list[Job]:
    """Step 3: fill in missing descriptions, then score each job against the ideal role, best first."""
    if any(job.url and not job.description for job in jobs):
        async with open_browser(headless) as context:
            await fill_descriptions(context, jobs, log)
    return await rank_jobs(jobs, ideal_role, log)


async def run(
    company: str,
    ideal_role: str = DEFAULT_IDEAL_ROLE,
    min_match_pct: int = DEFAULT_MIN_MATCH_PCT,
    headless: bool = True,
    log: Callable[..., None] = print,
) -> ScrapeResult:
    result = await find_careers(company, headless, log)
    if not result.careers_url:
        return result

    listing = await list_jobs(result.jobs_url or result.careers_url, company, False, headless, log)
    result.strategy = listing.strategy
    result.total_jobs = len(listing.jobs)
    result.jobs = filter_jobs(listing.jobs)
    result.screenshot = listing.screenshot
    log(f"{len(result.jobs)} matching of {len(listing.jobs)} total jobs")

    if result.jobs and ideal_role.strip():
        result.jobs = await rank(result.jobs, ideal_role, headless, log)
        result.min_match_pct = min_match_pct
        log(f"{len(suggestions(result.jobs, min_match_pct))} jobs at or above {min_match_pct}% match")
    return result
