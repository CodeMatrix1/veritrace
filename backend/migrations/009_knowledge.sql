-- Saved official pages that the chatbot answers from. They are kept permanently.
CREATE TABLE knowledge_pages (
  url TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  title TEXT NOT NULL,
  published TEXT,
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_chunks (
  id BIGSERIAL PRIMARY KEY,
  url TEXT NOT NULL REFERENCES knowledge_pages(url) ON DELETE CASCADE,
  position INT NOT NULL,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  search_vector TSVECTOR GENERATED ALWAYS AS (
    setweight(to_tsvector('english', title), 'A') || setweight(to_tsvector('english', text), 'B')
  ) STORED
);

CREATE INDEX idx_knowledge_search ON knowledge_chunks USING GIN (search_vector);
CREATE INDEX idx_usage_purpose ON usage (purpose, created_at);
