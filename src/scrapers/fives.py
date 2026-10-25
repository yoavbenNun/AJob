"""Read FIL Robotics listings returned by its ordinary public careers page."""

import base64
import re
from urllib.parse import urlsplit

from src.models import JobPosting
from src.scrapers.greenhouse import html_text


def supported(company):
    return urlsplit(company.careers_url).hostname == "jobs.fivesgroup.com"


def extract_jobs(company, payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get("code") != 200 or not isinstance(data, dict)
            or not isinstance(data.get("list"), list)
            or type(data.get("total")) is not int):
        raise ValueError("Invalid FIL Robotics search response")
    # The current Israel/company search has four results. Never silently claim
    # complete coverage if it grows beyond the page returned by the site.
    if data["total"] != len(data["list"]):
        raise ValueError("FIL Robotics search requires additional pages")
    jobs = {}
    for item in data["list"]:
        if not isinstance(item, dict) or item.get("company") != "FIL Robotics Ltd":
            continue
        identity, reference = str(item.get("id", "")), str(item.get("reference", ""))
        match = re.fullmatch(r"\d+-([A-Za-z0-9_-]+)", identity)
        if (not match or not reference.isdigit()
                or base64.urlsafe_b64encode(reference.encode()).decode().rstrip("=") != match[1]):
            raise ValueError("FIL Robotics requisition identity mismatch")
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError("FIL Robotics listing has no title")
        localities = item.get("localities") or []
        location = "; ".join(", ".join(str(v) for v in (place.get("city_label"), place.get("country")) if v)
                             for place in localities if isinstance(place, dict)) or "Not specified"
        description = "\n".join(html_text(str(item.get(key) or "")) for key in (
            "mission_description_formatted", "profile_description_formatted",
            "additional_descriptions_formatted"))
        url = f"https://jobs.fivesgroup.com/en/offer/{identity}"
        jobs.setdefault(url, JobPosting(title, company.name, location, url,
                                        description=description, source=company.careers_url))
    return list(jobs.values())


def scan_jobs(company, browser, timeout_ms):
    page = browser.new_page()
    try:
        # Let the public site establish its own anonymous session normally. Do
        # not reproduce, store or log the site's session credentials.
        def search_response(response):
            if response.url != "https://api.magnet.work/v2/job-offers" or response.request.method != "POST":
                return False
            request = response.request.post_data_json
            return (isinstance(request, dict) and request.get("countries") == [{"id": "Israel"}]
                    and request.get("q") == "FIL Robotics")

        with page.expect_response(search_response, timeout=timeout_ms) as pending:
            page.goto(company.careers_url, wait_until="domcontentloaded", timeout=timeout_ms)
        response = pending.value
        if response.status != 200:
            raise ValueError("FIL Robotics search unavailable")
        return extract_jobs(company, response.json())
    finally:
        page.close()
