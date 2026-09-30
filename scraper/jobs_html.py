"""Step 3: no job API found, so convert the page HTML to markdown and have the LLM pull out jobs."""

import asyncio
import re
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify
from playwright.async_api import Page
from pydantic import BaseModel

from scraper.careers_finder import settle
from scraper.filters import ROLE_KEYWORDS
from scraper.llm import MODEL, async_client
from scraper.models import Job

CHUNK_CHARS = 40_000
MAX_CHUNKS = 5
MAX_PAGES = 100
MAX_SEARCH_PAGES = 10
SEARCH_HINT = re.compile(r"search|keyword|job|role|title|position|find", re.I)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
NEXT_BUTTON = re.compile(r"^(next|next page|›|»|→|load more|show more|more jobs|see more)$", re.I)
STRIP_TAGS = ["script", "style", "noscript", "svg", "nav", "footer", "header", "iframe"]

INSTRUCTIONS = (
    "You are given a careers page converted to markdown. Extract every individual job posting listed. "
    "For each, return the title exactly as shown, the URL from its markdown link (relative is fine), and "
    "the location if shown. Ignore navigation, blog posts, department or category filters, and generic "
    "links like 'View all jobs'. Return an empty list if there are no job postings. Also return "
    "job_url_pattern: the shortest URL substring shared by every job posting link and no other link "
    "(e.g. '/careers/listing/' or 'jobs.lever.co/acme/'), or null if there is none."
)


class ExtractedJob(BaseModel):
    title: str
    url: str | None = None
    location: str | None = None


class JobList(BaseModel):
    jobs: list[ExtractedJob]
    job_url_pattern: str | None = None


def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    markdown = markdownify(str(soup), heading_style="ATX")
    # Collapse the blank-line runs markdownify leaves behind.
    lines = [line.rstrip() for line in markdown.splitlines()]
    return "\n".join(line for i, line in enumerate(lines) if line or (i and lines[i - 1]))


def chunk(text: str) -> list[str]:
    chunks, current = [], []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > CHUNK_CHARS and current:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


async def page_markdown(page: Page) -> str:
    # Job boards are often embedded in iframes, so include every frame's content.
    parts = []
    for frame in page.frames:
        try:
            parts.append(html_to_markdown(await frame.content()))
        except Exception:
            continue
    return "\n\n".join(p for p in parts if p.strip())


async def extract_chunk(client, text: str) -> JobList:
    response = await client.responses.parse(
        model=MODEL, instructions=INSTRUCTIONS, input=text, text_format=JobList
    )
    return response.output_parsed or JobList(jobs=[])


def jobs_by_pattern(markdown: str, pattern: str, page_url: str) -> list[ExtractedJob]:
    return [
        ExtractedJob(title=text.strip(), url=url)
        for text, url in MARKDOWN_LINK.findall(markdown)
        if pattern in url and text.strip() and not text.startswith("![")
    ]


async def find_next(page: Page):
    """The listing's enabled "Next"/"Load more" control, or None."""
    candidates = page.locator("a, button, [role=button]").filter(has_text=NEXT_BUTTON)
    aria = page.locator("a[aria-label*='next' i], button[aria-label*='next' i]")
    for locator in (candidates, aria):
        for i in range(await locator.count()):
            control = locator.nth(i)
            try:
                if not await control.is_visible() or await control.is_disabled():
                    continue
                if await control.get_attribute("aria-disabled") == "true":
                    continue
                return control
            except Exception:
                continue
    return None


async def click_next(page: Page) -> bool:
    control = await find_next(page)
    if not control:
        return False
    try:
        await control.click(timeout=3000)
    except Exception:
        return False
    await page.wait_for_timeout(1500)
    await settle(page, timeout=3000)
    return True


async def collect_pages(page: Page, pattern: str, seen: set, max_pages: int) -> tuple[list[Job], int]:
    """Read job links on the current page, then keep clicking "Next" until nothing new shows up."""
    jobs = to_jobs(jobs_by_pattern(await page_markdown(page), pattern, page.url), page.url, seen)
    pages = 1
    while pages < max_pages and await click_next(page):
        new = to_jobs(jobs_by_pattern(await page_markdown(page), pattern, page.url), page.url, seen)
        if not new:
            break
        jobs.extend(new)
        pages += 1
    return jobs, pages


# ---------- Search agent: use the site's search box instead of paging through every role ----------

class SearchBoxChoice(BaseModel):
    index: int | None


async def find_search_box(page: Page, log: Callable[..., None]):
    inputs = page.locator("input[type=search], input[type=text], input:not([type])")
    candidates = []
    for i in range(await inputs.count()):
        box = inputs.nth(i)
        try:
            if not await box.is_visible():
                continue
            attrs = await box.evaluate(
                "el => [el.placeholder, el.getAttribute('aria-label'), el.name, el.id, el.type,"
                " el.labels && el.labels[0] ? el.labels[0].innerText : ''].filter(Boolean).join(' | ')"
            )
            candidates.append((box, attrs))
        except Exception:
            continue
    if not candidates:
        return None
    matches = [c for c in candidates if SEARCH_HINT.search(c[1])]
    if len(matches) == 1 or (matches and len(candidates) > 1 and len(matches) < len(candidates)):
        log(f"Search box: {matches[0][1]!r}")
        return matches[0][0]

    # Ambiguous: let the LLM choose which input searches job listings.
    listing = "\n".join(f"{i}: {attrs or '(no label)'}" for i, (_, attrs) in enumerate(candidates))
    response = await async_client().responses.parse(
        model=MODEL,
        instructions=(
            "These are the visible text inputs on a careers page. Return the index of the input used to "
            "search job listings by keyword, or null if none of them is."
        ),
        input=listing,
        text_format=SearchBoxChoice,
    )
    choice = response.output_parsed.index if response.output_parsed else None
    if choice is None or not 0 <= choice < len(candidates):
        return None
    log(f"Search box (LLM pick): {candidates[choice][1]!r}")
    return candidates[choice][0]


async def search_for_roles(
    page: Page, jobs_url: str, pattern: str, seen: set, log: Callable[..., None]
) -> list[Job] | None:
    """Search each role keyword in the site's search box. Returns None if there is no usable search box."""
    jobs: list[Job] = []
    for keyword in ROLE_KEYWORDS:
        await page.goto(jobs_url, wait_until="domcontentloaded", timeout=30000)
        await settle(page, timeout=5000)
        box = await find_search_box(page, log)
        if not box:
            return None if not jobs else jobs
        await box.fill(keyword)
        await box.press("Enter")
        await page.wait_for_timeout(2000)
        await settle(page, timeout=5000)
        found, pages = await collect_pages(page, pattern, seen, MAX_SEARCH_PAGES)
        log(f"Searched '{keyword}': {len(found)} jobs across {pages} page(s)")
        jobs.extend(found)
    return jobs


def to_jobs(extracted: list[ExtractedJob], page_url: str, seen: set) -> list[Job]:
    jobs = []
    for e in extracted:
        url = urljoin(page_url, e.url) if e.url else None
        key = (e.title.strip().lower(), url)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(Job(title=e.title.strip(), url=url, location=e.location, source="html"))
    return jobs


async def jobs_from_html(page: Page, log: Callable[..., None] = print) -> tuple[list[Job], str]:
    """Returns (jobs, strategy); strategy is "html:search" when only search results were collected."""
    jobs_url = page.url
    markdown = await page_markdown(page)
    chunks = chunk(markdown)
    log(f"HTML fallback: {len(markdown):,} chars of markdown in {len(chunks)} chunk(s)")
    if len(chunks) > MAX_CHUNKS:
        log(f"Only sending the first {MAX_CHUNKS} chunks to the LLM")
        chunks = chunks[:MAX_CHUNKS]

    client = async_client()
    results = await asyncio.gather(*(extract_chunk(client, c) for c in chunks))
    seen: set = set()
    jobs = to_jobs([job for r in results for job in r.jobs], page.url, seen)
    pattern = next((r.job_url_pattern for r in results if r.job_url_pattern), None)
    if not jobs or not pattern:
        return jobs, "html"

    # The LLM learned what job links look like; use that to read further pages without it.
    log(f"Job link pattern: '{pattern}'")
    if not await find_next(page):
        return jobs, "html"

    # Paginated listing: searching for the target roles is much faster than paging through every role.
    searched = await search_for_roles(page, jobs_url, pattern, seen, log)
    if searched is not None:
        return jobs + searched, "html:search"

    log("No search box; paging through all listings")
    more, pages = await collect_pages(page, pattern, seen, MAX_PAGES)
    log(f"Paged through {pages} pages of listings")
    return jobs + more, "html"
