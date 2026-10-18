-- Additive migration. Apply explicitly only after approval; preserve legacy ledger.
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    slot_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','full','partial','failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS scan_runs_slot ON scan_runs(slot_key, started_at);
CREATE TABLE IF NOT EXISTS scan_source_metrics (
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id),
    company TEXT NOT NULL,
    metrics TEXT NOT NULL,
    PRIMARY KEY(run_id,company)
);
CREATE TABLE IF NOT EXISTS notification_batches (
    slot_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('pending','sending','sent','failed','uncertain')),
    message TEXT,
    prepared_at TEXT,
    updated_at TEXT NOT NULL,
    error_type TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    job_id BIGINT PRIMARY KEY REFERENCES job_postings(id),
    origin_slot TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    batch_slot TEXT REFERENCES notification_batches(slot_key)
);
CREATE INDEX IF NOT EXISTS notification_outbox_batch ON notification_outbox(batch_slot);
-- Upgrade an already-created round-1 schema without rewriting payloads.
ALTER TABLE notification_batches ADD COLUMN IF NOT EXISTS prepared_at TEXT;
UPDATE notification_batches SET prepared_at=updated_at
WHERE prepared_at IS NULL AND (message IS NOT NULL OR EXISTS (
    SELECT 1 FROM notification_outbox WHERE batch_slot=notification_batches.slot_key
));
ALTER TABLE scan_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_source_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_outbox ENABLE ROW LEVEL SECURITY;

-- Do not replay legacy deliveries: even failed legacy sends may have reached SMTP.
INSERT INTO notification_batches(slot_key,state,updated_at,error_type)
SELECT slot_key, CASE WHEN status='sent' THEN 'sent' ELSE 'uncertain' END,
       CAST(claimed_at AS TEXT), 'LegacyDeliveryRequiresReconciliation'
FROM notification_deliveries WHERE 1=1
ON CONFLICT(slot_key) DO NOTHING;
