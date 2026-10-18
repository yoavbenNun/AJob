"""Keyboard/mobile dismissal and multiselect with a disposable local dashboard."""

from pathlib import Path
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.storage.personal_notes_repository import PersonalNotesRepository
from src.web.app import create_app


def test_multiselect_dismiss_restore_and_mobile(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    ids = [repo.save(JobPosting(f'Student software {state}', 'Synthetic', 'Israel',
                               f'https://example.test/{state}', status=state, is_relevant=True))
           for state in ('new', 'reviewed', 'applied')]
    app = create_app(tmp_path / 'jobs.db', notification_recipient_updater=None,
                          outreach_database_path=tmp_path / 'outreach.db',
                          application_database_path=tmp_path / 'applications.db')
    notes = PersonalNotesRepository(tmp_path / 'personal_notes.db')
    notes.save(ids[0], 'Synthetic note survives dismissal')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    base = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                pytest.skip('Chromium is not installed in this environment')
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 900})
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                picker = page.locator('.status-picker')
                picker.locator('summary').focus()
                page.keyboard.press('Enter')
                picker.get_by_label('New', exact=True).check()
                picker.get_by_label('Reviewed', exact=True).check()
                expect(page.locator('#status-selection')).to_have_text('New, Reviewed')
                assert page.url == base + '/'  # No navigation while choosing.
                picker.get_by_label('New', exact=True).uncheck()
                expect(page.locator('#status-selection')).to_have_text('Reviewed')
                picker.get_by_label('New', exact=True).check()
                page.keyboard.press('Escape')
                expect(picker).not_to_have_attribute('open', '')
                page.get_by_role('button', name='Apply filters', exact=True).click()
                expect(page.locator('.job-card')).to_have_count(2)
                card = page.locator(f'[data-job-id="{ids[0]}"]')
                page.once('dialog', lambda dialog: dialog.dismiss())
                card.get_by_role('button', name='Dismiss Student software new', exact=True).click()
                expect(card).to_be_visible()
                page.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button', name='Dismiss Student software new', exact=True).click()
                expect(card).to_be_hidden()
                expect(page.get_by_role('button', name='Undo', exact=True)).to_be_focused()
                page.get_by_role('button', name='Undo', exact=True).click()
                expect(card).to_be_visible()
                assert repo.get_by_id(ids[0]).status == 'new'
                # A failed save keeps the card and original state.
                page.route('**/dismissal', lambda route: route.fulfill(status=503, body='Synthetic failure'))
                page.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button', name='Dismiss Student software new', exact=True).click()
                expect(page.locator('.job-feedback')).to_contain_text('Could not save')
                expect(card).to_be_visible()
                page.unroute('**/dismissal')
                page.once('dialog', lambda dialog: dialog.accept())
                card.get_by_role('button', name='Dismiss Student software new', exact=True).click()
                expect(card).to_be_hidden()
                page.reload()
                expect(card).to_have_count(0)
                page.goto(base + '/?status=ignored')
                expect(card).to_be_visible()
                card.locator('.note-trigger').hover()
                expect(card.get_by_role('tooltip')).to_have_text('Synthetic note survives dismissal')
                page.set_viewport_size({'width': 390, 'height': 844})
                card.get_by_role('button', name='Restore Student software new', exact=True).focus()
                page.keyboard.press('Enter')
                expect(card).to_be_hidden()
                page.goto(base)
                expect(page.locator('.job-card')).to_have_count(3)
                page.locator('.status-picker summary').click()
                page.locator('.status-picker').get_by_label('Reviewed', exact=True).check()
                page.get_by_role('button', name='Apply filters', exact=True).click()
                expect(page.locator('.job-card')).to_have_count(1)
                page.get_by_role('link', name='Reset filters', exact=True).click()
                expect(page.locator('.job-card')).to_have_count(3)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert notes.for_jobs([ids[0]]) == {ids[0]: 'Synthetic note survives dismissal'}
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
