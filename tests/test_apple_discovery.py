from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from src.config_loader import load_companies
from src.filtering.job_filter import prefilter_job
from src.scrapers import apple
from src.scrapers.careers_scraper import CareersScraper


IDS = ("200676939-1451", "200676939-0865", "200676939-6109")
CITIES = ("Haifa", "Herzliya", "Jerusalem")


def card(identity, city, slug="software-engineering-student"):
    url = f"/en-il/details/{identity}/{slug}?team=HRDWR"
    return f"""
    <div class="job-list-item"><div><h3><a href="{url}">Software Engineering Student</a></h3>
    Hardware</div><div class="job-title-location">Location<br>{city}</div>
    <a href="{url}">See full role description</a></div>
    """


@pytest.mark.parametrize("failure", [None, "503", "repeat", "scope"])
def test_apple_search_union_pagination_and_partial(failure):
    company = next(c for c in load_companies("config/companies.yaml") if c.name == "Apple")
    requests = []
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = playwright.chromium.launch()
        context = browser.new_context()

        def serve(route):
            query = parse_qs(urlsplit(route.request.url).query)
            assert query["location"] == ["israel-ISR"]
            focused, second = "search" in query, "page" in query
            requests.append((focused, second))
            if focused and second and failure == "503":
                route.fulfill(status=503, body="unavailable")
                return
            if focused and second and failure == "scope":
                route.fulfill(status=302, headers={"Location": company.careers_url + "&page=2"})
                return
            if focused:
                html = card(IDS[2], CITIES[2]) if second else (
                    card(IDS[0], CITIES[0]) + card(IDS[1], CITIES[1])
                    + card("200000001-1451", "Haifa", "renamed"))
                if second and failure == "repeat":
                    html = card(IDS[0], CITIES[0], "another-slug")
            else:
                html = card("200000002-1451" if second else "200000001-1451", "Haifa")
            if not second:
                query["page"] = ["2"]
                from urllib.parse import urlencode
                html += '<a aria-label="Next page" href="?' + urlencode(query, doseq=True) + '">Next</a>'
            route.fulfill(status=200, content_type="text/html", body=html)

        context.route("**/en-il/search?*", serve)
        scraper = CareersScraper(context, timeout_ms=1500)
        before = scraper._scan_rendered(company)
        requests.clear()
        after = scraper.scan_company(company)
        browser.close()
    assert requests[:4] == [(False, False), (False, True), (True, False), (True, True)]
    assert len(requests) == (5 if failure == "scope" else 4)
    assert len(before.jobs) == 2
    assert len(after.jobs) == (4 if failure else 5)
    assert after.error == ("AppleDiscoveryIncomplete" if failure else None)
    assert not after.closure_snapshot
    recovered = {apple.job_id(job.job_url): job for job in after.jobs if "200676939" in job.job_url}
    count = 2 if failure else 3
    assert set(recovered) == set(IDS[:count])
    for identity, city in zip(IDS[:count], CITIES):
        assert recovered[identity].location == city
        assert prefilter_job(recovered[identity]).relevant


def test_apple_identity_preserves_office_suffix_and_recognizes_aliases():
    urls = [f"https://jobs.apple.com/en-il/details/{identity}/old-slug?team=STDNT" for identity in IDS]
    index = apple.url_index(urls)
    assert [index[apple.job_id(url.replace("old-slug?team=STDNT", "new-slug"))] for url in urls] == urls
    assert apple.job_id(urls[0].replace("jobs.apple.com", "jobs.apple.com.example.test")) is None
