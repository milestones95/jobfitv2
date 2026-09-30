"""Step 4: open each matching job's page to get its description when the listing didn't include one."""

import asyncio
from typing import Callable

from playwright.async_api import BrowserContext

from scraper.careers_finder import settle
from scraper.jobs_html import page_markdown
from scraper.models import Job

MAX_DESCRIPTION_CHARS = 6000
CONCURRENCY = 4


async def fetch_description(context: BrowserContext, url: str) -> str | None:
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await settle(page)
        return (await page_markdown(page))[:MAX_DESCRIPTION_CHARS] or None
    except Exception:
        return None
    finally:
        await page.close()


async def fill_descriptions(context: BrowserContext, jobs: list[Job], log: Callable[..., None] = print) -> None:
    """Sets description in place on jobs that have a URL but no description. Failures leave it blank."""
    missing = [job for job in jobs if job.url and not job.description]
    if not missing:
        return
    log(f"Fetching descriptions for {len(missing)} job pages")
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fill(job: Job) -> None:
        async with semaphore:
            job.description = await fetch_description(context, job.url)

    await asyncio.gather(*(fill(job) for job in missing))
    failed = sum(1 for job in missing if not job.description)
    if failed:
        log(f"Could not load {failed} job pages; those are scored on title only")
