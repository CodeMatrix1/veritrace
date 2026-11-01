ALTER TABLE audits ADD COLUMN force_fresh BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE audits ADD COLUMN report_key TEXT;
ALTER TABLE audits ADD COLUMN reused_from UUID;

CREATE INDEX idx_audits_report_key ON audits (report_key, created_at);
