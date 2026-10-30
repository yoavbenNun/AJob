"""Amazon 10558894: a repeated read-more link must not replace the role title."""
import pytest

from src.filtering.job_filter import prefilter_job, classify_job
from src.models import Company
from src.scrapers.careers_scraper import extract_jobs_from_links

COMPANY = Company('Amazon', 'https://www.amazon.jobs/en/search?base_query=software%20student&country=ISR',
                  job_url_patterns=('/en/jobs/',), allowed_job_hosts=('www.amazon.jobs',))
URL = 'https://www.amazon.jobs/en/jobs/10558894/software-development-student'


@pytest.mark.parametrize('labels', [
    ['', '...Read more'], ['...Read more', ''], ['…Read more'],
    ['Read more'], ['Apply now', '...Read more'],
])
def test_student_title_survives_generic_and_empty_repeated_anchors(labels):
    jobs = extract_jobs_from_links(COMPANY,[dict(href=URL,text=label,context='Haifa, Israel') for label in labels])
    assert len(jobs) == 1
    job=jobs[0]
    assert job.title == 'Software Development Student' and job.job_url == URL
    assert prefilter_job(job).relevant
    job.description = ('Develop software for Annapurna Labs.\nBasic Qualifications\n'
                       'BSc student of Computer Science / Computer Engineering / SW Engineering / '
                       'Electrical Engineering (with at least 2 semesters left before graduation). '
                       'Available for 2 to 3 work days per week.')
    assert classify_job(job).relevant


@pytest.mark.parametrize('labels', [
    ['...Read more', 'Software Development Student — Annapurna Labs'],
    ['Software Development Student — Annapurna Labs', '...Read more'],
])
def test_rendered_title_takes_priority_over_navigation_and_slug(labels):
    jobs=extract_jobs_from_links(COMPANY,[dict(href=URL,text=label) for label in labels])
    assert jobs[0].title == 'Software Development Student — Annapurna Labs'


@pytest.mark.parametrize('slug', ['senior-software-engineer','sales-student','helpdesk-student','qa-intern'])
def test_slug_fallback_does_not_accept_non_development_roles(slug):
    url='https://www.amazon.jobs/en/jobs/1/'+slug
    job=extract_jobs_from_links(COMPANY,[dict(href=url,text='...Read more')])[0]
    job.description='Perform manual checks, respond to support requests and document results.'
    assert not classify_job(job).relevant


def test_untrusted_host_is_still_rejected():
    assert extract_jobs_from_links(COMPANY,[dict(href='https://evil.test/en/jobs/1/software-student',text='...Read more')]) == []
