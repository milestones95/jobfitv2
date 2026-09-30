"""Company name -> careers page -> job listings -> engineering roles -> ranked by fit."""

import asyncio
from typing import Callable

from playwright.async_api import async_playwright

from scraper.careers_finder import USER_AGENT, find_careers_url
from scraper.descriptions import fill_descriptions
from scraper.domain_lookup import get_company_domain
from scraper.filters import filter_jobs
from scraper.jobs_api import ResponseRecorder, jobs_from_ats, jobs_from_responses
from scraper.jobs_html import jobs_from_html
from scraper.models import ScrapeResult
from scraper.positions import follow_positions_link
from scraper.ranking import DEFAULT_IDEAL_ROLE, DEFAULT_MIN_MATCH_PCT, rank_jobs, suggestions


async def run(
    company: str,
    ideal_role: str = DEFAULT_IDEAL_ROLE,
    min_match_pct: int = DEFAULT_MIN_MATCH_PCT,
    headless: bool = True,
    log: Callable[..., None] = print,
) -> ScrapeResult:
    # The OpenAI client is synchronous, so keep it off the event loop.
    domain = await asyncio.to_thread(get_company_domain, company)
    log(f"Domain: {domain.domain} (confidence: {domain.confidence})")
    result = ScrapeResult(company=company, domain=domain.domain, confidence=domain.confidence)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
        recorder = ResponseRecorder(context)
        page = await context.new_page()
        try:
            result.careers_url = await find_careers_url(page, f"https://{domain.domain}", log)
            if not result.careers_url:
                return result
            log(f"Careers page: {result.careers_url}")

            # Drop homepage traffic and reload so we capture only the careers page's own API calls.
            recorder.clear()
            await page.reload(wait_until="domcontentloaded")
            result.jobs_url = await follow_positions_link(page, log)

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

            result.strategy = strategy
            result.total_jobs = len(jobs)
            result.jobs = filter_jobs(jobs)
            result.screenshot = await page.screenshot()
            log(f"Strategy: {strategy}; {len(result.jobs)} matching of {len(jobs)} total jobs")

            if result.jobs and ideal_role.strip():
                await fill_descriptions(context, result.jobs, log)
                result.jobs = await rank_jobs(result.jobs, ideal_role, log)
                result.min_match_pct = min_match_pct
                log(f"{len(suggestions(result.jobs, min_match_pct))} jobs at or above {min_match_pct}% match")
            return result
        finally:
            await browser.close()
