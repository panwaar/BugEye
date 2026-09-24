"""The review pipeline: index -> plan -> review every file -> verify findings -> write the report.

run_review() yields progress events as plain dicts, so the web UI (as SSE) and the CLI share it.
Event shapes:
    {"event": "agent_start" | "agent_step" | "agent_done" | "agent_failed", "agent": str, "message": str}
    {"event": "rag_failed" | "error", "message": str}
    {"event": "complete", "repo": str, "review": str, "security": str, "fixes": str}
"""
import functools
import logging
import time
from collections.abc import Callable, Generator, Iterator

from langchain_core.documents import Document

from agents import reviewer, verifier
from agents.findings import Finding, deduplicate, locate_findings
from agents.report_writer import ReviewStats, render_reports
from agents.reviewer import ReviewPlan
from config import Settings
from exceptions import BugEyeError
from rag.code_loader import load_codebase, split_documents
from rag.vector_store import IndexRegistry, RepoIndex, get_embeddings
from services import llm_service
from services.github_service import cloned_repo, fetch_pull_request, parse_repo

logger = logging.getLogger(__name__)

Event = dict
AGENTS = ("rag", "plan", "review", "verify", "report")

_RATE_LIMIT_ATTEMPTS = 3
_RATE_LIMIT_WAIT_SECONDS = 20


def run_review(repo: str, pr_number: int | None = None, *,
               settings: Settings, registry: IndexRegistry) -> Iterator[Event]:
    try:
        repo_name = parse_repo(repo)
    except BugEyeError as e:
        yield _event("error", message=str(e))
        return

    try:
        index, documents = yield from _index_repository(repo_name, settings)
    except Exception as e:
        message = _user_message(e, "Indexing")
        yield _event("agent_failed", "rag", message)
        yield _event("rag_failed", message=message)
        return
    registry.put(index)  # Chat uses the index; the review itself reads whole files

    ask_json = functools.partial(llm_service.ask_json, api_key=settings.groq_api_key, model=settings.groq_model)
    try:
        plan = yield from _plan(repo_name, documents, pr_number, settings)
        findings, failed_files = yield from _review(plan, ask_json)
        findings, stats = yield from _verify(findings, plan, settings, ask_json)
    except _StageFailed as e:
        yield _event("error", message=str(e))
        return

    stats.failed_files = failed_files
    yield _event("agent_start", "report", "Writing the report")
    reports = render_reports(findings, plan.files, stats)
    yield _event("agent_done", "report", f"{len(findings)} verified findings")
    yield _event("complete", repo=repo_name, **reports)


class _StageFailed(Exception):
    """A pipeline stage failed; the message is already safe to show to users."""


def _index_repository(repo_name: str, settings: Settings) -> Generator[Event, None, tuple[RepoIndex, list[Document]]]:
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
    yield _event("agent_step", "rag", f"Embedding {len(chunks)} chunks for codebase chat")
    index = RepoIndex(repo_name, chunks, get_embeddings(settings.embedding_model))
    yield _event("agent_done", "rag", f"Indexed {len(index.files)} files, {index.chunk_count} chunks")
    return index, loaded.documents


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


def _review(plan: ReviewPlan, ask_json: Callable[[str, str], dict]) -> Generator[Event, None, tuple[list[Finding], list[str]]]:
    yield _event("agent_start", "review", f"Reviewing {len(plan.reviewed_files)} files")
    findings: list[Finding] = []
    failed_files: list[str] = []
    last_error = ""
    total = len(plan.batches)
    for number, batch in enumerate(plan.batches, start=1):
        yield _event("agent_step", "review", f"Batch {number}/{total}: {batch.describe()}")
        content = reviewer.render_batch(plan, batch)
        try:
            batch_findings = yield from _with_rate_limit_retry(
                "review", lambda: reviewer.review_batch(content, ask_json))
        except Exception as e:
            last_error = _user_message(e, f"Review batch {number}")
            failed_files += batch.paths
            yield _event("agent_step", "review", f"Batch {number} failed: {last_error}")
            continue
        findings += batch_findings

    if set(failed_files) >= set(plan.reviewed_files):
        yield _event("agent_failed", "review", last_error)
        raise _StageFailed(last_error)
    yield _event("agent_done", "review", f"{len(findings)} candidate findings")
    return findings, list(dict.fromkeys(failed_files))


def _verify(findings: list[Finding], plan: ReviewPlan, settings: Settings,
            ask_json: Callable[[str, str], dict]) -> Generator[Event, None, tuple[list[Finding], ReviewStats]]:
    yield _event("agent_start", "verify", f"Checking {len(findings)} findings against the code")
    located, unsupported = locate_findings(findings, plan.files)
    if unsupported:
        yield _event("agent_step", "verify", f"Dropped {unsupported} whose quoted code is not in the repository")
    located = deduplicate(located)

    confirmed: list[Finding] = []
    rejected = 0
    groups = verifier.plan_verification(located, plan, settings.review_batch_chars)
    for number, group in enumerate(groups, start=1):
        yield _event("agent_step", "verify", f"Double-checking group {number}/{len(groups)} ({len(group)} findings)")
        try:
            kept = yield from _with_rate_limit_retry(
                "verify", lambda: verifier.verify_group(group, plan, settings.review_batch_chars, ask_json))
        except Exception as e:
            # Keep them, clearly marked, rather than silently losing possibly real issues.
            yield _event("agent_step", "verify", f"Could not double-check group {number}: "
                                                 f"{_user_message(e, 'Verification')}")
            confirmed += group
            continue
        rejected += len(group) - len(kept)
        confirmed += kept

    stats = ReviewStats(files_reviewed=len(plan.reviewed_files), lines_reviewed=plan.reviewed_lines,
                        skipped_files=plan.skipped_files, unsupported=unsupported, rejected=rejected)
    yield _event("agent_done", "verify", f"{len(confirmed)} confirmed, {unsupported + rejected} rejected")
    return confirmed, stats


def _with_rate_limit_retry(agent: str, call: Callable[[], object]) -> Generator[Event, None, object]:
    """Run an LLM call, waiting and retrying when Groq's per-minute token limit is hit."""
    for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
        try:
            return call()
        except BugEyeError as e:
            if e.status_code != 429 or attempt == _RATE_LIMIT_ATTEMPTS:
                raise
            wait = _RATE_LIMIT_WAIT_SECONDS * attempt
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
