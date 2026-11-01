ALTER TABLE audits ADD COLUMN extraction_key TEXT;

CREATE INDEX idx_audits_extraction_key ON audits (extraction_key, created_at);
