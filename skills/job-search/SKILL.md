---
name: job-search
description: Finds open roles at a company and ranks them by how well they fit the user, using the jobfit MCP tools (find_careers_page, list_jobs, rank_jobs, get_results, get_job_details). Use whenever the user wants to find jobs at a specific company, asks which roles at a company fit them, wants to compare openings across companies, or wants help with a specific posting (resume or cover letter tailoring).
---

# Job search with jobfit

The jobfit tools read a company's live careers site and score each role 0-100 against the
user's ideal role. Scoring runs on the server, so you get back a ranked list with one-line
reasons, not raw job descriptions.

## Workflow

1. **Find the careers page**: `find_careers_page(company)`. Tell the user which page you found
   and keep going; don't ask them to confirm it. If the user already gave a careers URL, skip
   this step and pass it to `list_jobs` as `careers_url`.
2. **List the roles**: `list_jobs(company=...)` or `list_jobs(careers_url=...)` returns a
   `task_id`. Call `get_results(task_id)` until `status` is `"done"`, usually under a minute.
   The result has a `listing_id` and the software engineering roles. Pass
   `engineering_only=false` if the user wants other kinds of roles too.
3. **Get the ideal role** if you don't already have it from this conversation, memory or the
   project (see below).
4. **Rank**: `rank_jobs(listing_id, ideal_role)` then `get_results(task_id)` until done. The first
   ranking of a listing takes 1-3 minutes because each job page is read; tell the user it's
   running. To rank specific roles, including non-engineering ones, pass their URLs as
   `job_urls`.
5. **Present** the results (below), then offer to dig into any role.

If `get_results` returns `"running"`, just call it again; it waits about 40 seconds each time.
If a task fails, show the user the error and, if the careers page looked wrong, ask for the right
URL and call `list_jobs(careers_url=...)`.

## Getting the ideal role

Ask briefly, in one message, then write it up as a few sentences of free text for `ideal_role`:

- **Interests**: the kind of work they want to do day to day (e.g. "browser automation agents",
  "distributed storage").
- **Must-haves**: e.g. IC role, remote, small team.
- **Nice-to-haves**: e.g. Go or Rust, early stage.
- **Dealbreakers**: e.g. on-call heavy, management track.
- **Seniority**, if they care.

Describe the work, not job titles: scores are based on each posting's responsibilities, and
titles are inconsistent across companies. Reuse the same ideal role for later companies in the
conversation unless the user changes it.

## Presenting results

- A table of `suggestions`: match %, role (linked to its URL), location, and the reason.
- Then a short list of `other_roles` with their scores.
- If there are no suggestions, say so and offer to lower `min_match_pct` (default 65) or broaden
  the ideal role.
- Scores near the cutoff can vary a few points between runs, so don't over-read small
  differences.

## Follow-ups

- **Re-rank** with a new ideal role or threshold: call `rank_jobs` again with the same
  `listing_id`. It reuses the stored descriptions, so it's fast.
- **Compare companies**: run steps 1-4 for each, then combine the suggestions into one table
  sorted by match.
- **Dig into a role, or tailor a resume or cover letter**: `get_job_details(url)` returns the full
  description. Tailor to what the posting actually asks for.
- Listings are reused for 6 hours; pass `refresh=true` to `list_jobs` to re-read the site.
