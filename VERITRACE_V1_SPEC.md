# Veritrace V1 Implementation Specification

**Status:** Frozen for V1. Change it only if a real test proves a failure. Sections 1–17 were brought in line with the code on Nov 1, 2026.
**Hackathon:** Theme 1, Civic Pulse, Digital Governance & Public Information. Problem statement P4.
**Deadline to confirm:** Sunday, Nov 1, 11:59 PM, in the timezone shown on the submission page.

## 1. Product

Veritrace is a text-only web app with a Chrome extension and a government information assistant. A user pastes or selects text, or gives a URL, and the app audits up to 3 factual claims, searches for evidence on both sides, and returns a plain answer, followed by a report that separates source statements, model assessments and uncertainties. It focuses on Indian public claims: official statistics, monetary policy, space missions and public health.

**Target users:** Fact-checkers, newsrooms, election and public-information teams, and government communications staff.

**Core promise:** Every source statement links to an exact passage saved from its source. A verified quote proves what a source said, not that the claim is true. The LLM interprets text, but it never supplies quotations or chooses the final verdict.

### 1.1 Scope

| Status | Features |
|---|---|
| **Must have** | Text and URL input, claim extraction, Tavily search, support and contradiction searches, evidence classification, citation validation, sufficiency check, one gap round, rule-based verdicts, plain answers, live trace, Evidence Ledger, saved audit state, saved reports, SSRF protection |
| **Built, optional** | Skeptic review (off by default, `ENABLE_SKEPTIC`), Fact Check Tools adapter (runs only with `FACT_CHECK_API_KEY`), Source History (on by default, `ENABLE_SOURCE_HISTORY`), Chrome extension (`extension/`, installed with Load unpacked), government information assistant (`/chat`, and the extension's Ask tab) |
| **Excluded** | Live feeds, images, video, audio, deepfake detection, knowledge graphs, vector search, embeddings, reranking, LangGraph, Redis, near-duplicate detection, multiple LLM providers, user accounts, server-side audit history, audit resume, PDF upload, translation |

### 1.2 Cut order

If time runs short, cut in this order:

1. Fact Check Tools adapter
2. Source History

Never cut contradiction search, citation validation, rule-based verdicts or the Evidence Ledger. Source History traces the earliest source found for a claim, and it is separate from evidence provenance. If it is cut, state in the deck that V1 does not trace a claim's earliest publication. The Evidence Ledger still gives exact quotes, links and retrieval times.

## 2. System Architecture

```text
Next.js on Vercel
Audit form | Live trace | Report
        │ REST + polling
        ▼
FastAPI on Hugging Face Spaces (one worker process)
API → asyncio queue → Orchestrator
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
  Adapters           LLM service       Rules and verdicts
  Tavily, fetcher    Groq              YAML + Python
        └─────────────────┬──────────────────┘
                          ▼
              PostgreSQL (state and evidence)
```

### 2.1 Technology

| Part | Choice |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind |
| Backend | Python 3.11+, FastAPI, Pydantic v2, HTTPX |
| Live updates | Browser polls the events endpoint every 2 seconds |
| Database | PostgreSQL on Neon or Supabase |
| LLM | Groq, `openai/gpt-oss-120b` in strict JSON schema mode, reached only through `services/llm.py` |
| Search | Tavily, basic depth |
| Extraction | trafilatura for HTML, PyMuPDF for PDF |
| Passage ranking | `rank_bm25`, in memory |
| Number handling | Python `Decimal` with `ROUND_HALF_UP` |

### 2.2 Separation of responsibilities

| Layer | Allowed to do | Not allowed to do |
|---|---|---|
| Python rules | Route sources, enforce limits, validate evidence, calculate verdicts | Interpret meaning from free text |
| LLM | Extract claims, classify, write queries, classify stance, review evidence, write the plain answer | Supply quotes, approve its own citations, choose verdicts, add evidence, decide whether more searches are allowed |
| Adapters | Retrieve external data and report status | Decide what the data means |
| Database | Store every stage | None |

## 3. Limits

| Item | Limit |
|---|---|
| Input text | 20,000 characters |
| Investigated claims per audit | 3 |
| Initial searches per claim | 4 |
| Gap rounds per claim | 1 |
| Gap searches per claim | 2 |
| Maximum Tavily attempts per audit | 18, counting retries |
| Results per search | 8 |
| Usable documents per claim | 5 |
| Fetch attempts per claim | 8 |
| Documents per domain, per claim | 2 |
| Passages per claim sent to the LLM | 8 |
| Passages per document | 2 |
| Redirects per fetch | 3 |
| Download size | 5 MB, enforced while streaming |
| Extracted text per document | 200,000 characters |
| PDF pages read | 50 |
| Concurrent audits | 3 |
| Tavily credits reserved for demo and judging | 300 |

Tavily's free tier was documented as 1,000 credits a month. Confirm this and the cost of each request at Hour 0.

The attempt budget counts every request sent to Tavily, including retries and timeouts, because a failed request may still use credits. Mandatory searches run before gap searches, so retries can only reduce the gap round. If the budget runs out, skip the remaining gap searches and record a coverage gap.

## 4. Repository Layout

Create a new public repo for this project. Do not publish from the existing `c:\Recollia` repository.

```text
veritrace/
├── README.md
├── .env.example
├── docker-compose.yml
├── docs/
│   └── architecture.png
├── extension/
│   ├── manifest.json
│   ├── background.js
│   ├── config.js
│   ├── sync.js
│   ├── panel.html
│   ├── panel.css
│   ├── panel.js
│   └── README.md
├── frontend/
│   ├── app/
│   │   ├── page.tsx
│   │   └── audit/[id]/page.tsx
│   ├── lib/
│   │   ├── api.ts
│   │   └── verdicts.ts
│   ├── .env.local
│   └── components/
│       ├── AuditForm.tsx
│       ├── LiveTrace.tsx
│       ├── ClaimReport.tsx
│       ├── EvidenceLedger.tsx
│       ├── EvidenceItem.tsx
│       ├── SourceHistory.tsx
│       └── VerdictBadge.tsx
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py
    ├── prompts.py
    ├── migrations/
    │   ├── 001_init.sql
    │   ├── 002_extraction_checkpoint.sql
    │   ├── 003_retrieved_via.sql
    │   ├── 004_simplify_evidence.sql
    │   ├── 005_report_reuse.sql
    │   ├── 006_input_context.sql
    │   ├── 007_permanent_cache.sql
    │   ├── 008_compress_page_text.sql
    │   ├── 009_knowledge.sql
    │   └── 010_limit_reset.sql
    ├── api/
    │   └── routes.py
    ├── schemas/
    │   ├── audit.py
    │   ├── claim.py
    │   ├── search.py
    │   ├── document.py
    │   ├── evidence.py
    │   ├── verdict.py
    │   └── llm_outputs.py
    ├── pipeline/
    │   ├── orchestrator.py
    │   └── verdict_engine.py
    ├── components/
    │   ├── text_extractor.py
    │   ├── claim_extractor.py
    │   ├── claim_classifier.py
    │   ├── evidence_requirements.py
    │   ├── source_router.py
    │   ├── query_planner.py
    │   ├── search_runner.py
    │   ├── candidate_selector.py
    │   ├── document_fetcher.py
    │   ├── passage_ranker.py
    │   ├── evidence_analyzer.py
    │   ├── evidence_validator.py
    │   ├── number_compare.py
    │   ├── sufficiency_checker.py
    │   ├── skeptic_reviewer.py
    │   ├── source_history.py
    │   └── report_writer.py
    ├── adapters/
    │   ├── base.py
    │   ├── tavily.py
    │   └── fact_check.py
    ├── services/
    │   ├── db.py
    │   ├── llm.py
    │   ├── trace.py
    │   ├── cache.py
    │   ├── limits.py
    │   └── url_safety.py
    ├── config/
    │   ├── settings.py
    │   ├── loader.py
    │   ├── evidence_rules.yaml
    │   └── sources.yaml
    └── tests/
```

## 5. Pipeline Overview

```text
1.  Extract text, and reuse a saved report if one matches
2.  Extract and label claims, or reuse a saved extraction
3.  Classify up to 3 factual claims
4.  Load evidence rules and sources
5.  Run 2 support and 2 contradiction searches
6.  Normalize, filter, rank and select documents
7.  Fetch documents and save snapshots
8.  Filter and deduplicate documents
9.  Rank passages with BM25
10. Classify stance using sentence IDs
11. Build quotes in Python and validate evidence
12. Check each key detail
13. Run one gap round, if needed
14. Run skeptic review, if enabled
15. Calculate verdict and confidence
16. Build Source History
17. Build Evidence Ledger, plain answer and report
```

Steps 4–15 run for each factual claim, one claim at a time. The support and contradiction searches are mandatory. If one fails, record a coverage gap, and the claim cannot receive High confidence.

## 6. Data Contracts

### 6.1 Audit input

```python
class AuditInput(BaseModel):
    type: Literal["text", "url"]
    content: str = Field(min_length=3, max_length=20000)
    fresh: bool = False  # Search again, even if this text was checked recently
    context: PageContext | None = None

class PageContext(BaseModel):
    page_title: str | None = Field(default=None, max_length=300)
    page_url: str | None = Field(default=None, max_length=2000)
```

A `url` input must be an `http` or `https` URL. `fresh` is saved as `audits.force_fresh`, and `context` as `audits.input_context`. The extension sends `context` so the report can link to the page. No LLM sees it, and `page_url` is never fetched.

### 6.2 Atomic claim

```python
class KeyDetail(BaseModel):
    id: str                 # Example: c1_d2
    text: str

class AtomicClaim(BaseModel):
    id: str
    position: int
    text: str
    source_text: str
    kind: Literal["factual", "not_checkable"]
    key_details: list[KeyDetail] = Field(default_factory=list)
    numbers: list[NumericFact] = Field(default_factory=list)
    time: str | None = None
    location: str | None = None
```

| Field | Meaning |
|---|---|
| `position` | Order in the input, which also sets key detail IDs such as `c1_d2` |
| `text` | Complete standalone statement that names its subject |
| `source_text` | Exact words from the input |
| `kind` | Only `factual` claims are investigated |
| `key_details` | Names, numbers, dates, groups and limits that evidence must address, each with a stable ID |
| `numbers` | Parsed by Python from the numeric key details |
| `time` | ISO date or range when known, otherwise the stated period |
| `location` | Stated place, otherwise null |

### 6.3 Classification

```python
class ClaimClassification(BaseModel):
    claim_id: str
    primary_domain: Domain
    secondary_domains: list[Domain] = Field(default_factory=list, max_length=2)
    claim_type: ClaimType
    fallback: bool = False  # The classifier failed, so don't reuse this result
```

- **Domains:** `government_policy`, `elections`, `health`, `science`, `finance`, `disaster`, `weather`, `general_news`
- **Claim types:** `official_record`, `scientific`, `reported_event`

Invalid domains fall back to `general_news`. Invalid or missing types become `official_record`, which uses the same evidence standard as every other type. A failed or invalid classification sets `fallback=True`, and that extraction is not reused by later audits.

### 6.4 Search

```python
@dataclass
class SearchRequest:
    claim_id: str
    purpose: Literal["support", "contradiction", "gap"]
    scope: Literal["official", "open"]
    query: str
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    max_results: int = 8
    adapter: str = "tavily"
    mandatory: bool = False        # True for the 4 initial searches

class RawResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    published_date: str | None = None
    score: float | None = None
    raw_content: str | None = None  # Full page text held by Tavily

class SearchHit(BaseModel):
    search_id: str
    url: str
    canonical_url: str
    domain: str
    title: str
    snippet: str
    rank: int
    provider_score: float | None = None
    published_at: datetime | None = None
    published_raw: str | None = None
    raw_content: str | None = None
```

Source tier is not stored on `SearchHit`, because it depends on the claim.

### 6.5 Document and passage

```python
@dataclass
class Sentence:
    id: str          # Example: doc_01_s42
    text: str

@dataclass
class Document:
    id: str
    ref: str         # Example: doc_01
    audit_id: str
    canonical_url: str
    domain: str
    title: str | None
    content_type: Literal["html", "pdf"]
    content_hash: str
    text_snapshot: str
    sentences: list[Sentence]
    language: str | None
    published_at: datetime | None
    published_raw: str | None
    retrieved_at: datetime
    truncated: bool = False
    retrieved_via: str = "direct"    # "tavily" when direct fetching failed

@dataclass
class Passage:                     # In memory only
    id: str
    document_id: str
    sentence_ids: list[str]
    text: str
    bm25_score: float
    origins: frozenset[str] = frozenset()
```

- **Document:** One fetched URL and its extracted text, stored per audit.
- **Passage:** Consecutive sentences selected during ranking.
- **Evidence:** One passage interpreted for one claim, with one stance.

Never give a whole document a stance, and never bundle supporting and contradicting sentences into one evidence item.

### 6.6 Evidence

```python
class Evidence(BaseModel):
    id: str
    claim_id: str
    document_id: str
    sentence_ids: list[str]
    text: str
    stance: Literal["supports", "contradicts", "irrelevant"]
    addresses_detail_ids: list[str] = Field(default_factory=list)
    numbers: list[NumericFact] = Field(default_factory=list)
    reason: str
    number_match: Literal["exact", "rounded"] | None = None
    validation_status: Literal["pending", "valid", "contextual", "invalid"] = "pending"
    validation_reason: str | None = None
    skeptic_status: Literal["unchanged", "downgraded", "flagged"] = "unchanged"
    skeptic_note: str | None = None
    used_in_verdict: bool = False
```

Python builds `text` by joining saved sentences. The rule engine sets `used_in_verdict`. Search origins are derived from `search_results`, and never affect stance.

### 6.7 Numbers

```python
class NumericFact(BaseModel):
    value: Decimal
    unit: str | None = None
    precision: int
    comparator: Literal["exact", "approx", "over", "under"]
    original_text: str
```

Python creates `NumericFact` objects for both claims and evidence. Define this model before the models that use it.

| Comparator | Set when the text says | Required match |
|---|---|---|
| `exact` | "exactly", "precisely" | Same value and unit |
| `approx` | "about", "nearly", or a plain number | Source rounds to the claim's stated precision |
| `over` | "over", "more than", "above" | Source value is greater |
| `under` | "under", "less than", "below" | Source value is smaller |

### 6.8 Verdict

```python
class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Literal["supported", "contradicted", "misleading",
                     "unverified_insufficient",
                     "unverified_conflicting", "error"]
    confidence: Literal["high", "medium", "low"] | None
    rule_id: str
    reason_code: str | None = None  # Example: required_primary_unavailable
    policy_version: str
    explanation: str
```

### 6.9 LLM output models

The LLM never fills in IDs or fields owned by Python, so each call has its own small output model in `schemas/llm_outputs.py`:

```python
class ExtractedClaim(BaseModel):
    text: str
    source_text: str
    kind: Literal["factual", "not_checkable"]
    key_details: list[str] = Field(default_factory=list)
    time: str | None = None
    location: str | None = None

class ExtractionOutput(BaseModel):
    claims: list[ExtractedClaim]

class ClassificationItem(BaseModel):
    claim_index: int
    primary_domain: str
    secondary_domains: list[str] = Field(default_factory=list)
    claim_type: str

class ClassificationOutput(BaseModel):
    items: list[ClassificationItem]

class InitialQueryOutput(BaseModel):
    support_query: str
    contradiction_query: str

class QueryOutput(BaseModel):         # Gap searches
    queries: list[str] = Field(max_length=2)

class EvidenceItem(BaseModel):
    sentence_ids: list[str]
    stance: Literal["supports", "contradicts", "irrelevant"]
    addresses_detail_ids: list[str] = Field(default_factory=list)
    reason: str
    numeric_values: list[str] = Field(default_factory=list)

class EvidenceOutput(BaseModel):
    items: list[EvidenceItem]

class SkepticIssue(BaseModel):
    evidence_id: str
    type: Literal["wrong_entity", "wrong_date", "wrong_location", "wrong_population",
                  "scope_mismatch", "broader_statement", "missing_qualification",
                  "outdated", "correlation_not_causation", "other"]
    explanation: str
    action: Literal["downgrade", "flag"]

class SkepticOutput(BaseModel):
    issues: list[SkepticIssue] = Field(default_factory=list)

class SummarySentence(BaseModel):
    text: str
    labels: list[str] = Field(default_factory=list)

class SummaryOutput(BaseModel):
    sentences: list[SummarySentence] = Field(default_factory=list)
```

Python then builds `AtomicClaim` and `Evidence` from these outputs, adding IDs, parsed numbers and quotes.

### 6.10 When to use Pydantic

Use Pydantic where data crosses a trust boundary or forms a shared contract. Use plain Python inside the pipeline.

| Use | For |
|---|---|
| **Pydantic** | API requests and responses, Groq outputs, Tavily responses, YAML config, `AtomicClaim`, `Evidence`, `NumericFact`, `ClaimVerdict` |
| **Dataclasses** | `Sentence`, `Document`, `Passage`, `SearchRequest`, candidates, `Sufficiency` |
| **Dicts and tuples** | Temporary values and simple function returns |

Pydantic checks shape only. It cannot show that a sentence exists, was supplied to the LLM, or supports the claim. `evidence_validator.py` does that, and both are required.

## 7. Pipeline Implementation

### Step 1: Extract text

- **Text input:** Trim whitespace, and store the original.
- **URL input:** Run the safe fetch in Step 7, then extract the text.
- **Failure:** Mark the audit `failed`, and ask the user to paste the text.
- **Saved report:** Once the text is known, look for a saved report (see Section 8.1). A match finishes the audit here, with no search or LLM calls.

### Step 2: Extract claims

Identical input on the same day, with the same model, system rules and prompt versions, reuses the saved claims and classifications. Later stages still run.

Prompt rules:

1. Cover every meaningful part of the input.
2. Split sentences with separate facts.
3. Every factual claim must name its subject, because each claim is checked alone. Replace "it", "they" or "the mission" with the name used in the input. If the input never makes the subject clear, use `not_checkable`. If an unnamed speaker states the position of a named country, body or group, make that party the subject.
4. Keep words such as "all", "only", "may" and "not".
5. Never add outside facts.
6. Put the subject and the exact measure, event, institution, group, place, period, number and comparison baseline in `key_details`. Never replace a specific term with a broader one.
7. Use `not_checkable` for opinions, predictions, instructions, and text that cannot be understood. A yes/no question about a specific fact is a claim to check.
8. Resolve relative dates against the supplied reference date. Write the resolved date in the claim text.
9. For "X said Y", create a claim about the statement, and a second about Y if Y is the main point.
10. Extract the literal factual claim from satire or jokes.

| Input | Handling |
|---|---|
| Single fact | One `factual` claim |
| Two facts in a sentence | Two `factual` claims |
| "This policy is terrible" | `not_checkable`, not investigated |
| "Prices will double next year" | `not_checkable`, not investigated |
| "Did the government ban this drug?" | `factual`: The government banned this drug |
| "A post falsely claimed X" | `factual`: X, the disputed fact itself |
| "Share this before it's deleted!" | `not_checkable`, not investigated |
| "They did something shocking" | `not_checkable`, listed in the report |
| Hedged fact, such as "may cause" | `factual`, keeping the hedge |

Code validation:

- `source_text` must appear in the input.
- `factual` claims must have non-empty `text`.
- `factual` claims must not start with a vague subject, such as "It was..." or "The mission was..." (`VAGUE_SUBJECT` in `claim_extractor.py`).
- Python adds any figure in the claim text that is missing from `key_details`.
- Python assigns stable key detail IDs, and parses numbers into `numbers`.
- On failure, retry once with the problem stated. If the retry fails, keep the first attempt. Any item still invalid becomes `not_checkable`.
- Keep the 3 most checkable factual claims, preferring claims with numbers, dates and named entities.
- List every remaining factual claim as not investigated.

### Step 3: Classify claims

The LLM selects one primary domain, up to 2 secondary domains, and one claim type. Source routing uses all selected domains.

A claim is `scientific` when it asserts a scientific, medical, technical or causal finding, even if an official body discussed it. `official_record` covers what an institution formally recorded, published, decided or implemented. `reported_event` covers any other occurrence or statement known mainly through reporting.

### Step 4: Load evidence rules

`config/evidence_rules.yaml` applies one standard to every claim type: `minimum_independent_sources: 2`, with no primary source required.

A claim is settled by either:

- **One official source**, which can reach High confidence.
- **Two distinct publishers**, capped at Medium confidence.

One unofficial publisher never settles a claim.

**Official sources are found by pattern, not by a list.** `OFFICIAL_HOST` in `source_router.py` matches `.gov.in`, `.nic.in` and `.int`, on any topic. Foreign government sites are not official here, because a US agency says nothing about an Indian claim. There is no longer a table of which source counts for which claim type.

`config/sources.yaml` has three small lists:

- `authority`: Steers official searches by claim domain. Its hosts also count as official, which covers bodies without an official address, such as `rbi.org.in`.
- `not_primary`: News outlets on government domains, such as `ddnews.gov.in`.
- `blocked`: Social and other sites excluded from search.

**Host matching:** A host matches an entry only if it equals the entry, or ends with `.` plus the entry. So `example-gov.in` must not match `gov.in`.

**PubMed is a V1 shortcut:** PubMed also indexes reviews and meta-analyses, so count a page as primary only if the page is not labelled as one of those. Treat them as context. An observational study does not establish causation on its own, so the skeptic checks for this on `causal` claims. Do not present this as a reliable authority classification.

**Startup check:** Parse both YAML files with Pydantic models when the backend starts, and stop if either is invalid.

### Step 5: Generate and run searches

| Search | Scope | Generated by |
|---|---|---|
| Support 1 | Official | Template: `{claim text}` |
| Support 2 | Open | LLM `support_query` |
| Contradiction 1 | Open | LLM `contradiction_query`, seeking exceptions or corrections |
| Contradiction 2 | Official | Template: `{claim text} clarification correction` |

One LLM call writes both queries. Each query is cut to 20 words.

Rules:

- Keep exact names, numbers and dates in every query.
- Generate contradiction queries using only the claim, without search results.
- If the LLM fails, use templates: `{claim text}` for open support, and `{claim text} exception limit denied` for open contradiction.
- If a claim has no official domains, change that search to open, and log it.
- For an event dated within 72 hours, run the open support search first.

Contradiction query focus:

| Claim type | Look for |
|---|---|
| `official_record` | Different figure, period or definition, official revision, eligibility limits, withdrawal or clarification |
| `scientific` | Failed replication, review articles, agency guidance, alternative causes |
| `reported_event` | Denial, different place or date, correction or misattribution |

Words such as "all", "never", "only" and "first" also trigger an exception-focused query.

Tavily request, with field names to confirm at Hour 0:

```python
payload = {
    "query": req.query,
    "search_depth": "basic",
    "max_results": req.max_results,
    "include_raw_content": True,
    "include_usage": True,
}
if req.include_domains:
    payload["include_domains"] = req.include_domains
if req.exclude_domains:
    payload["exclude_domains"] = req.exclude_domains
```

Official searches must send `include_domains`, and open searches must not. Open searches send the blocked list as `exclude_domains`. Tavily's `raw_content` is kept as a fallback for Step 7, and `usage.credits` gives the credits actually used.

Search cache key:

```python
key = sha256(json.dumps({
    "provider": req.adapter,
    "query": req.query,
    "depth": "basic",
    "raw_content": True,
    "include": sorted(req.include_domains),
    "exclude": sorted(req.exclude_domains),
    "max_results": req.max_results,
}, sort_keys=True).encode()).hexdigest()
```

| Cached result | TTL |
|---|---|
| Claim about the last 7 days | 1 hour |
| Claim using words such as "current", "latest" or "today" | 24 hours |
| Any other claim | Permanent |
| Empty result | 10 minutes |
| Error or rate limit | Never cached |

Page text is stored once in `page_texts`, keyed by URL and compressed with zlib, and social page text is not stored. `fresh` skips saved reports, saved extractions and cached searches. The cache stores search results only. Documents are fetched again in every audit that runs, so the retrieval time on evidence is the time of that audit. This includes Tavily text taken from a cached result, which may be older than the time shown.

### Step 6: Normalize, filter, rank and select

**Canonicalize URLs** with `urllib.parse`:

- Lowercase the scheme and host.
- Remove fragments and trailing slashes.
- Remove `utm_*` and `fbclid`.
- Keep all other query parameters.

**Pre-fetch filters:**

1. Invalid or non-HTTP(S) URL
2. Unsafe destination
3. Blocked domain
4. Duplicate canonical URL, merged with all search links kept
5. Unsupported file type

Social platforms and other domains in `blocked` are excluded from search and rejected here. Dates are never a filter.

**Candidate score:**

```python
def candidate_score(hit, claim, classification):
    text = f"{hit.title} {hit.snippet}"
    score = detail_overlap(text, [d.text for d in claim.key_details]) * 3
    tier = source_tier(hit.domain, classification)
    score += {"primary": 1.0, "secondary": 0.5, "unknown": 0.0}[tier]
    score += 1 / max(hit.rank, 1)
    return score
```

`detail_overlap` returns the share of `key_details` found in the text, from 0 to 1. Do not use `provider_score`.

**Select up to 5 documents:**

| Slot | Selection |
|---|---|
| 1 | Highest-scoring primary result |
| 2 | Highest-scoring contradiction-origin result not already selected |
| 3 | Highest-scoring support-origin result not already selected |
| 4–5 | Highest-scoring remaining results |

If a slot has no candidate, fill it with the highest remaining result. Apply the 2-per-domain limit throughout. Keep unselected candidates as replacements. A fetch failure uses a replacement, and never triggers a new search.

### Step 7: Fetch documents

Use `services/url_safety.py` for every request:

1. Allow only `http` and `https`.
2. Resolve the hostname.
3. Reject the URL if any resolved IP is private, loopback, link-local, multicast or reserved.
4. Disable automatic redirects.
5. Repeat Steps 1–4 for each redirect, with a maximum of 3.
6. Use a 5-second connect timeout and a 15-second read timeout.
7. Stop reading the stream above 5 MB.
8. Accept HTML and PDF only.
9. Confirm PDFs with the `%PDF-` file signature.

V1 does not pin the connection to the validated IP, so a small DNS rebinding risk remains. List it under known limitations in the README.

Extract HTML with trafilatura, and PDFs with PyMuPDF. Read at most 50 PDF pages, and stop at 200,000 extracted characters, recording any truncation. Mark image-only PDFs `unreadable`, because OCR is out of scope.

Extract HTML with `favor_recall` if the first attempt is empty. In PDFs, merge single newlines, which are line wraps, and keep blank lines as paragraph breaks. Never merge HTML text.

If a fetch returns 403, skip other pages on that domain for the audit. If direct fetching fails and Tavily holds text for the page, use that text and set `retrieved_via="tavily"`. The fetcher identifies itself as `VeritraceBot`, and never bypasses access restrictions.

Split text into sentences, then assign stable IDs in document order, such as `doc_01_s42`. Save the URL, text snapshot, sentences, SHA-256 hash, `published_at`, `published_raw` and `retrieved_at` on the document.

### Step 8: Filter and deduplicate

| Result | Action |
|---|---|
| Fetch failed | Record it, then use a replacement |
| Empty extraction, error page or login wall | Reject |
| Under about 100 words | Reject, unless it comes from a primary domain |
| Unsupported language | Keep the record, but do not use it as evidence |
| Clearly unrelated | Reject |
| Related, missing some details | Keep as a contextual candidate |
| Directly addresses the claim | Keep as a direct candidate |

**Clearly unrelated** means that the main subject and all `key_details` are absent.

**Deduplication:**

- Same canonical URL: one document for the audit.
- Same content hash: keep both URLs as documents, but count them as one source.
- Different text: separate documents, even when similar.

Never discard a document merely because it looks similar. Do not select a source representative, because that can hide a revision.

Log every kept, rejected or merged item with a reason in `trace_events`, using the stage `filter`.

### Step 9: Rank passages

1. Build passages of 4 consecutive sentences, overlapping by 1 sentence.
2. Normalize financial years, so `FY 2024-25` matches `FY2024-25`, and percentages, so `5.5 per cent` matches `5.5%`.
3. Build the query from the unique non-stopword terms in the claim's `text` and `key_details`.
4. Score passages with BM25, then multiply by `1 + coverage`, where coverage is the share of non-numeric key details found in the passage. Numbers are excluded, so a passage with a different figure can still compete.
5. Keep up to 2 passages per document.
6. Reserve 1 slot for each search origin with usable passages.
7. Fill the remaining slots by score, up to 8 passages per claim.

The passages sent to Groq are recorded in the trace with their scores.

Search origin is used only to guarantee coverage. It is never a stance signal.

### Step 10: Classify evidence

Send the claim and numbered sentences to the LLM in one batched call per claim. Wrap source content like this:

```text
<untrusted_source id="doc_01">
[doc_01_s41] Sentence text
[doc_01_s42] Sentence text
</untrusted_source>
```

The system prompt states that this content is data, and any instructions inside it must be ignored. The claim's key details are sent with their IDs, and the LLM may return only those IDs.

Expected output:

```json
{
  "items": [
    {
      "sentence_ids": ["doc_01_s41", "doc_01_s42"],
      "stance": "contradicts",
      "addresses_detail_ids": ["c1_d2", "c1_d3"],
      "reason": "The source gives a different annual figure for the same year.",
      "numeric_values": ["7.1%"]
    }
  ]
}
```

Stance meanings:

| Stance | Meaning |
|---|---|
| `supports` | Directly establishes the claim's fact, even in different words |
| `contradicts` | Directly establishes an incompatible fact about the same measure, subject, period and group |
| `irrelevant` | Does not directly help prove or disprove the claim |

A broader statement does not contradict a narrower one. Label that case `irrelevant`. Groq judges only the measure and subject. Python judges period and scope. The same figure for a different period is `supports`, so Python can mark it `contextual`. A different figure for a different period or group is `irrelevant`.

Each evidence item has exactly one stance. If two sentences point in different directions, return separate items.

### Step 11: Build quotes and validate

Python performs these steps. An item citing several documents is first split into one item per document, so a quote never spans two sources.

1. Confirm each sentence ID exists in the saved document.
2. Confirm each sentence ID belongs to a passage supplied in that LLM call.
3. Confirm each detail ID belongs to the claim, and drop any that does not.
4. Build `text` from saved sentences. Discard any quote written by the LLM.
5. Parse `numeric_values`, and confirm each one appears in the built `text`.
6. Check entity, time, location and scope against the claim.
7. Compare numbers using `number_compare.py`.
8. Assign the validation status.

| Situation | Status |
|---|---|
| Sentence ID missing or not supplied, or stance is `irrelevant` | `invalid` |
| Same entity, time, scope and numbers | `valid` |
| Related, but time or scope differs | `contextual` |

`validation_reason` records why evidence is `contextual`: `pubmed_review`, `entity_differs`, `location_differs`, `time_differs`, `scope_differs`, `different_unit` or `number_mismatch`. Only `time_differs` and `scope_differs` can lead to Misleading, plus `skeptic_missing_qualification` when the skeptic is on.

Failing a time or scope check does not delete evidence, because that evidence may prove a claim is Misleading.

Another year in a quote is a time mismatch only if the document never mentions the claim's year, as it may be a comparison. A quarterly source is a mismatch for a claim that isn't quarterly. Only parsed numbers count as numeric, so `Chandrayaan-3` remains an entity.

Publication after the event is not a failure. A later source can replace an earlier one only when it is demonstrably a newer version of the same record or policy.

**Number comparison:**

```python
from decimal import Decimal, ROUND_HALF_UP

def rounds_to_claim(source: Decimal, claim: Decimal, precision: int) -> bool:
    step = Decimal(1).scaleb(-precision)
    return source.quantize(step, rounding=ROUND_HALF_UP) == claim

def compare(claim: NumericFact, source: NumericFact) -> str:
    if claim.unit != source.unit:
        return "different_unit"
    if claim.comparator == "over":
        return "match" if source.value > claim.value else "mismatch"
    if claim.comparator == "under":
        return "match" if source.value < claim.value else "mismatch"
    if source.value == claim.value:
        return "exact"
    if claim.comparator == "approx" and rounds_to_claim(
        source.value, claim.value, claim.precision
    ):
        return "rounded"
    return "mismatch"
```

`different_unit` is not a match. Treat that evidence as `contextual`, unless a defined conversion applies.

Normalization rules:

- Parse `2 lakh` as 200000, and `5 crore` as 50000000.
- Treat percentages and percentage points as different units.
- `8%` has precision 0, `8.0%` has precision 1, and `8.00%` has precision 2.
- `7.6%` is a rounded match for `8%`.
- A rounded-only match can be Supported, but cannot get High confidence.
- Keep each original number string for display.

`8%` matching `7.6%` applies only to an `approx` claim stated at whole-number precision. "Exactly 8%" does not match `7.6%`.

### Step 12: Check sufficiency

Each key detail is one verification question. A key detail is answered when valid evidence lists its ID in `addresses_detail_ids`.

```python
@dataclass
class Sufficiency:
    action: Literal["stop", "investigate"]
    reason: str
    gaps: list[str] = field(default_factory=list)

def assess_sufficiency(claim, rules, evidence, documents, tiers,
                       contradiction_ran, gap_round_used):
    valid = [e for e in evidence if e.validation_status == "valid"]
    gaps = [d.id for d in claim.key_details
            if not any(d.id in e.addresses_detail_ids for e in valid)]
    if rules.minimum_independent_sources and not has_primary(valid):
        if independent_count(valid) < rules.minimum_independent_sources:
            gaps.append("independent_sources")
    if not contradiction_search_ran(claim):
        gaps.append("contradiction_search")
    if not gaps:
        return Sufficiency(action="stop", reason="requirements_met")
    if gap_round_used:
        return Sufficiency(action="stop", gaps=gaps, reason="search_limit")
    return Sufficiency(action="investigate", gaps=gaps, reason="missing_evidence")
```

**Independent sources** are an approximation: distinct publishers, after detectable duplicates are removed by URL and content hash. The UI and report say "distinct publishers", never "independently confirmed". This is not proof of independent reporting, because V1 cannot detect edited wire copies. They can still meet the two-source rule, so when a claim has no primary evidence, confidence is capped at Medium and the report states this limit.

### Step 13: Gap round

If the check returns `investigate`:

1. Python turns each gap into a plain description of what is missing.
2. The LLM writes up to 2 targeted queries, and sees the earlier searches.
3. Python drops exact repeats, and runs the rest. They are official searches if a primary source is missing and official domains exist, otherwise open.
4. New evidence passes through Steps 6–11. If that analysis fails, evidence already gathered is kept, and a coverage gap is recorded.
5. Sufficiency runs again, and the claim continues.

Python controls whether the round is allowed. The LLM cannot request another round.

### Step 14: Skeptic review

This step is optional and off by default. Set `ENABLE_SKEPTIC=true` to run it. A finding of type `other` can only flag evidence, never downgrade it.

The skeptic receives only the claim and its validated ledger. It checks for:

- Wrong entity, date, location or population
- A source that covers only part of the claim
- Broader statements treated as contradictions
- Missing qualifications
- Outdated evidence
- Correlation treated as causation

Output:

```json
{
  "issues": [
    {
      "evidence_id": "e7",
      "type": "scope_mismatch",
      "explanation": "The source covers low-income students, not all undergraduates.",
      "action": "downgrade"
    }
  ]
}
```

Rules:

- Every issue must reference an existing evidence ID.
- The skeptic cannot add evidence or upgrade evidence.
- `downgrade` changes `valid` to `contextual`.
- `flag` keeps the status, but lowers confidence.
- Python ignores unknown IDs.

If the skeptic fails after one retry, continue, record the gap, and cap confidence at Medium.

### Step 15: Calculate verdict and confidence

Strong evidence means `valid` evidence from one official source, or from 2 distinct publishers, for any claim type.

Apply these rules in order, and stop at the first match:

| Order | Rule ID | Condition | Verdict |
|---|---|---|---|
| 1 | `R0_ERROR` | Evidence analysis failed | Error |
| 2 | `R1_CONFLICT` | Strong evidence on both sides, unresolved by dates, definitions or revisions | Unverified: conflicting |
| 3 | `R2_CONTRADICTED` | Strong evidence disproves the claim | Contradicted |
| 4 | `R3_MISLEADING` | An official source supports the fact but is `contextual` for `time_differs`, `scope_differs` or `skeptic_missing_qualification`, and no valid official source supports it | Misleading |
| 5 | `R4_SUPPORTED` | Strong support, all key details answered, no valid contradicting evidence | Supported |
| 6 | `R5_INSUFFICIENT` | Anything else | Unverified: insufficient |

**Source precedence:**

1. Evidence must first match the claim's entity, time, location and scope.
2. Official sources are preferred for what an institution announced or published.
3. A newer document replaces an older one only if it is a newer version of the same record or policy.
4. Copied reporting counts as one source when detected.
5. Independent credible sources that disagree produce Unverified: conflicting.
6. Missing evidence is not proof of falsity.

Misleading is decided by Python only. Direct valid official support outranks it.

**Confidence:**

| Level | Rule |
|---|---|
| **High** | All requirements met, official evidence used, exact number match if relevant, contradiction search ran, no failed skeptic review, no flags |
| **Medium** | Verdict rule met, with a minor gap, no official source, a rounded-only number match, or one flag |
| **Low** | Unverified: insufficient, 2 or more unanswered gaps, or 2 or more flags |

`Error` has no confidence level. Confidence describes the strength of the evidence assessment. It is not a calibrated probability that the claim is true, and never show it as a percentage.

**Overall report result:**

Apply in order:

| Claim results | Report result |
|---|---|
| No factual claims | No factual claims |
| Any claim is Error | Assessment incomplete |
| All Supported | Supported |
| All Contradicted | Contradicted |
| Any mix of Supported with other verdicts | Partially supported |
| Otherwise | The most serious claim result |

Use this seriousness order: Contradicted, Misleading, Unverified: conflicting, Unverified: insufficient.

Error is a system failure, not a finding about truth, so it is reported separately. Show completed claim results alongside it.

### Step 16: Build Source History

This step is controlled by `ENABLE_SOURCE_HISTORY`. When false, it returns an empty result and the page section is hidden.

1. Collect documents used for a claim.
2. Sort them by `published_at`, with undated documents listed separately.
3. Show the title, publisher, date and link.
4. Label the first item **Earliest source found in this audit**.
5. Show this note: "This may not be the original source."

Never call any item the original source without direct evidence.

### Step 17: Build Evidence Ledger and report

Python builds the ledger:

| Kind | Content |
|---|---|
| `source_statement` | An exact saved quote from a `valid` source, with URL and retrieval time. It proves what the source said, whichever side it falls on |
| `model_assessment` | A stance reason or skeptic finding, linked to evidence IDs |
| `uncertain` | A gap, skipped claim, source failure, conflict or unreadable document |

Each claim's `explanation` comes from a Python template tied to its `rule_id`, not the LLM.

The report opens with a plain answer, written only from evidence already used in the verdict, with no extra searches:

- A Contradicted claim states the correct fact from the sources.
- If sources give different figures, each is reported and neither is chosen.
- The LLM sees quotes under internal labels such as `S1`, and lists the labels each sentence relies on. Readers see source links, not labels.
- Python removes bracketed labels, then rejects a sentence that cites unknown labels, covers more than one claim, uses internal terms, or has a number not found in that claim's quotes or the claim itself.
- A claim left with no sentence falls back to a direct quote from a source. Python writes this fallback.
- Unverified: insufficient and Error claims make no LLM call for this step, and use a fixed sentence.

A grounded number does not prove that the sentence is right, so label the answer **AI-written from the sources listed. Check them before relying on this.** `summary` holds the same sentences joined together.

All sentences appear together as one top answer, so each must name what it is about, and one sentence can cover several claims. Each also carries `claim_id` for the claim-by-claim view. Fixed fallback sentences carry `fallback` and are left out of a multi-claim summary.

The report shows every claim kind, all contradicting evidence, coverage gaps, and which evidence influenced the verdict.

## 8. Backend Services

### 8.1 Orchestrator

```python
STAGES = [
    extract_text,
    extract_claims,
    classify_claims,
    investigate_claims,
    build_source_history,
    write_report,
]

async def run_audit(audit_id):
    row = await db.get_audit(audit_id)
    state = AuditState(audit_id, row.input_type, row.input_content, row.force_fresh)
    await db.set_status(audit_id, "running")
    try:
        for stage in STAGES:
            await db.set_stage(audit_id, stage.__name__)
            await trace.emit(audit_id, stage.__name__, "started")
            state = await stage(state)
            await trace.emit(audit_id, stage.__name__, "completed")
            if stage is extract_text and await reuse_saved_report(state):
                return
    except PublicError as exc:
        await db.fail_audit(audit_id, exc.public_message)
        await trace.emit(audit_id, "audit", "failed", exc.public_message)
    except Exception:
        await db.fail_audit(audit_id, GENERIC_FAILURE_MESSAGE)
        await trace.emit(audit_id, "audit", "failed", GENERIC_FAILURE_MESSAGE)

async def investigate_claims(state):
    for claim in state.investigated_claims:
        try:
            await investigate_claim(claim, state)
        except Exception:
            await db.save_error_verdict(claim.id)
    state.verdicts = await db.load_verdicts(state.audit_id)
    return state
```

A failure on one claim records an `Error` verdict for that claim, and the audit continues. `write_report` uses the verdicts reloaded from the database, so saved Error verdicts always appear in the report. `write_report` marks the audit `completed`.

**Saved reports:** `report_key` hashes the whitespace-normalized text, model, system rules, all prompt versions, policy, rule and source versions, and the skeptic setting. It adds the date if the text uses relative time words, such as "last", "current" or "today". A match within `REPORT_REUSE_DAYS`, 7 by default, is copied to the new audit with `reused=true`, the original `checked_at`, and `reused_from` set. It is skipped when `fresh` is true or the setting is 0. `assessment_incomplete` and `unverified_insufficient` reports are not reused, so a temporary failure can't repeat for days. A reused copy is never reused itself.

### 8.2 Audit queue

Use `asyncio.Queue` with 3 workers. Save an audit as `queued` before adding it to the queue.

On startup:

- Requeue `queued` audits.
- Mark `running` audits `failed`, with the message "This audit was interrupted. Please submit it again."

V1 does not resume audits. A rerun can reuse a saved extraction and cached searches, so it costs few credits.

Audit `status` tracks the process only: `queued`, `running`, `completed`, `failed` and `cancelled`. A cancelled audit keeps that status, and its worker stops at the next step. `overall_result` holds the outcome, including `assessment_incomplete`, so a finished audit with one Error claim is `completed` with `assessment_incomplete`, and its other claim results stay visible. `failed` means no usable report exists.

### 8.3 LLM service

`services/llm.py` is the only module that may call Groq.

```python
async def generate_structured(
    prompt: str,
    schema: type[T],
    purpose: str,
    audit_id: UUID,
    prompt_version: str,
) -> T:
    ...
```

It must:

- Validate every response with Pydantic.
- Retry once with the validation error. A Groq 400 with `failed_generation` is a schema miss, so it counts as a bad reply and gets this retry.
- If a 429 asks for a wait over 60 seconds, try `GROQ_FALLBACK_MODEL` once, because Groq's daily limits are per model.
- Retry 429s, 5xx responses and network errors up to 3 times, using `retry-after` when given, otherwise exponential backoff.
- Limit concurrency to 2 calls.
- Record usage in `usage`.
- Raise a typed error when calls fail.

Each audit may use about 10–20 LLM calls. Groq documents 1,000 requests a day, 8,000 tokens a minute and 200,000 tokens a day for this model, but those figures may be Developer-plan limits, so confirm yours at `console.groq.com/settings/limits`. A full 3-claim audit could use about 20,000 tokens, which is an estimate. On a 429, wait for `retry-after`, and fail the call if the wait is over 60 seconds.

The client sets `reasoning_effort` to `low`, `include_reasoning` to false, and `max_completion_tokens` to 4,096. Strict schemas need every field in `required`, and `additionalProperties: false` on every object, so `services/llm.py` builds them from the Pydantic models. Pydantic still validates each response.

### 8.4 Concurrency limits

```python
LIMITS = {
    "tavily": asyncio.Semaphore(4),
    "web_fetch": asyncio.Semaphore(5),
    "llm": asyncio.Semaphore(2),
}
```

Use up to 2 retries with exponential backoff for timeouts and 5xx errors. Do not retry 4xx errors, except 429. For Tavily, use at most 1 retry, and record every attempt in `usage` and against the audit's attempt budget.

Each Tavily search also has a hard total limit of 20 seconds, because the HTTP read timeout only limits gaps between data. A search that hits it fails, can retry once within the attempt budget, and records a coverage gap if it still fails.

### 8.5 Trace events

Each event is saved first, then read by polling.

```json
{
  "id": 42,
  "stage": "contradiction_search",
  "status": "completed",
  "message": "Found 8 results and kept 5 candidates",
  "metadata": {"claim_id": "c1", "cached": false}
}
```

Show actions and short summaries. Do not show raw model reasoning.

### 8.6 Polling

```python
@router.get("/api/audits/{audit_id}/events")
async def events(audit_id: UUID, after_id: int = 0):
    rows = await db.trace_after(audit_id, after_id)
    return {
        "events": rows,
        "last_id": rows[-1].id if rows else after_id,
        "finished": await db.audit_finished(audit_id),
    }
```

The audit page starts with `after_id=0`, so a refresh replays the full trace. It then polls every 2 seconds with the latest `last_id`, and stops when `finished` is true and no events remain.

### 8.7 Quotas

- Apply `DAILY_AUDIT_LIMIT` across the app. It is an upper bound, not a guaranteed capacity. Each finished check with a result, including a saved report, uses one, and so does each answered Ask question. Failed, incomplete and cancelled checks, text with no claims, and unanswered questions don't. `python -m scripts.reset_daily_limit ../.env` restarts the count.
- Check credits before accepting an audit. One lock is enough, because there is only one backend process:

```python
budget_lock = asyncio.Lock()

async def accept_audit(audit_input):
    async with budget_lock:
        if await db.count_audits_today() >= DAILY_AUDIT_LIMIT:
            raise QuotaExceeded("The daily audit limit has been reached.")
        used = await db.credits_used_this_month("tavily")
        active = await db.count_active_audits()
        available = MONTHLY - used - RESERVE - active * MAX_CREDITS_PER_AUDIT
        if available < MAX_CREDITS_PER_AUDIT:
            raise QuotaExceeded()
        return await db.create_audit(audit_input)
```

- `MAX_CREDITS_PER_AUDIT` is 18 attempts multiplied by the credits per search confirmed at Hour 0.
- The audit is created inside the lock, so it counts as active for the next check.
- Track attempts and actual credits separately. Use the credit amount Tavily reports if it provides one.
- Count development and testing use, not just public audits. At 1 credit per search and a 300 reserve, 700 credits cover about 38 full-budget audits a month.
- When limits are reached, return a clear message, and keep completed reports readable.

### 8.8 Privacy

There is no login, audit list or history. An audit ID works like a private link: anyone holding it can read that report, but nobody can change it through the API. Audit IDs must be random UUIDs.

**Retention:** At startup and once a day, delete audits older than 30 days, plus expired `tool_cache` rows. Foreign-key cascades remove everything else linked to an audit.

State the 30-day period on the input page, and warn users not to submit confidential text.

### 8.9 Security

- Run the fetch rules in Step 7 for user URLs and search results.
- Treat all source text as untrusted data.
- Never let source text trigger a tool or change a limit.
- Escape all source text in the frontend.
- Keep secrets in host settings.
- Allow only `FRONTEND_ORIGIN` through CORS.
- Return generic public errors, and log details privately.

## 9. Database

Use SQL migrations in `backend/migrations`. Usage rows are kept when an audit is deleted, because credit totals depend on them.

```sql
CREATE TABLE audits (
  id UUID PRIMARY KEY,
  input_type TEXT NOT NULL,
  input_content TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT,
  overall_result TEXT,
  public_error TEXT,
  report JSONB,
  force_fresh BOOLEAN NOT NULL DEFAULT false,
  report_key TEXT,
  reused_from UUID,
  extraction_key TEXT,
  input_context JSONB,
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
  audit_id UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
  ref TEXT NOT NULL,
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
  retrieved_via TEXT NOT NULL DEFAULT 'direct',
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

CREATE INDEX idx_audits_extraction_key ON audits (extraction_key, created_at);
CREATE INDEX idx_audits_report_key ON audits (report_key, created_at);
CREATE INDEX idx_trace_audit_id ON trace_events (audit_id, id);
CREATE INDEX idx_claims_audit ON claims (audit_id);
CREATE INDEX idx_evidence_claim ON evidence (claim_id);
CREATE INDEX idx_search_results_doc ON search_results (document_id);
CREATE INDEX idx_documents_audit ON documents (audit_id);
CREATE INDEX idx_usage_provider ON usage (provider, created_at);
CREATE INDEX idx_tool_cache_expires ON tool_cache (expires_at);
```

## 10. API

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/audits` | Create an audit, and return `202` with its ID |
| `GET` | `/api/audits/{id}` | Return its status and report |
| `GET` | `/api/audits/{id}/events?after_id=0` | Return trace events after a cursor |
| `POST` | `/api/chat` | Answer from saved pages, live searches, or one linked page in `document_url`. Each answered question uses one of the shared daily limit |
| `GET` | `/api/chat/status` | Return the number of saved pages |
| `POST` | `/api/audits/{id}/cancel` | Cancel a queued or running audit. Returns 409 if it has finished |
| `GET` | `/api/health` | Return backend health and `daily_audits_remaining` |

**Create request:**

```json
{"type": "text", "content": "India's GDP grew by 8% in FY2024-25.", "fresh": false}
```

`fresh` is optional. Set it to true to skip saved reports. `context` is also optional, as defined in Section 6.1.

**Create response:**

```json
{"audit_id": "uuid", "status": "queued"}
```

**Status response:** `GET /api/audits/{id}` also returns `input_type`, `input_text`, `claims`, `source_page`, `created_at`, `elapsed_seconds` and `estimate_seconds`. The estimate is the median time per claim of the last 20 audits that ran in full, multiplied by the number of claims found, or 60 seconds per claim if there are none. It stays between 20 and 600 seconds.

**Report shape:**

```json
{
  "audit_id": "uuid",
  "status": "completed",
  "overall_result": "partially_supported",
  "answer": [
    {
      "claim_id": "uuid",
      "text": "Plain sentence.",
      "sources": [{"publisher": "example.gov.in", "url": "https://example.gov.in/page"}]
    }
  ],
  "summary": "The answer sentences joined together.",
  "summary_label": "AI-written from the sources listed. Check them before relying on this.",
  "checked_at": "2026-10-31T10:00:00+00:00",
  "reused": false,
  "source_page": {"title": "Page title", "url": "https://example.com/article"},
  "claims": [
    {
      "id": "uuid",
      "text": "Claim text",
      "kind": "factual",
      "verdict": "contradicted",
      "confidence": "medium",
      "rule_id": "R2_CONTRADICTED",
      "reason_code": null,
      "explanation": "Rule-based explanation",
      "evidence": [{"quote": "Exact quote", "source_tier": "primary"}],
      "gaps": []
    }
  ],
  "not_investigated": [],
  "ledger": [],
  "source_history": [],
  "coverage_gaps": []
}
```

## 11. Frontend

| Page | Requirements |
|---|---|
| `/` | Text and URL input, a Search again checkbox, input limits, remaining daily audits, clear error messages, recent audits |
| `/chat` | Government information assistant: schemes, policies, rights, services and public data, with structured answers, official sources, and questions about one linked page. It is not a fact-check or legal advice |
| `/audits` | Audits from this browser and the extension, kept in `localStorage`, because the API never lists audits |
| `/audit/[id]` | Exact text, claims, time left and Cancel while running, then plain answer, claim cards, Evidence Ledger, Source History, and Check again if it did not finish |

**Audit page layout:**

1. Overall verdict and one top answer that summarizes every claim, plus a note if it is a saved result
2. For several claims, a result count and a badge for each claim
3. The strongest evidence across all claims
4. Text verified, and claims not checked
5. Investigation details, collapsed once the audit ends: claim-by-claim results, live trace, ledger and Source History

The design principle is verdict first, explanation second, evidence third, and technical trace on demand. `frontend/lib/verdicts.ts` holds the wording for every verdict, and builds explanations from saved report data, with no extra LLM calls. Empty sections are hidden. Unverified reports must not imply that a claim is false.

Readers never see ledger labels, internal rule names or codes. Badges show Confirmed, Incorrect, Misleading, Unconfirmed, Sources disagree, Partly confirmed, Incomplete, Nothing to check, and Not checked. Internal verdict names stay as they are.

Evidence is ordered with evidence used in the verdict first, then official sources, then `valid` over `contextual`. Three cards show per group, and the rest sit under Show more.

Each evidence item shows:

- Publisher or domain, with Official source or Other source
- Supports, Contradicts or Contextual
- Exact validated quote
- Publication date, if known
- Source link
- Retrieval date
- A note if it was not used in this result

Use labels, as well as color, for verdicts and ledger types. Do not use `dangerouslySetInnerHTML` for source text.

## 12. Configuration

`.env.example`:

```text
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
TAVILY_API_KEY=
FACT_CHECK_API_KEY=
DATABASE_URL=
FRONTEND_ORIGIN=
DAILY_AUDIT_LIMIT=10
TAVILY_MONTHLY_CREDITS=1000
TAVILY_CREDIT_RESERVE=300
TAVILY_CREDITS_PER_SEARCH=1
ENABLE_SOURCE_HISTORY=true
ENABLE_SKEPTIC=false
REPORT_REUSE_DAYS=7
POLICY_VERSION=1
```

Frontend:

```text
NEXT_PUBLIC_API_URL=
```

Commit `.env.example`, and never commit real keys.

## 13. Deployment

| Service | Host | Notes |
|---|---|---|
| Frontend | Vercel | Set `NEXT_PUBLIC_API_URL` |
| Backend | Hugging Face Spaces, Docker | One worker process, because the queue is in memory |
| Database | Neon or Supabase | Run migrations before starting the backend |

Confirm each host's free limits, sleep behaviour and outbound network access at Hour 0.

## 14. Build Schedule

Time is short, so use these as shares of the hours left before the deadline.

| Phase | Share | Work | Exit condition |
|---|---|---|---|
| **Setup** | 5% | Confirm rules, create repo, deploy skeleton, test Groq and Tavily | Both APIs work from the deployed backend |
| **Core** | 35% | Steps 1–11, basic verdict, trace, report page | **One real claim gets a cited report on the live site** |
| **Quality** | 20% | Sufficiency, gap round, skeptic review, full verdict rules, confidence | Five claim types run end to end |
| **Product** | 15% | Evidence Ledger UI, live trace polish, optional items | Feature freeze |
| **Testing** | 10% | Gold set, failure tests, security tests | Known failures are fixed or documented |
| **Submission** | 15% | README, diagram, deck, video, link checks | Submitted at least 2 hours early |

### 14.1 Team split

| Role | Owns |
|---|---|
| **AI and rules** | Claim extraction, classification, prompts, verdict rules, skeptic review, gold set |
| **Search and data** | Tavily adapter, filtering, fetching, extraction, BM25, source configuration |
| **Backend platform** | FastAPI, database, orchestrator, polling, cache, quotas, deployment, security |
| **Frontend and pitch** | Web app, ledger UI, demo, deck, video |

## 15. Hour 0 Checklist

Do these before building features:

- [ ] Confirm the deadline and timezone.
- [ ] Confirm that core logic built today meets the rules.
- [ ] Confirm that using third-party APIs and free tiers is allowed.
- [ ] Create the new public repo, with `.gitignore` and `.env.example`.
- [ ] Deploy backend and frontend skeletons.
- [ ] Parse both YAML files in the backend environment.
- [ ] Apply all migrations to a clean PostgreSQL database, and check cascades and that `usage` rows survive audit deletion.
- [ ] Call Groq from the deployed backend, record the real quota, and confirm that strict schemas are accepted.
- [ ] Call Tavily from the deployed backend, and confirm parameter names and response format.
- [ ] Confirm whether `include_domains` or `max_results` changes credit cost.
- [ ] Run 5 real claims, and check that official searches return usable pages.
- [ ] Test outbound fetches from the host.
- [ ] Test a PDF from a government site.

If official search is weak, add USGS for a reliable demo claim, and record the decision in the README.

## 16. Testing

### 16.1 Unit tests

| Area | Test |
|---|---|
| Claims | Compound claims split, pronouns are not guessed, and vague subjects become `not_checkable` after one retry |
| Claim kinds | `not_checkable` items are not searched |
| Numbers | `₹50,000`, `Rs 50k`, `2 lakh` and `8.00%` parse correctly |
| Rounding | `7.6%` matches `8%`, and does not match `8.0%` or "exactly 8%" |
| Comparators | `over` and `under` reject equal values, and `exact` rejects near values |
| Units | Percent and percentage points are treated as different |
| Authority | Official sites match by pattern, lookalike hosts don't, and one unofficial publisher settles nothing |
| Host matching | `example-gov.in` does not match `gov.in` |
| Config | Invalid YAML stops startup |
| Key details | Unknown detail IDs are dropped |
| URLs | Tracking parameters are removed, and article IDs are kept |
| Selection | Slot rules and the 2-per-domain limit hold |
| Citations | Unknown sentence IDs and IDs not supplied to the LLM are rejected |
| Deduplication | Two publishers with identical text keep both URLs, and count as one source |
| Report result | Any Error produces Assessment incomplete, and answers and evidence carry `claim_id` and `source_tier` |
| Validation | A quarterly figure for an annual claim is `contextual` |
| Skeptic | Unknown evidence IDs are ignored |
| Verdicts | Each rule and rule order works |
| Plain answers | Numbers absent from the claim's quotes, internal terms and unknown labels are rejected, and a direct quote is used instead |
| Saved reports | A match skips search and LLM calls, `fresh` runs a new audit, and weak results are not reused |
| Prompts | Placeholders are valid, and examples never use real test topics |
| Page details | The title and address never reach any LLM prompt, and don't change the saved report key |

### 16.2 Security tests

- A direct private IP is blocked.
- A redirect to a private IP is blocked.
- A hostname that resolves to a private IP is blocked.
- A 6 MB response stops during streaming.
- A fake PDF is rejected.
- A page saying "ignore previous instructions" has no effect.
- No endpoint lists audits.

### 16.3 Failure tests

| Failure | Expected result |
|---|---|
| Tavily is down | Coverage gap and Unverified, or a clear audit failure if nothing is available |
| One document fails | Replacement is used |
| Groq fails during claim extraction | Audit fails with a clear message |
| Groq fails during the plain answer | Report appears with direct quotes from sources |
| Groq fails during the gap round | Earlier evidence is kept, and a coverage gap is shown |
| Backend restarts mid-audit | Audit shows as failed with a resubmit message |
| One claim fails | That claim shows Error, and the others complete |
| Search retries use up the attempt budget | Gap searches stop at the limit, and a coverage gap is shown |
| Credits fall near the reserve | New audits are refused with a clear message |
| Two audits start at the same moment with credits for one | Only one is accepted |
| Cleanup runs | Audits older than 30 days and their linked data are removed |
| Browser refreshes | The full trace is restored |

Run these against the deployed backend, as well as locally. Nothing counts as validated until a full audit has run there.

### 16.4 Gold set

Create 20–30 real claims, and review each by hand. Include:

- Supported
- Contradicted
- Misleading
- Unverified: conflicting
- Unverified: insufficient

Record the expected verdict and expected evidence passage. Measure retrieval and verdict quality separately:

```text
claim_id, claim, expected_verdict, expected_source, expected_passage,
actual_verdict, expected_source_found, passage_found, notes
```

Run the gold set once before tuning prompts. Report only measured results. Mention the sample size whenever you quote accuracy. Use the search cache to limit credit use.

## 17. Risks

| Risk | Response |
|---|---|
| LLM quota runs out | Cache, limit audits, reduce claims to 1 during judging if needed |
| Tavily credits run low | Use cache, enforce the 300-credit reserve, stop new audits with a clear message |
| Official search is weak | Add USGS, or focus the demo on domains that work |
| A site blocks fetching | Use a replacement, and record a coverage gap |
| Copied stories look like two publishers | Cap confidence at Medium when no official source exists. This risk is larger now that any claim type can be settled by 2 publishers |
| Dates are wrong or missing | Show the original date text, and never treat dates as proof of origin |
| Free host sleeps or restarts | Keep reports in Postgres, ask users to resubmit interrupted audits, and open the site before judging |
| LLM invents a citation | Sentence IDs and Python-built quotes prevent it |
| False claims about real people | Show evidence and uncertainty, and use assessment language |
| P4 coverage is partial | State the V1 scope and roadmap openly |

## 18. Submission Checklist

### 18.1 Form

- [ ] Theme 1 selected
- [ ] Project name confirmed as available
- [ ] Problem statement and context
- [ ] Target audience
- [ ] Solution description covering the investigation workflow
- [ ] Architecture, models and sources
- [ ] Live prototype link
- [ ] Demo video link
- [ ] Public repository link
- [ ] Pitch deck link

### 18.2 README

- Project summary
- Architecture diagram
- Setup steps
- Environment variables
- Migration steps
- Pipeline explanation
- Gold-set results, with sample size
- Known limitations: audit links work for anyone who has them, one person can use the whole daily audit limit, DNS rebinding risk remains, interrupted audits must be resubmitted, and PubMed source tiers are approximate
- Credit to projects that inspired the design, without copying code from unlicensed repos

### 18.3 Demo video, 2–3 minutes

1. **Problem, 20 seconds:** False claims spread faster than teams can check them.
2. **Live audit, 60 seconds:** Submit a real claim, and show both search directions.
3. **Evidence, 40 seconds:** Show the quote, its source, and any contradicting evidence.
4. **Honest uncertainty, 20 seconds:** Show an Unverified result.
5. **Architecture and results, 20 seconds:** Show the rule-based verdict and measured results.

Record against the deployed app, and include at least one claim the system has not already cached.

### 18.4 Deck, 5–7 slides

1. Title and team
2. Problem and context
3. Solution and key features
4. Live demo summary
5. Technical architecture
6. Evidence Ledger and rule-based verdicts
7. Results, limits and roadmap

**Roadmap:** Live feeds, image and media checks, synthetic media detection as an uncertain signal, more official adapters, and wider language support.

### 18.5 Final checks

- [ ] All links work in an incognito window.
- [ ] The live app works without a login.
- [ ] The repo contains no secrets.
- [ ] Tavily and Groq quotas can cover judging.
- [ ] The video and deck are publicly accessible.
- [ ] The form is submitted at least 2 hours early.

## 18A. Changes from Live Testing

This section records what live testing changed, and why. Earlier sections now match it.

### 18A.1 Product focus

Veritrace is an evidence-backed verification engine for Indian public claims. It focuses on official statistics, monetary policy, space missions and public health. Demo claims should rely on sources the system can read. MoSPI, RBI, ISRO and WHO have worked in live tests. PIB pages returned 403, so PIB must not be a required source.

### 18A.2 Search and retrieval

- **Queries:** Official and fallback queries use the full claim text, which keeps the topic. Gap searches are written from plain descriptions of what is missing.
- **Blocked sites:** Social platforms are excluded from search. After a 403, other pages on that domain are skipped for the audit.
- **Provider text:** Tavily raw content is requested with each search. It is used only when direct fetching fails, and documents record `retrieved_via`. The fetcher still identifies itself as `VeritraceBot`.
- **PDF text:** Single newlines are line wraps and are merged. Blank lines stay as paragraph breaks. HTML text is not merged.
- **HTML text:** Empty extraction is retried with `favor_recall`.
- **Passage ranking:** Financial-year and percentage forms are normalized. Query terms are unique, stopwords are removed, and the BM25 score is multiplied by coverage of entity and date details. Numbers are excluded from that coverage.
- **Trace:** The passages sent to Groq are recorded with their scores.

### 18A.3 Evidence and verdicts

- **Extraction checkpoint:** Identical input on the same day, with the same model and prompts, reuses saved claims and classifications. Later stages still run.
- **Mixed citations:** An item citing several documents is split into one evidence item per document.
- **Dates:** Another year in a quote is a mismatch only if the document never mentions the claim's year.
- **Entities:** Only parsed numbers are treated as numeric, so `Chandrayaan-3` remains an entity.
- **Source rules:** `not_primary` lists news outlets on government domains, such as `ddnews.gov.in`.
- **Saved reports:** Identical text reuses a saved report for `REPORT_REUSE_DAYS`, 7 by default, with no search or LLM calls. The key covers the text, model, prompt versions and rule versions, plus the date if the text uses relative time words. Weak results, meaning `unverified_insufficient` and `assessment_incomplete`, are not reused. Reports show when they were checked, and users can choose Search again.
- **Plain answers:** Reports open with a plain answer built from evidence already used in the verdict, with no extra searches. A Contradicted claim states the correct fact. If sources give different figures, each is reported and neither is chosen. Python rejects any sentence with a number not found in its cited quotes or the claim, or with internal terms, and falls back to a direct quote. Readers see source links, not ledger labels. Unverified: insufficient claims make no LLM call for this step.
- **Selected text only (Oct 31, 2026):** Surrounding text was tried for naming subjects, then removed. Claims must come from the submitted text alone, so nothing from the page can change what gets checked.
- **Viral claim handling (Nov 1, 2026):** "The government" counts as a clear subject. Yes/no questions about a specific fact become claims, a named body's stated plan is checkable, and "falsely claiming X" checks X. Reworded vague claims such as "NASA launched the mission" are rejected. A statistic must say which place or group it covers.
- **Numbers (Nov 1, 2026):** Python also reads figures from the quote itself. A figure a quote doesn't state doesn't count against it, but that quote can't confirm that detail. A missing currency isn't a conflict, and a bare number only clashes with a listed figure of similar size.
- **Backup model (Nov 1, 2026):** Groq's daily limit is per model, so a long 429 wait switches to `GROQ_FALLBACK_MODEL`.
- **Daily limit (Nov 1, 2026):** Finished checks with a result and answered Ask questions share one limit, shown on the website and in the extension.
- **Standalone claims:** A factual claim must name its subject. Python rejects vague subjects such as "The mission was...", asks Groq once to fix them, and otherwise marks them `not_checkable` so they are never searched. Figures missing from key details are added by Python, and a failed extraction retry keeps the first attempt.
- **Stances:** Only `supports`, `contradicts` and `irrelevant`. `context` and `limits_claim` were removed because they affected 2 of 75 evidence items, and neither was a sound Misleading verdict.
- **Misleading:** Decided by Python only. It needs an official source that supports the fact, marked `contextual` for `time_differs` or `scope_differs`, no strong contradiction, and no valid official support. If the skeptic is on, `skeptic_missing_qualification` also counts.
- **Labels:** Kinds are `factual` and `not_checkable`. Types are `official_record`, `scientific` and `reported_event`. If an official body's record would settle a claim, it is `official_record`.
- **Skeptic:** Off by default, as stated in Step 14.

- **Chatbot (Nov 1, 2026):** `scripts/seed_knowledge.py` saves official pages into `knowledge_pages` and `knowledge_chunks`. Text comes from the search cache and targeted Tavily searches, because most official sites block or don't serve text to the fetcher. `/api/chat` ranks passages with PostgreSQL full-text search and sends up to 8 to the LLM. Python rejects sentences without a valid source, or with a number absent from the cited passages. Saved pages come first. Questions with words such as "latest" go to a live search first, and any other question falls back to one on official domains. If that fails, a web search runs as the last step, and its answers are labelled as not official. Social sites are excluded, and searches share `CHAT_SEARCH_DAILY_LIMIT` a day. Only official pages are saved. Eligibility is described as "may qualify". Up to 3 earlier turns can be sent as `history`, and a follow-up is rewritten into a full question before anything else runs. PDF upload and translation are out of scope for V1, and audits don't use the chatbot.
- **Foreign official sites (Nov 1, 2026):** A US IRS page gave a High result for an Indian Supreme Court claim, so `.gov` and other foreign government sites no longer count as official.
- **Source trust (Oct 31, 2026):** A hand-made list can't cover every source, and a valid fact-checker result stayed Unverified. So the per-claim-type tiers were removed, and any claim can be settled by one official source found by pattern, or by 2 distinct publishers.

### 18A.4 Platform

- **Prompts:** Every LLM prompt, its version and related search hints live in `backend/prompts.py`. Raise a prompt's version when editing it. Prompt examples use an invented setting and must never use real test or demo claims, which a test enforces.
- **Evidence stance:** A different figure for a different period or group is `irrelevant`. The same figure for a different period is `supports`, so Python can mark it `contextual`.
- **Classification:** A claim is `scientific` when it asserts a scientific, medical, technical or causal finding, even if an official body discussed it. Otherwise `official_record` covers what an institution formally recorded or decided.
- **Gap queries:** Groq sees earlier searches, and exact repeats are dropped.
- **Database:** PostgreSQL must use UTF-8, and startup fails if it does not.
- **Frontend:** Next.js with two routes, `/` and `/audit/[id]`.
- **Measured result:** See Evaluation in the README.

## 19. Definition of Done

V1 is complete when:

- A reviewer can submit text or a URL on the live site without logging in.
- `not_checkable` sentences appear in the report, but use no search credits.
- Support and contradiction searches appear in the live trace.
- Every source statement links to an exact stored passage.
- Contradicting evidence is visible in the report.
- At least one partly true claim and one Unverified claim are demonstrated.
- A source failure produces a visible gap instead of a crash.
- Refreshing during an audit restores the trace.
- All accuracy figures come from actual runs.
