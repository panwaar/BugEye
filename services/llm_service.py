"""Thin wrapper around the Groq chat model used by every agent."""
import json
import re
from functools import lru_cache

import groq
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from exceptions import BugEyeError


@lru_cache(maxsize=4)
def get_llm(api_key: str, model: str) -> ChatGroq:
    # Retries back off on 429s, which Groq's free tier returns often.
    return ChatGroq(model=model, temperature=0, api_key=api_key, max_retries=3)


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


def _invoke(system_prompt: str, user_content: str, *, api_key: str | None, model: str,
            json_mode: bool = False) -> str:
    if not api_key:
        raise BugEyeError("Server is not configured: GROQ_API_KEY is missing.", status_code=503)
    llm = get_llm(api_key, model)
    if json_mode:
        llm = llm.bind(response_format={"type": "json_object"})
    try:
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_content)])
    except groq.RateLimitError as e:
        raise BugEyeError("LLM rate limit reached. Wait a minute and try again.", status_code=429) from e
    except groq.AuthenticationError as e:
        raise BugEyeError("LLM authentication failed. Check GROQ_API_KEY on the server.", status_code=503) from e
    except groq.APIConnectionError as e:
        raise BugEyeError("Could not reach the LLM service. Try again shortly.", status_code=503) from e
    except groq.NotFoundError as e:
        raise BugEyeError(f"Groq model {model!r} is not available. Set GROQ_MODEL to a current model.",
                          status_code=503) from e
    except groq.BadRequestError:
        raise
    except groq.APIStatusError as e:
        if e.status_code == 413:
            raise BugEyeError("A request exceeded your Groq plan's token limit. Lower REVIEW_BATCH_CHARS.",
                              status_code=413) from e
        raise
    return response.content
