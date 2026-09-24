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
![Groq](https://img.shields.io/badge/Groq-LLaMA_3.3-orange?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.1xx-009688?style=for-the-badge&logo=fastapi)
![ChromaDB](https://img.shields.io/badge/ChromaDB-VectorDB-red?style=for-the-badge)

**A pipeline of specialised LLM agents that indexes a GitHub repository with RAG, then produces a code review, a security report and concrete code fixes — streamed live to the browser.**

</div>

---

## ✨ Features

- 🧠 **RAG indexing** — shallow-clones the repo, splits code into line-numbered chunks, and embeds them locally (`all-MiniLM-L6-v2`) into ChromaDB
- 🔒 **Security scanner** — searches the index for security-relevant code (secrets, SQL, shell, auth, input handling) and reports findings by severity
- 📝 **Code review** — reviews the most relevant code, citing real file names and line ranges
- 🔀 **Pull request review** — give a PR number to review its diff, with related code pulled in as context
- 🎯 **Critic agent** — checks the draft review against the code and removes unsupported claims
- 🔧 **Fix suggester** — before/after code replacements with red/green highlighting
- 💬 **Codebase chat** — ask questions about the indexed repository
- ⚡ **Live progress** — every pipeline step is streamed to the UI as it happens

> BugEye sends the LLM the **most relevant excerpts** of the repository (plus the full file list), not every file — Groq's token limits make that impossible for real repositories. The amount is configurable with `MAX_CONTEXT_CHARS`.

---

## 🏗️ Architecture

```
POST /api/review ──▶ run_review()  (agents/orchestrator.py — yields progress events)
                        │
   1. RAG Agent         │  shallow clone → load files → chunk (with line numbers) → embed → Chroma
   2. Context Agent     │  PR diff (optional) + similarity searches → overview & security excerpts
   3. Security Scanner  │  LLM call on security-focused excerpts
   4. Review Agent      │  LLM call on overview excerpts (+ PR diff)
   5. Critic Agent      │  LLM call: verify the review against the same excerpts   (optional)
   6. Fix Suggester     │  LLM call: before/after fixes for the reviewed issues    (optional)
                        ▼
              Server-Sent Events ──▶ browser (or the CLI)

POST /api/chat ──▶ answer_question()  — searches the repo's index and asks the LLM
```

Indexes are kept in memory per repository (most recent `MAX_INDEXED_REPOS`), so chat always answers about the repository you analysed.

---

## 📁 Project Structure

```
BugEye/
├── app.py                  # FastAPI app factory + entry point
├── cli.py                  # Command-line runner
├── config.py               # Settings from .env
├── exceptions.py           # BugEyeError — failures that are safe to show users
├── dependencies.py         # FastAPI dependencies: app state, access token, rate limits
├── middleware.py           # Security headers (CSP etc.)
├── agents/
│   ├── orchestrator.py     # Runs the 6 agents in order, streams progress
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
| `ACCESS_TOKEN` | If set, the UI and API require this token. **Set it on public deployments** so strangers can't use up your Groq quota |
| `GITHUB_TOKEN` | Optional, for PR review rate limits |
| `REVIEW_LIMIT_PER_HOUR` / `CHAT_LIMIT_PER_HOUR` | Per-client rate limits (`0` disables) |
| `MAX_CONCURRENT_REVIEWS` | Analyses allowed to run at once |
| `MAX_CONTEXT_CHARS` | Code sent to the LLM per call |

---

## 🔐 Security notes

- Repository content is treated as untrusted: LLM output is sanitised with DOMPurify before rendering, a strict Content-Security-Policy is sent, prompts instruct the model to ignore instructions embedded in code, and symlinks in cloned repos are never followed.
- Repository names are strictly validated before cloning; clones are shallow, time-limited and never prompt for credentials.
- Internal errors are logged server-side; users only see generic messages.
- Never commit `.env` — it is git-ignored.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
