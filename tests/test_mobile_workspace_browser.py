"""Touch/viewport regressions on synthetic stores in Chromium and WebKit.

WebKit is Safari-engine coverage, not proof of behavior on physical iOS hardware.
"""
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect

from test_balanced_workspace import workspace


@pytest.fixture(params=['chromium', 'webkit'])
def mobile_workspace(workspace, request):
    app, repo, ids = workspace
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        with sync_playwright() as p:
            engine = getattr(p, request.param)
            if not Path(engine.executable_path).is_file():
                pytest.skip(f'{request.param} test browser is not installed')
            browser = engine.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width':390,'height':844},
                    is_mobile=True, has_touch=True, user_agent=p.devices['iPhone 13']['user_agent'])
                context.route('**/*', lambda route:route.continue_()
                              if route.request.url.startswith(origin + '/') else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error:errors.append(str(error)))
                yield page, origin, repo, ids
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize('width', [320,375,390,430])
def test_mobile_layout_order_focus_fonts_and_touch_targets(mobile_workspace, width):
    page, origin, _, ids = mobile_workspace
    page.set_viewport_size({'width':width,'height':844})
    page.goto(origin, wait_until='networkidle')
    assert page.evaluate('document.documentElement.scrollWidth') <= width
    assert page.locator('.job-card').first.get_attribute('data-job-id') == str(ids[2])
    assert page.locator('.job-card').first.bounding_box()['y'] < (800 if width==320 else 755)
    expect(page.locator('.advanced-filters')).not_to_have_attribute('open', '')
    for selector in ['.stat-card-link','.note-trigger','.dismiss-job','.text-link']:
        assert page.locator(selector).first.bounding_box()['height'] >= 44
    search = page.locator('.search-field input')
    assert search.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)') >= 16
    assert page.locator('meta[name=viewport]').get_attribute('content') == 'width=device-width, initial-scale=1'
    page.locator('.stat-card-link').tap()
    expect(page.locator('.job-card')).to_have_count(1)
    assert page.locator('.job-card').first.get_attribute('data-status') == 'new'
    page.goto(origin + f'/jobs/{ids[0]}')
    assert page.evaluate('document.documentElement.scrollWidth') <= width
    for target in page.locator('.workspace-nav a').all():
        assert target.bounding_box()['height'] >= 44
    assert page.locator('.status-form select').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)') >= 16
    external = page.get_by_role('link', name='Open original posting')
    assert external.get_attribute('target') == '_blank'
    assert {'noopener','noreferrer'} <= set(external.get_attribute('rel').split())


def test_touch_notes_status_dismiss_undo_restore_and_drafts(mobile_workspace):
    page, origin, repo, ids = mobile_workspace
    page.goto(origin)
    card = page.locator(f'.job-card[data-job-id="{ids[0]}"]')
    trigger = card.locator('.note-trigger')
    trigger.tap()
    panel = page.locator('.note-editor.is-open')
    text = panel.get_by_role('textbox')
    assert text.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)') >= 16
    note = 'Synthetic mobile note <script>window.noteInjected=true</script>'
    text.fill(note)
    page.locator('h1').tap()
    expect(panel).not_to_be_visible()
    trigger.tap()
    expect(text).to_have_value(note)
    page.route('**/personal-note', lambda route:route.fulfill(status=503, body='Temporary test failure'))
    panel.get_by_role('button', name='Save', exact=True).tap()
    expect(panel.locator('.note-error')).to_be_visible()
    expect(text).to_have_value(note)
    page.unroute('**/personal-note')
    panel.get_by_role('button', name='Save', exact=True).tap()
    expect(panel).not_to_be_visible()
    page.reload()
    trigger.tap()
    expect(text).to_have_value(note)
    assert page.evaluate('window.noteInjected') is None
    text.fill('Discarded synthetic edit')
    panel.get_by_role('button', name='Cancel', exact=True).tap()
    trigger.tap()
    expect(text).to_have_value(note)
    panel.get_by_role('button', name='Cancel', exact=True).tap()
    page.on('dialog', lambda dialog:dialog.accept())
    card.locator('.dismiss-job').tap()
    expect(card).not_to_be_visible()
    assert repo.get_by_id(ids[0]).status == 'ignored'
    page.get_by_role('button', name='Undo', exact=True).tap()
    expect(card).to_be_visible()
    assert repo.get_by_id(ids[0]).status == 'new'
    card.locator('.dismiss-job').tap()
    expect(card).not_to_be_visible()
    page.goto(origin + '/?status=ignored')
    card.locator('.dismiss-job').tap()
    expect(card).not_to_be_visible()
    page.goto(origin + f'/jobs/{ids[0]}')
    page.locator('.status-form select').select_option('applied')
    page.get_by_role('button', name='Save status', exact=True).tap()
    expect(page.locator('.detail-meta .status')).to_have_text('Applied')
    assert repo.get_by_id(ids[0]).status == 'applied'
    page.goto(origin)
    trigger.tap()
    expect(text).to_have_value(note)


def test_note_editor_tracks_simulated_keyboard_visual_viewport(mobile_workspace):
    page, origin, _, _ = mobile_workspace
    # Deterministic geometry only: this does not simulate an actual iOS keyboard.
    page.add_init_script('''
      const visible = new EventTarget();
      Object.assign(visible,{width:390,height:844,offsetTop:0,offsetLeft:0});
      Object.defineProperty(window,'visualViewport',{configurable:true,value:visible});
      window.testVisibleViewport=visible;
    ''')
    page.goto(origin)
    page.locator('.note-trigger').first.tap()
    panel = page.locator('.note-editor.is-open')
    panel.get_by_role('textbox').fill('Keep this draft during keyboard/zoom changes')
    page.evaluate('''() => {
      Object.assign(window.testVisibleViewport,{width:280,height:300,offsetTop:90,offsetLeft:35});
      window.testVisibleViewport.dispatchEvent(new Event('resize'));
    }''')
    box = panel.bounding_box()
    assert box['x'] >= 51 and box['x'] + box['width'] <= 299
    assert box['y'] >= 106 and box['y'] + box['height'] <= 374
    expect(panel.get_by_role('textbox')).to_have_value('Keep this draft during keyboard/zoom changes')
    # Resize/orientation changes must not dismiss editing.
    page.set_viewport_size({'width':844,'height':390})
    expect(panel).to_be_visible()


def test_mobile_multiselect_filters_keep_existing_behavior(mobile_workspace):
    page, origin, _, _ = mobile_workspace
    page.goto(origin)
    page.locator('.advanced-filters > summary').tap()
    picker = page.locator('.status-picker')
    picker.locator('summary').tap()
    picker.get_by_label('New', exact=True).check()
    picker.get_by_label('Reviewed', exact=True).check()
    picker.locator('summary').tap()
    page.locator('select[name=company]').select_option('Demo Systems')
    page.get_by_role('button', name='Apply filters', exact=True).tap()
    expect(page.locator('.job-card')).to_have_count(2)
    expect(page.locator('.filter-chips')).to_contain_text('New')
    expect(page.locator('.filter-chips')).to_contain_text('Reviewed')
    page.get_by_role('link', name='Reset filters', exact=True).tap()
    expect(page.locator('.job-card')).to_have_count(3)


def test_notes_fallback_without_popover_or_visual_viewport_apis(mobile_workspace):
    page, origin, _, _ = mobile_workspace
    page.add_init_script('''
      HTMLElement.prototype.showPopover=undefined;
      HTMLElement.prototype.hidePopover=undefined;
      Object.defineProperty(window,'visualViewport',{configurable:true,value:undefined});
    ''')
    page.goto(origin)
    trigger = page.locator('.note-trigger').first
    trigger.tap()
    panel = page.locator('.note-editor.is-open')
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    assert box['y'] >= 0 and box['y'] + box['height'] <= 844
    panel.get_by_role('textbox').fill('Synthetic fallback draft')
    page.locator('h1').tap()
    expect(panel).not_to_be_visible()
    trigger.tap()
    expect(panel.get_by_role('textbox')).to_have_value('Synthetic fallback draft')
