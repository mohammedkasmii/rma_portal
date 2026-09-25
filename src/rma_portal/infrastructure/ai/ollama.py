"""Ollama adapter for :class:`~rma_portal.application.ai.AIAdvisor`.

Configured by base URL, model and timeout. Data only ever leaves the process
towards that one endpoint, which must be local or on the private network:

* the URL must be ``http(s)`` and its host loopback, a private (RFC 1918/CGNAT/ULA) or link-local address,
  a single-label service name (``ollama``), ``host.docker.internal`` or a
  ``.local``/``.lan``/``.internal`` name -- a public host is refused at construction;
* redirects are never followed and environment proxies are ignored, so a response
  cannot bounce a request (and its context) somewhere else.

Answers are requested as structured JSON (Ollama's ``format`` = JSON schema) and
validated against the feature's pydantic model; anything else is an
``AiInvalidOutputError``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from rma_portal.application.ai import (
    RESULT_MODELS,
    AiContext,
    AiError,
    AiInvalidOutputError,
    AiTimeoutError,
    AiUnavailableError,
    AnomalyGroupsResult,
    DailySummaryResult,
    DossierSummaryResult,
    HighlightExplanationResult,
    PrioritySuggestionResult,
)
from rma_portal.domain.enums import AiFeature
from rma_portal.infrastructure.ai.prompts import SYSTEM_PROMPTS

logger = logging.getLogger(__name__)

MAX_CONTEXT_BYTES = 32_000
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".localdomain")
_LOCAL_NAMES = {"localhost", "host.docker.internal", "gateway.docker.internal"}
# Explicit private ranges: ``ipaddress.is_private`` would also accept documentation and
# other special-purpose blocks, which are not "the agency's own network".
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)


class OllamaConfigurationError(ValueError):
    """The configured endpoint is not a local Ollama URL."""


def validate_local_endpoint(base_url: str) -> str:
    """Return the normalised base URL, or raise if it could send data off the local network."""
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise OllamaConfigurationError("L'URL Ollama doit être une URL http(s) valide.")
    if parts.username or parts.password:
        raise OllamaConfigurationError("L'URL Ollama ne doit contenir aucun identifiant.")
    host = parts.hostname.lower()
    local = host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES) or "." not in host
    if not local:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            local = False
        else:
            local = (
                address.is_loopback
                or address.is_link_local
                or any(address in network for network in _PRIVATE_NETWORKS)
            )
    if not local:
        raise OllamaConfigurationError(
            "Ollama doit rester local : l'hôte configuré n'est ni loopback, ni privé, ni un nom de service interne."
        )
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


class OllamaAdvisor:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = validate_local_endpoint(base_url)
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    # --- features -----------------------------------------------------------------------------

    async def daily_summary(self, context: AiContext) -> DailySummaryResult:
        return await self._structured(context, DailySummaryResult)

    async def dossier_summary(self, context: AiContext) -> DossierSummaryResult:
        return await self._structured(context, DossierSummaryResult)

    async def explain_highlight(self, context: AiContext) -> HighlightExplanationResult:
        return await self._structured(context, HighlightExplanationResult)

    async def suggest_priority(self, context: AiContext) -> PrioritySuggestionResult:
        return await self._structured(context, PrioritySuggestionResult)

    async def group_anomalies(self, context: AiContext) -> AnomalyGroupsResult:
        return await self._structured(context, AnomalyGroupsResult)

    # --- transport ------------------------------------------------------------------------------

    async def _structured(self, context: AiContext, schema: type[BaseModel]) -> Any:
        feature: AiFeature = context.feature
        if RESULT_MODELS[feature] is not schema:  # a programming error, not a model error
            raise AiError(f"schéma inattendu pour {feature.value}")
        user_content = json.dumps(context.payload, ensure_ascii=False, sort_keys=True, default=str)
        if len(user_content.encode()) > MAX_CONTEXT_BYTES:
            raise AiError("Le contexte à analyser est trop volumineux.")
        body = {
            "model": self._model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS[feature]},
                {"role": "user", "content": user_content},
            ],
        }
        try:
            response = await self._client.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise AiTimeoutError("Ollama n'a pas répondu dans le délai imparti.") from exc
        except httpx.HTTPError as exc:
            raise AiUnavailableError(f"Ollama injoignable ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            # Includes 3xx: redirects are never followed.
            raise AiUnavailableError(f"Ollama a répondu HTTP {response.status_code}.")
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AiInvalidOutputError("Réponse Ollama sans contenu exploitable.") from exc
        try:
            return schema.model_validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise AiInvalidOutputError("La réponse ne respecte pas le schéma attendu.") from exc


def build_advisor(
    *, enabled: bool, base_url: str, model: str, timeout_seconds: float
) -> OllamaAdvisor | None:
    """The configured advisor, or ``None`` when AI is off or misconfigured (never raises)."""
    if not enabled:
        return None
    try:
        return OllamaAdvisor(base_url, model, timeout_seconds)
    except OllamaConfigurationError as exc:
        logger.warning("assistant local désactivé : %s", exc)
        return None
