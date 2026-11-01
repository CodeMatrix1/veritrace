import json

import httpx
import pytest

from schemas import llm_outputs
from schemas.llm_outputs import EvidenceOutput, ExtractionOutput, QueryOutput
from services.limits import Limits
from services.llm import (
    API_URL,
    GroqClient,
    LLMError,
    LLMService,
    TransientLLMError,
    strict_schema,
)


def objects_in(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from objects_in(value)
    elif isinstance(node, list):
        for item in node:
            yield from objects_in(item)


@pytest.mark.parametrize("name", [
    "ExtractionOutput", "ClassificationOutput", "InitialQueryOutput", "QueryOutput",
    "EvidenceOutput", "SkepticOutput", "SummaryOutput",
])
def test_every_llm_schema_meets_strict_rules(name):
    schema = strict_schema(getattr(llm_outputs, name))
    objects = list(objects_in(schema))
    assert objects
    for node in objects:
        assert node["additionalProperties"] is False
        assert set(node["required"]) == set(node["properties"])
    assert "$ref" not in json.dumps(schema)
    assert "$defs" not in schema


def test_nullable_and_enum_fields_convert():
    item = strict_schema(ExtractionOutput)["properties"]["claims"]["items"]
    assert item["properties"]["time"]["type"] == ["string", "null"]
    assert "factual" in item["properties"]["kind"]["enum"]
    assert "default" not in json.dumps(strict_schema(EvidenceOutput))


def test_field_names_are_kept():
    issue = strict_schema(llm_outputs.SkepticIssue)
    assert set(issue["properties"]) == {"evidence_id", "type", "explanation", "action"}


def client_with(handler):
    return GroqClient("key", "openai/gpt-oss-120b", transport=httpx.MockTransport(handler))


def completion(content):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    })


@pytest.mark.asyncio
async def test_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return completion('{"queries": ["q"]}')

    response = await client_with(handler).generate("rules", "prompt", QueryOutput, "gap")
    body = seen["body"]
    assert seen["url"] == API_URL
    assert seen["auth"] == "Bearer key"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["messages"][0]["content"].startswith("rules")
    assert (response.input_tokens, response.output_tokens) == (11, 7)


@pytest.mark.asyncio
async def test_429_carries_retry_after():
    client = client_with(lambda r: httpx.Response(429, headers={"retry-after": "7"}))
    with pytest.raises(TransientLLMError) as caught:
        await client.generate("s", "p", QueryOutput, "gap")
    assert caught.value.retry_after == 7


@pytest.mark.asyncio
async def test_400_is_permanent_and_500_is_transient():
    with pytest.raises(LLMError, match="groq_400"):
        await client_with(lambda r: httpx.Response(400)).generate("s", "p", QueryOutput, "g")
    with pytest.raises(TransientLLMError):
        await client_with(lambda r: httpx.Response(503)).generate("s", "p", QueryOutput, "g")


@pytest.mark.asyncio
async def test_schema_miss_is_retried_like_a_bad_reply():
    replies = [
        httpx.Response(400, json={"error": {"code": "json_validate_failed", "failed_generation": "{}"}}),
        completion('{"queries": ["q"]}'),
    ]
    service = LLMService(client_with(lambda r: replies.pop(0)), Usage(), Limits())
    result = await service.generate_structured("p", QueryOutput, "gap", None, "1")
    assert result.queries == ["q"]


class Usage:
    def __init__(self):
        self.statuses = []

    async def record_usage(self, audit_id, provider, purpose, status, **kw):
        self.statuses.append((provider, status))


@pytest.mark.asyncio
async def test_service_waits_for_retry_after_then_succeeds(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("services.llm.asyncio.sleep", fake_sleep)
    replies = [httpx.Response(429, headers={"retry-after": "5"}), completion('{"queries": []}')]
    service = LLMService(client_with(lambda r: replies.pop(0)), Usage(), Limits())
    result = await service.generate_structured("p", QueryOutput, "gap", None, "1")
    assert result.queries == []
    assert waits == [5.0]


@pytest.mark.asyncio
async def test_long_rate_limit_fails_instead_of_waiting(monkeypatch):
    async def no_sleep(seconds):
        raise AssertionError("must not wait")

    monkeypatch.setattr("services.llm.asyncio.sleep", no_sleep)
    client = client_with(lambda r: httpx.Response(429, headers={"retry-after": "3600"}))
    service = LLMService(client, Usage(), Limits())
    with pytest.raises(LLMError, match="unavailable"):
        await service.generate_structured("p", QueryOutput, "gap", None, "1")


@pytest.mark.asyncio
async def test_invalid_json_from_groq_retries_with_feedback():
    prompts = []

    def handler(request):
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        return completion("nope" if len(prompts) == 1 else '{"queries": ["q"]}')

    usage = Usage()
    service = LLMService(client_with(handler), usage, Limits())
    result = await service.generate_structured("p", QueryOutput, "gap", None, "1")
    assert result.queries == ["q"]
    assert "failed validation" in prompts[1]
    assert ("groq", "invalid_output") in usage.statuses


@pytest.mark.asyncio
async def test_used_up_model_falls_back_but_a_short_wait_does_not():
    import json

    import httpx

    from schemas.llm_outputs import SummaryOutput
    from services.llm import GroqClient, TransientLLMError

    seen = []
    wait = {"value": "900"}

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "big":
            return httpx.Response(429, headers={"retry-after": wait["value"]}, json={})
        reply = {"choices": [{"message": {"content": '{"sentences": []}'}}], "usage": {}}
        return httpx.Response(200, json=reply)

    client = GroqClient("key", "big", httpx.MockTransport(handler), fallback_model="small")
    response = await client.generate("s", "p", SummaryOutput, "summary")
    assert response.text == '{"sentences": []}'
    assert seen == ["big", "small"]

    seen.clear()
    wait["value"] = "5"
    with pytest.raises(TransientLLMError):
        await client.generate("s", "p", SummaryOutput, "summary")
    assert seen == ["big"]
    await client.close()


def test_short_replies_reserve_fewer_tokens():
    from schemas.llm_outputs import SummaryOutput
    from services.llm import MAX_COMPLETION_TOKENS, GroqClient

    client = GroqClient("key", "model")
    assert client.build_payload("s", "p", SummaryOutput, purpose="chat")["max_completion_tokens"] == 1500
    other = client.build_payload("s", "p", SummaryOutput, purpose="evidence_analysis")
    assert other["max_completion_tokens"] == MAX_COMPLETION_TOKENS


@pytest.mark.parametrize("error,wait,expected", [
    ("Rate limit reached on tokens per day (TPD): Limit 200000", "900", "one day"),
    ("Rate limit reached on tokens per minute (TPM): Limit 8000", "4", "one minute"),
    ("Rate limit reached on requests per minute (RPM)", "2", "one minute"),
    ("Something new", "900", "one day"),
    ("Something new", "5", "one minute"),
])
def test_limit_messages_name_the_right_wait(error, wait, expected):
    import httpx

    from services.llm import limit_message

    response = httpx.Response(429, headers={"retry-after": wait}, text=error)
    assert expected in limit_message(response)
