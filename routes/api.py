"""HTTP routes: the web page, the streaming review endpoint and codebase chat."""
import json
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from agents.chat_agent import answer_question
from agents.orchestrator import run_review
from dependencies import AppState, chat_rate_limit, get_state, review_rate_limit
from exceptions import BugEyeError
from services.github_service import parse_repo

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
router = APIRouter()

MAX_QUESTION_CHARS = 2000


# ── Request / response models ────────────────────────────────

def _validate_repo(value: str) -> str:
    try:
        return parse_repo(value)
    except BugEyeError as e:
        raise ValueError(str(e)) from None


class ReviewRequest(BaseModel):
    repo: str = Field(max_length=300, description="'owner/repo' or a github.com URL")
    pr: int | None = Field(default=None, description="Optional pull request number to review")

    check_repo = field_validator("repo")(_validate_repo)

    @field_validator("pr", mode="before")
    @classmethod
    def _validate_pr(cls, value):
        if value is None or str(value).strip() == "":
            return None
        try:
            number = int(str(value).strip().lstrip("#"))
        except ValueError:
            raise ValueError("PR number must be a positive integer.") from None
        if number <= 0:
            raise ValueError("PR number must be a positive integer.")
        return number


class ChatRequest(BaseModel):
    repo: str = Field(max_length=300, description="The analysed repository")
    question: str = Field(max_length=MAX_QUESTION_CHARS)

    check_repo = field_validator("repo")(_validate_repo)

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Please provide a question.")
        return value


class ChatResponse(BaseModel):
    answer: str


# ── Routes ───────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.post(
    "/api/review",
    dependencies=[Depends(review_rate_limit)],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent progress events"}},
)
def review(body: ReviewRequest, state: AppState = Depends(get_state)) -> StreamingResponse:
    """Index the repository and run the agent pipeline, streaming progress as server-sent events."""
    if not state.review_slots.acquire(blocking=False):
        raise BugEyeError("The server is busy with other analyses. Try again in a minute.", status_code=503)

    release = _release_once(state.review_slots)
    events = run_review(body.repo, body.pr, settings=state.settings, registry=state.registry,
                        cache=state.review_cache)
    return StreamingResponse(
        _sse(events, on_close=release),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        # Backstop in case the stream is dropped before it starts.
        background=BackgroundTask(release),
    )


@router.post("/api/chat", response_model=ChatResponse,
             dependencies=[Depends(chat_rate_limit)])
def chat(body: ChatRequest, state: AppState = Depends(get_state)) -> ChatResponse:
    """Answer a question about a repository that was analysed earlier."""
    answer = answer_question(body.repo, body.question, settings=state.settings, registry=state.registry)
    return ChatResponse(answer=answer)


# ── Helpers ──────────────────────────────────────────────────

def _sse(events: Iterable[dict], on_close: Callable[[], None]) -> Iterator[str]:
    try:
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"
    except Exception:
        logger.exception("Review stream crashed")
        yield f"data: {json.dumps({'event': 'error', 'message': 'Internal server error.'})}\n\n"
    finally:
        on_close()


def _release_once(semaphore: threading.BoundedSemaphore) -> Callable[[], None]:
    lock = threading.Lock()
    released = False

    def release() -> None:
        nonlocal released
        with lock:
            if released:
                return
            released = True
        semaphore.release()

    return release
