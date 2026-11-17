"""Apple Israel listing card extraction and location-specific role identity."""

import re
import time
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


def read_detail(page, job, timeout_ms):
    """Wait for a complete, matching posting, not unrelated page assets."""
    identity = job_id(job.job_url)
    deadline = time.monotonic() + timeout_ms / 1000
    response = page.goto(job.job_url, wait_until="commit", timeout=timeout_ms)
    if response is None or not response.ok or job_id(page.url) != identity:
        raise ValueError("Apple detail navigation did not return the requested posting")
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise TimeoutError("Apple detail budget exhausted")
    snapshot = page.wait_for_function(
        r"""({title, identity}) => {
            const main = document.querySelector('main');
            const normalize = text => text.trim().replace(/\s+/g, ' ').toLowerCase();
            if (!main) return false;
            const visible = selector => Array.from(main.querySelectorAll(selector))
                .filter(h => h.getClientRects().length);
            const titles = visible('h1');
            if (titles.length !== 1 || normalize(titles[0].innerText) !== normalize(title)) return false;
            const text = main.innerText;
            const lines = text.split(/\n/).map(line => line.trim()).filter(Boolean);
            const roles = lines.flatMap((line, index) => /^role number:?$/i.test(line) ? [index] : []);
            if (roles.length !== 1 || lines[roles[0] + 1] !== identity) return false;
            const headings = visible('h2,h3,h4,h5,h6')
                .map(h => normalize(h.innerText).replace(/:$/, ''));
            const description = lines.findIndex(line => /^description:?$/i.test(line));
            const minimum = lines.findIndex(line => /^minimum qualifications:?$/i.test(line));
            if (headings.filter(h => h === 'description').length !== 1 ||
                headings.filter(h => h === 'minimum qualifications').length !== 1) return false;
            if (description < 0 || minimum <= description + 1) return false;
            const hasContent = line => line && /\p{L}.*\p{L}/u.test(line)
                && !headings.includes(normalize(line).replace(/:$/, ''))
                && !/^(?:submit resume|back to search results|loading\b|please wait\b)/i.test(line);
            if (!hasContent(lines[description + 1]) || !hasContent(lines[minimum + 1]))
                return false;
            return text;
        }""",
        arg={"title": job.title, "identity": identity},
        timeout=remaining,
    ).json_value()
    if time.monotonic() >= deadline or job_id(page.url) != identity:
        raise ValueError("Apple detail changed or exceeded its budget")
    return snapshot, response.status
