"""Structured operational logging: rotating file + console handlers, a
correlation ID for every OmegaFlow connection attempt/synchronization, and
small START/END stage helpers built on ``time.perf_counter()``.

Instrumentation only -- nothing here changes waits, selectors, retries,
session handling or concurrency. Never log passwords, cookies, tokens,
storage values, request bodies, customer names, dossier numbers, HTML, or
URLs with query parameters -- see ``strip_query`` for the one call site that
previously logged a raw URL.
"""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import urlsplit

from rma_portal.config import Settings

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s operation_id=%(operation_id)s %(message)s"

_configured = False

_operation_id: contextvars.ContextVar[str] = contextvars.ContextVar("operation_id", default="-")


class _CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.operation_id = _operation_id.get()
        return True


def configure_logging(settings: Settings, *, level: int = logging.INFO) -> None:
    """Installs a console handler (visible alongside Uvicorn's own output --
    Uvicorn configures only its own ``uvicorn``/``uvicorn.access`` loggers,
    never the root logger) and a UTF-8 rotating file handler under
    ``settings.log_dir`` on the root logger.

    Guarded by a module-level flag so calling this more than once (e.g. a
    second ``build_application`` in tests) never installs duplicate
    handlers, which is what the "avoid duplicate messages" requirement
    means in practice. Third-party loggers (Camoufox/Playwright included)
    are left at their own default verbosity -- never turned up here.
    """
    global _configured
    if _configured:
        return
    _configured = True

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_LOG_FORMAT)
    correlation_filter = _CorrelationIdFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(correlation_filter)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / "rma-portal.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(correlation_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)


def new_operation_id(prefix: str) -> str:
    """A short correlation ID, e.g. ``sync-3f9a1c2b`` -- unique enough to
    grep for in a rotated log file without being a full UUID."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@contextmanager
def operation_context(operation_id: str) -> Generator[str]:
    """Binds ``operation_id`` to every log record emitted within this
    block, and to any ``asyncio.create_task`` started inside it (asyncio
    copies the current contextvars context at task-creation time) --
    restores whatever was bound before on exit.
    """
    token = _operation_id.set(operation_id)
    try:
        yield operation_id
    finally:
        _operation_id.reset(token)


@contextmanager
def ensure_operation_context(prefix: str) -> Generator[str]:
    """Like :func:`operation_context`, but only mints a new ID when none is
    already bound -- lets a stage that is sometimes reached standalone and
    sometimes as part of a larger operation keep the caller's correlation
    ID instead of starting a new one mid-operation.
    """
    current = _operation_id.get()
    if current != "-":
        yield current
        return
    with operation_context(new_operation_id(prefix)) as operation_id:
        yield operation_id


def current_operation_id() -> str:
    return _operation_id.get()


@contextmanager
def log_stage(logger: logging.Logger, stage: str, **fields: object) -> Generator[None]:
    """Logs START before the wrapped block and END afterward -- OK or
    FAILED, always with the elapsed time in milliseconds
    (``time.perf_counter()``) -- so a stalled stage is identifiable from a
    START with no matching END.

    Never suppresses the wrapped exception. On failure, logs the exception
    type and traceback (``exc_info=True``, which never includes local
    variables and preserves the original ``__cause__``/``__context__``
    chain) so the failed stage is diagnosable from this one line.
    ``**fields`` are appended to every line -- only pass values already
    known to be safe (never passwords, cookies, tokens, storage values,
    request bodies, customer names, dossier numbers, HTML or URLs with
    query parameters).
    """
    suffix = "".join(f" {key}={value}" for key, value in fields.items())
    started = time.perf_counter()
    logger.info("stage=%s outcome=START%s", stage, suffix)
    try:
        yield
    except BaseException as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "stage=%s outcome=FAILED elapsed_ms=%.1f exception_type=%s%s",
            stage,
            elapsed_ms,
            type(exc).__name__,
            suffix,
            exc_info=True,
        )
        raise
    else:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("stage=%s outcome=OK elapsed_ms=%.1f%s", stage, elapsed_ms, suffix)


def strip_query(url: str) -> str:
    """Drops the query string (and fragment) from ``url`` -- the one thing
    safe to log about an OmegaFlow request URL; query parameters can carry
    record/dossier identifiers and must never be logged."""
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        return parts.path
    return f"{parts.scheme}://{parts.netloc}{parts.path}"
