"""Application settings, read once from the environment (a local .env file is supported)."""
import os
from dataclasses import dataclass, fields
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    github_token: str | None = None
    # When set, the web UI and API require this token in the X-Access-Token header.
    access_token: str | None = None
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Indexing limits
    clone_timeout: int = 120
    max_files: int = 500
    max_file_bytes: int = 200_000
    chunk_size: int = 1000
    chunk_overlap: int = 100
    max_indexed_repos: int = 5

    # How much code is sent to the LLM per call (keeps requests inside Groq's token limits)
    max_context_chars: int = 8000
    max_pr_diff_chars: int = 8000

    # Abuse protection; a limit of 0 disables it
    review_limit_per_hour: int = 10
    chat_limit_per_hour: int = 60
    max_concurrent_reviews: int = 2
    # Number of reverse proxies in front of the app (Hugging Face Spaces has one)
    proxy_hops: int = 1

    port: int = 7860

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        values = {}
        for field in fields(cls):
            raw = os.getenv(field.name.upper())
            if raw is None or raw.strip() == "":
                continue
            values[field.name] = _coerce(field.name, raw.strip(), field.default)
        return cls(**values)


def _coerce(name: str, raw: str, default):
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError as e:
            raise ValueError(f"Environment variable {name.upper()} must be an integer, got {raw!r}") from e
    return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
