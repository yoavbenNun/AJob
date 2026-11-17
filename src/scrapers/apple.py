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
            const normalize = text => text.trim().replace(/\s+/g, ' ').toLowerCase();
            const containers = [...document.querySelectorAll('main')]
                .filter(node => node.getClientRects().length);
            // Apple exposes the posting under main; there is no trusted per-role
            // wrapper here. Reject the entire snapshot if ownership is ambiguous.
            if (containers.length !== 1) return false;
            const main = containers[0];
            const visible = selector => Array.from(main.querySelectorAll(selector))
                .filter(h => h.getClientRects().length);
            const titles = visible('h1');
            if (titles.length !== 1 || normalize(titles[0].innerText) !== normalize(title)) return false;
            const text = main.innerText;
            const lines = text.split(/\n/).map(line => line.trim()).filter(Boolean);
            // Inspect the same full snapshot that will be persisted, including
            // markers in prose and text split across DOM nodes or lines.
            if ((text.match(/\brole\s+number\b/gi) || []).length !== 1) return false;
            const ids = text.match(/\b[0-9]{9}-[0-9]{4}\b/g) || [];
            if (ids.length !== 1 || ids[0] !== identity) return false;
            const role = lines.findIndex(line => /^role\s+number(?:\s*:|\s|$)/i.test(line));
            if (role < 0) return false;
            const value = lines[role].replace(/^role\s+number\s*:?\s*/i, '');
            if ((value || lines[role + 1]) !== identity) return false;
            const headings = visible('h2,h3,h4,h5,h6')
                .map(h => normalize(h.innerText).replace(/:$/, ''));
            const section = label => {
                if (headings.filter(h => h === label).length !== 1) return -1;
                const matches = lines.flatMap((line, index) =>
                    normalize(line).replace(/:$/, '') === label ? [index] : []);
                return matches.length === 1 ? matches[0] : -1;
            };
            const description = section('description');
            const minimum = section('minimum qualifications');
            if (description <= role || minimum <= description + 1) return false;
            if (headings.some(h => /^(?:qualifications|requirements|minimum requirements)$/.test(h)))
                return false;
            const placeholder = line => /^(?:loading\b|please wait\b)/i.test(line);
            if (lines.slice(description + 1).some(placeholder)) return false;
            const hasContent = line => line && /\p{L}.*\p{L}/u.test(line)
                && !headings.includes(normalize(line).replace(/:$/, ''))
                && !/^(?:submit resume|back to search results)/i.test(line);
            if (!hasContent(lines[description + 1]) || !hasContent(lines[minimum + 1]))
                return false;
            const block = start => {
                const end = lines.findIndex((line, index) => index > start &&
                    headings.includes(normalize(line).replace(/:$/, '')));
                return lines.slice(start, end < 0 ? lines.length : end).join('\n');
            };
            const preferred = section('preferred qualifications');
            if (headings.includes('preferred qualifications') &&
                (preferred <= minimum || !hasContent(lines[preferred + 1]))) return false;
            const requirements = [block(minimum), ...(preferred < 0 ? [] : [block(preferred)])].join('\n');
            return {description: block(description) + '\n' + requirements, requirements, text};
        }""",
        arg={"title": job.title, "identity": identity},
        timeout=remaining,
    ).json_value()
    if time.monotonic() >= deadline or job_id(page.url) != identity:
        raise ValueError("Apple detail changed or exceeded its budget")
    return snapshot, response.status
