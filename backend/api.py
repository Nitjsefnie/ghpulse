"""Public FastAPI read routes for ghpulse."""
from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from backend.api_common import _iso, read_transaction, utc_now
from backend.api_dashboard import (
    _event_repository_ids,
    build_window,
)
from backend.api_dashboard import (
    router as dashboard_router,
)
from backend.cache import cache_response

router = APIRouter(prefix="/api")
router.include_router(dashboard_router)


@router.get("/me")
def me(request: Request) -> dict:
    """Return the middleware-resolved viewer identity, never credentials."""
    return {
        "user_id": getattr(request.state, "user_id", None),
        "is_guest": bool(getattr(request.state, "is_guest", False)),
    }


def _repository_options(rng: str) -> dict:
    generated_at = _iso(utc_now())
    with read_transaction() as connection:
        window = build_window(rng, connection=connection)
        ids = _event_repository_ids(connection, window, None)
        if not ids:
            return {"range": rng, "repositories": [], "generated_at": generated_at}
        rows = connection.execute(
            """
            SELECT node_id, name_with_owner, url
            FROM repositories
            WHERE node_id = ANY(%s)
              AND is_private IS FALSE AND is_external IS TRUE
            ORDER BY name_with_owner
            """,
            (ids,),
        ).fetchall()
        return {
            "range": rng,
            "repositories": [
                {"node_id": node_id, "name_with_owner": name, "url": url}
                for node_id, name, url in rows
            ],
            "generated_at": generated_at,
        }


@cache_response
def repositories(
    rng: str = Query("30d", alias="range"),
    visibility: str = "guest",
    fresh: int = Query(0),
) -> dict:
    """List public external repositories represented in the selected range."""
    del visibility
    del fresh
    return _repository_options(rng)


@router.get("/repositories")
def repositories_route(
    request: Request,
    rng: str = Query("30d", alias="range"),
    fresh: int = Query(0),
) -> dict:
    """Serve public external repositories for the selected range."""
    visibility = "guest" if bool(getattr(request.state, "is_guest", False)) else "authenticated"
    return repositories(rng=rng, visibility=visibility, fresh=fresh)
