import json
from unittest.mock import Mock

import pytest

from src.models import Company
from src.scrapers import paused_sources as feeds
from src.scrapers.careers_scraper import CareersScraper, _enrich_job


def test_appsflyer_embedded_feed_enforces_host_and_preserves_location():
    company = Company('AppsFlyer', 'https://careers.appsflyer.com/')
    jobs = [dict(title='Software Student', absolute_url=url,
                 location={'name':'Herzliya, Israel'},content='<p>Python development</p>')
            for url in ['https://careers.appsflyer.com/jobs/position/123',
                        'https://untrusted.example/jobs/123']]
    html = 'var af_gh_constants = ' + json.dumps({'af_gh_jobs':{'jobs':jobs}}) + ';'
    result = feeds.extract_appsflyer_jobs(company,html)
    assert len(result) == 1
    assert result[0].location == 'Herzliya, Israel'
    assert result[0].description == 'Python development'


@pytest.mark.parametrize('tenant',['Wix2','SAPITBusinessSysteme'])
def test_smartrecruiters_uses_verified_tenant_and_enriches_student(monkeypatch,tenant):
    company = Company('Example','https://careers.smartrecruiters.com/'+tenant,
                      allowed_job_hosts=('jobs.smartrecruiters.com',),job_url_patterns=('/'+tenant+'/',))
    urls = []
    def read(url,timeout):
        urls.append(url)
        if '?' in url:
            return json.dumps({'content':[{'id':'123','name':'Software Engineering Student',
                                          'location':{'fullLocation':'Tel Aviv, Israel'}}]})
        return json.dumps({'jobAd':{'sections':{'qualifications':{
            'title':'Requirements','text':'<p>Currently pursuing a Computer Science degree. Python.</p>'}}}})
    monkeypatch.setattr(feeds,'_read',read)
    browser = Mock()
    result = CareersScraper(browser).scan_company(company)
    assert result.error is None
    assert len(result.jobs) == 1
    assert 'country=il' in urls[0]
    assert result.jobs[0].job_url == 'https://jobs.smartrecruiters.com/'+tenant+'/123'
    assert 'Currently pursuing' in result.jobs[0].requirements
    _enrich_job(browser,result.jobs[0],1000)
    browser.new_page.assert_not_called()


def test_unknown_smartrecruiters_tenant_does_not_trigger_network(monkeypatch):
    read = Mock()
    monkeypatch.setattr(feeds,'_read',read)
    with pytest.raises(ValueError,match='Unsupported'):
        feeds.scan_public_feed(Company('Bad','https://careers.smartrecruiters.com/unverified'),1,1)
    read.assert_not_called()


def test_missing_appsflyer_feed_fails_visibly():
    with pytest.raises(ValueError,match='missing'):
        feeds.extract_appsflyer_jobs(Company('AppsFlyer','https://careers.appsflyer.com/'),'<html>Unavailable</html>')


def test_one_failed_detail_preserves_other_postings(monkeypatch):
    company = Company('Wix','https://careers.smartrecruiters.com/Wix2',
                      allowed_job_hosts=('jobs.smartrecruiters.com',),job_url_patterns=('/Wix2/',))
    def read(url,timeout):
        if '?' in url:
            return json.dumps({'content':[{'id':str(i),'name':'Software Engineering Student',
                                          'location':{'fullLocation':'Tel Aviv, Israel'}} for i in range(3)]})
        if url.endswith('/1'):
            raise OSError('unavailable')
        return json.dumps({'jobAd':{'sections':{'description':{'text':'Python student role'}}}})
    monkeypatch.setattr(feeds,'_read',read)
    with pytest.warns(RuntimeWarning,match='detail unavailable for posting 1'):
        jobs = feeds.scan_public_feed(company,1,1)
    assert len(jobs) == 3
    assert jobs[0].description and jobs[2].description
    assert not jobs[1].description
