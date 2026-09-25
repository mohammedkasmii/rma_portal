"""Session-state detection for rendered OmegaFlow pages.

No Playwright import here: every function takes already-rendered HTML, so
these are exercised directly against synthetic fixtures in ``tests/parser``
without a browser. Queue and detail parsing is generic and lives in
``workflow_parser``; see docs/omegaflow-contract.md for the captured markup.
"""

from __future__ import annotations

import re


class PortalMarkupError(ValueError):
    """The page did not contain the markup this parser depends on."""


_LOGIN_FORM_MARKERS = re.compile(r"type=[\"']password[\"']", re.IGNORECASE)
_SESSION_VALIDATION_MARKERS = ("refreshsession", "validate your session", "valider votre session")


def detect_auth_required(html: str) -> bool:
    """True when the login form or the session-revalidation screen is shown.

    OmegaFlow (Knack) has two distinct unauthenticated states: a real login
    form (``#email`` + a password input) and a session-revalidation panel
    (``#refreshSession`` button, no credential fields). Both must be
    treated as AUTH_REQUIRED.
    """
    lowered = html.casefold()
    if _LOGIN_FORM_MARKERS.search(html):
        return True
    return any(marker in lowered for marker in _SESSION_VALIDATION_MARKERS)
