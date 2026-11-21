"""Earnix's complete server-rendered search and scoped public posting sections."""

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from src.models import JobPosting
from src.scrapers.greenhouse import html_text
from src.urls import canonical_url


def posting_id(url):
    parts = urlsplit(url)
    match = re.fullmatch(r"/career/([0-9a-f]{2}\.[0-9a-f]{3})/[^/]+/?", parts.path)
    if parts.scheme == "https" and parts.netloc == "earnix.com" and match:
        return match[1]
    return None


class _Listings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.item = None
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "data-career-search-job" in attrs:
            self.item = dict(location=attrs.get("data-location"), title="", url="")
        if self.item is not None:
            if tag == "a":
                self.item["url"] = urljoin("https://earnix.com/", attrs.get("href", ""))
            if tag == "span" and not self.item["title"]:
                self.in_title = True

    def handle_data(self, data):
        if self.item is not None and self.in_title:
            self.item["title"] += data

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_title = False
        if tag == "li" and self.item is not None:
            self.item["title"] = " ".join(self.item["title"].split())
            self.items.append(self.item)
            self.item = None


def extract_jobs(company, html, metrics):
    from src.scrapers.official_sources import extract_listings

    parser = _Listings()
    parser.feed(html)
    counts = re.findall(r"\b(\d+) jobs in \d+ locations\b", html_text(html))
    if len(counts) != 1:
        raise ValueError("Earnix complete listing count missing")
    items = []
    for item in parser.items:
        if not posting_id(item["url"]):
            items.append({})
            continue
        # Search tracking and fragments do not identify a Comeet requisition.
        item["url"] = "https://earnix.com" + urlsplit(item["url"]).path.rstrip("/")
        items.append(item)
    jobs = extract_listings(company, items, metrics)
    # The current public search embeds every card (including hidden search
    # results). A new paginated/filtered layout must not silently truncate it.
    if metrics.get("invalid_listing") or len(jobs) != int(counts[0]):
        raise ValueError("Earnix listing snapshot incomplete")
    return jobs


class _Detail(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = ""
        self.uid = ""
        self.titles = []
        self.heading = ""
        self.capture = None
        self.parts = []
        self.depth = 0
        self.sections = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = canonical_url(attrs.get("href", ""))
        if tag == "script" and attrs.get("type") == "comeet-applyform":
            self.uid = attrs.get("data-position-uid", "").lower()
        if self.capture == "content":
            if tag == "div":
                self.depth += 1
            self.parts.append(self.get_starttag_text())
        elif tag in {"h1", "h3"}:
            self.capture, self.parts = tag, []
        elif tag == "div" and "data-career-content" in attrs:
            self.capture, self.parts, self.depth = "content", [], 1

    def handle_data(self, data):
        if self.capture:
            self.parts.append(escape(data) if self.capture == "content" else data)

    def handle_endtag(self, tag):
        if self.capture == "content":
            if tag == "div":
                self.depth -= 1
            if not self.depth:
                self.sections.setdefault(self.heading, []).append(html_text("".join(self.parts)))
                self.capture = None
            else:
                self.parts.append(f"</{tag}>")
        elif self.capture == tag:
            value = " ".join("".join(self.parts).split())
            if tag == "h1":
                self.titles.append(value)
            else:
                self.heading = value
            self.capture = None


def extract_detail(job: JobPosting, html: str) -> str:
    parser = _Detail()
    parser.feed(html)
    if (not posting_id(job.job_url) or parser.uid != posting_id(job.job_url)
            or parser.canonical != canonical_url(job.job_url)
            or parser.titles != [job.title]
            or any(len(parser.sections.get(name, [])) != 1
                   or not parser.sections[name][0] for name in ("Description", "Requirements"))):
        raise ValueError("Earnix posting identity or scoped content missing")
    return "\n".join(name + "\n" + parser.sections[name][0]
                     for name in ("Description", "Requirements"))
