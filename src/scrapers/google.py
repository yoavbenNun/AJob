"""Google Careers identity across different search contexts."""

import re
from urllib.parse import urlsplit

SEARCH = "https://www.google.com/about/careers/applications/jobs/results/"
ISRAEL_EARLY = SEARCH + "?location=Israel&target_level=EARLY&target_level=INTERN_AND_APPRENTICE"


def supported(company):
    return company.name == "Google" and company.careers_url == SEARCH + "?q=software%20intern"


def job_id(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc.lower() != "www.google.com":
        return None
    match = re.fullmatch(r"/about/careers/applications/jobs/results/([0-9]+)(?:-[^/]+)?/?", parts.path)
    return match[1] if match else None


def url_index(urls):
    result = {}
    for url in sorted(urls):
        if identity := job_id(url):
            result.setdefault(identity, url)
    return result
