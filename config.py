"""Application settings, read once from the environment (a local .env file is supported)."""
import os
from dataclasses import dataclass, fields
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"  # Finds issues
    # Double-checks findings. A different model uses a separate Groq daily quota; in testing, Qwen
    # rejected false findings that the gpt-oss models accepted.
    verify_model: str = "qwen/qwen3.8-27b"
    github_token: str | None = None
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Indexing limits
    clone_timeout: int = 120
    max_files: int = 500
    max_file_bytes: int = 200_000
    chunk_size: int = 1000
    chunk_overlap: int = 100
    max_indexed_repos: int = 5
    # Finished reviews kept in memory, keyed by commit: re-reviewing an unchanged repo costs no tokens
    review_cache_size: int = 20

    # Full review: every source file is sent to the LLM, in batches of at most review_batch_chars
    # (keeps each request inside Groq's token limits), up to max_review_chars per repository.
    review_batch_chars: int = 12000
    max_review_chars: int = 150_000
    max_pr_diff_chars: int = 4000  # Per changed file, in PR mode
    # Code sent to the LLM when answering a chat question
    max_context_chars: int = 8000

    # Abuse protection; a limit of 0 disables it
    review_limit_per_hour: int = 5
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
