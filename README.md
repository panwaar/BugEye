---
title: BugEye
emoji: 🐛
colorFrom: blue
colorTo: purple
sdk: docker
app_file: app.py
pinned: false
---

# 🐛 BugEye — AI-Powered Multi-Agent Code Review

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![LangChain](https://img.shields.io/badge/LangChain-1.x-green?style=for-the-badge)
![Groq](https://img.shields.io/badge/Groq-GPT--OSS_120B-orange?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.1xx-009688?style=for-the-badge&logo=fastapi)
![ChromaDB](https://img.shields.io/badge/ChromaDB-VectorDB-red?style=for-the-badge)

**A pipeline of LLM agents that reviews every source file of a GitHub repository, verifies each finding against the real code, and produces a code review, a security report and concrete fixes — streamed live to the browser.**

</div>

---

## ✨ Features

- 📂 **Full-repository review** — every source file is sent to the model in full, in batches sized to fit Groq's token limits
- ✅ **Verified findings** — each finding must quote real code; quotes that don't exist in the repo are dropped, and a second, skeptical model (a different one from the reviewer) rejects unsupported claims. Findings that couldn't be verified are never shown
- 🛑 **Quota-aware** — when the Groq quota runs out the run stops (no partial or guessed results) and a toast says when it frees up; later requests fail fast without calling Groq
- ♻️ **Cached reviews** — re-reviewing an unchanged commit returns instantly and uses no tokens
- 📍 **Exact locations** — file and line numbers are computed from the real file, never taken from the model
- 🔒 **Security report** — injection, XSS, secrets, auth and other vulnerabilities, by severity
- 🔧 **Code fixes** — the "current code" shown is copied from the file; the model only writes the replacement
- 🔀 **Pull request review** — give a PR number to review just the changed files, with their diffs
- 💬 **Codebase chat** — RAG over the indexed repository (local embeddings + ChromaDB)
- ⚡ **Live progress** — every step is streamed to the UI as it happens

---

## 🏗️ Architecture

```
POST /api/review ──▶ run_review()  (agents/orchestrator.py — yields progress events)
                        │
   1. RAG Agent         │  shallow clone → load files → chunk → embed → Chroma (for chat)
   2. Planner           │  every reviewable file (or the PR's changed files) → batches of whole files,
                        │  plus a map of the names each file defines
   3. Reviewer          │  one LLM call per batch → JSON findings, each quoting the code it is about
   4. Verifier          │  drop findings whose quote isn't in the repo → dedupe →
                        │  skeptical pass by a second model (VERIFY_MODEL) with the real code
   5. Report Writer     │  plain Python: Markdown reports built only from verified findings
                        ▼
              Server-Sent Events ──▶ browser (or the CLI)

POST /api/chat ──▶ answer_question()  — searches the repo's index and asks the LLM
```

Large repositories are capped by `MAX_REVIEW_CHARS` (application code is reviewed first, then config, then tests); anything skipped is listed in the report. On Groq's free tier a full review of a small repository takes a few minutes because of per-minute token limits.

---

## 📁 Project Structure

```
BugEye/
├── app.py                  # FastAPI app factory + entry point
├── cli.py                  # Command-line runner
├── config.py               # Settings from .env
├── exceptions.py           # BugEyeError — failures that are safe to show users
├── dependencies.py         # FastAPI dependencies: app state, rate limits
├── middleware.py           # Security headers (CSP etc.)
├── agents/
│   ├── orchestrator.py     # Runs the pipeline stages in order, streams progress
│   ├── reviewer.py         # Plans batches covering every file, reviews each batch
│   ├── verifier.py         # Checks findings against the real code
│   ├── findings.py         # Finding model, evidence matching, de-duplication
│   ├── report_writer.py    # Builds the review / security / fixes reports
│   ├── chat_agent.py       # Codebase Q&A
│   └── prompts.py          # System prompt for each agent
├── services/
│   ├── github_service.py   # Repo URL parsing, cloning, PR diffs
│   └── llm_service.py      # Groq client
├── rag/
│   ├── code_loader.py      # Read + chunk source files (with line numbers)
│   ├── vector_store.py     # ChromaDB index per repo
│   └── retriever.py        # Pick relevant code for each agent
├── routes/
│   └── api.py              # /, /api/review (SSE), /api/chat + request models
├── templates/index.html
├── static/css/style.css, static/js/app.js
├── Dockerfile
├── requirements.txt / requirements-dev.txt
└── .env.example
```

---

## ⚙️ Setup

```bash
git clone https://github.com/panwaar/BugEye
cd BugEye

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac/Linux

pip install -r requirements.txt
cp .env.example .env            # then set GROQ_API_KEY
```

Get a free Groq API key at [console.groq.com](https://console.groq.com). A `GITHUB_TOKEN` is optional — it only raises GitHub API limits for PR review.

### Run the web app

```bash
python app.py                                  # or: uvicorn app:app --reload --port 7860
```

Open **http://127.0.0.1:7860**. Interactive API docs are at **/docs**.

### Run from the command line

```bash
python cli.py panwaar/Portfolio
python cli.py https://github.com/owner/repo --pr 42 --out reports/
```

### Docker

```bash
docker build -t bugeye .
docker run -p 7860:7860 --env-file .env bugeye
```

---

## 🔧 Configuration

All settings are environment variables — see [.env.example](.env.example) for the full list with defaults. The important ones:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | **Required.** LLM access |
| `GROQ_MODEL` / `VERIFY_MODEL` | Reviewer and verifier models (separate models use separate Groq daily quotas) |
| `GITHUB_TOKEN` | Optional, for PR review rate limits |
| `REVIEW_LIMIT_PER_HOUR` / `CHAT_LIMIT_PER_HOUR` | Per-visitor rate limits — default 5 analyses and 60 questions per hour (`0` disables) |
| `MAX_CONCURRENT_REVIEWS` | Analyses allowed to run at once |
| `REVIEW_BATCH_CHARS` | Code per review request (lower it if Groq reports a request is too large) |
| `MAX_REVIEW_CHARS` | Total code reviewed per repository |

---

## 🔐 Security notes

- Repository content is treated as untrusted: LLM output is sanitised with DOMPurify before rendering, a strict Content-Security-Policy is sent, prompts instruct the model to ignore instructions embedded in code, and symlinks in cloned repos are never followed.
- Repository names are strictly validated before cloning; clones are shallow, time-limited and never prompt for credentials.
- Internal errors are logged server-side; users only see generic messages.
- Never commit `.env` — it is git-ignored.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
