"""``rma-portal`` command-line entry points, used by the Windows .bat scripts."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from rma_portal.bootstrap import build_application
from rma_portal.domain.enums import Role
from rma_portal.domain.models import DuplicateUsernameError
from rma_portal.infrastructure.security.passwords import WeakPasswordError


def _cmd_serve(_: argparse.Namespace) -> int:
    import uvicorn

    from rma_portal.web.app import create_app

    application = build_application()
    app = create_app(application)
    uvicorn.run(
        app,
        host=application.settings.host,
        port=application.settings.port,
        workers=1,
        log_level="info",
    )
    return 0


def _cmd_create_admin(args: argparse.Namespace) -> int:
    application = build_application()
    username = args.username or input("Identifiant administrateur : ").strip()
    display_name = args.display_name or input("Nom affiché : ").strip()
    password = args.password or getpass.getpass("Mot de passe (10 caractères minimum) : ")
    confirm = args.password or getpass.getpass("Confirmez le mot de passe : ")
    if password != confirm:
        print("Les mots de passe ne correspondent pas.", file=sys.stderr)
        return 1
    try:
        application.account_service.create_user(
            username=username, display_name=display_name, password=password, role=Role.ADMIN
        )
    except DuplicateUsernameError:
        print(f"L'identifiant '{username}' existe déjà.", file=sys.stderr)
        return 1
    except WeakPasswordError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Administrateur '{username}' créé avec succès.")
    return 0


def _cmd_configure_session(_: argparse.Namespace) -> int:
    from rma_portal.infrastructure.portal.session_setup import run_session_setup

    application = build_application()
    asyncio.run(run_session_setup(application.settings))
    return 0


def _cmd_migrate(_: argparse.Namespace) -> int:
    from alembic.config import Config

    from alembic import command
    from rma_portal.config import load_settings

    settings = load_settings()
    settings.ensure_directories()
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    print(f"Migrations appliquées : {settings.db_path}")
    return 0


def _cmd_import_sqlite(args: argparse.Namespace) -> int:
    from pathlib import Path

    from rma_portal.config import load_settings
    from rma_portal.infrastructure.db.session import create_engine_for
    from rma_portal.infrastructure.db.sqlite_import import (
        SqliteImportError,
        import_sqlite_database,
        upgrade_to_head,
    )

    settings = load_settings()
    if settings.uses_sqlite:
        print(
            "RMA_PORTAL_DATABASE_URL doit désigner la base PostgreSQL de destination.",
            file=sys.stderr,
        )
        return 1
    upgrade_to_head(settings.database_url)
    engine = create_engine_for(settings)
    try:
        report = import_sqlite_database(Path(args.source), engine)
    except SqliteImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    for label, values in (
        ("insérées", report.inserted),
        ("mises à jour", report.updated),
        ("déjà présentes", report.skipped),
    ):
        for table, count in sorted(values.items()):
            print(f"{table}: {count} ligne(s) {label}")
    print("Import terminé. Relancer la commande est sans danger.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rma-portal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Démarre le serveur web (0.0.0.0:8765).")
    serve.set_defaults(func=_cmd_serve)

    create_admin = subparsers.add_parser("create-admin", help="Crée un compte administrateur.")
    create_admin.add_argument("--username")
    create_admin.add_argument("--display-name")
    create_admin.add_argument("--password")
    create_admin.set_defaults(func=_cmd_create_admin)

    configure_session = subparsers.add_parser(
        "configure-session", help="Ouvre un navigateur visible pour la connexion OmegaFlow."
    )
    configure_session.set_defaults(func=_cmd_configure_session)

    migrate = subparsers.add_parser("migrate", help="Applique les migrations Alembic (upgrade head).")
    migrate.set_defaults(func=_cmd_migrate)

    import_sqlite = subparsers.add_parser(
        "import-sqlite",
        help="Importe (de façon idempotente) l'ancienne base SQLite dans PostgreSQL.",
    )
    import_sqlite.add_argument("--source", required=True, help="Chemin de rma_portal.sqlite3")
    import_sqlite.set_defaults(func=_cmd_import_sqlite)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
