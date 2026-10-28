"""Current UI behavior against disposable synthetic stores, never cloud services."""
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from test_balanced_workspace import workspace


@pytest.fixture
def browser_workspace(workspace):
    app, repo, ids = workspace
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).is_file():
                pytest.skip('Chromium is not installed in this environment')
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width':390,'height':844})
                context.route('**/*', lambda route: route.continue_()
                              if route.request.url.startswith(origin + '/') else route.abort())
                page = context.new_page()
                yield page, origin, repo, ids
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_balanced_layout_touch_targets_and_popover_bounds(browser_workspace, width):
    page, origin, _, _ = browser_workspace
    page.set_viewport_size({'width':width,'height':844 if width == 390 else 1000})
    page.goto(origin, wait_until='networkidle')
    assert page.evaluate('document.documentElement.scrollWidth') <= width
    first = page.locator('.job-card').first
    if width == 390:
        assert first.bounding_box()['y'] < 844
        assert not page.locator('.advanced-filters').evaluate('(e)=>e.open')
    for selector in ['.note-trigger', '.dismiss-job', '.text-link']:
        assert first.locator(selector).bounding_box()['height'] >= 44
    assert page.locator('.stat-card--accent strong').evaluate('(e)=>getComputedStyle(e).color') == 'rgb(255, 255, 255)'
    trigger = first.locator('.note-trigger')
    trigger.focus()
    page.keyboard.press('Enter')
    panel = page.locator('.note-editor.is-open')
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert box['y'] >= 0 and box['y'] + box['height'] <= page.viewport_size['height']
    panel.get_by_role('textbox').fill('Unsaved synthetic draft')
    page.locator('h1').click()
    expect(panel).not_to_be_visible()
    trigger.click()
    expect(panel.get_by_role('textbox')).to_have_value('Unsaved synthetic draft')
    panel.get_by_role('button', name='Cancel', exact=True).click()


def test_mobile_filter_drafts_multiselect_apply_reset_and_status(browser_workspace):
    page, origin, repo, ids = browser_workspace
    page.goto(origin)
    filters = page.locator('.advanced-filters')
    filters.locator(':scope > summary').click()
    picker = page.locator('.status-picker')
    picker.locator('summary').click()
    picker.get_by_label('New', exact=True).check()
    picker.get_by_label('Reviewed', exact=True).check()
    page.keyboard.press('Escape')
    filters.locator(':scope > summary').click()
    filters.locator(':scope > summary').click()
    expect(picker.locator('input[value="new"]')).to_be_checked()
    expect(picker.locator('input[value="reviewed"]')).to_be_checked()
    page.locator('select[name=company]').select_option('Demo Systems')
    page.get_by_role('button', name='Apply filters', exact=True).click()
    expect(page.locator('.job-card')).to_have_count(2)
    expect(page.locator('.filter-chips')).to_contain_text('New')
    expect(page.locator('.filter-chips')).to_contain_text('Reviewed')
    expect(page.locator('.filter-chips')).to_contain_text('Demo Systems')
    page.get_by_role('link', name='Reset filters', exact=True).click()
    expect(page.locator('.job-card')).to_have_count(3)
    page.locator('.search-field input').fill('no matching synthetic title')
    page.get_by_role('button', name='Search', exact=True).click()
    expect(page.get_by_role('heading', name='No matching jobs')).to_be_visible()
    page.get_by_role('link', name='Reset filters', exact=True).click()
    page.locator(f'.job-card[data-job-id="{ids[0]}"] h2 a').click()
    expect(page.locator('#resume-match > summary')).not_to_contain_text('Not calculated')
    page.locator('.status-form select').select_option('reviewed')
    page.get_by_role('button', name='Save status', exact=True).click()
    expect(page.locator('.detail-meta .status')).to_have_text('Reviewed')
    assert repo.get_by_id(ids[0]).status == 'reviewed'


def test_long_title_and_empty_description_do_not_overflow(browser_workspace):
    page, origin, repo, ids = browser_workspace
    job = repo.get_by_id(ids[0])
    job.title = 'Software Student — ' + 'Infrastructure and distributed systems ' * 5
    job.description = job.requirements = ''
    repo.save(job)
    page.goto(origin + '/?relevance=all')
    assert page.evaluate('document.documentElement.scrollWidth') == 390
    card = page.locator(f'.job-card[data-job-id="{ids[0]}"]')
    expect(card.locator('h2')).to_have_text(job.title)
    expect(card.locator('.text-link')).to_be_visible()


def test_premium_focus_reduced_motion_and_eligibility_contrast(browser_workspace):
    page, origin, _, _ = browser_workspace
    page.emulate_media(reduced_motion='reduce')
    page.goto(origin)
    card = page.locator('.job-card').first
    assert card.evaluate('(e)=>getComputedStyle(e).transitionDuration') == '0s'
    assert card.evaluate('(e)=>getComputedStyle(e).animationName') == 'none'
    button = page.get_by_role('button', name='Search', exact=True)
    button.focus()
    assert button.evaluate('(e)=>getComputedStyle(e).outlineWidth') == '3px'
    assert button.evaluate('(e)=>getComputedStyle(e).outlineStyle') == 'solid'
    button.evaluate('(e)=>e.disabled=true')
    assert button.evaluate('(e)=>getComputedStyle(e).cursor') == 'not-allowed'
    # Actual computed badge colors, not screenshots or a color-only meaning.
    badge = card.locator('.eligibility-badge')
    expect(badge).to_have_text('Student eligible')
    contrast = badge.evaluate('''e => {
      const c = getComputedStyle(e);
      const luminance = color => {
        const rgb = color.match(/[\\d.]+/g).slice(0,3).map(v => Number(v)/255)
          .map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
        return rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
      };
      const a = luminance(c.color), b = luminance(c.backgroundColor);
      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    }''')
    assert contrast >= 4.5


def test_native_status_form_preserves_real_origin(browser_workspace, monkeypatch):
    from src.web.security import LocalSecurityMiddleware
    page, origin, repo, ids = browser_workspace
    observed = []
    original = LocalSecurityMiddleware.dispatch
    async def capture(self, request, call_next):
        if request.method == "POST":
            observed.append({name: request.headers.get(name)
                             for name in ("origin", "referer", "sec-fetch-site")})
        return await original(self, request, call_next)
    monkeypatch.setattr(LocalSecurityMiddleware, "dispatch", capture)
    response = page.goto(origin + f"/jobs/{ids[0]}")
    assert response.headers["referrer-policy"] == "same-origin"
    page.locator(".status-form select").select_option("reviewed")
    page.get_by_role("button", name="Save status", exact=True).click()
    assert len(observed) == 1
    headers = observed[0]
    assert headers["origin"] == origin
    assert headers["referer"] == origin + f"/jobs/{ids[0]}"
    assert headers["sec-fetch-site"] == "same-origin"
    expect(page.locator(".detail-meta .status")).to_have_text("Reviewed")
    assert repo.get_by_id(ids[0]).status == "reviewed"


def test_referrer_policy_does_not_disclose_local_path_to_external_sites(browser_workspace):
    page, origin, _, ids = browser_workspace
    page.goto(origin + f"/jobs/{ids[0]}")
    observed = []
    def external(route):
        observed.append(route.request.all_headers())
        route.fulfill(status=200, content_type="text/html", body="Synthetic external page")
    # Intercept entirely in the browser: no external network or user data.
    page.context.route("https://example.test/privacy-check", external)
    page.evaluate("""() => {
        const link = document.createElement('a');
        link.href = 'https://example.test/privacy-check';
        link.textContent = 'Synthetic privacy check';
        document.body.append(link);
    }""")
    page.get_by_role("link", name="Synthetic privacy check").click()
    assert len(observed) == 1 and "referer" not in observed[0]
