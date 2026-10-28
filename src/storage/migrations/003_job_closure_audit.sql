-- Optional: apply only after report-only review and separate activation approval.
-- Never applied by scheduled scans. Retain these rows across Restore/rollback.
CREATE TABLE IF NOT EXISTS job_closure_dismissals (
    job_id BIGINT PRIMARY KEY REFERENCES job_postings(id),
    previous_status TEXT NOT NULL CHECK (previous_status IN ('new','reviewed')),
    checked_at TEXT NOT NULL,
    evidence TEXT NOT NULL
);
ALTER TABLE job_closure_dismissals ENABLE ROW LEVEL SECURITY;
