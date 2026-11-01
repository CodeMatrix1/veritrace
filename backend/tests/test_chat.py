import pytest

from components.chat import (
    DOCUMENT_NOT_READ,
    NOT_FOUND,
    SEARCH_PAUSED,
    answer_question,
    choose_section,
    is_official,
    is_state_source,
    readable,
    select_passages,
    split_chunks,
)
from schemas.audit import AuditInput, ChatRequest
from schemas.search import AdapterResult, RawResult
from services.llm import LLMError
from tests.conftest import FakeSearch, article

PAGE_URL = "https://www.rbi.org.in/page"
PAGE_TEXT = "The Reserve Bank of India set the repo rate at 5.5 per cent in June 2025."
LIVE_URL = "https://www.myscheme.gov.in/schemes/pm-kisan"
LIVE_TEXT = "PM-KISAN gives eligible farmer families Rs 6000 a year in three instalments. " * 6


def said(text, labels=("S1",), section="Answer"):
    return {"sentences": [{"text": text, "labels": list(labels), "section": section}]}


async def add_page(deps):
    await deps.repo.save_knowledge_page(PAGE_URL, "rbi.org.in", "Monetary policy", "2025-06-06", [PAGE_TEXT])


def live_search():
    result = RawResult(url=LIVE_URL, title="PM-KISAN", content="x", raw_content=LIVE_TEXT)
    return FakeSearch(default=AdapterResult(status="ok", results=[result], credits=1))


@pytest.mark.asyncio
async def test_checks_and_questions_share_one_limit_until_reset(make_deps):
    deps = make_deps({}, None, {})

    async def audit(result):
        audit_id = await deps.repo.create_audit(AuditInput(type="text", content="A claim here"), "1")
        await deps.repo.finish_audit(audit_id, result, {})

    await audit("supported")
    await deps.repo.record_usage(None, "veritrace", "chat_question", "ok")
    await deps.repo.record_usage(None, "groq", "chat", "ok")
    assert await deps.repo.count_audits_today() == 2
    await deps.repo.reset_daily_limit()
    assert await deps.repo.count_audits_today() == 0


@pytest.mark.parametrize("result", ["assessment_incomplete", "no_factual_claims"])
@pytest.mark.asyncio
async def test_checks_without_a_result_are_free(make_deps, result):
    deps = make_deps({}, None, {})
    audit_id = await deps.repo.create_audit(AuditInput(type="text", content="A claim here"), "1")
    await deps.repo.finish_audit(audit_id, result, {})
    failed = await deps.repo.create_audit(AuditInput(type="text", content="Another claim"), "1")
    await deps.repo.fail_audit(failed, "Failed")
    cancelled = await deps.repo.create_audit(AuditInput(type="text", content="One more"), "1")
    await deps.repo.cancel_audit(cancelled)
    assert await deps.repo.count_audits_today() == 0


def test_split_chunks_overlaps_and_keeps_every_word():
    words = [f"word{i}" for i in range(600)]
    chunks = split_chunks(" ".join(words))
    assert len(chunks) > 1
    assert set(" ".join(chunks).split()) == set(words)
    assert chunks[0].split()[-1] in chunks[1].split()


def test_one_page_cannot_fill_the_answer():
    rows = [{"url": "a"}] * 5 + [{"url": "b"}] * 5
    assert [r["url"] for r in select_passages(rows)] == ["a", "a", "b", "b"]


def test_question_length_and_link_are_checked():
    with pytest.raises(ValueError):
        ChatRequest(question="x" * 501)
    with pytest.raises(ValueError):
        ChatRequest(question="What is this?", document_url="javascript:alert(1)")


@pytest.mark.parametrize("domain,official", [
    ("mospi.gov.in", True), ("rbi.org.in", True), ("irs.gov", False), ("x.com", False),
])
def test_official_sites(domain, official):
    assert is_official(domain) is official


@pytest.mark.asyncio
async def test_no_source_says_so_without_calling_the_ai(make_deps):
    deps = make_deps({}, None, {})
    await add_page(deps)
    fake = FakeSearch()
    reply = await answer_question("Who won the football cup?", deps.repo, deps.llm, search=fake)
    assert (reply["found"], reply["answer"]) == (False, NOT_FOUND)
    assert deps.client.calls == []


@pytest.mark.asyncio
async def test_paused_search_says_so_and_a_reset_restarts_the_count(make_deps):
    deps = make_deps({}, None, {})
    reply = await answer_question("Who won the football cup?", deps.repo, deps.llm)
    assert reply["answer"] == SEARCH_PAUSED
    await deps.repo.record_usage(None, "tavily", "chat_search", "ok")
    assert await deps.repo.count_chat_searches_today() == 1
    await deps.repo.reset_daily_limit()
    assert await deps.repo.count_chat_searches_today() == 0


@pytest.mark.asyncio
async def test_saved_answer_is_grouped_and_keeps_its_source(make_deps):
    output = said("The repo rate was 5.5 per cent in June 2025 [S1].", section="Key dates")
    deps = make_deps({"chat": output}, None, {})
    await add_page(deps)
    reply = await answer_question("When was the repo rate set?", deps.repo, deps.llm)
    assert (reply["found"], reply["mode"]) == (True, "saved")
    assert reply["sections"] == [
        {"title": "Key dates", "sentences": ["The repo rate was 5.5 per cent in June 2025."]}
    ]
    assert reply["sources"][0]["quote"] == PAGE_TEXT
    assert reply["sources"][0]["official"] is True


@pytest.mark.parametrize("output", [
    said("The repo rate was 7.25 per cent."),
    said("The repo rate was 5.5 per cent.", labels=()),
    said("The repo rate was 5.5 per cent.", labels=("S9",)),
])
@pytest.mark.asyncio
async def test_invented_or_unsourced_answers_are_refused(make_deps, output):
    deps = make_deps({"chat": output}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm)
    assert (reply["found"], reply["sources"]) == (False, [])


@pytest.mark.asyncio
async def test_unknown_section_falls_back_to_answer(make_deps):
    output = said("The repo rate was 5.5 per cent.", section="Nonsense")
    deps = make_deps({"chat": output}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm)
    assert reply["sections"][0]["title"] == "Answer"


@pytest.mark.asyncio
async def test_missing_topic_searches_official_sites_and_saves_the_page(make_deps):
    fake = live_search()
    deps = make_deps({"chat": said("PM-KISAN gives Rs 6000 a year.")}, None, {})
    reply = await answer_question("Who qualifies for PM-KISAN?", deps.repo, deps.llm, search=fake)
    assert (reply["found"], reply["mode"]) == (True, "live")
    assert fake.requests[0].include_domains
    assert await deps.repo.knowledge_page_exists(LIVE_URL)


@pytest.mark.asyncio
async def test_web_search_is_the_last_step_and_is_labelled(make_deps):
    url = "https://blog.example.com/a"
    result = RawResult(url=url, title="B", content="x", raw_content=LIVE_TEXT)
    fake = FakeSearch(default=AdapterResult(status="ok", results=[result]))
    deps = make_deps({"chat": said("PM-KISAN gives Rs 6000 a year.")}, None, {})
    reply = await answer_question(
        "Who qualifies for PM-KISAN?", deps.repo, deps.llm, search=fake, blocked=["x.com"]
    )
    assert (reply["found"], reply["mode"]) == (True, "web")
    assert reply["sources"][0]["official"] is False
    assert [r.scope for r in fake.requests] == ["official", "open"]
    assert fake.requests[1].exclude_domains == ["x.com"]
    assert fake.requests[1].query == "Who qualifies for PM-KISAN? India"
    assert not await deps.repo.knowledge_page_exists(url)


@pytest.mark.asyncio
async def test_social_results_are_never_used_from_the_web(make_deps):
    result = RawResult(url="https://www.reddit.com/r/a", title="B", content="x", raw_content=LIVE_TEXT)
    fake = FakeSearch(default=AdapterResult(status="ok", results=[result]))
    deps = make_deps({"chat": said("PM-KISAN gives Rs 6000 a year.")}, None, {})
    reply = await answer_question("Who qualifies for PM-KISAN?", deps.repo, deps.llm, search=fake)
    assert reply["found"] is False
    assert deps.client.calls == []


@pytest.mark.asyncio
async def test_current_questions_go_to_live_sources_first(make_deps):
    fake = live_search()
    deps = make_deps({"chat": said("PM-KISAN gives Rs 6000 a year.")}, None, {})
    await add_page(deps)
    reply = await answer_question("What is the latest repo rate?", deps.repo, deps.llm, search=fake)
    assert reply["mode"] == "live"


@pytest.mark.asyncio
async def test_question_about_a_linked_page_uses_only_that_page(make_deps):
    url = "https://www.example.org/notice"
    deps = make_deps(
        {"chat": said("The notice takes effect in 2024.", section="Key dates")}, None,
        {url: article("The notice takes effect in 2024 for all banks.")},
    )
    fake = live_search()
    reply = await answer_question(
        "When does this take effect?", deps.repo, deps.llm, search=fake,
        fetcher=deps.fetcher, document_url=url,
    )
    assert (reply["found"], reply["mode"]) == (True, "document")
    assert fake.requests == []


@pytest.mark.asyncio
async def test_unreadable_link_says_so(make_deps):
    deps = make_deps({}, None, {})
    reply = await answer_question(
        "What is this?", deps.repo, deps.llm, fetcher=deps.fetcher,
        document_url="https://www.example.org/missing",
    )
    assert reply["answer"] == DOCUMENT_NOT_READ


@pytest.mark.parametrize("domain,state", [
    ("cic.gov.in", False), ("www.cic.gov.in", False), ("sic.assam.gov.in", True),
    ("rtionline.delhi.gov.in", True), ("rbi.org.in", False), ("who.int", False),
])
def test_state_sites_are_told_from_central_ones(domain, state):
    assert is_state_source(domain) is state


def test_central_sources_come_first_unless_a_state_is_asked_about():
    state = {"url": "a", "domain": "sic.assam.gov.in"}
    central = {"url": "b", "domain": "cic.gov.in"}
    assert select_passages([state, central], "How do I file an RTI?")[0] is central
    assert select_passages([state, central], "How do I file an RTI in Assam?")[0] is state


@pytest.mark.asyncio
async def test_saved_and_live_pages_are_read_together(make_deps):
    fake = live_search()
    deps = make_deps({"chat": said("The repo rate was 5.5 per cent.", labels=("S1", "S2"))}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm, search=fake)
    assert reply["mode"] == "live"
    prompt = deps.client.prompts["chat"][0]
    assert PAGE_TEXT in prompt and "PM-KISAN gives eligible" in prompt


@pytest.mark.parametrize("written,shown", [
    ("You qualify if you are a farmer.", "You may qualify if you are a farmer."),
    ("You are eligible if you are a farmer.", "You may be eligible if you are a farmer."),
])
@pytest.mark.asyncio
async def test_eligibility_is_never_promised(make_deps, written, shown):
    deps = make_deps({"chat": said(written)}, None, {})
    await deps.repo.save_knowledge_page(PAGE_URL, "rbi.org.in", "Rules", None, ["Farmers qualify for the repo rate."])
    reply = await answer_question("Who gets the repo rate?", deps.repo, deps.llm)
    assert reply["answer"] == shown


@pytest.mark.asyncio
async def test_partial_official_answer_tries_the_web(make_deps):
    partial = {**said("The repo rate has a fee."), "complete": False}
    result = RawResult(url="https://news.example.com/a", title="N", content="x", raw_content=LIVE_TEXT)
    fake = FakeSearch(default=AdapterResult(status="ok", results=[result]))
    deps = make_deps({"chat": [partial, said("PM-KISAN gives Rs 6000 a year.")]}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm, search=fake)
    assert reply["mode"] == "web"


@pytest.mark.asyncio
async def test_partial_answer_is_kept_if_nothing_is_better(make_deps):
    partial = {**said("The repo rate has a fee."), "complete": False}
    deps = make_deps({"chat": partial}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm)
    assert (reply["found"], reply["mode"]) == (True, "saved")


def test_quotes_are_tidied_for_reading():
    assert readable("**Aug 12** | [RBI invites comments](page.aspx?id=1) | ![a](b.png)") == (
        "Aug 12 RBI invites comments"
    )


@pytest.mark.parametrize("section,question,expected", [
    ("What changed", "What licenses does a startup need?", "Answer"),
    ("Who qualifies", "What licenses does a startup need?", "Answer"),
    ("What changed", "What changed in this rule?", "What changed"),
    ("Who qualifies", "Who qualifies for PM-KISAN?", "Who qualifies"),
    ("Documents needed", "Explain PM-KISAN in detail.", "Documents needed"),
    ("Limits", "What licenses does a startup need?", "Limits"),
])
def test_sections_only_appear_when_asked_for(section, question, expected):
    assert choose_section(section, question) == expected


@pytest.mark.asyncio
async def test_follow_up_reuses_the_earlier_sources_first(make_deps):
    fake = live_search()
    deps = make_deps(
        {"chat_rewrite": {"question": "What was the repo rate in detail?"},
         "chat": said("The repo rate was 5.5 per cent in June 2025.")},
        None, {},
    )
    await add_page(deps)
    history = [{"question": "a", "answer": "b", "source_urls": [PAGE_URL]}]
    reply = await answer_question("in detail?", deps.repo, deps.llm, search=fake, history=history)
    assert reply["sources"][0]["url"] == PAGE_URL
    assert fake.requests == []


@pytest.mark.asyncio
async def test_out_of_scope_answer_is_dropped(make_deps):
    output = {**said("Hello is a greeting."), "in_scope": False}
    deps = make_deps({"chat": output}, None, {})
    await add_page(deps)
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm)
    assert (reply["found"], reply["answer"]) == (False, SEARCH_PAUSED)


@pytest.mark.asyncio
async def test_follow_up_is_rewritten_before_searching(make_deps):
    fake = live_search()
    deps = make_deps(
        {"chat_rewrite": {"question": "Explain PM-KISAN eligibility in detail."},
         "chat": said("PM-KISAN gives Rs 6000 a year.")},
        None, {},
    )
    history = [{"question": "Who qualifies for PM-KISAN?", "answer": "Farmer families."}]
    reply = await answer_question(
        "explain in detail?", deps.repo, deps.llm, search=fake, history=history
    )
    assert fake.requests[0].query == "Explain PM-KISAN eligibility in detail."
    assert reply["understood_as"] == "Explain PM-KISAN eligibility in detail."
    assert "Farmer families." in deps.client.prompts["chat_rewrite"][0]


@pytest.mark.asyncio
async def test_no_history_means_no_extra_ai_call(make_deps):
    deps = make_deps({"chat": said("The repo rate was 5.5 per cent.")}, None, {})
    await add_page(deps)
    await answer_question("What was the repo rate?", deps.repo, deps.llm)
    assert deps.client.calls == ["chat"]


@pytest.mark.asyncio
async def test_failed_rewrite_keeps_the_question(make_deps):
    deps = make_deps(
        {"chat_rewrite": LLMError("unavailable"), "chat": said("The repo rate was 5.5 per cent.")},
        None, {},
    )
    await add_page(deps)
    history = [{"question": "a", "answer": "b"}]
    reply = await answer_question("What was the repo rate?", deps.repo, deps.llm, history=history)
    assert reply["understood_as"] == "What was the repo rate?"


@pytest.mark.asyncio
async def test_ai_failure_reaches_the_route(make_deps):
    deps = make_deps({"chat": LLMError("unavailable")}, None, {})
    await add_page(deps)
    with pytest.raises(LLMError):
        await answer_question("What was the repo rate?", deps.repo, deps.llm)
