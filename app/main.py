import asyncio
import contextlib
import logging
import mimetypes
import traceback
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.routes import router
from app.core import config, limits
from app.core.errors import UserError
from app.services.jobs import jobs
from app.services.store import store

logger = logging.getLogger(__name__)

# Only this site's own files may load or run. No inline script or style, no
# third-party hosts, no framing, no plugins.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    # Ignored over plain http (local runs); enforced once served over https.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def sweep() -> None:
    """Drop every upload, result and rate-limit record that is past its time."""
    store.purge()
    jobs.purge()
    limits.limiter.sweep()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async def sweeper() -> None:
        while True:
            await asyncio.sleep(config.settings.sweep_seconds)
            sweep()

    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()


# Interactive docs are off: nothing public should describe the API surface.
app = FastAPI(
    title="Sentiment Lens", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)
app.include_router(router)


def _error(message: str, status: int, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"error": message}, status_code=status, headers=headers)


@app.exception_handler(UserError)
async def user_error(_: Request, exc: UserError) -> JSONResponse:
    return _error(exc.message, exc.status, exc.retry_after)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
    # The default body echoes the caller's input back; keep it generic instead.
    return _error("The request wasn't understood.", 400)


def _cache_policy(path: str) -> str:
    if path.startswith("/static/fonts/"):
        return "public, max-age=31536000, immutable"
    if path == "/" or path.startswith("/static/"):
        return "no-cache"  # keep a copy, but check it is current before using it
    return "no-store"  # API replies can hold uploaded text; never cache them


@app.middleware("http")
async def guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Rate limits, a request ID, safe error handling and security headers for every reply.

    Unhandled errors are caught here rather than in an exception handler because
    Starlette re-raises after an `Exception` handler and the server then logs the
    full message. Messages can quote uploaded cell values, so only the error type
    and code locations are logged, next to the request ID the user is shown.
    """
    request_id = uuid.uuid4().hex[:12]
    path = request.url.path
    response: Response
    rule = limits.rule_for(request.method, path)
    wait = (
        limits.limiter.check(
            f"{rule.name}:{limits.client_address(request)}", rule.limit, rule.window
        )
        if rule
        else None
    )
    if rule and wait is not None:
        response = _error(f"{rule.message} {limits.wait_phrase(wait)}", 429, wait)
    else:
        try:
            response = await call_next(request)
        except Exception as exc:
            where = " <- ".join(
                f"{frame.name} ({frame.filename.rsplit('site-packages', 1)[-1]}:{frame.lineno})"
                for frame in reversed(traceback.extract_tb(exc.__traceback__)[-6:])
            )
            logger.error("request %s: unhandled %s at %s", request_id, type(exc).__name__, where)
            response = JSONResponse(
                {
                    "error": "Something went wrong on our side. Please try again.",
                    "request_id": request_id,
                },
                status_code=500,
            )
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("Cache-Control", _cache_policy(path))
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Windows has no registered type for .woff2 and would serve fonts as octet-stream.
mimetypes.add_type("font/woff2", ".woff2")
_STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
