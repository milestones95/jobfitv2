"""CLI: python -m scraper.main "Company Name" """

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from scraper.models import Job
from scraper.pipeline import run
from scraper.ranking import DEFAULT_IDEAL_ROLE, DEFAULT_MIN_MATCH_PCT, suggestions


def print_job(job: Job) -> None:
    score = f"{job.match_pct:3d}%  " if job.match_pct is not None else "  - "
    print(f"  {score}{job.title} — {job.location or 'n/a'} — {job.url or 'n/a'}")
    if job.reasoning:
        print(f"        why: {job.reasoning}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a company's software engineering jobs and rank them by fit.")
    parser.add_argument("company", help="Company name, e.g. 'Stripe'")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--role", help="Your ideal role, as free text (defaults to the one in scraper/ranking.py)")
    parser.add_argument("--role-file", type=Path, help="Read your ideal role from this file")
    parser.add_argument("--min-match", type=int, default=DEFAULT_MIN_MATCH_PCT, help="Minimum match %% to suggest")
    args = parser.parse_args()

    load_dotenv()

    ideal_role = args.role_file.read_text() if args.role_file else args.role or DEFAULT_IDEAL_ROLE
    result = asyncio.run(run(args.company, ideal_role, args.min_match, headless=not args.headed))
    if not result.careers_url:
        print(f"Careers page not found for {args.company}")
        return 1

    print()
    print(f"Careers page: {result.careers_url}")
    print(f"Jobs page:    {result.jobs_url}")
    print(f"Strategy:     {result.strategy}")
    print(f"Matching:     {len(result.jobs)} of {result.total_jobs} jobs")

    if result.min_match_pct is None:
        for job in result.jobs:
            print_job(job)
        return 0

    suggested = suggestions(result.jobs, result.min_match_pct)
    print(f"\nSuggested ({len(suggested)} at or above {result.min_match_pct}% match):")
    if not suggested:
        print("  (none — try lowering --min-match)")
    for job in suggested:
        print_job(job)
    others = result.jobs[len(suggested):]
    if others:
        print("\nOther roles:")
        for job in others:
            print_job(job)
    return 0


if __name__ == "__main__":
    sys.exit(main())
