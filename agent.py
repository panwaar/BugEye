import os
import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
load_dotenv()

from github_tools import fetch_pr_details
from rag_tools import build_vector_store, search_codebase
from prompts import (
    SYSTEM_PROMPT, REPO_ANALYSIS_TEMPLATE,
    REVIEW_PROMPT, CRITIC_PROMPT, FIX_SUGGESTER_PROMPT, SECURITY_PROMPT
)



def get_llm():
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        temperature=0,
        api_key=os.getenv("GROQ_API_KEY")
    )


def emit(event: str, data: dict):
    return f"data: {json.dumps({'event': event, **data})}\n\n"


def review_pr_stream(repo_name: str, pr_number: int = None):
    """
    Generator that streams SSE events.
    RAG MUST succeed — if it fails, everything stops.
    """
    llm = get_llm()

    # ── Step 1: RAG Agent — MANDATORY ──────────────────────────
    yield emit("agent_start", {"agent": "rag", "message": "Connecting to GitHub..."})
    yield emit("agent_step", {"agent": "rag", "message": "Cloning repository..."})

    try:
        vector_store = build_vector_store(repo_name)
        yield emit("agent_step", {"agent": "rag", "message": "Chunking code files..."})
        yield emit("agent_step", {"agent": "rag", "message": "Building vector embeddings..."})
        yield emit("agent_step", {"agent": "rag", "message": "Storing in ChromaDB..."})
        yield emit("agent_done", {"agent": "rag", "message": "Codebase indexed successfully"})

    except Exception as e:
        yield emit("agent_done", {"agent": "rag", "message": f"Failed: {str(e)[:80]}"})
        yield emit("rag_failed", {
            "message": f"RAG indexing failed: {str(e)[:120]}. Cannot proceed without codebase context."
        })
        return  # Stop everything — no RAG = no review

    # ── Step 2: Fetch/Load codebase ─────────────────────────────
    yield emit("agent_start", {"agent": "fetch", "message": "Initializing..."})

    try:
        yield emit("agent_step", {"agent": "fetch", "message": "Searching codebase via RAG..."})
        codebase_context = search_codebase(vector_store, "code structure architecture overview", k=8)
        code_content = REPO_ANALYSIS_TEMPLATE.format(
            repo=repo_name,
            codebase_context=codebase_context
        )
        yield emit("agent_done", {"agent": "fetch", "message": "Codebase loaded"})

    except Exception as e:
        yield emit("agent_done", {"agent": "fetch", "message": f"Failed: {str(e)[:80]}"})
        yield emit("error", {"message": f"Failed to load codebase: {str(e)}"})
        return

    # ── Step 3: Security Scanner ────────────────────────────────
    yield emit("agent_start", {"agent": "security", "message": "Starting security scan..."})
    yield emit("agent_step", {"agent": "security", "message": "Scanning for hardcoded secrets..."})
    yield emit("agent_step", {"agent": "security", "message": "Checking for injection vulnerabilities..."})
    yield emit("agent_step", {"agent": "security", "message": "Analyzing authentication patterns..."})

    try:
        security_response = llm.invoke([
            SystemMessage(content=SECURITY_PROMPT),
            HumanMessage(content=f"## Code to analyze:\n{code_content}")
        ])
        security_report = security_response.content
        yield emit("agent_done", {"agent": "security", "message": "Security scan complete"})
    except Exception as e:
        yield emit("agent_done", {"agent": "security", "message": "Failed"})
        yield emit("error", {"message": f"Security scan failed: {str(e)}"})
        return

    # ── Step 4: Review Agent ────────────────────────────────────
    yield emit("agent_start", {"agent": "review", "message": "Starting code review..."})
    yield emit("agent_step", {"agent": "review", "message": "Analyzing logic and structure..."})
    yield emit("agent_step", {"agent": "review", "message": "Checking error handling..."})
    yield emit("agent_step", {"agent": "review", "message": "Evaluating code quality..."})

    try:
        review_response = llm.invoke([
            SystemMessage(content=REVIEW_PROMPT),
            HumanMessage(content=f"## Code:\n{code_content}")
        ])
        initial_review = review_response.content
        yield emit("agent_done", {"agent": "review", "message": "Initial review complete"})
    except Exception as e:
        yield emit("agent_done", {"agent": "review", "message": "Failed"})
        yield emit("error", {"message": f"Review failed: {str(e)}"})
        return

    # ── Step 5: Critic Agent ────────────────────────────────────
    yield emit("agent_start", {"agent": "critic", "message": "Reviewing the review..."})
    yield emit("agent_step", {"agent": "critic", "message": "Checking for vague feedback..."})
    yield emit("agent_step", {"agent": "critic", "message": "Improving clarity and tone..."})

    try:
        critic_response = llm.invoke([
            SystemMessage(content=CRITIC_PROMPT),
            HumanMessage(content=initial_review)
        ])
        improved_review = critic_response.content
        yield emit("agent_done", {"agent": "critic", "message": "Review polished"})
    except Exception as e:
        # Critic is optional — use initial review if it fails
        improved_review = initial_review
        yield emit("agent_done", {"agent": "critic", "message": "Skipped — using initial review"})

    # ── Step 6: Fix Suggester ───────────────────────────────────
    yield emit("agent_start", {"agent": "fixes", "message": "Generating code fixes..."})
    yield emit("agent_step", {"agent": "fixes", "message": "Identifying fixable issues..."})
    yield emit("agent_step", {"agent": "fixes", "message": "Writing replacement code..."})
    yield emit("agent_step", {"agent": "fixes", "message": "Formatting diff view..."})

    try:
        fixes_response = llm.invoke([
            SystemMessage(content=FIX_SUGGESTER_PROMPT),
            HumanMessage(content=f"## Review:\n{improved_review}\n\n## Code:\n{code_content}")
        ])
        fix_suggestions = fixes_response.content
        yield emit("agent_done", {"agent": "fixes", "message": "Code fixes ready"})
    except Exception as e:
        fix_suggestions = "Fix suggestions unavailable."
        yield emit("agent_done", {"agent": "fixes", "message": "Skipped"})

    # ── Send final result ───────────────────────────────────────
    yield emit("complete", {
        "review": improved_review,
        "security": security_report,
        "fixes": fix_suggestions
    })