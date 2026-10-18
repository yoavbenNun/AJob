"""Transactional discovery/outbox storage; PostgreSQL in cloud, SQLite in tests.

One reusable connection, short transactions, no schema changes on scanner startup.
No notification is marked sent until SMTP has returned successfully. Interrupted
sending is held as uncertain, never automatically made pending again.
"""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from src.models import JobPosting
from src.job_content import description_problem
from src.urls import canonical_url


def now():
    return datetime.now(timezone.utc).isoformat()


class SlotBusy(RuntimeError):
    pass


class ScanStore:
    def __init__(self, repository):
        self.repository = repository
        self.postgres = hasattr(repository, "database_url")
        self.connection = repository._connect()
        self.lock_key = None
        self.transactions = 0
        self.statements = 0
        if self.postgres:
            self.connection.autocommit = True
            self.connection.execute("SELECT set_config('statement_timeout','5000',false)")
            self.connection.execute("SELECT set_config('lock_timeout','2000',false)")
        else:
            self.connection.isolation_level = None
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=2000")

    def close(self):
        self.connection.close()  # also releases PostgreSQL's session advisory lock

    def execute(self, sql, values=()):
        self.statements += 1
        query = sql.replace("?", "%s") if self.postgres and isinstance(sql, str) else sql
        return self.connection.execute(query, values)

    @contextmanager
    def transaction(self):
        self.transactions += 1
        if self.postgres:
            with self.connection.transaction():
                yield
        else:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def migrate(self):
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            self._apply_migration(path)

    def _apply_migration(self, path):
        sql = path.read_text()
        sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        with self.transaction():
            for statement in sql.split(";"):
                if statement.strip() and (self.postgres or not statement.strip().startswith("ALTER TABLE")):
                    if not self.postgres and "UPDATE notification_batches SET prepared_at" in statement:
                        columns = {row['name'] for row in self.execute('PRAGMA table_info(notification_batches)')}
                        if 'prepared_at' not in columns:
                            self.execute('ALTER TABLE notification_batches ADD COLUMN prepared_at TEXT')
                    self.execute(statement)

    def verify_schema(self):
        for table in ("scan_runs", "scan_source_metrics", "notification_batches", "notification_outbox"):
            self.execute(f"SELECT * FROM {table} LIMIT 0")
        self.execute("SELECT prepared_at FROM notification_batches LIMIT 0")

    def managed_windows_exist(self):
        """Detect activation without requiring round-2 tables on old schemas."""
        if self.postgres:
            exists = self.execute("SELECT to_regclass('scan_windows') AS name").fetchone()['name']
        else:
            exists = self.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scan_windows'").fetchone()
        return bool(exists and self.execute('SELECT slot_key FROM scan_windows LIMIT 1').fetchone())

    def acquire_slot(self, slot):
        if self.postgres and self.lock_key is None:
            key = int.from_bytes(hashlib.sha256(slot.encode()).digest()[:8], "big", signed=True)
            row = self.execute("SELECT pg_try_advisory_lock(?) AS acquired", (key,)).fetchone()
            if not row["acquired"]:
                raise SlotBusy("Delivery slot has an active scanner")
            self.lock_key = key

    def start_run(self, slot):
        self.acquire_slot(slot)
        run_id = str(uuid.uuid4())
        with self.transaction():
            # Only the lock owner can recover a previous run. Sending is not a
            # retryable state: SMTP acceptance may have preceded the interruption.
            self.execute("UPDATE scan_runs SET status='partial',finished_at=? WHERE slot_key=? AND status='running'", (now(), slot))
            self.execute("UPDATE notification_batches SET state='uncertain',updated_at=? WHERE slot_key=? AND state='sending'", (now(), slot))
            self.execute("INSERT INTO scan_runs(run_id,slot_key,status,started_at) VALUES (?,?,'running',?)", (run_id, slot, now()))
        return run_id

    def finish_run(self, run_id, summary):
        with self.transaction():
            self.execute("UPDATE scan_runs SET status=?,finished_at=?,summary=? WHERE run_id=?", (summary.status, now(), json.dumps(summary.counters()), run_id))

    def record_metrics(self, run_id, company, metrics):
        with self.transaction():
            self.execute("INSERT INTO scan_source_metrics VALUES (?,?,?) ON CONFLICT(run_id,company) DO UPDATE SET metrics=excluded.metrics", (run_id, company, json.dumps(metrics)))

    def known_urls(self):
        return {row["job_url"] for row in self.execute("SELECT job_url FROM job_postings").fetchall()}

    def validate_write(self):
        """Overridden by the scheduled scanner to fence an expired owner."""

    def save_discovery(self, job, slot):
        job.job_url = canonical_url(job.job_url)
        if not job.job_url or job.discovered_at is None:
            raise ValueError("Discovery requires canonical URL and timestamp")
        values = (job.title, job.company, job.location, job.job_url, job.description,
                  job.requirements, job.source, job.discovered_at.isoformat(),
                  job.is_relevant if self.postgres else int(job.is_relevant), job.status)
        with self.transaction():
            self.validate_write()
            inserted = self.execute("""INSERT INTO job_postings
                (title,company,location,job_url,description,requirements,source,discovered_at,is_relevant,status)
                VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_url) DO NOTHING RETURNING id""", values).fetchone()
            if inserted:
                job.id = int(inserted["id"])
                payload = asdict(job)
                payload["discovered_at"] = job.discovered_at.isoformat()
                self.execute("INSERT INTO notification_outbox(job_id,origin_slot,snapshot) VALUES (?,?,?)", (job.id, slot, json.dumps(payload)))
            else:
                valid = description_problem(job.description, job.requirements) is None
                self.execute("""UPDATE job_postings SET title=?,company=?,location=?,
                    description=CASE WHEN ? THEN ? ELSE description END,
                    requirements=CASE WHEN ? THEN ? ELSE requirements END,
                    source=?,discovered_at=?,is_relevant=? WHERE job_url=?""",
                    values[:3] + (description_problem(job.description) is None, job.description,
                                  valid, job.requirements) + values[6:9] + (job.job_url,))
                job.id = int(self.execute("SELECT id FROM job_postings WHERE job_url=?", (job.job_url,)).fetchone()["id"])
        return bool(inserted)

    def reject_known(self, url):
        with self.transaction():
            self.validate_write()
            self.execute("UPDATE job_postings SET is_relevant=? WHERE job_url=?", (False if self.postgres else 0, canonical_url(url)))

    def batch_state(self, slot):
        row = self.execute("SELECT state FROM notification_batches WHERE slot_key=?", (slot,)).fetchone()
        return row["state"] if row else None

    def scan_was_full(self, slot):
        return self.execute("SELECT run_id FROM scan_runs WHERE slot_key=? AND status='full' LIMIT 1", (slot,)).fetchone() is not None

    def prepare_batch(self, slot, *, origin_before=None):
        with self.transaction():
            self.execute("INSERT INTO notification_batches(slot_key,state,updated_at) VALUES (?,'pending',?) ON CONFLICT(slot_key) DO NOTHING", (slot, now()))
            row = self.execute("SELECT state,message,prepared_at FROM notification_batches WHERE slot_key=?" +
                               (" FOR UPDATE" if self.postgres else ""), (slot,)).fetchone()
            if row["state"] not in {"pending", "failed"}:
                return []
            if row["prepared_at"] is None:
                # Claim pending discoveries across failed/partial earlier scans;
                # origin_slot remains unchanged for auditability. Frozen batches
                # never acquire new jobs after a send attempt.
                rows = self.execute("SELECT job_id FROM notification_outbox WHERE batch_slot IS NULL" +
                    (" AND origin_slot<=?" if origin_before else "") +
                    (" FOR UPDATE SKIP LOCKED" if self.postgres else ""),
                    (origin_before,) if origin_before else ()).fetchall()
                for item in rows:
                    self.execute("UPDATE notification_outbox SET batch_slot=? WHERE job_id=? AND batch_slot IS NULL", (slot, item["job_id"]))
                self.execute("UPDATE notification_batches SET prepared_at=? WHERE slot_key=?", (now(), slot))
            rows = self.execute("SELECT o.snapshot,j.status AS current_status FROM notification_outbox o JOIN job_postings j ON j.id=o.job_id "
                                "WHERE o.batch_slot=? AND j.status<>'ignored' ORDER BY o.job_id", (slot,)).fetchall()
        jobs = []
        for row in rows:
            payload = json.loads(row["snapshot"])
            payload['status'] = row['current_status']
            payload["discovered_at"] = datetime.fromisoformat(payload["discovered_at"])
            jobs.append(JobPosting(**payload))
        return jobs

    def pending_batches(self):
        return [row["slot_key"] for row in self.execute("SELECT slot_key FROM notification_batches WHERE state IN ('pending','failed') ORDER BY updated_at").fetchall()]

    def dismissed_batch_urls(self, slot):
        return [row['job_url'] for row in self.execute(
            "SELECT j.job_url FROM notification_outbox o JOIN job_postings j ON j.id=o.job_id "
            "WHERE o.batch_slot=? AND j.status='ignored'", (slot,)).fetchall()]

    def freeze_message(self, slot, message, *, previous_message=None):
        with self.transaction():
            # Only definitely unsent batches may be recomposed after a dismissal.
            # Membership, delivery state and sent/uncertain messages stay intact.
            self.execute("UPDATE notification_batches SET message=? WHERE slot_key=? "
                         + ("AND message=? " if previous_message is not None else "AND message IS NULL ")
                         + "AND state IN ('pending','failed')",
                         (message, slot, previous_message) if previous_message is not None else (message, slot))
        return self.execute("SELECT message FROM notification_batches WHERE slot_key=?", (slot,)).fetchone()["message"]

    def begin_send(self, slot):
        with self.transaction():
            row = self.execute("UPDATE notification_batches SET state='sending',updated_at=? WHERE slot_key=? AND state IN ('pending','failed') AND message IS NOT NULL RETURNING slot_key", (now(), slot)).fetchone()
        return bool(row)

    def finish_send(self, slot, state, error_type=""):
        if state not in {"sent", "failed", "uncertain"}:
            raise ValueError("Invalid delivery result")
        with self.transaction():
            self.execute("UPDATE notification_batches SET state=?,updated_at=?,error_type=? WHERE slot_key=? AND state='sending'", (state, now(), error_type[:80], slot))
