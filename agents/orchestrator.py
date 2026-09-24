"""The review pipeline: index -> plan -> review every file -> verify findings -> write the report.

run_review() yields progress events as plain dicts, so the web UI (as SSE) and the CLI share it.
Event shapes:
    {"event": "agent_start" | "agent_step" | "agent_done" | "agent_failed", "agent": str, "message": str}
    {"event": "rag_failed" | "error" | "quota_exhausted", "message": str}
    {"event": "complete", "repo": str, "review": str, "security": str, "fixes": str}
"""
import functools
import logging
import math
import time
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass

from langchain_core.documents import Document

from agents import reviewer, verifier
from agents.findings import Finding, deduplicate, locate_findings
from agents.report_writer import ReviewStats, render_reports
from agents.review_cache import ReviewCache
from agents.reviewer import ReviewPlan
from config import Settings
from exceptions import BugEyeError, RateLimitedError
from rag.code_loader import load_codebase, split_documents
from rag.vector_store import IndexRegistry, RepoIndex, get_embeddings
from services import llm_service
from services.github_service import cloned_repo, fetch_pull_request, head_commit, parse_repo

logger = logging.getLogger(__name__)

Event = dict
AGENTS = ("rag", "plan", "review", "verify", "report")

_RATE_LIMIT_ATTEMPTS = 3
_RATE_LIMIT_WAIT_SECONDS = 20
_MAX_WAIT_SECONDS = 60


@dataclass
class _Indexed:
    index: RepoIndex
    documents: list[Document]
    commit: str | None


def run_review(repo: str, pr_number: int | None = None, *, settings: Settings,
               registry: IndexRegistry, cache: ReviewCache | None = None) -> Iterator[Event]:
    try:
        repo_name = parse_repo(repo)
    except BugEyeError as e:
        yield _event("error", message=str(e))
        return

    try:
        indexed = yield from _index_repository(repo_name, settings)
    except Exception as e:
        message = _user_message(e, "Indexing")
        yield _event("agent_failed", "rag", message)
        yield _event("rag_failed", message=message)
        return
    registry.put(indexed.index)  # Chat uses the index; the review itself reads whole files

    # Pull requests aren't cached: their changes can move while the repository's HEAD stays put.
    cache_key = None
    if cache is not None and pr_number is None and indexed.commit:
        cache_key = ReviewCache.key(repo_name, indexed.commit, settings.groq_model, settings.verify_model)
        cached = cache.get(cache_key)
        if cached is not None:
            note = f"Reused the review of commit {indexed.commit[:7]}"
            for agent in AGENTS[1:]:
                yield _event("agent_done", agent, note)
            yield _event("complete", repo=repo_name, **cached)
            return

    # Don't start work that can only fail: the quota is known to be used up.
    try:
        for model in (settings.groq_model, settings.verify_model):
            llm_service.ensure_quota(model)
    except RateLimitedError as e:
        yield _event("quota_exhausted", message=str(e))
        return

    review_ask = functools.partial(llm_service.ask_json, api_key=settings.groq_api_key, model=settings.groq_model)
    verify_ask = functools.partial(llm_service.ask_json, api_key=settings.groq_api_key, model=settings.verify_model)
    try:
        plan = yield from _plan(repo_name, indexed.documents, pr_number, settings)
        findings, stats = yield from _review(plan, review_ask)
        findings = yield from _verify(findings, plan, stats, settings, verify_ask)
    except _QuotaExhausted as e:
        yield _event("quota_exhausted", message=str(e))
        return
    except _StageFailed as e:
        yield _event("error", message=str(e))
        return

    yield _event("agent_start", "report", "Writing the report")
    reports = render_reports(findings, plan.files, stats)
    if cache_key and stats.complete:
        cache.put(cache_key, reports)
    yield _event("agent_done", "report", f"{len(findings)} verified findings")
    yield _event("complete", repo=repo_name, **reports)


class _StageFailed(Exception):
    """A pipeline stage failed; the message is already safe to show to users."""


class _QuotaExhausted(Exception):
    """Groq refused a request for quota reasons. The run stops: no partial or guessed results."""


def _index_repository(repo_name: str, settings: Settings) -> Generator[Event, None, _Indexed]:
    yield _event("agent_start", "rag", f"Indexing {repo_name}")
    yield _event("agent_step", "rag", "Cloning repository (shallow)")
    with cloned_repo(repo_name, settings.clone_timeout) as path:
        yield _event("agent_step", "rag", "Reading source files")
        commit = head_commit(path)
        loaded = load_codebase(path, max_files=settings.max_files, max_file_bytes=settings.max_file_bytes)
    if not loaded.documents:
        raise BugEyeError("No supported source files were found in the repository.")
    skipped = f" ({loaded.skipped} skipped)" if loaded.skipped else ""
    yield _event("agent_step", "rag", f"Loaded {len(loaded.documents)} files{skipped}")

    chunks = split_documents(loaded.documents, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
    yield _event("agent_step", "rag", f"Embedding {len(chunks)} chunks for codebase chat")
    index = RepoIndex(repo_name, chunks, get_embeddings(settings.embedding_model))
    yield _event("agent_done", "rag", f"Indexed {len(index.files)} files, {index.chunk_count} chunks")
    return _Indexed(index=index, documents=loaded.documents, commit=commit)


def _plan(repo_name: str, documents: list[Document], pr_number: int | None,
          settings: Settings) -> Generator[Event, None, ReviewPlan]:
    yield _event("agent_start", "plan", "Planning the review")
    try:
        pr = None
        if pr_number is not None:
            yield _event("agent_step", "plan", f"Fetching PR #{pr_number} from GitHub")
            pr = fetch_pull_request(repo_name, pr_number, token=settings.github_token,
                                    max_patch_chars=settings.max_pr_diff_chars)
        plan = reviewer.plan_review(repo_name, documents, batch_chars=settings.review_batch_chars,
                                    max_total_chars=settings.max_review_chars, pr=pr)
    except Exception as e:
        message = _user_message(e, "Planning the review")
        yield _event("agent_failed", "plan", message)
        raise _StageFailed(message) from e

    scope = f"PR #{plan.pr.number}: " if plan.pr else ""
    skipped = f", {len(plan.skipped_files)} over the size limit" if plan.skipped_files else ""
    yield _event("agent_done", "plan", f"{scope}{len(plan.reviewed_files)} files ({plan.reviewed_lines:,} lines) "
                                       f"in {len(plan.batches)} batches{skipped}")
    return plan


def _review(plan: ReviewPlan, ask_json: Callable[[str, str], dict]) -> Generator[Event, None, tuple[list[Finding], ReviewStats]]:
    yield _event("agent_start", "review", f"Reviewing {len(plan.reviewed_files)} files")
    findings: list[Finding] = []
    failed: list[str] = []
    error = ""
    total = len(plan.batches)
    for number, batch in enumerate(plan.batches, start=1):
        yield _event("agent_step", "review", f"Batch {number}/{total}: {batch.describe()}")
        content = reviewer.render_batch(plan, batch)
        try:
            findings += yield from _with_rate_limit_retry(
                "review", lambda: reviewer.review_batch(content, ask_json))
        except RateLimitedError as e:
            yield _event("agent_failed", "review", str(e))
            raise _QuotaExhausted(str(e)) from e
        except Exception as e:
            error = _user_message(e, f"Review batch {number}")
            failed += batch.paths
            yield _event("agent_step", "review", f"Batch {number} failed: {error}")

    failed = list(dict.fromkeys(failed))
    if set(failed) >= set(plan.reviewed_files):
        yield _event("agent_failed", "review", error)
        raise _StageFailed(error)

    reviewed = [path for path in plan.reviewed_files if path not in failed]
    stats = ReviewStats(files_reviewed=len(reviewed),
                        lines_reviewed=sum(plan.files[p].count("\n") + 1 for p in reviewed),
                        skipped_files=plan.skipped_files, failed_files=failed, review_error=error)
    yield _event("agent_done", "review", f"{len(findings)} candidate findings")
    return findings, stats


def _verify(findings: list[Finding], plan: ReviewPlan, stats: ReviewStats, settings: Settings,
            ask_json: Callable[[str, str], dict]) -> Generator[Event, None, list[Finding]]:
    yield _event("agent_start", "verify", f"Checking {len(findings)} findings against the code")
    located, stats.unsupported = locate_findings(findings, plan.files)
    if stats.unsupported:
        yield _event("agent_step", "verify", f"Dropped {stats.unsupported} whose quoted code is not in the repository")
    located = deduplicate(located)

    confirmed: list[Finding] = []
    groups = verifier.plan_verification(located, plan, settings.review_batch_chars)
    for number, group in enumerate(groups, start=1):
        yield _event("agent_step", "verify", f"Double-checking group {number}/{len(groups)} ({len(group)} findings)")
        try:
            kept = yield from _with_rate_limit_retry(
                "verify", lambda: verifier.verify_group(group, plan, settings.review_batch_chars, ask_json))
        except RateLimitedError as e:
            yield _event("agent_failed", "verify", str(e))
            raise _QuotaExhausted(str(e)) from e
        except Exception as e:
            # Unchecked findings are withheld: showing false alarms costs more trust than missing a real one.
            stats.verify_error = _user_message(e, "Verification")
            stats.unverified += len(group)
            yield _event("agent_step", "verify", f"Could not double-check group {number}: {stats.verify_error}")
            continue
        stats.rejected += len(group) - len(kept)
        confirmed += kept

    rejected = stats.unsupported + stats.rejected
    unverified = f", {stats.unverified} not checked" if stats.unverified else ""
    yield _event("agent_done", "verify", f"{len(confirmed)} confirmed, {rejected} rejected{unverified}")
    return confirmed


def _with_rate_limit_retry(agent: str, call: Callable[[], object]) -> Generator[Event, None, object]:
    """Run an LLM call, waiting and retrying when Groq's per-minute token limit is hit."""
    for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
        try:
            return call()
        except RateLimitedError as e:
            if e.daily or attempt == _RATE_LIMIT_ATTEMPTS:
                raise
            wait = math.ceil(min(e.retry_after or _RATE_LIMIT_WAIT_SECONDS * attempt, _MAX_WAIT_SECONDS))
            yield _event("agent_step", agent, f"Groq rate limit reached, waiting {wait}s")
            time.sleep(wait)
    raise AssertionError("unreachable")


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
