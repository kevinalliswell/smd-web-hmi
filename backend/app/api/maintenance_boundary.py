"""Hold business-write admission until the complete ASGI request has finished."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.schemas import err
from app.services.maintenance_service import MaintenanceBlockedError, MaintenanceManager


class MaintenanceWriteBoundary:
    def __init__(self, app: ASGIApp, *, manager: MaintenanceManager) -> None:
        self.app, self.manager = app, manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Existing GET routes, including report downloads, only read. WS only
        # authenticates by SELECT and handles ping; no refresh/write channel exists.
        # New endpoints must keep these methods read-only. POST login/logout also
        # write account state and are intentionally paused; public health stays up.
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        try:
            release = self.manager.reserve_business_mutation()
        except MaintenanceBlockedError as exc:
            response = JSONResponse(
                status_code=503, content=err("maintenance_active", str(exc)), headers={"Retry-After": "5"}
            )
            await response(scope, receive, send)
            return
        try:
            # BaseHTTPMiddleware.call_next returns before dependency teardown and
            # response background tasks. Await the complete inner ASGI application.
            await self.app(scope, receive, send)
        finally:
            release()
