"""Choosing which code chunks to send to the LLM and formatting them."""
from collections.abc import Sequence

from langchain_core.documents import Document

from rag.vector_store import RepoIndex

OVERVIEW_QUERIES = (
    "application entry point main function server startup",
    "configuration settings environment variables",
    "core business logic classes and functions",
    "error handling exceptions try except catch",
    "database models queries persistence",
    "API routes request handlers",
)

SECURITY_QUERIES = (
    "password secret api key token credentials",
    "sql query execute raw string formatting",
    "subprocess shell exec eval command execution",
    "authentication login session cookie jwt authorization",
    "user input request parameters validation",
    "html template rendering innerHTML unescaped output",
    "file path open read write upload",
)


def retrieve(index: RepoIndex, queries: Sequence[str], *, k_per_query: int = 4, max_chars: int) -> list[Document]:
    """Run several queries and merge results round-robin (best hit of each query first),
    dropping duplicates, until the character budget is used up."""
    ranked = [index.search(q, k_per_query) for q in queries]
    selected: list[Document] = []
    seen: set[str] = set()
    used = 0
    for rank in range(k_per_query):
        for results in ranked:
            if rank >= len(results):
                continue
            chunk = results[rank]
            chunk_id = chunk.metadata.get("id")
            if chunk_id in seen or used + len(chunk.page_content) > max_chars:
                continue
            seen.add(chunk_id)
            used += len(chunk.page_content)
            selected.append(chunk)
    return selected


def format_location(chunk: Document) -> str:
    source = chunk.metadata.get("source", "unknown")
    start, end = chunk.metadata.get("start_line"), chunk.metadata.get("end_line")
    return f"`{source}` (lines {start}-{end})" if start else f"`{source}`"


def format_chunks(chunks: Sequence[Document]) -> str:
    if not chunks:
        return "(no relevant code found)"
    return "\n\n".join(f"### {format_location(c)}\n```\n{c.page_content}\n```" for c in chunks)


def format_file_list(files: Sequence[str], limit: int = 150) -> str:
    listed = "\n".join(f"- {f}" for f in files[:limit])
    if len(files) > limit:
        listed += f"\n- ... and {len(files) - limit} more"
    return listed
