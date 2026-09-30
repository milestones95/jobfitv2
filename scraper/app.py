"""Minimal web UI: uvicorn scraper.app:app --reload"""

import base64
import html

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from scraper.pipeline import run
from scraper.ranking import DEFAULT_IDEAL_ROLE, DEFAULT_MIN_MATCH_PCT

load_dotenv()

app = FastAPI()


class LookupRequest(BaseModel):
    company: str
    ideal_role: str = DEFAULT_IDEAL_ROLE
    min_match_pct: int = DEFAULT_MIN_MATCH_PCT


@app.post("/api/lookup")
async def lookup(req: LookupRequest):
    result = await run(req.company, req.ideal_role, req.min_match_pct)
    data = result.model_dump(exclude={"screenshot": True, "jobs": {"__all__": {"description"}}})
    data["screenshot"] = base64.b64encode(result.screenshot).decode() if result.screenshot else None
    return data


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.replace("__IDEAL_ROLE__", html.escape(DEFAULT_IDEAL_ROLE)).replace(
        "__MIN_MATCH__", str(DEFAULT_MIN_MATCH_PCT)
    )


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Careers Finder</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 16px; color: #222; }
  input { flex: 1; padding: 10px; font-size: 16px; border: 1px solid #ccc; border-radius: 6px; }
  button { padding: 10px 18px; font-size: 16px; border: 0; border-radius: 6px; background: #222; color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: wait; }
  #result { margin-top: 24px; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; }
  dt { color: #666; }
  dd { margin: 0; word-break: break-all; }
  img { width: 100%; margin-top: 16px; border: 1px solid #ddd; border-radius: 6px; }
  .error { color: #b00; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; vertical-align: top; }
  th { color: #666; font-weight: 500; }
  label { display: block; margin-top: 12px; color: #666; font-size: 14px; }
  textarea { width: 100%; box-sizing: border-box; padding: 10px; font: 14px system-ui, sans-serif; border: 1px solid #ccc; border-radius: 6px; }
  #min { width: 80px; flex: none; }
  .score { font-weight: 600; white-space: nowrap; }
  .why { color: #555; font-size: 14px; }
  h2 { margin-top: 28px; font-size: 18px; }
</style>
</head>
<body>
<h1>Careers Finder</h1>
<form id="form">
  <div style="display: flex; gap: 8px;">
    <input id="company" placeholder="Company name, e.g. Stripe" autofocus required>
    <button id="go">Find</button>
  </div>
  <label for="role">Ideal role</label>
  <textarea id="role" rows="8">__IDEAL_ROLE__</textarea>
  <label for="min">Min match %</label>
  <input id="min" type="number" min="0" max="100" value="__MIN_MATCH__">
</form>
<div id="result"></div>
<script>
const form = document.getElementById('form');
const result = document.getElementById('result');
const button = document.getElementById('go');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const company = document.getElementById('company').value.trim();
  const ideal_role = document.getElementById('role').value;
  const min_match_pct = parseInt(document.getElementById('min').value, 10) || 0;
  button.disabled = true;
  result.textContent = 'Searching ' + company + '... (this can take up to a minute)';
  try {
    const res = await fetch('/api/lookup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({company, ideal_role, min_match_pct}),
    });
    if (!res.ok) throw new Error('Server error ' + res.status);
    const d = await res.json();
    result.innerHTML = '';
    const dl = document.createElement('dl');
    const row = (label, node) => {
      const dt = document.createElement('dt'); dt.textContent = label;
      const dd = document.createElement('dd'); dd.append(node);
      dl.append(dt, dd);
    };
    const link = (url) => {
      const a = document.createElement('a'); a.href = url; a.target = '_blank'; a.textContent = url; return a;
    };
    row('Company', d.company);
    row('Domain', link('https://' + d.domain));
    row('Confidence', d.confidence);
    row('Careers page', d.careers_url ? link(d.careers_url) : 'Not found');
    if (d.jobs_url) row('Jobs page', link(d.jobs_url));
    row('Found via', d.strategy || 'nothing found');
    row('Matching roles', d.jobs.length + ' of ' + d.total_jobs + ' jobs');
    result.append(dl);
    const scored = d.min_match_pct !== null;
    const suggested = scored ? d.jobs.filter(j => j.match_pct >= d.min_match_pct) : [];
    const others = scored ? d.jobs.filter(j => j.match_pct < d.min_match_pct) : d.jobs;
    const table = (jobs) => {
      const t = document.createElement('table');
      t.innerHTML = scored
        ? '<thead><tr><th>Match</th><th>Role</th><th>Location</th></tr></thead>'
        : '<thead><tr><th>Role</th><th>Location</th></tr></thead>';
      const tbody = document.createElement('tbody');
      for (const job of jobs) {
        const tr = document.createElement('tr');
        if (scored) {
          const score = document.createElement('td');
          score.className = 'score';
          score.textContent = job.match_pct + '%';
          tr.append(score);
        }
        const title = document.createElement('td');
        title.append(job.url ? link(job.url) : job.title);
        if (job.url) title.firstChild.textContent = job.title;
        if (job.reasoning) {
          const why = document.createElement('div');
          why.className = 'why';
          why.textContent = job.reasoning;
          title.append(why);
        }
        const loc = document.createElement('td');
        loc.textContent = job.location || '';
        tr.append(title, loc);
        tbody.append(tr);
      }
      t.append(tbody);
      return t;
    };
    const section = (heading, jobs) => {
      const h = document.createElement('h2'); h.textContent = heading;
      result.append(h, table(jobs));
    };
    if (scored) {
      const h = document.createElement('h2');
      h.textContent = 'Suggested roles (' + suggested.length + ' at or above ' + d.min_match_pct + '% match)';
      result.append(h);
      if (suggested.length) result.append(table(suggested));
      else { const p = document.createElement('p'); p.textContent = 'None — try lowering the min match %.'; result.append(p); }
    }
    if (others.length) section(scored ? 'Other roles' : 'Roles', others);
    if (d.screenshot) {
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + d.screenshot;
      img.alt = 'Screenshot of ' + d.careers_url;
      result.append(img);
    }
  } catch (err) {
    result.innerHTML = '<p class="error"></p>';
    result.firstChild.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""
