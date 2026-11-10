"""Synthetic UI validation; these screenshots are not discovery evidence."""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect
import pytest

from test_contact_discovery import job, record, source
from test_contact_draft_web import configured


@pytest.mark.parametrize("width", [360, 1280])
def test_cards_drafts_copy_offline_and_layout(tmp_path, record, width):
    record["headline"] = "Synthetic engineering headline with long context " * 5
    client, job_id = configured(tmp_path, record)
    recruiter = {**record, "full_name": "Test Recruiter", "headline": "Talent Acquisition",
                 "role_category": "recruiter", "profile_url": "https://linkedin.com/in/synthetic-recruiter"}
    employee = {**record, "full_name": "Test Employee", "headline": "",
                "role_category": "company_employee", "profile_url": "https://linkedin.com/in/synthetic-employee"}
    client.app.state.contact_source = source(tmp_path, [recruiter, record, employee])
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium unavailable")
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": width, "height": 900})
            # Never modify the user's actual clipboard.
            context.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { window.testCopied = value; }}})")
            page = context.new_page()
            external = []

            def serve(route):
                url = urlsplit(route.request.url)
                if url.netloc != "127.0.0.1":
                    external.append(url.netloc)
                    route.abort()
                    return
                response = client.request(route.request.method, url.path,
                                          content=route.request.post_data_buffer)
                route.fulfill(status=response.status_code, body=response.content,
                              content_type=response.headers.get("content-type"))

            page.route("**/*", serve)
            page.goto(f"http://127.0.0.1/jobs/{job_id}")
            page.locator("#outreach summary").click()
            cards = page.locator(".contact-person-card")
            expect(cards).to_have_count(3)
            assert cards.first.locator(".contact-person-card__name").inner_text() == "Test Person"
            expect(cards.first.locator("img")).to_have_count(0)
            assert cards.first.locator(".contact-person-card__headline").get_attribute("title") == record["headline"].strip()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            boxes = [cards.nth(i).bounding_box() for i in range(3)]
            if width == 360:
                assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
            else:
                assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
                assert boxes[0]["width"] <= 245

            # Only synthetic fixtures appear in visual QA artifacts.
            page.locator(".contact-intro").evaluate("(node) => node.textContent = 'Synthetic QA fixtures — not real discovered contacts.'")
            artifacts = Path(".pytest_cache/aj005-artifacts").resolve()
            artifacts.mkdir(parents=True, exist_ok=True)
            page.locator("#outreach").screenshot(path=str(artifacts / f"contacts-{width}.png"))

            pending = []
            page.route("**/contacts/draft", lambda route: pending.append(route))
            cards.first.get_by_role("button", name="Draft message").click()
            expect(page.locator(".contact-draft-dialog")).to_have_attribute("aria-busy", "true")
            expect(page.locator("#contact-draft-status")).to_have_text("Preparing a draft…")
            assert pending
            route = pending.pop()
            response = client.post(urlsplit(route.request.url).path, content=route.request.post_data_buffer)
            route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            page.unroute("**/contacts/draft")
            expect(page.locator("#contact-draft-status")).to_contain_text("Draft ready")
            editor = page.locator("#contact-draft-body")
            assert len(editor.input_value()) <= 200
            editor.fill("Edited synthetic draft.")
            page.get_by_role("button", name="Copy message", exact=True).click()
            assert page.evaluate("window.testCopied") == "Edited synthetic draft."
            editor.fill("x" * 201)
            expect(page.get_by_role("button", name="Copy message", exact=True)).to_be_disabled()
            page.locator("#contact-draft-style").select_option("inquiry")
            expect(page.locator("#contact-draft-status")).to_contain_text("Draft ready")
            assert "opening at Example" in editor.input_value()
            original = editor.input_value()
            page.get_by_role("button", name="Revise draft", exact=True).click()
            expect(page.locator("#contact-draft-status")).to_contain_text("Draft ready")
            assert editor.input_value() != original
            page.locator("#contact-draft-style").select_option("referral")
            expect(page.locator("#contact-draft-status")).to_contain_text("Draft ready")
            assert "if you are not able to help" in editor.input_value()
            editor.fill("Keep my edited text offline.")
            page.route("**/contacts/draft", lambda route: route.abort())
            page.get_by_role("button", name="Revise draft", exact=True).click()
            expect(page.locator("#contact-draft-status")).to_contain_text("Draft unavailable")
            expect(editor).to_have_value("Keep my edited text offline.")
            page.keyboard.press("Escape")
            expect(page.locator(".contact-draft-dialog")).not_to_be_visible()
            expect(cards.first.get_by_role("button", name="Draft message")).to_be_focused()
            assert external == []
            context.close()
        finally:
            browser.close()
