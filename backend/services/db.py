import json
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg

from config.settings import RETENTION_DAYS
from schemas.audit import AuditInput
from schemas.claim import AtomicClaim, ClaimClassification
from schemas.document import Document
from schemas.evidence import Evidence
from schemas.search import SearchHit, SearchRequest
from schemas.verdict import ClaimVerdict

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def apply_migrations(database_url: str) -> list[str]:
    conn = await asyncpg.connect(database_url)
    try:
        encoding = await conn.fetchval("SHOW server_encoding")
        if encoding != "UTF8":
            raise RuntimeError(
                f"The database uses {encoding}, but Veritrace needs UTF8 to store "
                "sources in any language. Recreate it with ENCODING 'UTF8'."
            )
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text("utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES ($1)", path.name
                )
            applied.append(path.name)
        return applied
    finally:
        await conn.close()


def compress_text(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), level=9)


class Repository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "Repository":
        pool = await asyncpg.create_pool(
            database_url, min_size=1, max_size=10, init=_init_connection
        )
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    # Audits

    async def create_audit(self, audit_input: AuditInput, policy_version: str) -> str:
        audit_id = str(uuid.uuid4())
        await self.pool.execute(
            "INSERT INTO audits (id, input_type, input_content, status, policy_version, "
            "force_fresh, input_context) VALUES ($1, $2, $3, 'queued', $4, $5, $6)",
            audit_id, audit_input.type, audit_input.content, policy_version, audit_input.fresh,
            audit_input.context.model_dump() if audit_input.context else None,
        )
        return audit_id

    async def get_audit(self, audit_id: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM audits WHERE id = $1", audit_id)

    async def set_status(self, audit_id: str, status: str) -> None:
        await self.pool.execute(
            "UPDATE audits SET status = $2, updated_at = now() "
            "WHERE id = $1 AND status <> 'cancelled'",
            audit_id, status,
        )

    async def set_stage(self, audit_id: str, stage: str) -> None:
        await self.pool.execute(
            "UPDATE audits SET current_stage = $2, updated_at = now() "
            "WHERE id = $1 AND status <> 'cancelled'",
            audit_id, stage,
        )

    async def fail_audit(self, audit_id: str, public_error: str) -> None:
        await self.pool.execute(
            "UPDATE audits SET status = 'failed', public_error = $2, updated_at = now() "
            "WHERE id = $1 AND status <> 'cancelled'",
            audit_id, public_error,
        )

    async def finish_audit(
        self, audit_id: str, overall_result: str, report: dict[str, Any]
    ) -> None:
        await self.pool.execute(
            "UPDATE audits SET status = 'completed', overall_result = $2, report = $3, "
            "updated_at = now() WHERE id = $1 AND status <> 'cancelled'",
            audit_id, overall_result, report,
        )

    async def audit_finished(self, audit_id: str) -> bool:
        status = await self.pool.fetchval("SELECT status FROM audits WHERE id = $1", audit_id)
        return status in ("completed", "failed", "cancelled", None)

    async def cancel_audit(self, audit_id: str) -> bool:
        """Mark an audit cancelled, so its worker stops at the next step."""
        row = await self.pool.fetchval(
            "UPDATE audits SET status = 'cancelled', updated_at = now() "
            "WHERE id = $1 AND status IN ('queued', 'running') RETURNING id",
            audit_id,
        )
        return row is not None

    async def audit_cancelled(self, audit_id: str) -> bool:
        status = await self.pool.fetchval("SELECT status FROM audits WHERE id = $1", audit_id)
        return status == "cancelled"

    async def typical_seconds_per_claim(self) -> float | None:
        """Return the median time per claim of recent audits that ran in full."""
        return await self.pool.fetchval(
            "SELECT percentile_cont(0.5) WITHIN GROUP "
            "(ORDER BY extract(epoch FROM updated_at - created_at) / claims) FROM ("
            "SELECT a.created_at, a.updated_at, count(c.id) AS claims FROM audits a "
            "JOIN claims c ON c.audit_id = a.id AND c.investigated "
            "WHERE a.status = 'completed' AND a.reused_from IS NULL "
            "GROUP BY a.id ORDER BY a.created_at DESC LIMIT 20) recent"
        )

    async def claim_texts(self, audit_id: str) -> list[str]:
        rows = await self.pool.fetch(
            "SELECT text FROM claims WHERE audit_id = $1 AND investigated ORDER BY position",
            audit_id,
        )
        return [r["text"] for r in rows]

    async def queued_audit_ids(self) -> list[str]:
        rows = await self.pool.fetch(
            "SELECT id FROM audits WHERE status = 'queued' ORDER BY created_at"
        )
        return [str(r["id"]) for r in rows]

    async def fail_interrupted_audits(self, message: str) -> int:
        rows = await self.pool.fetch(
            "UPDATE audits SET status = 'failed', public_error = $1, updated_at = now() "
            "WHERE status = 'running' RETURNING id",
            message,
        )
        return len(rows)

    async def count_active_audits(self) -> int:
        return await self.pool.fetchval(
            "SELECT count(*) FROM audits WHERE status IN ('queued', 'running')"
        )

    async def count_audits_today(self) -> int:
        """Count useful checks and answered questions, which share one daily limit.

        Failed, incomplete and cancelled checks, and text with no claims, aren't counted.
        """
        return await self.pool.fetchval(
            "WITH start AS (SELECT greatest(date_trunc('day', now()), "
            "(SELECT max(reset_at) FROM limit_resets)) AS at) "
            "SELECT (SELECT count(*) FROM audits, start WHERE created_at >= start.at "
            "AND status = 'completed' "
            "AND overall_result NOT IN ('assessment_incomplete', 'no_factual_claims')) "
            "+ (SELECT count(*) FROM usage, start WHERE purpose = 'chat_question' "
            "AND created_at >= start.at)"
        )

    async def reset_daily_limit(self) -> None:
        await self.pool.execute("INSERT INTO limit_resets DEFAULT VALUES")

    # Claims

    async def save_claims(
        self, audit_id: str, investigated: list[AtomicClaim], skipped: list[AtomicClaim]
    ) -> None:
        rows = [
            (
                c.id, audit_id, c.position, c.text, c.source_text, c.kind,
                [d.model_dump() for d in c.key_details],
                [n.model_dump(mode="json") for n in c.numbers],
                c.time, c.location, flag,
            )
            for flag, group in ((True, investigated), (False, skipped))
            for c in group
        ]
        await self.pool.executemany(
            "INSERT INTO claims (id, audit_id, position, text, source_text, kind, "
            "key_details, numbers, time, location, investigated) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            rows,
        )

    async def save_classification(self, c: ClaimClassification) -> None:
        await self.pool.execute(
            "UPDATE claims SET primary_domain = $2, secondary_domains = $3, claim_type = $4 "
            "WHERE id = $1",
            c.claim_id, c.primary_domain, list(c.secondary_domains), c.claim_type,
        )

    async def set_report_key(self, audit_id: str, key: str) -> None:
        await self.pool.execute("UPDATE audits SET report_key = $2 WHERE id = $1", audit_id, key)

    async def find_report(
        self, key: str, audit_id: str, max_age_days: int
    ) -> asyncpg.Record | None:
        """Find the newest reusable report from an audit that really ran.

        Weak or incomplete results are skipped, so a temporary search or fetch
        failure can't be repeated for days.
        """
        return await self.pool.fetchrow(
            "SELECT id, overall_result, report, created_at FROM audits "
            "WHERE report_key = $1 AND id <> $2 AND status = 'completed' "
            "AND reused_from IS NULL AND report IS NOT NULL "
            "AND overall_result NOT IN ('assessment_incomplete', 'unverified_insufficient') "
            "AND created_at > now() - make_interval(days => $3) "
            "ORDER BY created_at DESC LIMIT 1",
            key, audit_id, max_age_days,
        )

    async def finish_reused_audit(
        self, audit_id: str, source_id: str, overall_result: str, report: dict[str, Any]
    ) -> None:
        await self.pool.execute(
            "UPDATE audits SET status = 'completed', overall_result = $2, report = $3, "
            "reused_from = $4, updated_at = now() WHERE id = $1 AND status <> 'cancelled'",
            audit_id, overall_result, report, source_id,
        )

    async def set_extraction_key(self, audit_id: str, key: str) -> None:
        await self.pool.execute(
            "UPDATE audits SET extraction_key = $2 WHERE id = $1", audit_id, key
        )

    async def find_extraction(
        self, key: str, audit_id: str
    ) -> tuple[list[AtomicClaim], list[AtomicClaim], dict[str, ClaimClassification]] | None:
        """Copy the latest finished extraction for this key, with new claim IDs."""
        source_id = await self.pool.fetchval(
            "SELECT id FROM audits WHERE extraction_key = $1 AND id <> $2 "
            "ORDER BY created_at DESC LIMIT 1",
            key, audit_id,
        )
        if source_id is None:
            return None
        rows = await self.pool.fetch(
            "SELECT * FROM claims WHERE audit_id = $1 ORDER BY position", source_id
        )
        investigated, skipped, classifications = [], [], {}
        for row in rows:
            claim = AtomicClaim(
                id=str(uuid.uuid4()),
                position=row["position"],
                text=row["text"],
                source_text=row["source_text"],
                kind=row["kind"],
                key_details=row["key_details"],
                numbers=row["numbers"],
                time=row["time"],
                location=row["location"],
            )
            if not row["investigated"]:
                skipped.append(claim)
                continue
            investigated.append(claim)
            classifications[claim.id] = ClaimClassification(
                claim_id=claim.id,
                primary_domain=row["primary_domain"],
                secondary_domains=row["secondary_domains"],
                claim_type=row["claim_type"],
            )
        return investigated, skipped, classifications

    # Searches

    async def save_search(
        self,
        req: SearchRequest,
        status: str,
        attempts: int,
        cached: bool,
        raw_count: int,
    ) -> str:
        search_id = str(uuid.uuid4())
        request = {
            "include_domains": req.include_domains,
            "exclude_domains": req.exclude_domains,
            "max_results": req.max_results,
        }
        await self.pool.execute(
            "INSERT INTO searches (id, claim_id, purpose, scope, adapter, query, request, "
            "status, attempts, raw_result_count, cached) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            search_id, req.claim_id, req.purpose, req.scope, req.adapter, req.query,
            request, status, attempts, raw_count, cached,
        )
        return search_id

    async def save_search_results(self, hits: list[SearchHit]) -> None:
        if not hits:
            return
        await self.pool.executemany(
            "INSERT INTO search_results (id, search_id, url, canonical_url, domain, rank, "
            "title, snippet, published_raw) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            [
                (
                    str(uuid.uuid4()), h.search_id, h.url, h.canonical_url, h.domain,
                    h.rank, h.title, h.snippet, h.published_raw,
                )
                for h in hits
            ],
        )

    async def set_unique_candidates(self, search_ids: list[str], count: int) -> None:
        await self.pool.execute(
            "UPDATE searches SET unique_candidate_count = $2 WHERE id = ANY($1::uuid[])",
            search_ids, count,
        )

    async def link_search_results(
        self, search_ids: list[str], canonical_url: str, document_id: str
    ) -> None:
        await self.pool.execute(
            "UPDATE search_results SET document_id = $3 "
            "WHERE search_id = ANY($1::uuid[]) AND canonical_url = $2",
            search_ids, canonical_url, document_id,
        )

    # Documents and evidence

    async def save_document(self, d: Document) -> None:
        await self.pool.execute(
            "INSERT INTO documents (id, ref, audit_id, canonical_url, domain, title, "
            "content_type, content_hash, text_snapshot, sentences, language, published_at, "
            "published_raw, retrieved_at, retrieved_via) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)",
            d.id, d.ref, d.audit_id, d.canonical_url, d.domain, d.title, d.content_type,
            d.content_hash, d.text_snapshot,
            [{"id": s.id, "text": s.text} for s in d.sentences],
            d.language, d.published_at, d.published_raw, d.retrieved_at, d.retrieved_via,
        )

    async def save_evidence(self, items: list[Evidence]) -> None:
        if not items:
            return
        await self.pool.executemany(
            "INSERT INTO evidence (id, claim_id, document_id, sentence_ids, text, stance, "
            "addresses_detail_ids, numbers, reason, number_match, "
            "validation_status, validation_reason, skeptic_status, skeptic_note, "
            "used_in_verdict) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
            "$13, $14, $15) "
            "ON CONFLICT (id) DO UPDATE SET validation_status = EXCLUDED.validation_status, "
            "validation_reason = EXCLUDED.validation_reason, "
            "skeptic_status = EXCLUDED.skeptic_status, skeptic_note = EXCLUDED.skeptic_note, "
            "used_in_verdict = EXCLUDED.used_in_verdict",
            [
                (
                    e.id, e.claim_id, e.document_id, e.sentence_ids, e.text, e.stance,
                    e.addresses_detail_ids,
                    [n.model_dump(mode="json") for n in e.numbers],
                    e.reason, e.number_match, e.validation_status,
                    e.validation_reason, e.skeptic_status, e.skeptic_note,
                    e.used_in_verdict,
                )
                for e in items
            ],
        )

    # Verdicts and ledger

    async def save_verdict(self, v: ClaimVerdict) -> None:
        await self.pool.execute(
            "INSERT INTO verdicts (claim_id, verdict, confidence, rule_id, reason_code, "
            "policy_version, explanation) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (claim_id) DO UPDATE SET verdict = EXCLUDED.verdict, "
            "confidence = EXCLUDED.confidence, rule_id = EXCLUDED.rule_id, "
            "reason_code = EXCLUDED.reason_code, explanation = EXCLUDED.explanation",
            v.claim_id, v.verdict, v.confidence, v.rule_id, v.reason_code,
            v.policy_version, v.explanation,
        )

    async def load_verdicts(self, audit_id: str) -> dict[str, ClaimVerdict]:
        rows = await self.pool.fetch(
            "SELECT v.* FROM verdicts v JOIN claims c ON c.id = v.claim_id "
            "WHERE c.audit_id = $1",
            audit_id,
        )
        return {
            str(r["claim_id"]): ClaimVerdict(**{**dict(r), "claim_id": str(r["claim_id"])})
            for r in rows
        }

    async def save_ledger(self, audit_id: str, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        await self.pool.executemany(
            "INSERT INTO ledger_entries (id, audit_id, claim_id, label, kind, statement, "
            "evidence_ids) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            [
                (
                    str(uuid.uuid4()), audit_id, e["claim_id"], e["label"], e["kind"],
                    e["statement"], e["evidence_ids"],
                )
                for e in entries
            ],
        )

    # Trace

    async def add_trace(
        self,
        audit_id: str,
        stage: str,
        status: str,
        message: str | None,
        metadata: dict[str, Any],
    ) -> None:
        await self.pool.execute(
            "INSERT INTO trace_events (audit_id, stage, status, message, metadata) "
            "VALUES ($1, $2, $3, $4, $5)",
            audit_id, stage, status, message, metadata,
        )

    async def trace_after(self, audit_id: str, after_id: int) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            "SELECT id, stage, status, message, metadata, created_at FROM trace_events "
            "WHERE audit_id = $1 AND id > $2 ORDER BY id",
            audit_id, after_id,
        )
        return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]

    # Search cache

    async def cache_get(self, cache_key: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM tool_cache WHERE cache_key = $1 AND expires_at > now()", cache_key
        )

    async def cache_put(
        self,
        cache_key: str,
        adapter: str,
        response: dict[str, Any],
        status: str,
        retrieved_at: datetime,
        expires_at: datetime,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO tool_cache (cache_key, adapter, response, status, retrieved_at, "
            "expires_at) VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (cache_key) DO UPDATE SET response = EXCLUDED.response, "
            "status = EXCLUDED.status, retrieved_at = EXCLUDED.retrieved_at, "
            "expires_at = EXCLUDED.expires_at",
            cache_key, adapter, response, status, retrieved_at, expires_at,
        )

    # Usage

    async def record_usage(
        self,
        audit_id: str | None,
        provider: str,
        purpose: str,
        status: str,
        model: str | None = None,
        prompt_version: str | None = None,
        credits_used: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        await self.pool.execute(
            "INSERT INTO usage (audit_id, provider, model, purpose, prompt_version, status, "
            "credits_used, input_tokens, output_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            audit_id, provider, model, purpose, prompt_version, status,
            credits_used, input_tokens, output_tokens,
        )

    async def credits_used_this_month(self, provider: str) -> float:
        value = await self.pool.fetchval(
            "SELECT coalesce(sum(credits_used), 0) FROM usage "
            "WHERE provider = $1 AND created_at >= date_trunc('month', now())",
            provider,
        )
        return float(value)

    # Retention

    async def page_texts(self, urls: list[str]) -> dict[str, str]:
        rows = await self.pool.fetch(
            "SELECT url, body, raw_content FROM page_texts WHERE url = ANY($1::text[])", urls
        )
        return {
            r["url"]: zlib.decompress(r["body"]).decode("utf-8") if r["body"] else r["raw_content"]
            for r in rows
        }

    async def save_page_texts(self, texts: dict[str, str]) -> None:
        await self.pool.executemany(
            "INSERT INTO page_texts (url, body) VALUES ($1, $2) "
            "ON CONFLICT (url) DO UPDATE SET body = $2, updated_at = now()",
            [(url, compress_text(text)) for url, text in texts.items()],
        )

    async def compress_page_texts(self) -> int:
        """Compress text saved before bodies existed, a few rows at a time."""
        done = 0
        while rows := await self.pool.fetch(
            "SELECT url, raw_content FROM page_texts WHERE body IS NULL LIMIT 50"
        ):
            await self.pool.executemany(
                "UPDATE page_texts SET body = $2, raw_content = NULL WHERE url = $1",
                [(r["url"], compress_text(r["raw_content"])) for r in rows],
            )
            done += len(rows)
        return done

    async def knowledge_page_exists(self, url: str) -> bool:
        return await self.pool.fetchval("SELECT 1 FROM knowledge_pages WHERE url = $1", url) == 1

    async def save_knowledge_page(
        self, url: str, domain: str, title: str, published: str | None, chunks: list[str]
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO knowledge_pages (url, domain, title, published) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (url) DO NOTHING",
                url, domain, title, published,
            )
            await conn.executemany(
                "INSERT INTO knowledge_chunks (url, position, title, text) "
                "VALUES ($1, $2, $3, $4)",
                [(url, i, title, chunk) for i, chunk in enumerate(chunks)],
            )

    async def count_knowledge_pages(self) -> int:
        return await self.pool.fetchval("SELECT count(*) FROM knowledge_pages")

    async def search_knowledge(self, question: str, limit: int = 40) -> list[dict[str, Any]]:
        """Rank passages that share any term with the question, best matches first."""
        rows = await self.pool.fetch(
            "WITH q AS (SELECT replace(plainto_tsquery('english', $1)::text, '&', '|')::tsquery AS query) "
            "SELECT c.url, c.title, c.text, p.domain, p.published "
            "FROM knowledge_chunks c JOIN knowledge_pages p USING (url), q "
            "WHERE q.query <> ''::tsquery AND c.search_vector @@ q.query "
            "ORDER BY ts_rank_cd(c.search_vector, q.query, 1) DESC LIMIT $2",
            question, limit,
        )
        return [dict(r) for r in rows]

    async def knowledge_passages(self, question: str, urls: list[str]) -> list[dict[str, Any]]:
        """Rank saved passages from particular pages, for a follow-up question."""
        rows = await self.pool.fetch(
            "SELECT c.url, c.title, c.text, p.domain, p.published "
            "FROM knowledge_chunks c JOIN knowledge_pages p USING (url) WHERE c.url = ANY($2::text[]) "
            "ORDER BY ts_rank_cd(c.search_vector, replace(plainto_tsquery('english', $1)::text, "
            "'&', '|')::tsquery, 1) DESC LIMIT 16",
            question, urls,
        )
        return [dict(r) for r in rows]

    async def count_chat_searches_today(self) -> int:
        return await self.pool.fetchval(
            "SELECT count(*) FROM usage WHERE purpose = 'chat_search' "
            "AND created_at >= date_trunc('day', now())"
        )

    async def delete_expired(self) -> tuple[int, int]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        audits = await self.pool.fetch(
            "DELETE FROM audits WHERE created_at < $1 RETURNING id", cutoff
        )
        cache = await self.pool.fetch(
            "DELETE FROM tool_cache WHERE expires_at <= now() RETURNING cache_key"
        )
        return len(audits), len(cache)
