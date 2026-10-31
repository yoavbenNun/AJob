"""Bounded Stratasys SuccessFactors sitemap and server-rendered job search.

The advertised RSS is disallowed by robots.txt; do not request /services/.
Search membership is authoritative, not the sitemap's batch lastmod dates.
No closure snapshot is emitted from this filtered, budgeted collection.
"""

from html.parser import HTMLParser
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from src.filtering.job_filter import prefilter_job
from src.job_content import description_problem
from src.models import JobPosting


ORIGIN = "https://careers.stratasys.com"
MAX_PAGES = 5
MAX_DETAILS = 12
MAX_BYTES = 2_000_000
SOURCE_SECONDS = 90
BLOCKS = {"p", "li", "div", "section", "h1", "h2", "h3", "br"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


def _compact(text):
    return " ".join(text.split())


def posting_id(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "careers.stratasys.com" or parts.query or parts.fragment:
        raise ValueError("Untrusted Stratasys posting URL")
    match = re.fullmatch(r"/job/[^/]+/([1-9]\d*)/", parts.path)
    if not match:
        raise ValueError("Missing Stratasys posting identity")
    return match[1]


def _allowed(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "careers.stratasys.com" or parts.fragment:
        raise ValueError("Untrusted Stratasys request")
    if parts.path not in {"/sitemap.xml", "/search/"}:
        posting_id(url)
    return url


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        _allowed(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _open(request, timeout):
    return build_opener(_PublicRedirect()).open(request, timeout=timeout)


class _Page(HTMLParser):
    """Read observed listing rows and explicit careersite property spans only."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.rows = []
        self.row = None
        self.fields = {}
        self.labels = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag in BLOCKS:
            self.handle_data("\n")
        buffer = None
        if tag == "tr" and "data-row" in classes:
            if self.row is not None:
                raise ValueError("Malformed Stratasys listing rows")
            self.row = {"links": [], "locations": []}
            self.rows.append(self.row)
        if self.row is not None and tag == "a" and "jobTitle-link" in classes:
            buffer = []
            self.row["links"].append((attributes.get("href", ""), buffer))
        if self.row is not None and "jobLocation" in classes:
            buffer = []
            self.row["locations"].append(buffer)
        if "paginationLabel" in classes:
            buffer = []
            self.labels.append(buffer)
        field = attributes.get("data-careersite-propertyid")
        if field in {"title", "location", "description", "customfield1"}:
            buffer = []
            self.fields.setdefault(field, []).append(buffer)
        if tag not in VOID:
            self.stack.append((tag, buffer))

    def handle_data(self, data):
        if any(tag in {"script", "style", "nav", "footer"} for tag, _ in self.stack):
            return
        for _, buffer in self.stack:
            if buffer is not None:
                buffer.append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                if tag == "tr":
                    self.row = None
                break
        if tag in BLOCKS:
            self.handle_data("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def field(self, name):
        buffers = self.fields.get(name, [])
        if len(buffers) > 1:
            raise ValueError("Ambiguous Stratasys detail field")
        return "".join(buffers[0]).strip() if buffers else ""


def sitemap_ids(text):
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise ValueError("Unexpected Stratasys XML declaration")
    root = ElementTree.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] != "urlset":
        raise ValueError("Unsupported Stratasys sitemap structure")
    identities = set()
    for entry in root:
        locations = [node.text for node in entry if node.tag.rsplit("}", 1)[-1] == "loc"]
        if len(locations) != 1 or not locations[0]:
            raise ValueError("Missing Stratasys sitemap URL")
        identities.add(posting_id(locations[0].strip()))
    if not identities:
        raise ValueError("Empty Stratasys sitemap")
    return identities


def parse_listing(text):
    page = _Page()
    page.feed(text)
    labels = {_compact("".join(buffer)) for buffer in page.labels}
    if len(labels) != 1:
        raise ValueError("Missing Stratasys result count")
    match = re.fullmatch(r"Results (\d+)\s*[–-]\s*(\d+) of (\d+)", labels.pop())
    if not match:
        raise ValueError("Invalid Stratasys result count")
    first, last, total = map(int, match.groups())
    if not (0 <= first <= last <= total) or len(page.rows) != (last - first + 1 if total else 0):
        raise ValueError("Incomplete Stratasys listing page")
    jobs = []
    for row in page.rows:
        links = [(urljoin(ORIGIN, href), _compact("".join(buffer))) for href, buffer in row["links"]]
        if not links or any(not title for _, title in links):
            raise ValueError("Missing Stratasys listing title")
        if len({(posting_id(url), title) for url, title in links}) != 1:
            raise ValueError("Conflicting Stratasys listing identity")
        location = _compact(" ".join("".join(buffer) for buffer in row["locations"]))
        jobs.append((links[0][0], links[0][1], location or "Not specified"))
    return jobs, first, last, total


def _location(text):
    location = _compact(text)
    # SuccessFactors renders city, region, country. IL in the *country* slot is
    # Israel; Chicago, IL, US must never acquire an Israel label.
    pieces = [part.strip() for part in location.split(",")]
    if len(pieces) >= 3 and pieces[-1] == "IL":
        pieces[-1] = "Israel"
        location = ", ".join(pieces)
    return location or "Not specified"


def parse_detail(text, job):
    page = _Page()
    page.feed(text)
    title = _compact(page.field("title"))
    requisition = _compact(page.field("customfield1"))
    description = page.field("description")
    if (title != job.title or not re.fullmatch(r"[1-9]\d*", requisition)
            or description_problem(description) or len(description) < 150):
        raise ValueError("Invalid or unavailable Stratasys job detail")
    # Preserve source wording while adding canonical section labels understood
    # by the existing shared extractor; advantages must remain optional.
    lines = []
    for line in description.splitlines():
        if _compact(line) == "Must have for this role:":
            lines.append("Requirements:")
        elif _compact(line) == "Advantages:":
            lines.append("Preferred Qualifications:")
        lines.append(line)
    description = "\n".join(lines)
    job.description = f"Requisition ID: {requisition}\n{description}"
    job.location = _location(page.field("location") or job.location)
    return job


def fetch_jobs(company, timeout_ms):
    if company.careers_url != ORIGIN + "/search/":
        raise ValueError("Unsupported Stratasys source")
    deadline = time.monotonic() + SOURCE_SECONDS
    metrics = dict(raw_listings=0, duplicates_avoided=0, pages_fetched=0,
                   details_fetched=0, http_requests=0, page_limit_reached=False,
                   location_filter_status="explicit_listing_and_detail")

    def remaining_seconds():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Stratasys source budget exceeded")
        return remaining

    def read(url):
        _allowed(url)
        remaining = remaining_seconds()
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xml"})
        metrics["http_requests"] += 1
        with _open(request, timeout=min(15, max(.1, timeout_ms / 1000), remaining)) as response:
            remaining_seconds()
            resolved = _allowed(response.geturl())
            if urlsplit(url).path.startswith("/job/") and posting_id(resolved) != posting_id(url):
                raise ValueError("Stratasys detail redirected to a different posting")
            chunks, size = [], 0
            while True:
                remaining_seconds()
                chunk = response.read1(min(64_000, MAX_BYTES + 1 - size))
                remaining_seconds()
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Oversized Stratasys response")
            payload = b"".join(chunks)
        return payload.decode("utf-8"), resolved

    indexed = sitemap_ids(read(ORIGIN + "/sitemap.xml")[0])
    metrics["sitemap_jobs"] = len(indexed)
    jobs = {}
    offset, expected_total = 0, None
    for _ in range(MAX_PAGES):
        url = ORIGIN + "/search/?" + urlencode(dict(q="", sortColumn="referencedate", sortDirection="desc", startrow=offset))
        rows, first, last, total = parse_listing(read(url)[0])
        metrics["pages_fetched"] += 1
        if (first != (offset + 1 if total else 0)
                or (expected_total is not None and total != expected_total)):
            raise ValueError("Stratasys pagination changed or did not advance")
        expected_total = total
        metrics["raw_listings"] += len(rows)
        new_ids = {posting_id(url) for url, _, _ in rows} - jobs.keys()
        if rows and not new_ids:
            raise ValueError("Repeated Stratasys listing page")
        for url, title, location in rows:
            identity = posting_id(url)
            if identity in jobs:
                metrics["duplicates_avoided"] += 1
                continue
            job = JobPosting(title, company.name, _location(location), url, source=company.careers_url)
            if prefilter_job(job).relevant:
                if metrics["details_fetched"] >= MAX_DETAILS:
                    raise ValueError("Stratasys detail budget exceeded")
                detail, resolved = read(url)
                job = parse_detail(detail, job)
                job.job_url = resolved
                metrics["details_fetched"] += 1
            jobs[identity] = job
        if last == total:
            break
        if last <= offset:
            raise ValueError("Stratasys pagination made no progress")
        offset = last
    else:
        raise ValueError("Stratasys listing page budget exceeded")
    if len(jobs) != expected_total:
        raise ValueError("Stratasys listing count and unique identities disagree")
    metrics["sitemap_only"] = len(indexed - jobs.keys())
    metrics["listing_only"] = len(jobs.keys() - indexed)
    metrics["global_discovered"] = len(jobs)
    remaining_seconds()
    return list(jobs.values()), metrics
