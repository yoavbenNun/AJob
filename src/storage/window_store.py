"""Bounded scheduled windows and fenced notification leases on the existing DB."""
from datetime import datetime, timedelta, timezone
import json
import uuid

from src.scheduling.israel_schedule import ISRAEL_TIMEZONE
from src.storage.scan_store import ScanStore


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def expected_windows(reference, days=1):
    if reference.tzinfo is None:
        raise ValueError('A timezone-aware clock is required')
    today = reference.astimezone(ISRAEL_TIMEZONE).date()
    for offset in reversed(range(days)):
        day = today - timedelta(days=offset)
        for hour in (8, 11, 16):
            local = datetime(day.year, day.month, day.day, hour, 17, tzinfo=ISRAEL_TIMEZONE)
            yield local.isoformat(), stamp(local), stamp(local + timedelta(hours=2))


class LeaseLost(RuntimeError):
    pass


class WindowStore(ScanStore):
    def __init__(self, repository, clock=utcnow):
        super().__init__(repository)
        self.clock = clock
        self.scan_owner = None
        self.delivery_owners = {}
        if self.postgres:
            self.execute("SELECT set_config('idle_in_transaction_session_timeout','15000',false)")

    def verify_schema(self):
        super().verify_schema()
        for table in ('scan_windows', 'delivery_leases', 'dispatcher_state'):
            self.execute(f'SELECT * FROM {table} LIMIT 0')

    def seed_windows(self):
        for slot, scheduled, deadline in expected_windows(self.clock()):
            self.execute("INSERT INTO scan_windows(slot_key,scheduled_at,deadline_at) VALUES (?,?,?) ON CONFLICT(slot_key) DO NOTHING", (slot, scheduled, deadline))
            # Preserve a round-1 completed scan during rollout. SMTP failures
            # must not turn an existing full scan back into scheduled work.
            full = self.execute("SELECT run_id,started_at,finished_at FROM scan_runs WHERE slot_key=? AND status='full' ORDER BY started_at DESC LIMIT 1", (slot,)).fetchone()
            if full:
                self.execute("UPDATE scan_windows SET status='full',run_id=?,started_at=?,finished_at=? WHERE slot_key=? AND status='scheduled' AND attempts=0", (full['run_id'],full['started_at'],full['finished_at'],slot))

    def _interrupt(self, row, reason):
        now = stamp(self.clock())
        jobs = self.execute('SELECT count(*) AS n FROM notification_outbox WHERE origin_slot=?', (row['slot_key'],)).fetchone()['n']
        state = 'partial' if jobs or row['status'] == 'partial' else 'failed'
        self.execute('UPDATE scan_windows SET status=?,finished_at=?,lease_until=NULL,retry_at=?,error_type=? WHERE slot_key=?',
                     (state, now, stamp(self.clock() + timedelta(minutes=5)), reason, row['slot_key']))
        if row['run_id']:
            self.execute("UPDATE scan_runs SET status=?,finished_at=? WHERE run_id=? AND status='running'", (state, now, row['run_id']))
        self.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,'pending',?) ON CONFLICT(slot_key) DO NOTHING", (row['slot_key'], now))

    def recover(self):
        now = stamp(self.clock())
        with self.transaction():
            rows = self.execute("SELECT * FROM scan_windows WHERE status!='full'" + (' FOR UPDATE SKIP LOCKED' if self.postgres else '')).fetchall()
            for row in rows:
                if row['deadline_at'] <= now:
                    if row['error_type'] not in {'WindowExpired', 'MissedWindow'}:
                        self._interrupt(row, 'WindowExpired' if row['started_at'] else 'MissedWindow')
                elif row['status'] == 'running' and row['lease_until'] <= now:
                    self._interrupt(row, 'ScanLeaseExpired')
            rows = self.execute("SELECT * FROM delivery_leases WHERE state='processing' AND lease_until<=?", (now,)).fetchall()
            for candidate in rows:
                # Every sender/recovery path locks batch then lease, in that
                # order. Recheck after locking: the sender may just have finished.
                self.execute('SELECT slot_key FROM notification_batches WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (candidate['slot_key'],)).fetchone()
                row = self.execute('SELECT * FROM delivery_leases WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (candidate['slot_key'],)).fetchone()
                if row['state'] != 'processing' or row['lease_until'] > now:
                    continue
                state = 'uncertain' if row['smtp_started_at'] else 'failed'
                retry = stamp(self.clock() + timedelta(minutes=5)) if state == 'failed' else None
                self.execute('UPDATE delivery_leases SET state=?,retry_at=?,error_type=? WHERE slot_key=?', (state, retry, 'DeliveryLeaseExpired', row['slot_key']))
                self.execute("UPDATE notification_batches SET state=?,updated_at=?,error_type=? WHERE slot_key=? AND state!='sent'", (state, now, 'DeliveryLeaseExpired', row['slot_key']))
            # Old round-1 sending has no trustworthy lease; never auto-replay it.
            self.execute("UPDATE notification_batches SET state='uncertain',error_type='UnownedLegacySend' WHERE state='sending' AND slot_key NOT IN (SELECT slot_key FROM delivery_leases)")

    def claim_window(self):
        now = stamp(self.clock())
        row = self.execute('SELECT * FROM scan_windows WHERE scheduled_at<=? ORDER BY scheduled_at DESC LIMIT 1', (now,)).fetchone()
        if (not row or row['status'] in {'full', 'running'} or row['deadline_at'] <= now
                or row['attempts'] >= 2 or (row['retry_at'] and row['retry_at'] > now)):
            return None
        run_id = str(uuid.uuid4())
        with self.transaction():
            # The lock protects CLAIMING only, never an entire scan. A stale
            # connected session cannot block later windows after its lease ends.
            if self.postgres and not self.execute('SELECT pg_try_advisory_xact_lock(714021887) AS acquired').fetchone()['acquired']:
                return None
            active = self.execute("SELECT slot_key FROM scan_windows WHERE status='running' AND lease_until>? AND deadline_at>? LIMIT 1", (now,now)).fetchone()
            if active:
                return None
            changed = self.execute("UPDATE scan_windows SET status='running',run_id=?,started_at=COALESCE(started_at,?),heartbeat_at=?,lease_until=?,attempts=attempts+1,error_type='' WHERE slot_key=? AND status IN ('scheduled','partial','failed') AND attempts<2 AND deadline_at>? AND (retry_at IS NULL OR retry_at<=?) RETURNING slot_key",
                (run_id, now, now, stamp(self.clock() + timedelta(minutes=2)), row['slot_key'], now, now)).fetchone()
            if not changed:
                return None
            self.execute("INSERT INTO scan_runs(run_id,slot_key,status,started_at) VALUES (?,?,'running',?)", (run_id, row['slot_key'], now))
        self.scan_owner = (row['slot_key'], run_id)
        return row['slot_key'], run_id, datetime.fromisoformat(row['deadline_at'])

    def heartbeat(self):
        if not self.scan_owner:
            return False
        slot, owner = self.scan_owner
        now = stamp(self.clock())
        row = self.execute("UPDATE scan_windows SET heartbeat_at=?,lease_until=? WHERE slot_key=? AND run_id=? AND status='running' AND lease_until>? AND deadline_at>? RETURNING slot_key",
            (now, stamp(self.clock() + timedelta(minutes=2)), slot, owner, now, now)).fetchone()
        return bool(row)

    def validate_write(self):
        if self.scan_owner:
            slot, owner = self.scan_owner
            row = self.execute('SELECT status,run_id,lease_until,deadline_at FROM scan_windows WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (slot,)).fetchone()
            now = stamp(self.clock())
            if not row or row['status'] != 'running' or row['run_id'] != owner or min(row['lease_until'], row['deadline_at']) <= now:
                raise LeaseLost('Scan ownership expired')

    def completed_sources(self, slot):
        # Metrics are JSON text on both supported adapters.
        full = set()
        for row in self.execute('SELECT m.company,m.metrics FROM scan_source_metrics m JOIN scan_runs r ON r.run_id=m.run_id WHERE r.slot_key=?', (slot,)):
            if json.loads(row['metrics']).get('status') == 'full':
                full.add(row['company'])
        return full

    def finish_window(self, run_id, summary, enabled_names):
        slot, owner = self.scan_owner
        if owner != run_id:
            raise LeaseLost('Wrong scan owner')
        completed = self.completed_sources(slot)
        if set(enabled_names) <= completed:
            summary.status = 'full'
        elif completed and summary.status == 'failed':
            summary.status = 'partial'
        with self.transaction():
            self.validate_write()
            self.execute('UPDATE scan_runs SET status=?,finished_at=?,summary=? WHERE run_id=?', (summary.status, stamp(self.clock()), json.dumps(summary.counters()), run_id))
            self.execute("UPDATE scan_windows SET status=?,finished_at=?,lease_until=NULL,retry_at=?,error_type=? WHERE slot_key=? AND run_id=?", (summary.status, stamp(self.clock()), stamp(self.clock() + timedelta(minutes=5)), '' if summary.status == 'full' else 'SourceFailureOrBudget', slot, run_id))
        self.scan_owner = None

    def prepare_batch(self, slot):
        # An old failure summary must not consume discoveries from a later slot.
        return super().prepare_batch(slot, origin_before=slot)

    def claim_delivery(self, slot):
        now = stamp(self.clock())
        with self.transaction():
            self.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,'pending',?) ON CONFLICT(slot_key) DO NOTHING", (slot, now))
            batch = self.execute('SELECT state FROM notification_batches WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (slot,)).fetchone()
            if batch['state'] in {'sent', 'sending', 'uncertain'}:
                return False
            row = self.execute('SELECT * FROM delivery_leases WHERE slot_key=?', (slot,)).fetchone()
            if row and (row['state'] in {'processing','sent','uncertain'} or row['attempts'] >= 3 or (row['retry_at'] and row['retry_at'] > now)):
                return False
            owner = str(uuid.uuid4())
            self.execute("INSERT INTO delivery_leases(slot_key,state,owner,lease_until,attempts) VALUES (?,'processing',?,?,1) ON CONFLICT(slot_key) DO UPDATE SET state='processing',owner=excluded.owner,lease_until=excluded.lease_until,smtp_started_at=NULL,attempts=delivery_leases.attempts+1,error_type=''", (slot, owner, stamp(self.clock() + timedelta(minutes=10))))
        self.delivery_owners[slot] = owner
        return True

    def begin_send(self, slot):
        with self.transaction():
            self.execute('SELECT slot_key FROM notification_batches WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (slot,)).fetchone()
            owner = self.delivery_owners.get(slot)
            row = self.execute("UPDATE delivery_leases SET smtp_started_at=? WHERE slot_key=? AND owner=? AND state='processing' AND lease_until>? RETURNING slot_key", (stamp(self.clock()), slot, owner, stamp(self.clock()))).fetchone()
            if not row:
                return False
            return bool(self.execute("UPDATE notification_batches SET state='sending',updated_at=? WHERE slot_key=? AND state IN ('pending','failed') AND message IS NOT NULL RETURNING slot_key", (stamp(self.clock()), slot)).fetchone())

    def finish_send(self, slot, state, error_type=''):
        if state not in {'sent','failed','uncertain'}:
            raise ValueError('Invalid notification result')
        with self.transaction():
            self.execute('SELECT slot_key FROM notification_batches WHERE slot_key=?' + (' FOR UPDATE' if self.postgres else ''), (slot,)).fetchone()
            owner = self.delivery_owners.get(slot)
            row = self.execute("SELECT attempts FROM delivery_leases WHERE slot_key=? AND owner=? AND state='processing'" + (' FOR UPDATE' if self.postgres else ''), (slot, owner)).fetchone()
            if not row:
                return
            retry = stamp(self.clock() + timedelta(minutes=5 * 2 ** (row['attempts'] - 1))) if state == 'failed' else None
            self.execute('UPDATE delivery_leases SET state=?,retry_at=?,error_type=? WHERE slot_key=? AND owner=?', (state, retry, error_type[:80], slot, owner))
            self.execute('UPDATE notification_batches SET state=?,updated_at=?,error_type=? WHERE slot_key=?', (state, stamp(self.clock()), error_type[:80], slot))

    def report(self, days=7):
        rows = []
        for slot, scheduled, deadline in expected_windows(self.clock(), days):
            row = self.execute('SELECT * FROM scan_windows WHERE slot_key=?', (slot,)).fetchone()
            batch = self.execute('SELECT state,error_type FROM notification_batches WHERE slot_key=?', (slot,)).fetchone()
            lease = self.execute('SELECT * FROM delivery_leases WHERE slot_key=?', (slot,)).fetchone()
            jobs = self.execute('SELECT count(*) AS n FROM notification_outbox WHERE origin_slot=?', (slot,)).fetchone()['n']
            state = row['status'] if row else ('failed' if deadline <= stamp(self.clock()) else 'scheduled')
            expired_scan = bool(row and row['status'] == 'running' and row['lease_until'] <= stamp(self.clock()))
            if expired_scan:
                state = 'partial' if jobs else 'failed'
            mail = lease['state'] if lease else (batch['state'] if batch else 'pending')
            expired_delivery = bool(lease and mail == 'processing' and lease['lease_until'] <= stamp(self.clock()))
            if expired_delivery:
                mail = 'uncertain' if lease['smtp_started_at'] else 'failed'
            if mail == 'sending':
                mail = 'uncertain'
            duration = None
            if row and row['started_at']:
                end = datetime.fromisoformat(row['finished_at']) if row['finished_at'] else self.clock()
                duration = max(0, (end - datetime.fromisoformat(row['started_at'])).total_seconds())
            rows.append(dict(slot=slot, scheduled=scheduled, started=row['started_at'] if row else None,
                scan=state, jobs_discovered=jobs, notification=mail, duration_seconds=duration,
                run_id=row['run_id'] if row else None,
                scan_attempts=row['attempts'] if row else 0,
                notification_attempts=lease['attempts'] if lease else 0,
                retry_exhausted=bool((lease and lease['attempts'] >= 3 and lease['state'] == 'failed') or (row and row['attempts'] >= 2 and state in {'failed','partial'})),
                failure_reason=('DeliveryLeaseExpired' if expired_delivery else 'ScanLeaseExpired' if expired_scan else row['error_type'] if row else 'NotObserved' if state == 'failed' else '') or (batch['error_type'] if batch else '')))
        return rows
