"""``python -m rma_poc {login,verify,status}`` -- see docs/ubuntu-browser-poc.md.

Every command prints one final ``RESULT=<NAME>`` line and exits with the
matching ``ExitCode``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from rma_portal.infrastructure.portal.session_state import load_session_state

from .common import ExitCode, PocConfig, configure_logging
from .doctor import collect_checks, ready
from .login import run_login
from .verify import run_verify

logger = logging.getLogger("rma_poc")

# Grace on top of the command's own bounded waits (browser start + close).
_OVERALL_GRACE_SECONDS = 150.0


def _status(cfg: PocConfig) -> ExitCode:
    """Count-only summary of the saved state -- never names or values."""
    state = load_session_state(cfg.state_path)
    profile_present = cfg.profile_dir.is_dir() and any(cfg.profile_dir.iterdir())
    summary: dict[str, object] = {
        "state_file": state is not None,
        "profile_present": profile_present,
    }
    if state is not None:
        summary.update(
            captured_at=state.get("captured_at"),
            size_bytes=cfg.state_path.stat().st_size,
            cookies=len(state.get("cookies", [])),
            local_storage=sum(len(o.get("localStorage", [])) for o in state.get("origins", [])),
            session_storage=len(state.get("session_storage", {}).get("items", {})),
        )
    print(json.dumps(summary, sort_keys=True))
    return ExitCode.OK if state is not None else ExitCode.AUTH_REQUIRED


def _doctor(cfg: PocConfig) -> ExitCode:
    checks = collect_checks(
        base_url=cfg.base_url, profile_dir=cfg.profile_dir, state_path=cfg.state_path
    )
    for check in checks:
        print(check.line())
    return ExitCode.OK if ready(checks) else ExitCode.NOT_READY


async def _guarded(coro, overall_timeout_s: float) -> ExitCode:
    try:
        return await asyncio.wait_for(coro, timeout=overall_timeout_s)
    except TimeoutError, PlaywrightTimeoutError:
        logger.error("bounded wait expired")
        return ExitCode.TIMEOUT
    except Exception as exc:  # noqa: BLE001 - type only; messages can echo page details
        logger.error("browser error: %s", type(exc).__name__)
        return ExitCode.BROWSER_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rma_poc")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="visible login + detect + atomic capture")
    login.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for login")
    verify = sub.add_parser("verify", help="headless verification of the saved session")
    verify.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for READY")
    verify.add_argument(
        "--fresh-profile",
        action="store_true",
        help="use a throw-away browser profile (proves the state file alone suffices)",
    )
    sub.add_parser("status", help="count-only summary of the saved state")
    sub.add_parser("doctor", help="read-only infrastructure diagnostics (no browser launch)")
    args = parser.parse_args(argv)

    cfg = PocConfig.from_env()
    configure_logging(cfg.log_path)

    if args.command == "status":
        code = _status(cfg)
    elif args.command == "doctor":
        code = _doctor(cfg)
    elif args.command == "login":
        code = asyncio.run(
            _guarded(run_login(cfg, timeout_s=args.timeout), args.timeout + _OVERALL_GRACE_SECONDS)
        )
    else:
        code = asyncio.run(
            _guarded(
                run_verify(cfg, timeout_s=args.timeout, fresh_profile=args.fresh_profile),
                args.timeout + _OVERALL_GRACE_SECONDS,
            )
        )

    ok_names = {"login": "CAPTURED", "verify": "READY", "status": "PRESENT", "doctor": "READY"}
    print(f"RESULT={ok_names[args.command] if code is ExitCode.OK else code.name}")
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
