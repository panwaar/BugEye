"""The review pipeline: index -> gather context -> security scan -> review -> critique -> fixes.

run_review() yields progress events as plain dicts, so the web UI (as SSE) and the CLI share it.
Event shapes:
    {"event": "agent_start" | "agent_step" | "agent_done" | "agent_failed", "agent": str, "message": str}
    {"event": "rag_failed" | "error", "message": str}
    {"event": "complete", "repo": str, "review": str, "security": str, "fixes": str}
"""
import functools
import logging
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass

from agents import prompts
from config import Settings
from exceptions import BugEyeError
from rag.code_loader import load_codebase, split_documents
from rag.retriever import OVERVIEW_QUERIES, SECURITY_QUERIES, format_chunks, format_file_list, retrieve
from rag.vector_store import IndexRegistry, RepoIndex, get_embeddings
from services import llm_service
from services.github_service import PullRequest, cloned_repo, fetch_pull_request, parse_repo

logger = logging.getLogger(__name__)

Event = dict
AGENTS = ("rag", "fetch", "security", "review", "critic", "fixes")


def run_review(repo: str, pr_number: int | None = None, *,
               settings: Settings, registry: IndexRegistry) -> Iterator[Event]:
    try:
        repo_name = parse_repo(repo)
    except BugEyeError as e:
        yield _event("error", message=str(e))
        return

    # Indexing is mandatory: without real code context the LLM would only hallucinate a review.
    try:
        index = yield from _index_repository(repo_name, settings)
    except Exception as e:
        message = _user_message(e, "Indexing")
        yield _event("agent_failed", "rag", message)
        yield _event("rag_failed", message=message)
        return
    registry.put(index)

    try:
        context = yield from _gather_context(index, pr_number, settings)
    except Exception as e:
        message = _user_message(e, "Gathering context")
        yield _event("agent_failed", "fetch", message)
        yield _event("error", message=message)
        return

    ask = functools.partial(llm_service.ask, api_key=settings.groq_api_key, model=settings.groq_model)
    try:
        security = yield from _llm_stage(
            "security", "Scanning for vulnerabilities", ask, prompts.SECURITY_PROMPT, context.security)
        review = yield from _llm_stage(
            "review", "Reviewing code quality", ask, prompts.REVIEW_PROMPT, context.general)
    except _StageFailed as e:
        yield _event("error", message=str(e))
        return

    # The critic and fix stages are optional: if they fail, the results so far are still useful.
    try:
        review = yield from _llm_stage(
            "critic", "Checking the review against the code", ask, prompts.CRITIC_PROMPT,
            f"## Draft review\n{review}\n\n{context.general}", optional=True)
    except _StageFailed:
        pass
    try:
        fixes = yield from _llm_stage(
            "fixes", "Writing code fixes", ask, prompts.FIX_SUGGESTER_PROMPT,
            f"## Review\n{review}\n\n{context.general}", optional=True)
    except _StageFailed as e:
        fixes = f"Fix suggestions unavailable: {e}"

    yield _event("complete", repo=repo_name, review=review, security=security, fixes=fixes)


@dataclass(frozen=True)
class ReviewContext:
    general: str  # Prompt content for review / critic / fixes
    security: str  # Same, but with security-focused excerpts


class _StageFailed(Exception):
    """A pipeline stage failed; the message is already safe to show to users."""


def _index_repository(repo_name: str, settings: Settings) -> Generator[Event, None, RepoIndex]:
    yield _event("agent_start", "rag", f"Indexing {repo_name}")
    yield _event("agent_step", "rag", "Cloning repository (shallow)")
    with cloned_repo(repo_name, settings.clone_timeout) as path:
        yield _event("agent_step", "rag", "Reading source files")
        loaded = load_codebase(path, max_files=settings.max_files, max_file_bytes=settings.max_file_bytes)
    if not loaded.documents:
        raise BugEyeError("No supported source files were found in the repository.")
    skipped = f" ({loaded.skipped} skipped)" if loaded.skipped else ""
    yield _event("agent_step", "rag", f"Loaded {len(loaded.documents)} files{skipped}")

    chunks = split_documents(loaded.documents, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    yield _event("agent_step", "rag", f"Embedding {len(chunks)} chunks")
    index = RepoIndex(repo_name, chunks, get_embeddings(settings.embedding_model))
    yield _event("agent_done", "rag", f"Indexed {len(index.files)} files, {index.chunk_count} chunks")
    return index


def _gather_context(index: RepoIndex, pr_number: int | None,
                    settings: Settings) -> Generator[Event, None, ReviewContext]:
    yield _event("agent_start", "fetch", "Selecting relevant code")
    pr = None
    if pr_number is not None:
        yield _event("agent_step", "fetch", f"Fetching PR #{pr_number} from GitHub")
        pr = fetch_pull_request(index.repo_name, pr_number,
                                token=settings.github_token, max_chars=settings.max_pr_diff_chars)

    # For a PR, search for code related to the change first, then the general overview.
    general_queries = ([pr.title, *pr.changed_files[:8]] if pr else []) + list(OVERVIEW_QUERIES)
    yield _event("agent_step", "fetch", f"Running {len(general_queries) + len(SECURITY_QUERIES)} similarity searches")
    general_chunks = retrieve(index, general_queries, max_chars=settings.max_context_chars)
    security_chunks = retrieve(index, SECURITY_QUERIES, max_chars=settings.max_context_chars)

    files = {c.metadata["source"] for c in general_chunks + security_chunks}
    yield _event("agent_done", "fetch",
                 f"Selected {len(general_chunks) + len(security_chunks)} excerpts from {len(files)} files")
    return ReviewContext(
        general=_render_context(index, pr, general_chunks),
        security=_render_context(index, pr, security_chunks),
    )


def _render_context(index: RepoIndex, pr: PullRequest | None, chunks) -> str:
    parts = [f"# Repository: {index.repo_name}"]
    if pr:
        parts.append(f"## Pull request\n{pr.markdown}")
    parts.append(f"## Files in the repository ({len(index.files)})\n{format_file_list(index.files)}")
    parts.append(f"## Relevant code excerpts\n{format_chunks(chunks)}")
    return "\n\n".join(parts)


def _llm_stage(agent: str, description: str, ask: Callable[[str, str], str],
               system_prompt: str, content: str, *, optional: bool = False) -> Generator[Event, None, str]:
    yield _event("agent_start", agent, description)
    yield _event("agent_step", agent, f"Sending {len(content):,} characters to the LLM")
    try:
        result = ask(system_prompt, content)
    except Exception as e:
        message = _user_message(e, description)
        yield _event("agent_failed", agent, f"{message} (skipped)" if optional else message)
        raise _StageFailed(message) from e
    yield _event("agent_done", agent, "Complete")
    return result


def _user_message(exc: Exception, action: str) -> str:
    if isinstance(exc, BugEyeError):
        return str(exc)
    logger.error("%s failed", action, exc_info=exc)
    return f"{action} failed due to an internal error."


def _event(kind: str, agent: str | None = None, message: str | None = None, **data) -> Event:
    event: Event = {"event": kind}
    if agent is not None:
        event["agent"] = agent
    if message is not None:
        event["message"] = message
    event.update(data)
    return event
