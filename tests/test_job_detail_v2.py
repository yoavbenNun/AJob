from fastapi.testclient import TestClient

from src.models import JobPosting
from src.storage.job_repository import JobRepository
from src.web.app import _source_role_preview, _prose_requirements, create_app


def test_preview_uses_complete_source_paragraph_and_preserves_limit():
    text = 'Build Python APIs for internal tools. These tools must only use test data.'
    job = JobPosting('Developer', 'Example', 'Israel', 'https://example.com/job', description=text)
    assert _source_role_preview(job) == text
    assert job.description == text


def test_prose_requirements_and_sparse_data():
    assert _prose_requirements('You must have experience with Python. Build APIs.') == {
        'required': ['You must have experience with Python.']}
    assert not _prose_requirements('Build APIs. Work with Python.')
    assert not _prose_requirements('Preferred: experience with Python is a plus.')
    job = JobPosting('Developer', 'Example', 'Israel', 'https://example.com/job')
    assert not _source_role_preview(job)


def test_sparse_page_has_search_and_no_empty_requirements(tmp_path):
    db = tmp_path / 'jobs.db'
    repo = JobRepository(db)
    repo.initialize()
    job_id = repo.save(JobPosting('C++ & Python', 'A & B', 'Israel', 'https://example.com/job'))
    app = create_app(db, outreach_database_path=tmp_path / 'outreach.db',
                     application_database_path=tmp_path / 'applications.db')
    response = TestClient(app, base_url='http://127.0.0.1').get(f'/jobs/{job_id}')
    assert response.status_code == 200
    assert 'id="requirements"' not in response.text
    assert 'Search LinkedIn for people' in response.text
    assert 'Search results are not verified contacts' in response.text
    assert 'A+%26+B+C%2B%2B+%26+Python' in response.text


def test_detailed_and_nonstandard_text_stays_source_based():
    description = ('Build distributed services using Python. Maintain the deployment pipeline.\n\n'
                   'Your background\nYou must have experience with Python. '
                   'We require knowledge of SQL.')
    job = JobPosting('Engineer', 'Example', 'Israel', 'https://example.com/job', description=description)
    assert _source_role_preview(job) == 'Build distributed services using Python. Maintain the deployment pipeline.'
    assert _prose_requirements(description) == {'required': [
        'You must have experience with Python.', 'We require knowledge of SQL.']}
    assert job.description == description
