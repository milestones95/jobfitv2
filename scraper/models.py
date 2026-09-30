from typing import Literal

from pydantic import BaseModel


class Job(BaseModel):
    title: str
    url: str | None = None
    location: str | None = None
    source: Literal["api", "html"]
    description: str | None = None
    # Set by scraper.ranking: 0-100 fit against the user's ideal role, and why.
    match_pct: int | None = None
    reasoning: str | None = None


class ScrapeResult(BaseModel):
    company: str
    domain: str
    confidence: str
    careers_url: str | None = None
    jobs_url: str | None = None
    # Which strategy produced the jobs: "ats:greenhouse", "api", "html", "html:search" (only the
    # site's search results for ROLE_KEYWORDS, so total_jobs is not every opening), or None.
    strategy: str | None = None
    total_jobs: int = 0
    jobs: list[Job] = []
    # Jobs scoring at or above this are the suggestions; None if jobs weren't scored.
    min_match_pct: int | None = None
    screenshot: bytes | None = None
