# scrape-jobs

Give it a company name and it lists that company's software engineering job postings.

1. An LLM (OpenAI) looks up the company's domain.
2. Playwright opens the homepage, finds the careers link in the footer and follows it.
3. If the careers page links to an "open positions" page, it goes there.
4. It gets the jobs from the first source that works:
   - a known job board API (Greenhouse, Lever, Ashby)
   - the JSON API responses the page loaded, if they look like job data; it pages through the API for the rest
   - the page HTML converted to markdown, with the LLM extracting job titles and links
5. It keeps roles whose title contains a phrase in `ROLE_KEYWORDS` (`scraper/filters.py`).

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
```

## Web UI
```
uvicorn scraper.app:app --reload
```
Then open http://localhost:8000
