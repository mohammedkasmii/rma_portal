"""Ollama adapter: local-only endpoint, structured JSON, and every failure mode."""

from __future__ import annotations

import json

import httpx
import pytest

from rma_portal.application.ai import (
    AiContext,
    AiError,
    AiInvalidOutputError,
    AiTimeoutError,
    AiUnavailableError,
)
from rma_portal.domain.enums import AiFeature
from rma_portal.infrastructure.ai.ollama import (
    MAX_CONTEXT_BYTES,
    OllamaAdvisor,
    OllamaConfigurationError,
    build_advisor,
    validate_local_endpoint,
)

CONTEXT = AiContext(AiFeature.DAILY_SUMMARY, {"workflows": [{"nom": "File test", "actifs": 3}]})


def _advisor(handler, base_url: str = "http://ollama:11434", timeout: float = 5.0) -> OllamaAdvisor:
    return OllamaAdvisor(base_url, "qwen3:8b", timeout, transport=httpx.MockTransport(handler))


def _reply(content: str | dict, status: int = 200) -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(status, json={"message": {"role": "assistant", "content": body}})


VALID_SUMMARY = {"headline": "Charge normale.", "highlights": ["3 dossiers actifs"], "attention": []}


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://192.168.1.20:11434",
        "http://10.0.0.5:11434",
        "http://172.20.0.4:11434",
        "http://ollama:11434",
        "http://host.docker.internal:11434",
        "http://gpu-box.lan:11434",
        "http://ai.agency.local",
        "https://ollama.internal:11434/",
    ],
)
def test_local_and_private_endpoints_are_accepted(url):
    assert validate_local_endpoint(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com",
        "http://ollama.example.com:11434",
        "http://8.8.8.8:11434",
        "http://203.0.113.9:11434",
        "ftp://localhost:11434",
        "localhost:11434",
        "http://user:secret@localhost:11434",
        "",
    ],
)
def test_public_or_malformed_endpoints_are_refused(url):
    with pytest.raises(OllamaConfigurationError):
        validate_local_endpoint(url)


def test_build_advisor_is_none_when_disabled_or_misconfigured_and_never_raises():
    assert build_advisor(enabled=False, base_url="http://ollama:11434", model="m", timeout_seconds=5) is None
    assert build_advisor(enabled=True, base_url="https://api.openai.com", model="m", timeout_seconds=5) is None
    advisor = build_advisor(enabled=True, base_url="http://ollama:11434", model="m", timeout_seconds=5)
    assert advisor is not None and advisor.model == "m"


@pytest.mark.asyncio
async def test_structured_request_goes_only_to_the_configured_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _reply(VALID_SUMMARY)

    result = await _advisor(handler).daily_summary(CONTEXT)

    assert result.headline == "Charge normale."
    (request,) = seen
    assert (request.url.scheme, request.url.host, request.url.port, request.url.path) == (
        "http", "ollama", 11434, "/api/chat",
    )
    body = json.loads(request.content)
    assert body["model"] == "qwen3:8b" and body["stream"] is False
    assert body["format"]["type"] == "object" and "headline" in body["format"]["properties"]
    assert body["options"]["temperature"] == 0.2
    system, user = body["messages"]
    assert system["role"] == "system" and "n'invente aucun fait" in system["content"]
    assert json.loads(user["content"]) == CONTEXT.payload  # exactly the minimal context, nothing else
    assert "authorization" not in {k.lower() for k in request.headers}


@pytest.mark.asyncio
async def test_a_redirect_is_never_followed_so_data_cannot_leave_the_endpoint():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/collect"})

    with pytest.raises(AiUnavailableError, match="HTTP 302"):
        await _advisor(handler).daily_summary(CONTEXT)

    assert seen == ["http://ollama:11434/api/chat"]


@pytest.mark.asyncio
async def test_each_feature_validates_against_its_own_schema():
    contexts = {
        "dossier_summary": (AiFeature.DOSSIER_SUMMARY, {"summary": "Ok.", "key_points": [], "open_questions": []}),
        "explain_highlight": (AiFeature.HIGHLIGHT_EXPLANATION, {"explanation": "Nouveau.", "facts_used": ["a"]}),
        "suggest_priority": (
            AiFeature.PRIORITY_SUGGESTION,
            {"priority": "HAUTE", "next_action": "Ouvrir.", "rationale": "Nouveau."},
        ),
        "group_anomalies": (
            AiFeature.ANOMALY_GROUPING,
            {"groups": [{"title": "G", "summary": "S", "anomaly_ids": ["old-1"]}]},
        ),
    }
    for method, (feature, answer) in contexts.items():
        advisor = _advisor(lambda request, answer=answer: _reply(answer))
        result = await getattr(advisor, method)(AiContext(feature, {"x": 1}))
        assert result.model_dump()  # parsed into the feature's own model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "pas du json",
        '{"headline": ""}',
        '{"headline": "ok", "highlights": ["a","b","c","d","e","f","g","h","i"]}',
        '{"unknown": true}',
        "[]",
    ],
)
async def test_malformed_or_schema_violating_output_is_invalid(content):
    with pytest.raises(AiInvalidOutputError):
        await _advisor(lambda request: _reply(content)).daily_summary(CONTEXT)


@pytest.mark.asyncio
async def test_priority_must_be_one_of_the_allowed_values():
    advisor = _advisor(lambda request: _reply({"priority": "URGENT", "next_action": "x", "rationale": "y"}))

    with pytest.raises(AiInvalidOutputError):
        await advisor.suggest_priority(AiContext(AiFeature.PRIORITY_SUGGESTION, {}))


@pytest.mark.asyncio
async def test_response_without_a_message_is_invalid():
    with pytest.raises(AiInvalidOutputError):
        await _advisor(lambda request: httpx.Response(200, json={"done": True})).daily_summary(CONTEXT)
    with pytest.raises(AiInvalidOutputError):
        await _advisor(lambda request: httpx.Response(200, text="<html>")).daily_summary(CONTEXT)


@pytest.mark.asyncio
async def test_timeouts_and_connection_errors_are_distinct_soft_failures():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AiTimeoutError):
        await _advisor(slow).daily_summary(CONTEXT)
    with pytest.raises(AiUnavailableError):
        await _advisor(refused).daily_summary(CONTEXT)
    with pytest.raises(AiUnavailableError, match="HTTP 500"):
        await _advisor(lambda request: httpx.Response(500)).daily_summary(CONTEXT)
    with pytest.raises(AiUnavailableError, match="HTTP 404"):
        await _advisor(lambda request: httpx.Response(404, json={"error": "model not found"})).daily_summary(CONTEXT)


@pytest.mark.asyncio
async def test_oversized_context_is_refused_before_anything_is_sent():
    sent = []

    def handler(request):
        sent.append(request)
        return _reply(VALID_SUMMARY)

    huge = AiContext(AiFeature.DAILY_SUMMARY, {"blob": "x" * (MAX_CONTEXT_BYTES + 1)})

    with pytest.raises(AiError, match="trop volumineux"):
        await _advisor(handler).daily_summary(huge)

    assert sent == []


@pytest.mark.asyncio
async def test_ping_reports_reachability_without_raising():
    assert await _advisor(lambda request: httpx.Response(200, json={"models": []})).ping() is True
    assert await _advisor(lambda request: httpx.Response(503)).ping() is False

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert await _advisor(refused).ping() is False


def test_unknown_public_host_cannot_be_reached_even_through_the_constructor():
    with pytest.raises(OllamaConfigurationError):
        OllamaAdvisor("https://ollama.example.org", "m", 5.0)
