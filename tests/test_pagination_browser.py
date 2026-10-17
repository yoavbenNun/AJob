"""Exercise plain ATS pagination markup against a real browser DOM."""

import pytest
from playwright.sync_api import Error, sync_playwright

from src.scrapers.careers_scraper import _go_to_next_page


def test_plain_next_link_advances_but_disabled_and_unchanged_links_do_not():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            page = browser.new_page()
            page.set_content("""
                <a id="job" href="https://example.com/jobs/1">First job</a>
                <nav class="pagination">
                  <a class="next" href="#" onclick="event.preventDefault();
                    document.querySelector('#job').href='https://example.com/jobs/2';
                    this.classList.add('disabled');">Next</a>
                </nav>
            """)
            assert _go_to_next_page(page) is True
            assert page.locator("#job").get_attribute("href").endswith("/2")
            # Disabled anchors are technically enabled in Playwright; filtering
            # their CSS class is required to avoid clicking the last page again.
            page.locator("a.next").evaluate("a => a.onclick = () => { throw Error('disabled link clicked'); }")
            assert _go_to_next_page(page) is False
            page.locator("a.next").evaluate("a => { a.classList.remove('disabled'); a.onclick = e => e.preventDefault(); }")
            assert _go_to_next_page(page) is False
            # A fixed site notice must not hide usable same-origin page links.
            page.route("https://example.com/list/*", lambda route: route.fulfill(
                content_type="text/html", body=(
                    '<a href="https://example.com/jobs/3">Third job</a>'
                    if route.request.url.endswith("/2") else
                    '<nav class="pagination"><a class="next" href="/list/2">Next</a></nav>'
                    '<div style="position:fixed;inset:0;z-index:999">Site notice</div>'
                )
            ))
            page.goto("https://example.com/list/1")
            assert _go_to_next_page(page) is True
            assert page.url == "https://example.com/list/2"
        finally:
            browser.close()
