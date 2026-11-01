CREATE TABLE audits (
  id UUID PRIMARY KEY,
  input_type TEXT NOT NULL,
  input_content TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT,
  overall_result TEXT,
  public_error TEXT,
  report JSONB,
  policy_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE claims (
  id UUID PRIMARY KEY,
  audit_id UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  position INT NOT NULL,
  text TEXT NOT NULL,
  source_text TEXT NOT NULL,
  kind TEXT NOT NULL,
  key_details JSONB NOT NULL DEFAULT '[]',
  numbers JSONB NOT NULL DEFAULT '[]',
  time TEXT,
  location TEXT,
  primary_domain TEXT,
  secondary_domains JSONB NOT NULL DEFAULT '[]',
  claim_type TEXT,
  investigated BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE searches (
  id UUID PRIMARY KEY,
  claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  purpose TEXT NOT NULL,
  scope TEXT NOT NULL,
  adapter TEXT NOT NULL,
  query TEXT NOT NULL,
  request JSONB NOT NULL,
  status TEXT NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  raw_result_count INT NOT NULL DEFAULT 0,
  unique_candidate_count INT NOT NULL DEFAULT 0,
  cached BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id UUID PRIMARY KEY,
  ref TEXT NOT NULL,
  audit_id UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  canonical_url TEXT NOT NULL,
  domain TEXT NOT NULL,
  title TEXT,
  content_type TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  text_snapshot TEXT NOT NULL,
  sentences JSONB NOT NULL,
  language TEXT,
  published_at TIMESTAMPTZ,
  published_raw TEXT,
  retrieved_at TIMESTAMPTZ NOT NULL,
  UNIQUE (audit_id, canonical_url)
);

CREATE TABLE search_results (
  id UUID PRIMARY KEY,
  search_id UUID NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
  document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  domain TEXT NOT NULL,
  rank INT NOT NULL,
  title TEXT,
  snippet TEXT,
  published_raw TEXT
);

CREATE TABLE evidence (
  id UUID PRIMARY KEY,
  claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  sentence_ids JSONB NOT NULL,
  text TEXT NOT NULL,
  stance TEXT NOT NULL,
  addresses_detail_ids JSONB NOT NULL DEFAULT '[]',
  numbers JSONB NOT NULL DEFAULT '[]',
  reason TEXT NOT NULL,
  limits_claim BOOLEAN NOT NULL DEFAULT false,
  number_match TEXT,
  validation_status TEXT NOT NULL,
  validation_reason TEXT,
  skeptic_status TEXT NOT NULL DEFAULT 'unchanged',
  skeptic_note TEXT,
  used_in_verdict BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE verdicts (
  claim_id UUID PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
  verdict TEXT NOT NULL,
  confidence TEXT,
  rule_id TEXT NOT NULL,
  reason_code TEXT,
  policy_version TEXT NOT NULL,
  explanation TEXT NOT NULL
);

CREATE TABLE ledger_entries (
  id UUID PRIMARY KEY,
  audit_id UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  claim_id UUID REFERENCES claims(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  kind TEXT NOT NULL,
  statement TEXT NOT NULL,
  evidence_ids JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE trace_events (
  id BIGSERIAL PRIMARY KEY,
  audit_id UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tool_cache (
  cache_key TEXT PRIMARY KEY,
  adapter TEXT NOT NULL,
  response JSONB NOT NULL,
  status TEXT NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE usage (
  id BIGSERIAL PRIMARY KEY,
  audit_id UUID REFERENCES audits(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  model TEXT,
  purpose TEXT NOT NULL,
  prompt_version TEXT,
  status TEXT NOT NULL,
  attempts INT NOT NULL DEFAULT 1,
  credits_used NUMERIC,
  input_tokens INT,
  output_tokens INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_trace_audit_id ON trace_events (audit_id, id);
CREATE INDEX idx_claims_audit ON claims (audit_id);
CREATE INDEX idx_evidence_claim ON evidence (claim_id);
CREATE INDEX idx_search_results_doc ON search_results (document_id);
CREATE INDEX idx_documents_audit ON documents (audit_id);
CREATE INDEX idx_usage_provider ON usage (provider, created_at);
CREATE INDEX idx_tool_cache_expires ON tool_cache (expires_at);
