"""Real Chromium UI checks against synthetic jobs and a local disposable server."""

import socket
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
import uvicorn

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.web.app import create_app


def test_notes_card_interaction_and_failed_save(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    database = tmp_path / 'jobs.db'
    repository = JobRepository(database)
    repository.initialize()
    ids = [repository.save(JobPosting(f'Software Student {i}', 'Example', 'Israel',
                                       f'https://example.test/jobs/{i}')) for i in (1, 2)]
    app = create_app(database, outreach_database_path=tmp_path / 'outreach.db',
                     application_database_path=tmp_path / 'applications.db', notification_recipient_updater=None)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    base = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    browser = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                pytest.skip('Chromium is not installed in this environment')
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 900})
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            card = page.locator('.job-card').filter(has=page.get_by_role('heading', name='Software Student 1', exact=True))
            trigger = card.get_by_role('button', name='Personal notes for Software Student 1', exact=True)
            trigger.focus()
            page.keyboard.press('Enter')
            panel = card.get_by_role('dialog')
            expect(panel).to_be_visible()
            text = panel.get_by_role('textbox')
            text.fill('First note\nAsk about student hours.')
            page.screenshot(path=str(tmp_path / 'notes-desktop.png'))
            page.locator('h1').hover()
            expect(panel).to_be_visible()  # Hovering outside does not dismiss.
            text.click()
            expect(panel).to_be_visible()  # Editing inside does not dismiss.
            page.locator('h1').click()
            expect(panel).not_to_be_visible()
            assert PersonalNotesRepository(tmp_path / 'personal_notes.db').for_jobs(ids) == {}
            trigger.click()
            expect(text).to_have_value('First note\nAsk about student hours.')
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel).not_to_be_visible()
            expect(trigger).to_be_focused()
            trigger.hover()
            expect(card.get_by_role('tooltip')).to_have_text('First note\nAsk about student hours.')
            expect(card.locator('.personal-note')).to_have_class('personal-note has-note')
            trigger.click()
            text.fill('Cancelled draft')
            panel.get_by_role('button', name='Cancel', exact=True).click()
            trigger.click()
            expect(text).to_have_value('First note\nAsk about student hours.')
            text.fill('Edited note')
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel).not_to_be_visible()
            page.reload()
            trigger.hover()
            expect(card.get_by_role('tooltip')).to_have_text('Edited note')
            trigger.click()
            text.fill('Keep this after failed save')
            page.route('**/personal-note', lambda route: route.fulfill(status=503, body='Temporary failure'))
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel.get_by_role('alert')).to_contain_text('Your text is still here')
            expect(text).to_have_value('Keep this after failed save')
            page.unroute('**/personal-note')
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel).not_to_be_visible()
            trigger.click()
            raw = '<img src=x onerror=alert(1)><script>alert(1)</script>'
            text.fill(raw)
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel).not_to_be_visible()
            trigger.hover()
            expect(card.get_by_role('tooltip')).to_have_text(raw)
            assert card.locator('script, .note-preview img').count() == 0
            page.get_by_role('button', name='Personal notes for Software Student 2', exact=True).click()
            other = page.locator('.note-editor.is-open')
            other.get_by_role('textbox').fill('Separate note')
            other.get_by_role('button', name='Save', exact=True).click()
            expect(other).not_to_be_visible()
            trigger.click()
            text.fill('')
            panel.get_by_role('button', name='Save', exact=True).click()
            expect(panel).not_to_be_visible()
            notes = PersonalNotesRepository(tmp_path / 'personal_notes.db')
            assert notes.for_jobs(ids) == {ids[1]: 'Separate note'}
            # Narrow viewport: the editor stays inside the viewport and works by tap.
            page.set_viewport_size({'width': 375, 'height': 700})
            trigger.click()
            expect(panel).to_be_visible()
            box = panel.bounding_box()
            assert 0 <= box['x'] and box['x'] + box['width'] <= 375
            page.screenshot(path=str(tmp_path / 'notes-mobile.png'))
            page.keyboard.press('Escape')
            expect(panel).not_to_be_visible()
            assert not errors
            browser.close()
            browser = None
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
