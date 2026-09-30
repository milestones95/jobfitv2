"""Step 5: score each job 0-100 against the user's ideal role and rank them.

Ported from job_search_helper/job_fit_finder.py. Scores are judged on the posting's actual
responsibilities, not its title, since titles are inconsistent across companies.
"""

import asyncio
from typing import Callable

from openai import AsyncOpenAI
from pydantic import BaseModel

from scraper.llm import MODEL, async_client
from scraper.models import Job

DEFAULT_IDEAL_ROLE = """\
Founding engineer, software engineer, technical member of staff or full stack engineer or backend engineer role building an AI-native product from 0 to 1.

Ideally involves browser automation, agents that navigate and interact with
the web, or similar interfaces where AI takes action rather than just
generating text.

Uses MCP (Model Context Protocol) and/or generative AI such as open ai in the actual
product, not just internal tooling.

Values direct customer contact — talking to users, listening for pain
points, and shipping fixes fast — over a purely backend/infra-driven
process.

Uses metrics like retention (not just usage or activation) as a core signal
for what's broken and what to build next.

Small team, high ownership, ambiguity expected — willing to wear whatever
hat the day requires.
"""
DEFAULT_MIN_MATCH_PCT = 65
# Bounds LLM calls per lookup on very large boards; listings are roughly newest-first.
MAX_JOBS_TO_SCORE = 150
DESC_TRUNCATE_CHARS = 6000
CONCURRENCY = 5

INSTRUCTIONS = (
    "You are a precise technical recruiter. Score how well ONE job posting matches a candidate's "
    "ideal-role criteria, judging primarily on the actual responsibilities and day-to-day work "
    "described in the posting, NOT on the job title (titles are inconsistent across companies). "
    "Do not reward keyword stuffing: a passing mention of a technology in a boilerplate blurb counts "
    "for far less than it being the core of the role.\n\n"
    "Anchor points:\n"
    "- 90-100: the core of the role is exactly what the candidate described.\n"
    "- 70-89: substantially matches, with one minor gap.\n"
    "- 40-69: same general title/domain, but the day-to-day focus is only tangentially related.\n"
    "- 10-39: shares surface keywords, but the substance of the role is unrelated.\n"
    "- 0-9: no meaningful connection.\n\n"
    "If the description is missing, too thin, or mostly culture/benefits boilerplate, say so in the "
    "reasoning and score from the title only, no higher than 60. In the reasoning (1-2 sentences), "
    "name which responsibilities matched or were missing."
)


class JobScore(BaseModel):
    score: int
    reasoning: str


async def score_job(client: AsyncOpenAI, job: Job, ideal_role: str) -> tuple[int, str]:
    """Never raises: a failure scores 0 so one bad job doesn't abort the run."""
    description = (job.description or "")[:DESC_TRUNCATE_CHARS] or "(no description available)"
    prompt = f'''Candidate's ideal role — responsibilities and must-haves:
"""{ideal_role.strip()}"""

Job posting to evaluate (title is context only, weight the description):
Title: {job.title}
Location: {job.location or ""}
Description:
"""{description}"""'''
    try:
        response = await client.responses.parse(
            model=MODEL, instructions=INSTRUCTIONS, input=prompt, text_format=JobScore
        )
        result = response.output_parsed
        return max(0, min(100, result.score)), result.reasoning.strip()[:400]
    except Exception as e:
        return 0, f"[scoring error: {e}]"


async def rank_jobs(jobs: list[Job], ideal_role: str, log: Callable[..., None] = print) -> list[Job]:
    """Scores every job (one LLM call each) and sorts best first. Does not apply a threshold; see suggestions()."""
    if len(jobs) > MAX_JOBS_TO_SCORE:
        log(f"{len(jobs)} jobs exceeds cap of {MAX_JOBS_TO_SCORE}; scoring the first {MAX_JOBS_TO_SCORE} only")
        jobs = jobs[:MAX_JOBS_TO_SCORE]
    log(f"Scoring {len(jobs)} jobs against the ideal role")
    client = async_client()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def score(job: Job) -> Job:
        async with semaphore:
            match_pct, reasoning = await score_job(client, job, ideal_role)
        return job.model_copy(update={"match_pct": match_pct, "reasoning": reasoning})

    ranked = await asyncio.gather(*(score(job) for job in jobs))
    failures = sum(1 for job in ranked if job.reasoning.startswith("[scoring error"))
    if failures:
        log(f"{failures}/{len(ranked)} jobs failed to score (shown at 0%)")
    return sorted(ranked, key=lambda job: job.match_pct, reverse=True)


def suggestions(jobs: list[Job], min_match_pct: int) -> list[Job]:
    return [job for job in jobs if job.match_pct is not None and job.match_pct >= min_match_pct]
