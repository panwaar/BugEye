"""Thin wrapper around the Groq chat models used by every agent."""
import json
import re
import threading
import time
from functools import lru_cache

import groq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from exceptions import BugEyeError, RateLimitedError

# Qwen models think out loud; "parsed" moves the reasoning out of the reply so JSON mode works.
_MODEL_OPTIONS = {"qwen/": {"reasoning_format": "parsed"}}
_RETRY_IN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")
_DEFAULT_QUOTA_PAUSE_SECONDS = 600

# Models whose daily quota is known to be used up, and until when (time.monotonic()).
# Requests fail fast until then instead of hammering Groq with calls that can only fail.
_exhausted_until: dict[str, float] = {}
_exhausted_lock = threading.Lock()


def ensure_quota(model: str) -> None:
    """Raise RateLimitedError if this model's daily quota is known to be used up."""
    with _exhausted_lock:
        until = _exhausted_until.get(model, 0.0)
    remaining = until - time.monotonic()
    if remaining > 0:
        raise RateLimitedError(f"Groq's daily token limit for {model} is used up; "
                               f"it frees up in {_human_duration(remaining)}.",
                               retry_after=remaining, daily=True)


def _remember_exhausted(model: str, error: RateLimitedError) -> None:
    pause = error.retry_after or _DEFAULT_QUOTA_PAUSE_SECONDS
    with _exhausted_lock:
        _exhausted_until[model] = time.monotonic() + pause


@lru_cache(maxsize=8)
def get_llm(api_key: str, model: str) -> ChatGroq:
    options = next((opts for prefix, opts in _MODEL_OPTIONS.items() if model.startswith(prefix)), {})
    # A couple of quick retries smooth over brief rate-limit blips; longer waits are handled by the caller.
    return ChatGroq(model=model, temperature=0, api_key=api_key, max_retries=2, **options)


def ask(system_prompt: str, user_content: str, *, api_key: str | None, model: str) -> str:
    """Send one system + user message pair and return the model's text reply."""
    return _invoke(system_prompt, user_content, api_key=api_key, model=model)


def ask_json(system_prompt: str, user_content: str, *, api_key: str | None, model: str) -> dict:
    """Like ask(), but the model must answer with a JSON object, which is parsed and returned."""
    try:
        text = _invoke(system_prompt, user_content, api_key=api_key, model=model, json_mode=True)
    except groq.BadRequestError:
        # Groq rejects replies that aren't valid JSON in JSON mode; retry in plain mode and extract it.
        text = _invoke(system_prompt, user_content, api_key=api_key, model=model)
    return parse_json_object(text)


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from a model reply (tolerates code fences and surrounding prose)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise BugEyeError("The model returned malformed output.", status_code=502)


def rate_limit_error(message: str, model: str) -> RateLimitedError:
    """Turn Groq's 429 message into an error that says which limit was hit and when it frees up."""
    daily = "per day" in message
    match = _RETRY_IN.search(message)
    retry_after = None
    if match and any(match.groups()):
        hours, minutes, seconds = match.groups()
        retry_after = int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
    if daily:
        when = f"; it frees up in {_human_duration(retry_after)}" if retry_after else ""
        return RateLimitedError(f"Groq's daily token limit for {model} is used up{when}.",
                                retry_after=retry_after, daily=True)
    return RateLimitedError("LLM rate limit reached. Wait a minute and try again.", retry_after=retry_after)


def _invoke(system_prompt: str, user_content: str, *, api_key: str | None, model: str,
            json_mode: bool = False) -> str:
    if not api_key:
        raise BugEyeError("Server is not configured: GROQ_API_KEY is missing.", status_code=503)
    ensure_quota(model)
    llm = get_llm(api_key, model)
    if json_mode:
        llm = llm.bind(response_format={"type": "json_object"})
    try:
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_content)])
    except groq.RateLimitError as e:
        error = rate_limit_error(str(e), model)
        if error.daily:
            _remember_exhausted(model, error)
        raise error from e
    except groq.AuthenticationError as e:
        raise BugEyeError("LLM authentication failed. Check GROQ_API_KEY on the server.", status_code=503) from e
    except groq.APIConnectionError as e:
        raise BugEyeError("Could not reach the LLM service. Try again shortly.", status_code=503) from e
    except groq.NotFoundError as e:
        raise BugEyeError(f"Groq model {model!r} is not available. Set GROQ_MODEL / VERIFY_MODEL "
                          "to a current model.", status_code=503) from e
    except groq.BadRequestError:
        raise
    except groq.APIStatusError as e:
        if e.status_code == 413:
            raise BugEyeError("A request exceeded your Groq plan's token limit. Lower REVIEW_BATCH_CHARS.",
                              status_code=413) from e
        raise
    return response.content


def _human_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if seconds < 90:
        return f"{round(seconds)} s"
    if minutes < 90:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"
