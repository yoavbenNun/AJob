"""Apple Israel listing card extraction and location-specific role identity."""

import re
from urllib.parse import urlsplit


def supported(company):
    return company.careers_url == "https://jobs.apple.com/en-il/search?location=israel-ISR"


def job_id(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    match = re.fullmatch(r"/en-il/details/([0-9]+-[0-9]+)(?:/[^/]+)?/?", parts.path)
    return match[1] if parts.scheme == "https" and parts.netloc == "jobs.apple.com" and match else None


def url_index(urls):
    index = {}
    for url in sorted(urls):
        if identity := job_id(url):
            index.setdefault(identity, url)
    return index


def read_links(page):
    return page.locator(".job-list-item h3 a[href]").evaluate_all(
        """anchors => anchors.map(a => {
            const location = a.closest('.job-list-item').querySelector('.job-title-location');
            return {href: a.href, text: a.innerText.trim(),
                    context: (location?.innerText || '').replace(/^Location\\s*/i, '').trim()};
        })"""
    )
