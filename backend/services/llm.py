import asyncio
from dataclasses import dataclass
from prompts import SYSTEM_RULES
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from services.db import Repository
from services.limits import Limits

T = TypeVar("T", bound=BaseModel)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_COMPLETION_TOKENS = 4096
TRANSIENT_RETRIES = 3
MAX_RETRY_WAIT_SECONDS = 60.0
DROPPED_KEYWORDS = {"title", "description", "default", "minItems", "maxItems"}


class LLMError(Exception):
    pass


class TransientLLMError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMClient(Protocol):
    provider: str
    model: str

    async def generate(
        self, system: str, prompt: str, schema: type[BaseModel], purpose: str
    ) -> LLMResponse: ...


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Build a schema Groq's strict mode accepts from a Pydantic model.

    Strict mode needs every field in required and additionalProperties false.
    Fields with defaults stay required here, and may be null or empty. Pydantic
    still enforces the size limits that this drops.
    """
    root = model.model_json_schema()
    definitions = root.get("$defs", {})

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            return convert(definitions[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:
            options = [convert(option) for option in node["anyOf"]]
            types = [option.get("type") for option in options]
            if len(options) == 2 and "null" in types:
                value = options[1 - types.index("null")]
                return {**value, "type": [value["type"], "null"]}
            return {"anyOf": options}

        result = {
            key: value
            for key, value in node.items()
            if key not in DROPPED_KEYWORDS and key not in ("$defs", "properties", "items")
        }
        if "items" in node:
            result["items"] = convert(node["items"])
        if node.get("type") == "object":
            properties = node.get("properties", {})
            result["properties"] = {name: convert(p) for name, p in properties.items()}
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result

    return convert(root)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class GroqClient:
    provider = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
        fallback_model: str | None = None,
    ):
        self.model = model
        self.fallback_model = fallback_model
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=60.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    def build_payload(
        self, system: str, prompt: str, schema: type[BaseModel], model: str | None = None
    ) -> dict[str, Any]:
        # Groq's reasoning docs advise keeping all instructions in the user message.
        return {
            "model": model or self.model,
            "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
            "temperature": 0,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning_effort": "low",
            "include_reasoning": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            },
        }

    async def generate(
        self, system: str, prompt: str, schema: type[BaseModel], purpose: str
    ) -> LLMResponse:
        try:
            response = await self.client.post(
                API_URL, json=self.build_payload(system, prompt, schema)
            )
            # A long wait means this model's daily limit is used up, and limits are per model.
            wait = _retry_after(response) or 0
            if response.status_code == 429 and wait > MAX_RETRY_WAIT_SECONDS and self.fallback_model:
                response = await self.client.post(
                    API_URL, json=self.build_payload(system, prompt, schema, self.fallback_model)
                )
        except httpx.HTTPError as exc:
            raise TransientLLMError(type(exc).__name__) from exc

        if response.status_code == 429:
            raise TransientLLMError("429", _retry_after(response))
        if response.status_code >= 500:
            raise TransientLLMError(str(response.status_code))
        if response.status_code == 400 and "failed_generation" in response.text:
            # The model missed the schema, so treat it as a bad reply that can be retried.
            return LLMResponse(text="")
        if response.status_code != 200:
            raise LLMError(f"groq_{response.status_code}")

        try:
            data = response.json()
            text = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("groq_bad_response") from exc
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class LLMService:
    """The only way the pipeline reaches a model."""

    def __init__(
        self,
        client: LLMClient,
        repo: Repository,
        limits: Limits,
        backoff_seconds: float = 1.0,
    ):
        self.client = client
        self.repo = repo
        self.limits = limits
        self.backoff_seconds = backoff_seconds

    async def generate_structured(
        self,
        prompt: str,
        schema: type[T],
        purpose: str,
        audit_id: str,
        prompt_version: str,
    ) -> T:
        request = prompt
        for attempt in range(2):
            response = await self._call(request, schema, purpose, audit_id, prompt_version)
            try:
                return schema.model_validate_json(response.text)
            except ValidationError as exc:
                await self._record(audit_id, purpose, prompt_version, "invalid_output")
                if attempt == 1:
                    raise LLMError("invalid_output") from exc
                request = (
                    f"{prompt}\n\nYour last response failed validation:\n"
                    f"{exc.errors(include_url=False, include_input=False)}\n"
                    "Return corrected JSON only."
                )
        raise LLMError("invalid_output")

    async def _call(
        self,
        prompt: str,
        schema: type[BaseModel],
        purpose: str,
        audit_id: str,
        prompt_version: str,
    ) -> LLMResponse:
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                async with self.limits.llm:
                    response = await self.client.generate(
                        SYSTEM_RULES, prompt, schema, purpose
                    )
            except TransientLLMError as exc:
                await self._record(audit_id, purpose, prompt_version, "transient_error")
                wait = exc.retry_after
                if wait is None:
                    wait = self.backoff_seconds * 2**attempt
                if attempt == TRANSIENT_RETRIES or wait > MAX_RETRY_WAIT_SECONDS:
                    raise LLMError("unavailable") from exc
                await asyncio.sleep(wait)
                continue
            except LLMError:
                await self._record(audit_id, purpose, prompt_version, "error")
                raise
            await self._record(
                audit_id, purpose, prompt_version, "ok",
                response.input_tokens, response.output_tokens,
            )
            return response
        raise LLMError("unavailable")

    async def _record(
        self,
        audit_id: str,
        purpose: str,
        prompt_version: str,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        await self.repo.record_usage(
            audit_id, self.client.provider, purpose, status,
            model=self.client.model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
