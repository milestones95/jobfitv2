"""MCP server: find a company's jobs and rank them by fit, from Claude.

Local (Claude Code):   python -m scraper.mcp_server
Hosted (claude.ai):    python -m scraper.mcp_server --http [--host 0.0.0.0] [--port 8000]   -> /mcp
"""

import argparse
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from scraper import pipeline, tasks
from scraper.descriptions import fetch_description
from scraper.filters import filter_jobs, matches_role
from scraper.models import Job
from scraper.ranking import DEFAULT_MIN_MATCH_PCT, suggestions

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

WAIT_SECONDS = 40

INSTRUCTIONS = """\
These tools find a company's open roles on its live careers site and rank them against the \
user's ideal role.

Workflow:
1. find_careers_page(company). Mention the careers page you found and keep going; don't stop \
to ask the user to confirm it.
2. list_jobs(company=... or careers_url=...), then call get_results(task_id) until status is \
"done" (up to about a minute). If the user gave you a careers URL, pass it and skip step 1.
3. If you don't know the user's ideal role yet, ask what kind of work they want (interests, \
must-haves, dealbreakers, seniority) and write it up as a few sentences of free text.
4. rank_jobs(listing_id, ideal_role), then call get_results(task_id) until "done". This takes \
1-3 minutes because each job's page is read and scored; tell the user it's running.
5. Show the suggestions as a table: match %, role (linked), location, and the one-line reason. \
Then briefly list the other roles, and offer get_job_details for any role the user wants to dig \
into (e.g. to tailor a resume or cover letter).

To re-rank with a different ideal role or threshold, call rank_jobs again with the same \
listing_id; it reuses the stored descriptions. To compare companies, run the steps for each.\
"""

mcp = MCPServer("jobfit", instructions=INSTRUCTIONS)


def job_summary(job: Job, with_reasoning: bool = False) -> dict:
    data = {"title": job.title, "url": job.url, "location": job.location}
    if job.match_pct is not None:
        data["match_pct"] = job.match_pct
    if with_reasoning and job.reasoning:
        data["reasoning"] = job.reasoning
    return data


def company_from_url(url: str) -> str:
    host = urlparse(url).hostname or url
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs")]
    return parts[0] if parts else host


@mcp.tool()
async def find_careers_page(company: str) -> dict:
    """Find a company's careers page and its open-positions page (if separate). Takes ~15 seconds."""
    result = await pipeline.find_careers(company, log=tasks.log_stderr)
    return result.model_dump(include={"company", "domain", "confidence", "careers_url", "jobs_url"})


@mcp.tool()
async def list_jobs(
    company: str | None = None,
    careers_url: str | None = None,
    engineering_only: bool = True,
    refresh: bool = False,
) -> dict:
    """Start reading every open role from a company's live careers site. Returns a task_id; call
    get_results(task_id) for the listing_id and roles. Pass careers_url if you have it, otherwise
    company. engineering_only limits the returned roles to software engineering titles (all roles
    are still stored and can be ranked by URL). Results from the last 6 hours are reused unless
    refresh is true."""
    if not company and not careers_url:
        raise ToolError("Pass company or careers_url")
    company = company or company_from_url(careers_url)

    async def work(log) -> dict:
        source_url, follow = careers_url, True
        if not source_url:
            found = await pipeline.find_careers(company, log=log)
            if not found.careers_url:
                raise RuntimeError(f"Couldn't find a careers page for {company}")
            # find_careers already followed any open-positions link.
            source_url, follow = found.jobs_url or found.careers_url, False

        listing = None if refresh else tasks.recent_listing(source_url)
        if listing:
            log(f"Reusing jobs read {int((time.time() - listing.fetched_at) / 60)} minutes ago")
        else:
            scraped = await pipeline.list_jobs(source_url, company, follow, log=log)
            listing = tasks.Listing(
                id=uuid.uuid4().hex[:12], company=company, source_url=source_url, url=scraped.url,
                strategy=scraped.strategy, jobs=scraped.jobs, fetched_at=time.time(),
            )
            tasks.save_listing(listing)

        engineering = filter_jobs(listing.jobs)
        shown = engineering if engineering_only else listing.jobs
        return {
            "listing_id": listing.id,
            "company": listing.company,
            "jobs_page": listing.url,
            "strategy": listing.strategy,
            "total_jobs": len(listing.jobs),
            "engineering_jobs": len(engineering),
            "jobs": [{**job_summary(j), "engineering": matches_role(j.title)} for j in shown],
        }

    return tasks.start_task("list_jobs", work).summary()


@mcp.tool()
async def rank_jobs(
    listing_id: str,
    ideal_role: str,
    min_match_pct: int = DEFAULT_MIN_MATCH_PCT,
    job_urls: list[str] | None = None,
) -> dict:
    """Start scoring jobs from a listing 0-100 against the user's ideal role (free text describing
    the work they want). Ranks the listing's software engineering roles, or only job_urls if given.
    Returns a task_id; call get_results(task_id). Takes 1-3 minutes the first time (each job page
    is read); re-ranking the same listing only re-scores."""
    listing = tasks.get_listing(listing_id)
    if not listing:
        raise ToolError(f"Unknown listing_id {listing_id}; call list_jobs first")
    if not ideal_role.strip():
        raise ToolError("ideal_role is empty; ask the user what kind of role they want")
    jobs = [j for j in listing.jobs if j.url in set(job_urls)] if job_urls else filter_jobs(listing.jobs)
    if not jobs:
        raise ToolError("No matching jobs to rank in this listing")

    async def work(log) -> dict:
        ranked = await pipeline.rank(jobs, ideal_role, log=log)
        # rank() filled descriptions in place on the listing's jobs; keep them for next time.
        tasks.save_listing(listing)
        suggested = suggestions(ranked, min_match_pct)
        return {
            "listing_id": listing.id,
            "company": listing.company,
            "min_match_pct": min_match_pct,
            "suggestions": [job_summary(j, with_reasoning=True) for j in suggested],
            "other_roles": [job_summary(j) for j in ranked[len(suggested):]],
        }

    return tasks.start_task("rank_jobs", work).summary()


@mcp.tool()
async def get_results(task_id: str) -> dict:
    """Get a list_jobs or rank_jobs task's result. Waits up to ~40 seconds for it to finish; if
    status is still "running", call again."""
    task = await tasks.wait_for_task(task_id, WAIT_SECONDS)
    if not task:
        raise ToolError(f"Unknown task_id {task_id}")
    return task.summary()


@mcp.tool()
async def get_job_details(url: str) -> dict:
    """Get the full description of one job posting, for a closer look or tailoring a resume or
    cover letter."""
    stored = tasks.find_stored_job(url)
    if stored and stored[1].description:
        job = stored[1]
    else:
        async with pipeline.open_browser() as context:
            description = await fetch_description(context, url)
        if not description:
            raise ToolError(f"Couldn't load {url}")
        if stored:
            listing, job = stored
            job.description = description
            tasks.save_listing(listing)
        else:
            job = Job(title="", url=url, description=description, source="html")
    return {**job_summary(job), "description": job.description}


@mcp.prompt()
def find_jobs(company: str, ideal_role: str = "") -> str:
    """Find and rank a company's open roles for me."""
    role = f"My ideal role:\n{ideal_role}" if ideal_role.strip() else "Ask me about my ideal role before ranking."
    return f"Find the open roles at {company} that best fit me, using the jobfit tools.\n\n{role}"


def main() -> None:
    parser = argparse.ArgumentParser(description="jobfit MCP server")
    parser.add_argument("--http", action="store_true", help="Serve streamable HTTP at /mcp instead of stdio")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
