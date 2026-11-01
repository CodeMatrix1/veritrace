-- Page text is stored once, as the same page can appear in many searches.
CREATE TABLE page_texts (
  url TEXT PRIMARY KEY,
  raw_content TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO page_texts (url, raw_content)
SELECT DISTINCT ON (result->>'url') result->>'url', result->>'raw_content'
FROM tool_cache, jsonb_array_elements(response->'results') AS result
WHERE length(coalesce(result->>'raw_content', '')) > 0
ORDER BY result->>'url', length(result->>'raw_content') DESC;

UPDATE tool_cache
SET response = jsonb_build_object('results', coalesce((
  SELECT jsonb_agg(result - 'raw_content')
  FROM jsonb_array_elements(response->'results') AS result
), '[]'::jsonb));

-- Results kept for 24 hours become permanent. Short-lived ones stay short-lived.
UPDATE tool_cache
SET expires_at = now() + interval '100 years'
WHERE status = 'ok' AND expires_at - retrieved_at >= interval '2 hours';
