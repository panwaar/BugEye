"""FastAPI dependencies: shared app state, access-token auth and per-client rate limits."""
import hmac
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from config import Settings
from exceptions import BugEyeError
from rag.vector_store import IndexRegistry


class RateLimiter:
    """In-memory sliding window: at most `limit` hits per `window` seconds per key.

    A limit of 0 or less disables limiting. State is per process, which is fine
    because the app runs as a single worker (the vector indexes live in memory too).
    """

    _PRUNE_EVERY = 1000

    def __init__(self, limit: int, window: float = 3600.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % self._PRUNE_EVERY == 0:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _prune(self, now: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= now - self.window]
        for key in stale:
            del self._hits[key]


@dataclass
class AppState:
    """Services shared by all requests. One instance per app, so tests can build isolated apps.

    Everything lives in process memory, so run a single worker.
    """

    settings: Settings
    registry: IndexRegistry
    review_limiter: RateLimiter
    chat_limiter: RateLimiter
    review_slots: threading.BoundedSemaphore

    @classmethod
    def create(cls, settings: Settings, registry: IndexRegistry | None = None) -> "AppState":
        return cls(
            settings=settings,
            registry=registry if registry is not None else IndexRegistry(settings.max_indexed_repos),
            review_limiter=RateLimiter(settings.review_limit_per_hour),
            chat_limiter=RateLimiter(settings.chat_limit_per_hour),
            review_slots=threading.BoundedSemaphore(max(1, settings.max_concurrent_reviews)),
        )


def get_state(request: Request) -> AppState:
    return request.app.state.bugeye


# ── Auth ─────────────────────────────────────────────────────

access_token_header = APIKeyHeader(name="X-Access-Token", auto_error=False)


def require_access_token(request: Request, provided: str | None = Security(access_token_header)) -> None:
    """When ACCESS_TOKEN is configured, reject requests that don't send it."""
    expected = get_state(request).settings.access_token
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise BugEyeError("A valid access token is required.", status_code=401)


# ── Rate limits ──────────────────────────────────────────────

def client_ip(request: Request, proxy_hops: int) -> str:
    """The caller's IP address.

    Behind `proxy_hops` trusted reverse proxies, it's read from X-Forwarded-For counting
    from the right — entries further left are supplied by the client and can be forged.
    """
    if proxy_hops > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(forwarded) >= proxy_hops:
            return forwarded[-proxy_hops]
    return request.client.host if request.client else "unknown"


def review_rate_limit(request: Request) -> None:
    state = get_state(request)
    if not state.review_limiter.allow(client_ip(request, state.settings.proxy_hops)):
        raise BugEyeError("Too many analyses from your address. Try again later.", status_code=429)


def chat_rate_limit(request: Request) -> None:
    state = get_state(request)
    if not state.chat_limiter.allow(client_ip(request, state.settings.proxy_hops)):
        raise BugEyeError("Too many questions from your address. Try again later.", status_code=429)
