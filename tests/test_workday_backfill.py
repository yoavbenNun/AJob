"""Repair planning is read-only and failures never become replacement content."""

from dataclasses import asdict
import os
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from src.models import JobPosting
from src.scrapers.workday_details import WorkdayIdentityError
from src.storage.workday_backfill import prepare_backfill, apply_backfill, summary


class ReadConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query, params=()):
        assert query.startswith('SELECT')
        return SimpleNamespace(fetchall=lambda: self.rows, fetchone=lambda: None)


def damaged(number):
    return asdict(JobPosting('Software Student', 'Intel', 'Israel',
                             f'https://intel.wd1.myworkdayjobs.com/job/Israel/Software_JR{number}',
                             id=number, source='https://intel.wd1.myworkdayjobs.com/en-US/External',
                             description='Page not found.', status='applied'))


def test_access_denial_stops_requests_for_same_source():
    calls = []
    def denied(*args):
        calls.append(args)
        raise HTTPError(args[1], 403, 'Denied', {}, None)
    plan = prepare_backfill(ReadConnection([damaged(1), damaged(2)]), fetch=denied)
    assert len(calls) == 1
    assert [row['error'] for row in plan['rows']] == ['HTTP403', 'AccessDeniedSource']
    assert all(row['after'] is None for row in plan['rows'])
    assert summary(plan)['candidates'] == 2


def test_valid_existing_description_not_even_fetched():
    row = damaged(1)
    row['description'] = 'Develop Python software for cloud services.'
    assert not prepare_backfill(ReadConnection([row]), fetch=lambda *a: pytest.fail('unexpected request'))['rows']


def test_identity_mismatch_prevents_any_apply():
    def mismatch(*args):
        raise WorkdayIdentityError('Mismatched requisition')
    connection = ReadConnection([damaged(1)])
    plan = prepare_backfill(connection, fetch=mismatch)
    assert plan['identity_errors'] == 1
    with pytest.raises(WorkdayIdentityError):
        apply_backfill(connection, plan)


def test_resume_reuses_only_unchanged_verified_results():
    from src.scrapers.workday_details import WorkdayDetail
    row = damaged(1)
    detail = WorkdayDetail(row['source'] + '/job/Israel/Software_JR1', row['title'], 'JR1',
                           'Develop Python software. Requirements: currently pursuing a degree.')
    plan = prepare_backfill(ReadConnection([row]), fetch=lambda *a: detail)
    resumed = prepare_backfill(ReadConnection([row]), previous=plan,
                                fetch=lambda *a: pytest.fail('Repeated successful request'))
    assert resumed == plan
    row['status'] = 'reviewed'
    calls = []
    prepare_backfill(ReadConnection([row]), previous=plan, fetch=lambda *a: calls.append(a) or detail)
    assert len(calls) == 1


@pytest.mark.skipif(os.name != 'nt', reason='Current-user Windows DPAPI backup')
def test_large_protected_backup_restores_without_securestring_truncation():
    from scripts.backfill_workday import protected_json
    raw = 'synthetic data ' * 15000
    assert protected_json(protected_json(raw), decrypt=True) == raw
