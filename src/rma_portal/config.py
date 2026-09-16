"""Runtime configuration.

Every path defaults under ``%LOCALAPPDATA%\\RMAPortal`` so the application
never writes inside the source checkout. Everything here is overridable via
environment variables (``RMA_PORTAL_*``) for tests and CI, which point
``RMA_PORTAL_DATA_DIR`` at a temporary directory.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / "RMAPortal"


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    host: str = "0.0.0.0"
    port: int = 8765
    poll_interval_seconds: int = 300
    session_lifetime_hours: int = 12
    session_verify_timeout_seconds: int = 45
    max_note_length: int = 2000
    omegaflow_base_url: str = "https://omegaflow.ma"
    omegaflow_start_route: str = "https://omegaflow.ma/#dossiers-en-instance-accord/"
    omegaflow_procedure_value: str = "5ed644a2faf17c0015d8c367"
    portal_timezone: str = "Africa/Casablanca"
    portal_locale: str = "fr-FR"
    headless_browser: bool = True
    session_cookie_name: str = "rma_portal_session"
    session_secret: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rma_portal.sqlite3"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def browser_lock_path(self) -> Path:
        return self.data_dir / "browser-profile.lock"

    @property
    def session_secret_path(self) -> Path:
        return self.data_dir / "session-secret.key"

    @property
    def session_state_path(self) -> Path:
        """Captured OmegaFlow browser session (cookies/localStorage/
        sessionStorage) -- see infrastructure.portal.session_state. Lives
        next to the browser profile, never inside the source checkout."""
        return self.data_dir / "omegaflow-session-state.json"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.browser_profile_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def load_or_create_session_secret(self) -> str:
        if self.session_secret:
            return self.session_secret
        path = self.session_secret_path
        if path.exists():
            secret = path.read_text(encoding="utf-8").strip()
            if secret:
                self.session_secret = secret
                return secret
        secret = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        self.session_secret = secret
        return secret


def load_settings() -> Settings:
    settings = Settings(
        data_dir=_env_path("RMA_PORTAL_DATA_DIR", _default_data_dir()),
        host=os.environ.get("RMA_PORTAL_HOST", "0.0.0.0"),
        port=_env_int("RMA_PORTAL_PORT", 8765),
        poll_interval_seconds=_env_int("RMA_PORTAL_POLL_INTERVAL_SECONDS", 300),
        session_lifetime_hours=_env_int("RMA_PORTAL_SESSION_LIFETIME_HOURS", 12),
        session_verify_timeout_seconds=_env_int("RMA_PORTAL_SESSION_VERIFY_TIMEOUT_SECONDS", 45),
        headless_browser=_env_bool("RMA_PORTAL_HEADLESS_BROWSER", True),
        session_secret=os.environ.get("RMA_PORTAL_SESSION_SECRET", ""),
    )
    return settings
