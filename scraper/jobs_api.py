"""Step 2: find job listings in the JSON API responses the careers page loads.

Flow: record every JSON response -> keep only responses that read like job postings
(grep for signals such as "requirements" or "base salary") -> find the array of job
objects -> learn which fields hold title/url/location -> page through the API for the rest.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import BrowserContext, Page, Response
from pydantic import BaseModel

from scraper.llm import MODEL, async_client
from scraper.models import Job

MAX_BODY_CHARS = 20_000_000
MAX_PAGES = 50

# Phrases that appear in job descriptions. One of these is enough to call a response job data.
STRONG_SIGNALS = [
    "requirements",
    "qualifications",
    "responsibilities",
    "what you'll do",
    "what you will do",
    "about the role",
    "about you",
    "base salary",
    "salary range",
    "compensation",
    "about us",
]
# Phrases common in job *lists* that omit descriptions. Weaker, so several are required.
WEAK_SIGNALS = [
    "full-time",
    "full time",
    "employment type",
    "apply",
    "department",
    "remote",
    "hybrid",
    "location",
    "benefits",
]
MIN_WEAK_SIGNALS = 3

TITLE_KEYS = ["title", "jobTitle", "job_title", "postingTitle", "positionTitle", "text", "name"]
URL_KEYS = [
    "absolute_url",
    "hostedUrl",
    "jobUrl",
    "job_url",
    "applyUrl",
    "canonicalUrl",
    "externalUrl",
    "url",
    "link",
    "href",
    "externalPath",
]
LOCATION_KEYS = [
    "location",
    "locationName",
    "locationsText",
    "location_name",
    "categories.location",
    "locations",
    "city",
]
EXTRA_JOB_KEYS = {
    "id", "department", "departments", "team", "employmentType", "workplaceType",
    "updated_at", "publishedAt", "postedOn", "categories", "requisition_id", "isRemote",
}
TOTAL_KEYS = ["total", "totalCount", "total_count", "totalResults", "totalHits", "count"]
OFFSET_KEYS = ["offset", "start", "from", "skip"]
PAGE_KEYS = ["page", "pageNumber", "page_number", "pageNum"]
LIMIT_KEYS = ["limit", "size", "pageSize", "page_size", "per_page", "perPage", "rows"]
SKIP_HEADERS = {"content-length", "host", "cookie", "accept-encoding"}


@dataclass
class CapturedResponse:
    url: str
    method: str
    post_data: str | None
    headers: dict[str, str]
    text: str
    data: Any


class ResponseRecorder:
    """Collects JSON fetch/XHR responses for every page in a browser context."""

    def __init__(self, context: BrowserContext):
        self.responses: list[CapturedResponse] = []
        context.on("response", self._on_response)

    def clear(self) -> None:
        self.responses = []

    async def _on_response(self, response: Response) -> None:
        request = response.request
        if request.resource_type not in ("fetch", "xhr"):
            return
        if "json" not in response.headers.get("content-type", ""):
            return
        try:
            text = await response.text()
            if len(text) > MAX_BODY_CHARS:
                return
            data = json.loads(text)
        except Exception:
            return
        self.responses.append(
            CapturedResponse(
                url=response.url,
                method=request.method,
                post_data=request.post_data,
                headers=request.headers,
                text=text,
                data=data,
            )
        )


# ---------- Step 2a: grep for job-posting signals ----------

def normalize_text(text: str) -> str:
    text = text.lower()
    for apostrophe in ("’", "\\u2019", "&#39;", "&#x27;", "&rsquo;", "\\u0027"):
        text = text.replace(apostrophe, "'")
    return text.replace("&amp;", "&")


def signal_hits(text: str, company: str) -> tuple[list[str], list[str]]:
    normalized = normalize_text(text)
    strong = [s for s in STRONG_SIGNALS + [f"about {company.lower()}"] if s in normalized]
    weak = [s for s in WEAK_SIGNALS if s in normalized]
    return strong, weak


def is_job_response(text: str, company: str) -> bool:
    strong, weak = signal_hits(text, company)
    return bool(strong) or len(weak) >= MIN_WEAK_SIGNALS


# ---------- Step 2b: find the array of job objects ----------

def get_path(obj: Any, path: str | tuple) -> Any:
    parts = path.split(".") if isinstance(path, str) else path
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and str(part).isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def iter_arrays(data: Any, path: tuple = ()):
    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data[:5]):
            yield path, data
        for i, item in enumerate(data[:5]):
            yield from iter_arrays(item, path + (i,))
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from iter_arrays(value, path + (key,))


def first_key(items: list[dict], keys: list[str], want_str: bool = True) -> str | None:
    for key in keys:
        values = [get_path(item, key) for item in items[:5]]
        present = [v for v in values if v not in (None, "", [], {})]
        if not present:
            continue
        if want_str and not all(isinstance(v, str) for v in present):
            continue
        return key
    return None


def score_array(items: list[dict]) -> int:
    if not first_key(items, TITLE_KEYS):
        return 0
    keys = set().union(*(item.keys() for item in items[:5]))
    score = 10
    if first_key(items, URL_KEYS):
        score += 5
    if first_key(items, LOCATION_KEYS, want_str=False):
        score += 3
    score += 2 * len(keys & EXTRA_JOB_KEYS)
    score += min(len(items), 50) // 5
    return score


@dataclass
class JobArray:
    response: CapturedResponse
    path: tuple
    items: list[dict]
    score: int


def find_job_array(responses: list[CapturedResponse]) -> JobArray | None:
    best: JobArray | None = None
    for response in responses:
        for path, items in iter_arrays(response.data):
            score = score_array(items)
            if score and (best is None or (score, len(items)) > (best.score, len(best.items))):
                best = JobArray(response, path, items, score)
    return best


# ---------- Step 2c: learn the job object format ----------

class FieldMap(BaseModel):
    title_path: str
    url_path: str | None = None
    # e.g. "https://example.com/careers/{id}" with {dotted.path} placeholders into the job object.
    url_template: str | None = None
    location_path: str | None = None


def heuristic_field_map(items: list[dict]) -> FieldMap | None:
    title = first_key(items, TITLE_KEYS)
    url = first_key(items, URL_KEYS)
    if not title or not url:
        return None
    return FieldMap(title_path=title, url_path=url, location_path=first_key(items, LOCATION_KEYS, want_str=False))


async def llm_field_map(items: list[dict], page_url: str) -> FieldMap | None:
    sample = json.dumps(items[:2], indent=1, default=str)[:12000]
    response = await async_client().responses.parse(
        model=MODEL,
        instructions=(
            "You are given sample job objects from a careers site API. Identify which fields hold the job "
            "title, the job posting URL, and the location. Use dotted paths (e.g. 'categories.location', "
            "'locations.0.name'). If no field holds a full or relative URL, set url_template to a URL built "
            "from the careers page URL using {dotted.path} placeholders, e.g. 'https://site.com/jobs/{id}'. "
            "Leave url_template empty if a URL cannot be built."
        ),
        input=f"Careers page URL: {page_url}\n\nSample job objects:\n{sample}",
        text_format=FieldMap,
    )
    return response.output_parsed


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return as_text(value.get("name") or value.get("text") or value.get("label") or value.get("title"))
    if isinstance(value, list):
        parts = [as_text(v) for v in value]
        return "; ".join(p for p in parts if p) or None
    return str(value)


def resolve_url(value: str, page_url: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    # Workday returns paths like "/job/..." relative to the career site, not the host root.
    if "myworkdayjobs.com" in page_url and value.startswith("/job"):
        return page_url.split("?")[0].rstrip("/") + value
    return urljoin(page_url, value)


def fill_template(template: str, item: dict) -> str | None:
    missing = False

    def replace(match: re.Match) -> str:
        nonlocal missing
        value = get_path(item, match.group(1))
        if value is None:
            missing = True
            return ""
        return str(value)

    url = re.sub(r"\{([\w.]+)\}", replace, template)
    return None if missing else url


def map_jobs(items: list[dict], fields: FieldMap, page_url: str) -> list[Job]:
    jobs = []
    for item in items:
        title = as_text(get_path(item, fields.title_path))
        if not title:
            continue
        url = None
        raw_url = get_path(item, fields.url_path) if fields.url_path else None
        if isinstance(raw_url, str) and raw_url:
            url = resolve_url(raw_url, page_url)
        elif fields.url_template:
            url = fill_template(fields.url_template, item)
        location = as_text(get_path(item, fields.location_path)) if fields.location_path else None
        jobs.append(Job(title=title, url=url, location=location, source="api"))
    return jobs


# ---------- Step 2d: page through the API for the rest of the jobs ----------

@dataclass
class PagingParam:
    where: str  # "query" or "body"
    key: str
    value: int
    step: int


def find_paging_param(params: dict, where: str, first_page_size: int) -> PagingParam | None:
    limit = next((int(params[k]) for k in LIMIT_KEYS if str(params.get(k, "")).isdigit()), None)
    for key in OFFSET_KEYS:
        if str(params.get(key, "")).isdigit():
            return PagingParam(where, key, int(params[key]), limit or first_page_size)
    for key in PAGE_KEYS:
        if str(params.get(key, "")).isdigit():
            return PagingParam(where, key, int(params[key]), 1)
    return None


def find_total(data: Any) -> int | None:
    if isinstance(data, dict):
        for key in TOTAL_KEYS:
            if isinstance(data.get(key), int):
                return data[key]
    return None


async def paginate(
    context: BrowserContext, job_array: JobArray, log: Callable[..., None]
) -> list[dict]:
    source = job_array.response
    items = list(job_array.items)
    parsed = urlparse(source.url)
    query = dict(parse_qsl(parsed.query))
    body = None
    if source.post_data:
        try:
            body = json.loads(source.post_data)
        except json.JSONDecodeError:
            body = None

    param = find_paging_param(body, "body", len(items)) if isinstance(body, dict) else None
    param = param or find_paging_param(query, "query", len(items))
    if not param:
        return items

    total = find_total(source.data)
    headers = {k: v for k, v in source.headers.items() if k.lower() not in SKIP_HEADERS and not k.startswith(":")}
    seen = {json.dumps(item, sort_keys=True, default=str) for item in items}
    value = param.value

    for _ in range(MAX_PAGES):
        if total is not None and len(items) >= total:
            break
        value += param.step
        if param.where == "body":
            body[param.key] = value
            url, data = source.url, json.dumps(body)
        else:
            query[param.key] = str(value)
            url, data = urlunparse(parsed._replace(query=urlencode(query))), None
        try:
            response = await context.request.fetch(url, method=source.method, headers=headers, data=data)
            page_data = await response.json()
        except Exception as e:
            log(f"Pagination stopped: {e}")
            break
        page_items = get_path(page_data, job_array.path)
        if not isinstance(page_items, list):
            break
        new = [i for i in page_items if json.dumps(i, sort_keys=True, default=str) not in seen]
        if not new:
            break
        seen.update(json.dumps(i, sort_keys=True, default=str) for i in new)
        items.extend(new)

    log(f"Paginated via {param.where} param '{param.key}': {len(job_array.items)} -> {len(items)} jobs")
    return items


# ---------- Entry points ----------

async def jobs_from_array(
    context: BrowserContext, job_array: JobArray, page_url: str, log: Callable[..., None], paginate_api: bool = True
) -> list[Job]:
    items = await paginate(context, job_array, log) if paginate_api else job_array.items
    fields = heuristic_field_map(items)
    if fields:
        log(f"Job format: title={fields.title_path} url={fields.url_path} location={fields.location_path}")
    else:
        log("Unrecognized job format; asking the LLM to map fields")
        fields = await llm_field_map(items, page_url)
        if not fields:
            return []
        log(f"LLM job format: {fields.model_dump(exclude_none=True)}")
    return map_jobs(items, fields, page_url)


async def jobs_from_responses(
    context: BrowserContext, responses: list[CapturedResponse], company: str, page_url: str,
    log: Callable[..., None] = print,
) -> list[Job]:
    job_responses = [r for r in responses if is_job_response(r.text, company)]
    log(f"Captured {len(responses)} JSON responses; {len(job_responses)} look like job data")
    if not job_responses:
        return []
    job_array = find_job_array(job_responses)
    if not job_array:
        log("No array of job objects found in those responses")
        return []
    path = ".".join(map(str, job_array.path)) or "(root)"
    log(f"Jobs array at '{path}' in {job_array.response.url} ({len(job_array.items)} items)")
    return await jobs_from_array(context, job_array, page_url, log)


# ---------- Known ATS shortcut ----------

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([\w-]+)"),
     "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)"),
     "https://api.lever.co/v0/postings/{token}?mode=json"),
    ("ashby", re.compile(r"(?:jobs\.ashbyhq\.com/|posting-api/job-board/)([\w.%-]+)"),
     "https://api.ashbyhq.com/posting-api/job-board/{token}"),
]
IGNORED_TOKENS = {"embed", "v1", "boards", "api", "jobs"}


def detect_ats(urls: list[str]) -> tuple[str, str, str] | None:
    for name, pattern, endpoint in ATS_PATTERNS:
        for url in urls:
            match = pattern.search(url)
            if match and match.group(1).lower() not in IGNORED_TOKENS:
                token = match.group(1)
                return name, token, endpoint.format(token=token)
    return None


async def jobs_from_ats(
    context: BrowserContext, page: Page, responses: list[CapturedResponse], log: Callable[..., None] = print
) -> tuple[str, list[Job]] | None:
    links = await page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
    urls = [page.url] + [f.url for f in page.frames] + [r.url for r in responses] + links
    found = detect_ats(urls)
    if not found:
        return None
    name, token, endpoint = found
    log(f"Detected {name} board '{token}'; fetching {endpoint}")
    try:
        response = await context.request.get(endpoint)
        if not response.ok:
            log(f"{name} API returned {response.status}")
            return None
        data = await response.json()
    except Exception as e:
        log(f"{name} API failed: {e}")
        return None
    captured = CapturedResponse(endpoint, "GET", None, {}, "", data)
    job_array = find_job_array([captured])
    if not job_array:
        return None
    # Ashby includes unlisted postings; drop them.
    items = [i for i in job_array.items if i.get("isListed", True) is not False]
    job_array.items = items
    jobs = await jobs_from_array(context, job_array, page.url, log, paginate_api=False)
    return f"ats:{name}", jobs
