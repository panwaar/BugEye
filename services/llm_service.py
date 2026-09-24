"""Thin wrapper around the Groq chat model used by every agent."""
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
    if not api_key:
        raise BugEyeError("Server is not configured: GROQ_API_KEY is missing.", status_code=503)
    try:
        response = get_llm(api_key, model).invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])
    except groq.RateLimitError as e:
        raise BugEyeError("LLM rate limit reached. Wait a minute and try again.", status_code=429) from e
    except groq.AuthenticationError as e:
        raise BugEyeError("LLM authentication failed. Check GROQ_API_KEY on the server.", status_code=503) from e
    except groq.APIConnectionError as e:
        raise BugEyeError("Could not reach the LLM service. Try again shortly.", status_code=503) from e
    return response.content
