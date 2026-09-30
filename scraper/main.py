"""CLI: python -m scraper.main "Company Name" """

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from scraper.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a company's software engineering job postings.")
    parser.add_argument("company", help="Company name, e.g. 'Stripe'")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    load_dotenv()

    result = asyncio.run(run(args.company, headless=not args.headed))
    if not result.careers_url:
        print(f"Careers page not found for {args.company}")
        return 1

    print()
    print(f"Careers page: {result.careers_url}")
    print(f"Jobs page:    {result.jobs_url}")
    print(f"Strategy:     {result.strategy}")
    print(f"Matching:     {len(result.jobs)} of {result.total_jobs} jobs")
    for job in result.jobs:
        print(f"  - {job.title} — {job.location or 'n/a'} — {job.url or 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
