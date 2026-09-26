"""Client for the browser container's private login control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class BrowserControlError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserControlResult:
    state: str
    started: bool
    result: str | None = None


class BrowserControlClient:
    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    def start_login(self) -> BrowserControlResult:
        request = Request(
            f"{self._base_url}/login",
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                payload = json.load(response)
        except HTTPError as exc:
            raise BrowserControlError(f"browser control returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise BrowserControlError(type(exc).__name__) from None
        state = payload.get("state")
        if state != "CONNECTING":
            raise BrowserControlError("unexpected browser control response")
        return BrowserControlResult(
            state=state, started=payload.get("started") is True, result=payload.get("result")
        )
