from typing import Literal

from pydantic import BaseModel


class Job(BaseModel):
    title: str
    url: str | None = None
    location: str | None = None
    source: Literal["api", "html"]


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
    screenshot: bytes | None = None
