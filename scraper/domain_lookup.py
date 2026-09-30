"""Resolve a company name to its official website using an LLM."""

from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

from scraper.llm import MODEL, sync_client

SYSTEM_PROMPT = (
    "You map company names to their primary official website domain. "
    "Return only the bare domain (e.g. 'stripe.com'): no scheme, no 'www.', no path. "
    "Pick the main corporate site, not a regional variant, product subdomain, or social profile."
)


class CompanyDomain(BaseModel):
    domain: str
    confidence: Literal["high", "medium", "low"]


def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    if d.startswith("www."):
        d = d[4:]
    return d.split("/")[0]


def get_company_domain(company: str, client: OpenAI | None = None) -> CompanyDomain:
    client = client or sync_client()
    response = client.responses.parse(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=f"Company: {company}",
        text_format=CompanyDomain,
    )
    result = response.output_parsed
    if result is None:
        raise RuntimeError(f"Could not parse a domain for {company!r}")
    result.domain = normalize_domain(result.domain)
    return result
