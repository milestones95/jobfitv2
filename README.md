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
