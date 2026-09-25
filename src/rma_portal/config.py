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
    max_detail_reads_per_cycle: int = 60
    portal_timezone: str = "Africa/Casablanca"
    portal_locale: str = "fr-FR"
    headless_browser: bool = True
    session_cookie_name: str = "rma_portal_session"
    session_secret: str = ""
    novnc_url: str = ""
    """Where an administrator reconnects the OmegaFlow session (the noVNC desktop)."""
    allowed_origins: tuple[str, ...] = ()
    """Extra origins accepted by the same-origin mutation check (behind a proxy)."""
    cookie_secure: bool = False
    run_scheduler: bool = True
    """False for the API process of the production stack: the worker owns polling."""
    ollama_enabled: bool = False
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: float = 45.0
    browser_profile_dir_override: str = ""
    browser_lock_path_override: str = ""
    session_state_path_override: str = ""
    log_dir_override: str = ""
    """Production containers keep the browser profile and captured session on their own
    volumes, shared between the browser desktop and the worker."""
    database_url_override: str = ""
    """Full SQLAlchemy URL (``RMA_PORTAL_DATABASE_URL``). Empty means the
    legacy single-file SQLite database under ``data_dir``; the production
    Docker deployment sets a PostgreSQL URL."""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rma_portal.sqlite3"

    @property
    def database_url(self) -> str:
        return self.database_url_override or f"sqlite:///{self.db_path.as_posix()}"

    @property
    def uses_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def browser_profile_dir(self) -> Path:
        if self.browser_profile_dir_override:
            return Path(self.browser_profile_dir_override)
        return self.data_dir / "browser-profile"

    @property
    def browser_lock_path(self) -> Path:
        if self.browser_lock_path_override:
            return Path(self.browser_lock_path_override)
        return self.data_dir / "browser-profile.lock"

    @property
    def session_secret_path(self) -> Path:
        return self.data_dir / "session-secret.key"

    @property
    def session_state_path(self) -> Path:
        """Captured OmegaFlow browser session (cookies/localStorage/
        sessionStorage) -- see infrastructure.portal.session_state. Lives
        next to the browser profile, never inside the source checkout."""
        if self.session_state_path_override:
            return Path(self.session_state_path_override)
        return self.data_dir / "omegaflow-session-state.json"

    @property
    def log_dir(self) -> Path:
        if self.log_dir_override:
            return Path(self.log_dir_override)
        return self.data_dir / "logs"

    @property
    def worker_heartbeat_path(self) -> Path:
        return self.data_dir / "worker.heartbeat"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.browser_profile_dir,
            self.log_dir,
            self.session_state_path.parent,
        ):
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
    defaults = Settings()
    settings = Settings(
        data_dir=_env_path("RMA_PORTAL_DATA_DIR", _default_data_dir()),
        host=os.environ.get("RMA_PORTAL_HOST", "0.0.0.0"),
        port=_env_int("RMA_PORTAL_PORT", 8765),
        poll_interval_seconds=_env_int("RMA_PORTAL_POLL_INTERVAL_SECONDS", 300),
        omegaflow_base_url=os.environ.get("RMA_PORTAL_OMEGAFLOW_BASE_URL", defaults.omegaflow_base_url),
        omegaflow_start_route=os.environ.get(
            "RMA_PORTAL_OMEGAFLOW_START_ROUTE", defaults.omegaflow_start_route
        ),
        portal_timezone=os.environ.get("RMA_PORTAL_TIMEZONE", defaults.portal_timezone),
        max_detail_reads_per_cycle=_env_int("RMA_PORTAL_MAX_DETAIL_READS_PER_CYCLE", 60),
        session_lifetime_hours=_env_int("RMA_PORTAL_SESSION_LIFETIME_HOURS", 12),
        session_verify_timeout_seconds=_env_int("RMA_PORTAL_SESSION_VERIFY_TIMEOUT_SECONDS", 45),
        headless_browser=_env_bool("RMA_PORTAL_HEADLESS_BROWSER", True),
        session_secret=os.environ.get("RMA_PORTAL_SESSION_SECRET", ""),
        database_url_override=os.environ.get("RMA_PORTAL_DATABASE_URL", ""),
        browser_profile_dir_override=os.environ.get("RMA_PORTAL_BROWSER_PROFILE_DIR", ""),
        browser_lock_path_override=os.environ.get("RMA_PORTAL_BROWSER_LOCK_PATH", ""),
        session_state_path_override=os.environ.get("RMA_PORTAL_SESSION_STATE_PATH", ""),
        log_dir_override=os.environ.get("RMA_PORTAL_LOG_DIR", ""),
        novnc_url=os.environ.get("RMA_PORTAL_NOVNC_URL", ""),
        allowed_origins=tuple(
            origin.strip().rstrip("/")
            for origin in os.environ.get("RMA_PORTAL_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        ),
        cookie_secure=_env_bool("RMA_PORTAL_COOKIE_SECURE", False),
        run_scheduler=_env_bool("RMA_PORTAL_RUN_SCHEDULER", True),
        ollama_enabled=_env_bool("RMA_PORTAL_OLLAMA_ENABLED", False),
        ollama_base_url=os.environ.get("RMA_PORTAL_OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        ollama_model=os.environ.get("RMA_PORTAL_OLLAMA_MODEL", "qwen3:8b"),
        ollama_timeout_seconds=float(os.environ.get("RMA_PORTAL_OLLAMA_TIMEOUT_SECONDS", "45")),
    )
    return settings
