"""Reviewable jobs must not disappear solely because a resume score is low."""
import re
from dataclasses import asdict

from fastapi.testclient import TestClient

from src.matching.resume_matcher import match_resume_to_job
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text
from src.storage.job_repository import JobRepository
from src.storage.scan_store import ScanStore
from src.web.app import create_app


def test_low_score_potential_roles_respect_location_and_status(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    resume_text = ('Candidate\nEDUCATION\nThird-year B.Sc. student in Software Engineering, '
                   'Bar-Ilan University, Israel')
    resume_path = tmp_path / 'candidate.txt'
    resume_path.write_text(resume_text, encoding='utf-8')
    monkeypatch.setattr('src.web.app.get_active_resume', lambda _: resume_path)
    repo = JobRepository(tmp_path / 'jobs.db')
    repo.initialize()
    # Faithful excerpt of the stored Mobileye #84 posting, with no user data.
    description = ('Test Automation Developer- Student Position\n'
                   'Jerusalem, IsraelR&D – Software /Student /On-Site\n'
                   'APPLY FOR THIS JOB\n'
                   'Studies towards B.Sc. or M.Sc. in Computer Science\\ Software Engineering.\n'
                   'At least 2 years till graduation.\nExperience in Python.')
    jobs = []
    for state in ('new', 'reviewed', 'applied', 'ignored'):
        jobs.append(JobPosting('Test Automation Developer- Student Position', 'Mobileye',
                               'Not specified', f'https://example.test/{state}',
                               description=description, status=state))
    jobs.append(JobPosting('Software Student', 'Example', 'Israel', 'https://example.test/ineligible',
                           description='Minimum qualifications:\nCurrently enrolled in a master’s degree program.',
                           requirements='Currently enrolled in a master’s degree program.'))
    jobs.append(JobPosting('Software Intern', 'Example', 'Not specified', 'https://example.test/unknown',
                           description='Internship. Requirements: Python.'))
    jobs.append(JobPosting('Software Intern', 'Example', 'Warsaw, Poland', 'https://example.test/foreign',
                           description='Internship. Requirements: Python.'))
    jobs.append(JobPosting('Software Student', 'Example', 'Israel', 'https://example.test/low-score-student',
                           description='Student position. Join our engineering team.'))
    store = ScanStore(repo)
    store.migrate()
    assert store.save_discovery(jobs[0], 'synthetic-slot')
    outbox_before = [dict(row) for row in store.execute('SELECT * FROM notification_outbox')]
    for job in jobs[1:]:
        repo.save(job)
    before = [asdict(repo.get_by_id(job.id)) for job in jobs]
    match = match_resume_to_job(parse_resume_text(resume_text), jobs[0])
    assert match.eligibility_status == 'potentially_eligible' and match.score < 30
    ineligible = match_resume_to_job(parse_resume_text(resume_text), jobs[4])
    assert ineligible.eligibility_status == 'ineligible'
    student = match_resume_to_job(parse_resume_text(resume_text), jobs[7])
    assert student.eligibility_status == 'student_eligible' and student.score < 30

    client = TestClient(create_app(tmp_path / 'jobs.db', resume_directory=tmp_path,
                                  outreach_database_path=tmp_path / 'outreach.db',
                                  application_database_path=tmp_path / 'applications.db',
                                  personal_notes_database_path=tmp_path / 'notes.db',
                                  notification_recipient_updater=None), base_url='http://127.0.0.1')
    def shown(path):
        response = client.get(path)
        assert response.status_code == 200
        return {int(i) for i in re.findall(r'<article class="job-card" data-job-id="(\d+)"', response.text)}

    assert shown('/') == {jobs[i].id for i in (0, 1, 2, 7)}
    assert shown('/?relevance=possible') == {jobs[i].id for i in (0, 1, 2)}
    assert shown('/?location=review') == {jobs[5].id}
    assert shown('/?status=ignored') == {jobs[3].id}
    assert shown('/?status=reviewed&status=applied') == {jobs[1].id, jobs[2].id}
    assert [asdict(repo.get_by_id(job.id)) for job in jobs] == before
    assert [dict(row) for row in store.execute('SELECT * FROM notification_outbox')] == outbox_before
    store.close()
