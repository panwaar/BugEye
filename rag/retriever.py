"""Finding the code chunks relevant to a chat question and formatting them for the LLM."""
import re
from collections.abc import Sequence

from langchain_core.documents import Document

from rag.vector_store import RepoIndex


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
    return "\n\n".join(f"### {format_location(c)}\n{_fenced(c.page_content)}" for c in chunks)


def _fenced(code: str) -> str:
    # A longer fence than any backtick run inside the code, so embedded ``` can't close it early.
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{code}\n{fence}"

