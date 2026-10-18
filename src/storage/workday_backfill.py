"""Explicit, two-phase repair of damaged Workday rows, never an outbox producer."""

from collections import Counter
from datetime import datetime
from urllib.error import HTTPError

from src.job_content import description_problem
from src.scrapers.careers_scraper import extract_requirements
from src.scrapers.workday_details import fetch_workday_detail, WorkdayIdentityError


def snapshot(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in dict(row).items()}


def prepare_backfill(connection, fetch=fetch_workday_detail, blocked=(), previous=None):
    rows = connection.execute(
        "SELECT * FROM job_postings WHERE source LIKE 'https://%%.myworkdayjobs.com/%%' "
        "ORDER BY id"
    ).fetchall()
    plan = {'version': 1, 'rows': [], 'identity_errors': 0}
    denied = set(blocked)
    cached = {entry['before']['id']: entry for entry in (previous or {}).get('rows', [])}
    for row in rows:
        if not description_problem(row['description'], row['requirements']):
            continue
        entry = {'before': snapshot(row), 'after': None, 'error': ''}
        plan['rows'].append(entry)
        prior = cached.get(row['id'])
        if prior and prior['before'] == entry['before']:
            if prior['after'] and not prior['error']:
                entry['after'] = prior['after']
                continue
            if prior['error'] == 'HTTP404':
                entry['error'] = 'HTTP404'
                continue
        if row['company'] in denied:
            entry['error'] = 'AccessDeniedSource'
            continue
        try:
            detail = fetch(row['source'], row['job_url'], row['title'])
            entry['after'] = dict(job_url=detail.url, description=detail.description,
                                  requirements=extract_requirements(detail.description))
        except WorkdayIdentityError:
            plan['identity_errors'] += 1
            entry['error'] = 'IdentityMismatch'
        except HTTPError as error:
            entry['error'] = 'HTTP' + str(error.code)
            if error.code in {403, 429}:
                denied.add(row['company'])
        except Exception as error:
            entry['error'] = type(error).__name__
    for entry in plan['rows']:
        if entry['after']:
            conflict = connection.execute(
                'SELECT id FROM job_postings WHERE job_url=%s AND id<>%s',
                (entry['after']['job_url'], entry['before']['id']),
            ).fetchone()
            if conflict:
                plan['identity_errors'] += 1
                entry['error'] = 'URLCollision'
    proposed = [entry['after']['job_url'] for entry in plan['rows'] if entry['after']]
    if len(proposed) != len(set(proposed)):
        plan['identity_errors'] += 1
    return plan


def summary(plan):
    companies = {}
    for entry in plan['rows']:
        counts = companies.setdefault(entry['before']['company'], Counter())
        counts['ready' if entry['after'] and not entry['error'] else entry['error']] += 1
    return {'candidates': len(plan['rows']), 'identity_errors': plan['identity_errors'],
            'companies': companies}


def apply_backfill(connection, plan):
    """Call only after an independently verified protected backup of this plan."""
    if plan['version'] != 1 or plan['identity_errors']:
        raise WorkdayIdentityError('Backfill identity checks failed; no changes applied')
    ready = [entry for entry in plan['rows'] if entry['after'] and not entry['error']]
    changed = []
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='3s'")
        for entry in ready:
            before, after = entry['before'], entry['after']
            if set(after) != {'job_url', 'description', 'requirements'} or description_problem(after['description']):
                raise ValueError('Invalid repair payload')
            row = connection.execute('SELECT * FROM job_postings WHERE id=%s FOR UPDATE',
                                     (before['id'],)).fetchone()
            if row is None or snapshot(row) != before:
                raise RuntimeError('Row changed since dry run; transaction rolled back')
            updated = connection.execute(
                'UPDATE job_postings SET job_url=%s,description=%s,requirements=%s '
                'WHERE id=%s RETURNING *',
                (after['job_url'], after['description'], after['requirements'], before['id']),
            ).fetchone()
            if snapshot(updated) != {**before, **after}:
                raise RuntimeError('Unexpected row change; transaction rolled back')
            changed.append(before['id'])
    return changed


def restore_backfill(connection, plan):
    """Restore only repaired columns, refusing to overwrite subsequent edits."""
    changed = []
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='3s'")
        for entry in plan['rows']:
            if not entry['after'] or entry['error']:
                continue
            before, after = entry['before'], entry['after']
            row = connection.execute('SELECT * FROM job_postings WHERE id=%s FOR UPDATE',
                                     (before['id'],)).fetchone()
            if row is None or snapshot(row) != {**before, **after}:
                raise RuntimeError('Cannot restore over subsequent changes')
            connection.execute('UPDATE job_postings SET job_url=%s,description=%s,requirements=%s WHERE id=%s',
                               (before['job_url'], before['description'], before['requirements'], before['id']))
            changed.append(before['id'])
    return changed
