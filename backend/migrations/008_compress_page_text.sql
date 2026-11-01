ALTER TABLE page_texts ADD COLUMN body BYTEA;
ALTER TABLE page_texts ALTER COLUMN raw_content DROP NOT NULL;
-- The body is already compressed, so PostgreSQL shouldn't try again.
ALTER TABLE page_texts ALTER COLUMN body SET STORAGE EXTERNAL;
