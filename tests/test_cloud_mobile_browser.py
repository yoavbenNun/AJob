"""Real mobile engines against HTTPS interception and synthetic ASGI stores.

No listening port, external requests, production database or real owner data.
"""
from pathlib import Path
from dataclasses import replace
import html

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

from test_cloud_dashboard import cloud_workspace
from test_cloud_auth import PASSWORD
from src.web.cloud import build_cloud_app

ORIGIN='https://localhost'


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('width', [375, 390])
def test_cloud_mobile_login_notes_status_filters_and_logout(cloud_workspace, engine, width):
    _, services, ids, config, store = cloud_workspace
    app=build_cloud_app(replace(config,public_origin=ORIGIN),services,store)
    # Browser owns its cookie jar. Each intercepted call supplies that cookie;
    # no server socket or TLS exception is used to simulate the HTTPS edge.
    client = TestClient(app,base_url=ORIGIN,follow_redirects=False)
    with sync_playwright() as p:
        browser_type=getattr(p,engine)
        if not Path(browser_type.executable_path).exists():
            pytest.skip(f'{engine} test browser unavailable')
        browser=browser_type.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':844},is_mobile=True,has_touch=True)
        events=[]
        offline=False
        def route_request(route):
            request=route.request
            if offline:
                route.abort('internetdisconnected')
                return
            if not request.url.startswith(ORIGIN+'/'):
                route.abort()
                return
            client.cookies.clear()
            headers=request.all_headers()
            # WebKit's intercepted POST headers omit the network Cookie header.
            # Bridge only cookies held by its own URL-scoped browser jar. This
            # transport shim does not establish real provider TLS/iOS behavior.
            cookies=context.cookies(request.url)
            if cookies:
                headers['cookie']='; '.join(f"{c['name']}={c['value']}" for c in cookies)
            response=client.request(request.method,request.url,
                headers=headers,content=request.post_data_buffer)
            events.append((request.method,request.url.removeprefix(ORIGIN),response.status_code,
                           request.headers.get('origin')))
            # WebKit cannot fulfill a mocked redirect. Preserve the real cookie
            # result and trigger a new intercepted navigation; redirect codes
            # themselves are independently asserted by the ASGI auth tests.
            for cookie in response.cookies.jar:
                if cookie.value:
                    context.add_cookies([{'name':cookie.name,'value':cookie.value,
                        'url':ORIGIN,'secure':True,'httpOnly':True,'sameSite':'Strict'}])
                else:
                    context.clear_cookies(name=cookie.name)
            if response.status_code==303:
                target=response.headers['location']
                assert target.startswith('/') and not target.startswith('//')
                route.fulfill(status=200,content_type='text/html',headers={'Referrer-Policy':'same-origin'},body=
                    '<meta http-equiv="refresh" content="0;url='+html.escape(target,quote=True)+'">')
            else:
                headers={k:v for k,v in response.headers.items() if k not in {'set-cookie','content-length'}}
                route.fulfill(status=response.status_code,headers=headers,body=response.content)
        context.route('**/*',route_request)
        page=context.new_page()
        page.on('console',lambda message:events.append(('console',message.type,message.text)))
        page.on('requestfailed',lambda request:events.append(('failed',request.url,request.failure)))
        try:
            page.goto(ORIGIN+'/login')
            expect(page.get_by_role('heading',name='AJob sign in')).to_be_visible()
            page.get_by_label('Password',exact=True).fill(PASSWORD)
            page.get_by_role('button',name='Sign in',exact=True).tap()
            try:
                page.wait_for_url(ORIGIN+'/',wait_until='domcontentloaded')
                expect(page.locator('.job-card')).to_have_count(3,timeout=15000)
            except AssertionError:
                raise AssertionError((events,page.content()[-600:])) from None
            assert page.locator('.job-card').first.get_attribute('data-job-id')==str(ids[2])
            assert page.evaluate('document.documentElement.scrollWidth')<=width
            card=page.locator(f'.job-card[data-job-id="{ids[0]}"]')
            card.locator('.note-trigger').tap()
            panel=page.locator('.note-editor.is-open')
            panel.get_by_role('textbox').fill('Synthetic cloud draft')
            page.locator('h1').tap()
            card.locator('.note-trigger').tap()
            expect(panel.get_by_role('textbox')).to_have_value('Synthetic cloud draft')
            offline=True
            panel.get_by_role('button',name='Save',exact=True).tap()
            expect(panel.locator('.note-error')).to_be_visible()
            expect(panel.get_by_role('textbox')).to_have_value('Synthetic cloud draft')
            assert services.notes.for_jobs(ids)=={}
            offline=False
            panel.get_by_role('button',name='Save',exact=True).tap()
            expect(panel).not_to_be_visible()
            page.reload()
            assert services.notes.for_jobs(ids)=={ids[0]:'Synthetic cloud draft'}
            page.on('dialog',lambda dialog:dialog.accept())
            card.locator('.dismiss-job').tap()
            expect(card).not_to_be_visible()
            page.get_by_role('button',name='Undo',exact=True).tap()
            expect(card).to_be_visible()
            page.goto(ORIGIN+f'/jobs/{ids[0]}')
            external=page.get_by_role('link',name='Open original posting')
            assert external.get_attribute('href')=='https://example.test/jobs/0'
            assert external.get_attribute('target')=='_blank'
            page.locator('.status-form select').select_option('applied')
            page.get_by_role('button',name='Save status',exact=True).tap()
            expect(page.locator('.detail-meta .status')).to_have_text('Applied')
            page.goto(ORIGIN+'/?status=applied&status=reviewed')
            expect(page.locator('.job-card')).to_have_count(3)
            page.get_by_role('button',name='Sign out',exact=True).tap()
            expect(page.get_by_role('heading',name='AJob sign in')).to_be_visible()
            assert services.notes.for_jobs(ids)=={ids[0]:'Synthetic cloud draft'}
        finally:
            context.close()
            browser.close()
