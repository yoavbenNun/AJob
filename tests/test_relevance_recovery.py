"""Historical recovery uses isolated SQLite and synthetic job data only."""
import json

import pytest

from src.main import main, _recovery_target
from src.models import JobPosting
from src.resume.resume_parser import parse_resume_text
from src.storage.job_repository import JobRepository
from src.storage.relevance_recovery import RecoveryApplyError, apply, preview
from src.storage.scan_store import ScanStore


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    result = JobRepository(tmp_path / 'jobs.db')
    result.initialize()
    return result


def posting(title='Software Engineering Student', *, status='reviewed', location='Jerusalem, Israel', url='1'):
    return JobPosting(title, 'Example', location, f'https://example.com/job/{url}',
                      description='A student role focused on low-level software development.',
                      requirements='Students who have completed at least 3 semesters.',
                      is_relevant=False, status=status)


def test_apple_style_recovery_preserves_history_and_repeated_scan(repo):
    store = ScanStore(repo)
    store.migrate()
    job = posting()
    job.requirements = ('Minimum Qualifications\nPursuing a B.Sc.\\M.Sc. in Electrical Engineering, '
                        'Computer Engineering.\nStudents who have completed at least 3 semesters.')
    assert store.save_discovery(job, 'old')
    before = [dict(r) for r in store.execute('SELECT * FROM notification_outbox')]
    entries, digest, snapshots = preview(repo, [job.id, job.id], target='test')
    assert len(entries) == 1 and entries[0]['candidate'] and len(digest) == 64
    candidate = parse_resume_text('Student\nEDUCATION\nB.Sc. Software Engineering, Example University')
    assessed, _, _ = preview(repo, [job.id], candidate, target='test')
    assert assessed[0]['eligibility'] == 'ineligible' and not assessed[0]['candidate']
    assert repo.get_by_id(job.id).is_relevant is False
    assert apply(repo, entries, snapshots) == [(job.id, True)]
    updated = repo.get_by_id(job.id)
    assert updated.is_relevant and updated.status == 'reviewed'
    assert [dict(r) for r in store.execute('SELECT * FROM notification_outbox')] == before
    job.is_relevant = True
    assert not store.save_discovery(job, 'later')
    assert repo.get_by_id(job.id).status == 'reviewed'
    assert [dict(r) for r in store.execute('SELECT * FROM notification_outbox')] == before
    store.close()


def test_fixed_sample_keeps_ignored_applied_foreign_and_bad_roles(repo):
    jobs = [posting(url='suitable'), posting(status='ignored', url='ignored'),
            posting(status='applied', url='applied'), posting(location='London, UK', url='foreign'),
            posting(title='Sales Student', url='sales'), posting(location='Not specified', url='unknown')]
    accepted = posting(url='already-accepted'); accepted.is_relevant = True
    jobs.append(accepted)
    for job in jobs:
        repo.save(job)
    entries, _, snapshots = preview(repo, [j.id for j in jobs], target='test')
    assert [e['candidate'] for e in entries] == [True, False, False, False, False, True, False]
    assert entries[-2]['location'] == 'unknown' and entries[-2]['eligibility'] == 'not assessed'
    assert sum(j.is_relevant for j in repo.list_all()) == 1
    assert len(apply(repo, entries, snapshots)) == 2
    assert sum(j.is_relevant for j in repo.list_all()) == 3
    assert repo.get_by_id(jobs[1].id).status == 'ignored'
    assert repo.get_by_id(jobs[2].id).status == 'applied'


def test_compare_and_swap_preserves_concurrent_user_and_scanner_changes(repo):
    job = posting(); repo.save(job)
    entries, _, snapshots = preview(repo, [job.id], target='test')
    repo.update_status(job.id, 'ignored')
    assert apply(repo, entries, snapshots) == [(job.id, False)]
    assert not repo.get_by_id(job.id).is_relevant
    repo.update_status(job.id, 'reviewed')
    entries, _, snapshots = preview(repo, [job.id], target='test')
    job.description = 'Changed by a later scan.'
    repo.save(job)
    assert apply(repo, entries, snapshots) == [(job.id, False)]


def test_cli_requires_same_reviewed_plan_and_target(repo, capsys):
    job = posting(); repo.save(job)
    args = ['recover-relevance', '--target', 'sqlite', '--database', str(repo.database_path), '--job-id', str(job.id)]
    assert main(args) == 0
    out = capsys.readouterr().out
    plan, _ = json.JSONDecoder().raw_decode(out)
    assert main(args + ['--apply', '--approve', '0' * 64]) == 1
    assert not repo.get_by_id(job.id).is_relevant
    assert main(args + ['--apply', '--approve', plan['approval_digest']]) == 0
    assert repo.get_by_id(job.id).is_relevant
    assert main(args + ['--apply', '--approve', plan['approval_digest']]) == 1


def test_missing_job_and_overlarge_request_fail_before_writes(repo):
    job = posting(); repo.save(job)
    with pytest.raises(ValueError):
        preview(repo, [job.id, 999], target='test')
    with pytest.raises(ValueError):
        preview(repo, list(range(1, 102)), target='test')
    assert not repo.get_by_id(job.id).is_relevant


def test_digest_binds_database_target_and_current_data(repo):
    job = posting(); repo.save(job)
    _, before, _ = preview(repo, [job.id], target='one')
    _, other, _ = preview(repo, [job.id], target='two')
    assert before != other
    repo.update_status(job.id, 'ignored')
    _, changed, _ = preview(repo, [job.id], target='one')
    assert before != changed
    first = _recovery_target('postgresql://scanner.tenant1:secret@example.com/postgres', repo.database_path)
    second = _recovery_target('postgresql://scanner.tenant2:secret@example.com/postgres', repo.database_path)
    assert first != second and 'secret' not in first
    assert preview(repo, [job.id], target=first)[1] != preview(repo, [job.id], target=second)[1]


@pytest.mark.parametrize('url', ['https://[invalid', 'https:///jobs/no-host'])
def test_invalid_job_url_is_reported_and_other_jobs_are_reviewed(repo, url):
    bad, good = posting(url='bad'), posting(url='good')
    repo.save(bad); repo.save(good)
    with repo._connect() as connection:
        connection.execute('UPDATE job_postings SET job_url = ? WHERE id = ?', (url, bad.id))
    entries, _, snapshots = preview(repo, [bad.id, good.id], target='test')
    assert entries[0]['url_valid'] is False and entries[0]['candidate'] is False
    assert entries[1]['url_valid'] is True and entries[1]['candidate'] is True
    assert apply(repo, entries, snapshots) == [(good.id, True)]
    assert repo.get_by_id(bad.id).job_url == url
    assert not repo.get_by_id(bad.id).is_relevant


@pytest.mark.parametrize('database', ['reviewed', 'unreviewed'])
def test_rejects_connection_string_database_override(repo, database):
    with pytest.raises(ValueError, match='unsupported connection options'):
        _recovery_target(f'postgresql://qa@localhost/postgres?dbname={database}', repo.database_path)


def test_pooler_tls_options_remain_compatible_and_credentials_hidden(repo, monkeypatch):
    for key in ('PGSERVICE', 'PGSERVICEFILE', 'PGHOSTADDR', 'PGOPTIONS', 'PGPORT'):
        monkeypatch.delenv(key, raising=False)
    one = _recovery_target('postgresql://scanner.project@pooler.example:5432/reviewed?sslmode=require', repo.database_path)
    two = _recovery_target('postgresql://scanner.project@pooler.example:5432/unreviewed?sslmode=require', repo.database_path)
    assert one != two
    assert 'sslmode' not in one
    assert preview(repo, [repo.save(posting())], target=one)[1] != preview(repo, [1], target=two)[1]


def test_ambiguous_libpq_environment_fails_closed(repo, monkeypatch):
    monkeypatch.setenv('PGPORT', '9999')
    with pytest.raises(ValueError, match='ambiguous'):
        _recovery_target('postgresql://qa@localhost/postgres', repo.database_path)


def test_partial_failure_reports_committed_ids(repo, monkeypatch):
    first, second = posting(url='1'), posting(url='2')
    repo.save(first); repo.save(second)
    entries, _, snapshots = preview(repo, [first.id, second.id], target='test')
    original = repo.recover_relevance_if_unchanged
    def interrupted(job):
        if job.id == second.id:
            raise RuntimeError('database unavailable')
        return original(job)
    monkeypatch.setattr(repo, 'recover_relevance_if_unchanged', interrupted)
    with pytest.raises(RecoveryApplyError) as error:
        apply(repo, entries, snapshots)
    assert error.value.results == [(first.id, True)]


def test_cli_reports_committed_ids_on_later_failure(repo, monkeypatch, capsys):
    first, second = posting(url='1'), posting(url='2')
    repo.save(first); repo.save(second)
    args = ['recover-relevance', '--target', 'sqlite', '--database', str(repo.database_path),
            '--job-id', str(first.id), '--job-id', str(second.id)]
    assert main(args) == 0
    digest = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]['approval_digest']
    original = repo.recover_relevance_if_unchanged
    def interrupted(job):
        if job.id == second.id:
            raise RuntimeError('database unavailable')
        return original(job)
    monkeypatch.setattr(repo, 'recover_relevance_if_unchanged', interrupted)
    monkeypatch.setattr('src.main.create_job_repository', lambda _: repo)
    assert main(args + ['--apply', '--approve', digest]) == 1
    captured = capsys.readouterr()
    assert f'"updated_ids": [{first.id}]' in captured.out
    assert 'may have committed' in captured.err


def test_unrecorded_education_is_reviewable_not_ineligible(repo):
    from src.matching.resume_matcher import match_resume_to_job
    job = posting(); job.requirements = 'Minimum qualifications: B.Sc. in Computer Science.'
    resume = parse_resume_text('Dana Developer\nSKILLS\nPython')
    result = match_resume_to_job(resume, job)
    assert result.eligibility_status == 'potentially_eligible'
    assert result.eligible


def test_known_closed_field_mismatch_still_rejects_without_degree_level():
    from src.matching.resume_matcher import match_resume_to_job
    job = posting(); job.requirements = 'Minimum qualifications: B.Sc. in Electrical Engineering, Computer Engineering.'
    resume = parse_resume_text('Dana Developer\nEDUCATION\nSoftware Engineering student')
    assert match_resume_to_job(resume, job).eligibility_status == 'ineligible'
