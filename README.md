# scrape-jobs

Give it a company name and your ideal role, and it lists that company's software engineering jobs ranked by how well they fit you.

1. An LLM (OpenAI) looks up the company's domain.
2. Playwright opens the homepage, finds the careers link in the footer and follows it.
3. If the careers page links to an "open positions" page, it goes there.
4. It gets the jobs from the first source that works:
   - the JSON API responses the page loaded, if they look like job data; it pages through the API for the rest
   - the page HTML converted to markdown, with the LLM extracting job titles and links
   - as a last resort, a known job board API (Greenhouse, Lever, Ashby) linked from the page, since those can list stale postings
5. It keeps roles whose title contains a phrase in `ROLE_KEYWORDS` (`scraper/filters.py`).
6. For those roles it gets each description (from the job API, or by opening the job page), has the LLM score it 0-100 against your ideal role based on the actual responsibilities, and suggests the ones at or above the minimum match % (default 65). The default ideal role is in `scraper/ranking.py`.

## Setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # add your OPENAI_API_KEY
```

## Usage
```
python -m scraper.main "Stripe"
python -m scraper.main "Airbnb" --headed   # show the browser
python -m scraper.main "Stripe" --role "Backend engineer on payments infra" --min-match 50
python -m scraper.main "Stripe" --role-file my_role.txt
```

## Web UI
```
uvicorn scraper.app:app --reload
```
Then open http://localhost:8000

## MCP server (use it from Claude)
`scraper/mcp_server.py` exposes the same steps as tools, so you can ask Claude "which roles at Browserbase fit me?":

| Tool | What it does |
|---|---|
| `find_careers_page(company)` | Finds the careers page and open-positions page |
| `list_jobs(company or careers_url)` | Reads every role from the live site (background task) |
| `rank_jobs(listing_id, ideal_role, min_match_pct)` | Fetches descriptions and scores the roles (background task) |
| `get_results(task_id)` | Waits for a `list_jobs` / `rank_jobs` task and returns its result |
| `get_job_details(url)` | One job's full description |

Listings and their descriptions are stored in `.cache/` for 6 hours, so re-ranking with a different ideal role only re-scores.
`skills/job-search/SKILL.md` is a Claude skill that walks Claude through the workflow; copy it to `~/.claude/skills/` (Claude Code) or upload it to claude.ai.

### Claude Code
```
claude mcp add --scope user jobfit -e PYTHONPATH=/path/to/scrape-jobs -- /path/to/scrape-jobs/.venv/bin/python -m scraper.mcp_server
```

### claude.ai
Run it over HTTP (`python -m scraper.mcp_server --http`, served at `/mcp`), or build the `Dockerfile` and deploy it to a host that runs long-lived containers (e.g. Fly.io or Render) with `OPENAI_API_KEY` set. Then add `https://<host>/mcp` in claude.ai under Settings → Connectors → Add custom connector.
The server has no auth yet, and every search uses your OpenAI key and runs a browser, so don't share the URL publicly.
