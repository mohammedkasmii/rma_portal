"""Versioned JSON API (``/api/v1``) consumed by the React client."""

from __future__ import annotations

from fastapi import APIRouter

from rma_portal.web.api import auth, operations, workspace

router = APIRouter(prefix="/api/v1")


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


router.include_router(auth.router)
router.include_router(workspace.router)
router.include_router(operations.router)
