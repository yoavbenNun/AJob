-- OPTIONAL, MANUAL ONLY. Never applied by scanner startup or normal migrations.
-- Apply and verify before separately enabling AJOB_SCAN_ALERT_DEDUP=1.
CREATE TABLE IF NOT EXISTS scan_alert_schema (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL CHECK (version = 1)
);
INSERT INTO scan_alert_schema(id, version) VALUES (1, 1) ON CONFLICT(id) DO NOTHING;
CREATE TABLE IF NOT EXISTS scan_alert_receipts (
    fingerprint TEXT NOT NULL,
    repository TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_attempt INTEGER NOT NULL CHECK (run_attempt > 0),
    event_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed')),
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    PRIMARY KEY (fingerprint, repository, run_id, run_attempt),
    CHECK ((state = 'pending' AND confirmed_at IS NULL) OR
           (state = 'confirmed' AND confirmed_at IS NOT NULL))
);
ALTER TABLE scan_alert_schema ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_alert_receipts ENABLE ROW LEVEL SECURITY;
