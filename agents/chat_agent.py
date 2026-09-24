"""Answering questions about an indexed repository."""
from agents import prompts
from config import Settings
from exceptions import BugEyeError
from rag.retriever import format_chunks, retrieve
from rag.vector_store import IndexRegistry
from services import llm_service
from services.github_service import parse_repo


def answer_question(repo: str, question: str, *, settings: Settings, registry: IndexRegistry) -> str:
    index = registry.get(parse_repo(repo))
    if index is None:
        raise BugEyeError(
            "This repository is no longer indexed on the server. Run the analysis again.", status_code=404)

    chunks = retrieve(index, [question], k_per_query=6, max_chars=settings.max_context_chars)
    content = (
        f"# Repository: {index.repo_name}\n\n"
        f"## Relevant code excerpts\n{format_chunks(chunks)}\n\n"
        f"## Question\n{question}"
    )
    return llm_service.ask(prompts.CHAT_PROMPT, content, api_key=settings.groq_api_key, model=settings.groq_model)
