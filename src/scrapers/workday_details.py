"""Public Workday detail URLs and verified posting identity; no listing scans."""

from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from src.job_content import clean_job_text, description_problem
from src.urls import canonical_url


class WorkdayIdentityError(ValueError):
    pass


class WorkdayTitleChanged(ValueError):
    """Same verified requisition/URL, but changed role wording needs review."""


def workday_parts(source):
    parsed = urlsplit(source)
    if parsed.scheme != 'https' or not re.fullmatch(
        r'[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com', parsed.netloc, re.I
    ):
        raise ValueError('Unsupported Workday host')
    parts = parsed.path.strip('/').split('/')
    if len(parts) != 2 or not re.fullmatch(r'[a-z]{2}-[a-z]{2}', parts[0], re.I):
        raise ValueError('Unsupported Workday site')
    if not re.fullmatch(r'[\w-]+', parts[1]):
        raise ValueError('Unsupported Workday site')
    return parsed, parts[1]


def workday_urls(source, posting_url):
    """externalPath is origin-relative, but /job needs the recruiting site prefix."""
    base, site = workday_parts(source)
    posting = urlsplit(posting_url)
    if posting.netloc and posting.netloc.casefold() != base.netloc.casefold():
        raise WorkdayIdentityError('Posting host differs from its source')
    path = posting.path
    if '/job/' not in path or any(part in {'.', '..'} for part in path.split('/')):
        raise WorkdayIdentityError('Invalid posting path')
    prefix, tail = path.split('/job/', 1)
    if prefix and prefix not in {'/' + site, base.path.rstrip('/')}:
        raise WorkdayIdentityError('Posting site differs from its source')
    if not tail:
        raise WorkdayIdentityError('Posting has no job path')
    root = f'https://{base.netloc}'
    public = canonical_url(root + base.path.rstrip('/') + '/job/' + tail)
    api = root + '/wday/cxs/' + base.hostname.split('.')[0] + '/' + site + '/job/' + tail
    return public, api


def legacy_workday_url(source, posting_url):
    """Alias only the exact historical missing-site URL, preserving existing row IDs."""
    try:
        public, _ = workday_urls(source, posting_url)
    except ValueError:
        return None
    base = urlsplit(public)
    return f'https://{base.netloc}/job/' + base.path.split('/job/', 1)[1]


def requisition_id(url):
    return urlsplit(url).path.rsplit('_', 1)[-1].casefold()


def workday_location(primary, additional=None):
    """Use explicit labels only; aggregate counts carry no geographic evidence."""
    values = [primary] + (additional if isinstance(additional, list) else [])
    locations, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = " ".join(value.split())
        if not value or re.fullmatch(
            r"(?:\d+|multiple|various)\s+locations?|locations?|not specified|unknown",
            value, re.I,
        ):
            continue
        if value.casefold() not in seen:
            locations.append(value)
            seen.add(value.casefold())
    # Semicolons preserve separate alternatives for the existing commute policy.
    return "; ".join(locations)


@dataclass(frozen=True)
class WorkdayDetail:
    url: str
    title: str
    requisition: str
    description: str
    location: str = ""


def fetch_workday_detail(source, posting_url, expected_title, timeout=20):
    public, api = workday_urls(source, posting_url)
    request = Request(api, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=timeout) as response:
        if urlsplit(response.geturl()).netloc != urlsplit(api).netloc:
            raise WorkdayIdentityError('Unexpected detail redirect')
        payload = json.load(response)
    info = payload.get('jobPostingInfo', {})
    title, req = info.get('title', ''), info.get('jobReqId', '')
    normalize = lambda text: re.sub(r'\W+', ' ', text.casefold()).strip()
    token = requisition_id(public)
    if not req or not re.fullmatch(re.escape(req.casefold()) + r'(?:-\d+)?', token):
        raise WorkdayIdentityError('Detail requisition differs from the saved posting')
    external = info.get('externalUrl')
    if external and (workday_urls(source, external)[0] != public):
        raise WorkdayIdentityError('Detail canonical URL differs from the requested posting')
    if normalize(title) != normalize(expected_title):
        raise WorkdayTitleChanged('Verified requisition has changed title; manual review required')
    description = clean_job_text(info.get('jobDescription', ''))
    if description_problem(description) or len(description) < 150:
        raise ValueError('Workday detail is missing or invalid')
    return WorkdayDetail(public, title, req, title + '\n' + description,
                         workday_location(info.get('location'), info.get('additionalLocations')))
