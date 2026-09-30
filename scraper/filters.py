import re

from scraper.models import Job

ROLE_KEYWORDS = [
    "software engineer",
    "agent engineer",
    "member of technical staff",
    "forward deployed engineer",
]


def matches_role(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title).lower()
    return any(keyword in normalized for keyword in ROLE_KEYWORDS)


def filter_jobs(jobs: list[Job]) -> list[Job]:
    return [job for job in jobs if matches_role(job.title)]
