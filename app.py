"""FastAPI app factory and entry point.

Production (Docker):  uvicorn app:app --host 0.0.0.0 --port 7860
Local dev:            python app.py
"""
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import Settings, get_settings
from dependencies import AppState
from exceptions import BugEyeError
from middleware import SecurityHeadersMiddleware
from rag.vector_store import IndexRegistry
from routes.api import router

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None, registry: IndexRegistry | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="BugEye",
        description="Multi-agent AI code review for GitHub repositories",
        version="2.0.0",
    )
    app.state.bugeye = AppState.create(settings, registry)
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.include_router(router)
    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Every error response is JSON of the form {"error": "..."}."""

    @app.exception_handler(BugEyeError)
    async def bugeye_error(_request: Request, exc: BugEyeError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": _validation_message(exc)}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error on %s", request.url.path, exc_info=exc)
        return JSONResponse({"error": "Internal server error."}, status_code=500)


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    error = errors[0]
    message = str(error.get("msg", "Invalid value")).removeprefix("Value error, ")
    if error.get("type") == "value_error":
        return message  # Our own validators already write complete sentences
    field = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
    return f"{field}: {message}" if field else message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=get_settings().port)
