-- Round 2: additive operational state; apply only during an approved rollout.
CREATE TABLE IF NOT EXISTS scan_windows (
    slot_key TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','running','full','partial','failed')),
    started_at TEXT,
    finished_at TEXT,
    run_id TEXT,
    lease_until TEXT,
    heartbeat_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    error_type TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS delivery_leases (
    slot_key TEXT PRIMARY KEY REFERENCES notification_batches(slot_key),
    state TEXT NOT NULL CHECK(state IN ('pending','processing','sent','failed','uncertain')),
    owner TEXT NOT NULL,
    lease_until TEXT,
    smtp_started_at TEXT,
    retry_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS delivery_retry ON delivery_leases(state,retry_at);
CREATE TABLE IF NOT EXISTS dispatcher_state (
    name TEXT PRIMARY KEY,
    next_try TEXT,
    last_accepted_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT ''
);
ALTER TABLE scan_windows ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE dispatcher_state ENABLE ROW LEVEL SECURITY;
