"""Background tasks and stored job listings for the MCP server.

Listing jobs and ranking them take minutes, longer than a client should block on one tool call,
so they run as asyncio tasks that the client polls. Listings (jobs plus any descriptions fetched
while ranking) are kept on disk so re-ranking or reading one job doesn't scrape the site again.
"""

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from scraper.models import Job

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "listings"
LISTING_TTL_SECONDS = 6 * 60 * 60
# Each running task holds a Chromium instance, so cap how many run at once.
BROWSER_SLOTS = asyncio.Semaphore(2)


def log_stderr(message: str) -> None:
    # Over stdio, stdout carries the MCP protocol, so progress goes to stderr.
    print(message, file=sys.stderr, flush=True)


# ---------- Tasks ----------

@dataclass
class Task:
    id: str
    kind: str
    status: str = "running"  # "running", "done" or "failed"
    step: str = "Starting"
    result: Any = None
    error: str | None = None
    handle: asyncio.Task | None = field(default=None, repr=False)

    def summary(self) -> dict:
        data = {"task_id": self.id, "kind": self.kind, "status": self.status}
        if self.status == "running":
            data["step"] = self.step
        elif self.status == "done":
            data["result"] = self.result
        else:
            data["error"] = self.error
        return data


TASKS: dict[str, Task] = {}


def start_task(kind: str, work: Callable[[Callable[[str], None]], Awaitable[Any]]) -> Task:
    """Runs work(log) in the background. log() messages become the task's current step."""
    task = Task(id=uuid.uuid4().hex[:12], kind=kind)

    def log(message: str) -> None:
        task.step = message
        log_stderr(f"[{kind} {task.id}] {message}")

    async def body() -> None:
        try:
            if BROWSER_SLOTS.locked():
                log("Waiting for another search to finish")
            async with BROWSER_SLOTS:
                task.result = await work(log)
            task.status = "done"
        except Exception as e:
            task.status, task.error = "failed", f"{type(e).__name__}: {e}"
            log_stderr(f"[{kind} {task.id}] failed: {task.error}")

    TASKS[task.id] = task
    task.handle = asyncio.create_task(body())
    return task


async def wait_for_task(task_id: str, timeout: float) -> Task | None:
    task = TASKS.get(task_id)
    if task and task.handle and not task.handle.done():
        try:
            await asyncio.wait_for(asyncio.shield(task.handle), timeout)
        except asyncio.TimeoutError:
            pass
    return task


# ---------- Listings ----------

class Listing(BaseModel):
    id: str
    company: str
    source_url: str  # The URL list_jobs was asked to read.
    url: str  # The page the jobs were actually read from.
    strategy: str | None = None
    jobs: list[Job] = []
    fetched_at: float


LISTINGS: dict[str, Listing] = {}


def save_listing(listing: Listing) -> None:
    LISTINGS[listing.id] = listing
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{listing.id}.json").write_text(listing.model_dump_json())


def get_listing(listing_id: str) -> Listing | None:
    if listing_id not in LISTINGS:
        path = CACHE_DIR / f"{listing_id}.json"
        if not path.exists():
            return None
        LISTINGS[listing_id] = Listing.model_validate_json(path.read_text())
    return LISTINGS[listing_id]


def _all_listings() -> list[Listing]:
    if CACHE_DIR.exists():
        for path in CACHE_DIR.glob("*.json"):
            if path.stem not in LISTINGS:
                try:
                    LISTINGS[path.stem] = Listing.model_validate_json(path.read_text())
                except (ValueError, json.JSONDecodeError):
                    continue
    return list(LISTINGS.values())


def recent_listing(source_url: str) -> Listing | None:
    """The newest listing of source_url fetched within the TTL, if any."""
    fresh = [
        l for l in _all_listings()
        if l.source_url == source_url and time.time() - l.fetched_at < LISTING_TTL_SECONDS
    ]
    return max(fresh, key=lambda l: l.fetched_at, default=None)


def find_stored_job(url: str) -> tuple[Listing, Job] | None:
    """The most recently fetched stored copy of the job at url."""
    for listing in sorted(_all_listings(), key=lambda l: l.fetched_at, reverse=True):
        for job in listing.jobs:
            if job.url == url:
                return listing, job
    return None
