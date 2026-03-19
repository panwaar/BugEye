---
title: BugEye
emoji: 🐛
colorFrom: blue
colorTo: purple
sdk: docker
app_file: app.py
pinned: false
---



# 🐛 BugEye — AI-Powered Multi-Agent Code Review System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![LangChain](https://img.shields.io/badge/LangChain-1.2-green?style=for-the-badge)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic-purple?style=for-the-badge)
![Groq](https://img.shields.io/badge/Groq-LLaMA_3.3-orange?style=for-the-badge)
![Flask](https://img.shields.io/badge/Flask-2.0-black?style=for-the-badge&logo=flask)
![ChromaDB](https://img.shields.io/badge/ChromaDB-VectorDB-red?style=for-the-badge)

**An autonomous multi-agent AI system that performs deep code reviews, security vulnerability scanning, and intelligent fix suggestions — powered by RAG, LLMs, and real-time streaming.**

[Demo](#demo) · [Features](#features) · [Architecture](#architecture) · [Setup](#setup) · [Usage](#usage)

</div>

---

## 🚀 What is BugEye?

BugEye is a **production-grade agentic AI application** that analyzes any GitHub repository using a pipeline of specialized AI agents. Each agent has a specific role — from indexing the codebase with RAG to scanning for security vulnerabilities to suggesting actual code fixes.

Unlike simple LLM wrappers, BugEye implements a true **multi-agent orchestration pattern** where agents communicate, critique each other's output, and produce progressively refined results.

---

## ✨ Features

- 🧠 **RAG-Powered Codebase Indexing** — Clones and embeds the entire repository into a vector database using HuggingFace embeddings + ChromaDB
- 🔒 **Autonomous Security Scanner** — Detects hardcoded secrets, SQL injection, XSS, command injection, insecure auth, and more
- 📝 **Context-Aware Code Review** — Reviews code with full codebase understanding — not just the diff
- 🎯 **Self-Critiquing Agent** — A second LLM agent reviews and improves the first agent's output
- 🔧 **Intelligent Fix Suggester** — Generates actual before/after code replacements with red/green diff view
- 💬 **RAG Chat Interface** — Ask anything about the codebase in natural language
- ⚡ **Real-Time Streaming UI** — Server-Sent Events (SSE) stream agent progress live to the browser
- 🌐 **Flask Web App** — Clean, responsive dark-mode UI — no terminal required

---

## 🏗️ Architecture

```
User enters GitHub repo
        ↓
┌─────────────────────────────────────────┐
│           Orchestrator                  │
│                                         │
│  ┌──────────┐    ┌──────────────────┐   │
│  │ RAG Agent│───▶│  ChromaDB Vector │   │
│  │ (index)  │    │  Store           │   │
│  └──────────┘    └──────────────────┘   │
│       │                                 │
│       ▼                                 │
│  ┌──────────────┐                       │
│  │ Fetch Agent  │ ← GitHub API          │
│  └──────────────┘                       │
│       │                                 │
│       ▼                                 │
│  ┌──────────────┐                       │
│  │  Security    │ ← CRITICAL/MEDIUM/LOW │
│  │  Scanner     │                       │
│  └──────────────┘                       │
│       │                                 │
│       ▼                                 │
│  ┌──────────────┐                       │
│  │ Review Agent │ ← RAG context aware   │
│  └──────────────┘                       │
│       │                                 │
│       ▼                                 │
│  ┌──────────────┐                       │
│  │ Critic Agent │ ← self-improvement    │
│  └──────────────┘                       │
│       │                                 │
│       ▼                                 │
│  ┌──────────────┐                       │
│  │ Fix Suggester│ ← before/after diffs  │
│  └──────────────┘                       │
└─────────────────────────────────────────┘
        ↓
  Results streamed live to browser
  + RAG Chat enabled
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **LLM** | LLaMA 3.3 70B via Groq API |
| **Agent Framework** | LangChain + LangGraph (ReAct pattern) |
| **RAG** | HuggingFace Embeddings + ChromaDB |
| **Embeddings Model** | `all-MiniLM-L6-v2` (local, no API cost) |
| **GitHub Integration** | PyGitHub API |
| **Backend** | Flask + Server-Sent Events (SSE) |
| **Frontend** | Vanilla JS + Marked.js |
| **Streaming** | Real-time SSE pipeline |

---

## ⚙️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/your-username/bugeye
cd bugeye
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set up environment variables
```bash
cp .env.example .env
```

Edit `.env`:
```env
GROQ_API_KEY=your_groq_api_key
GITHUB_TOKEN=your_github_token
GROQ_MODEL=llama-3.3-70b-versatile
```

Get your keys:
- **Groq API Key** (free) → [console.groq.com](https://console.groq.com)
- **GitHub Token** → [github.com/settings/tokens](https://github.com/settings/tokens) (needs `repo` scope)

### 5. Run
```bash
python app.py
```

Open **http://127.0.0.1:5000** 🚀

---

## 📖 Usage

1. Enter any public GitHub repository (e.g. `panwaar/Portfolio`)
2. Click **Analyze Repository**
3. Watch the **multi-agent pipeline** run in real time
4. View results across 3 tabs:
   - 📝 **Code Review** — quality issues with file + line references
   - 🔒 **Security Report** — vulnerabilities by severity (CRITICAL / MEDIUM / LOW)
   - 🔧 **Code Fixes** — before/after code replacements
5. Use the **RAG Chat** to ask anything about the codebase

---

## 📁 Project Structure

```
bugeye/
├── app.py            # Flask server + SSE streaming endpoints
├── agent.py          # Multi-agent orchestrator
├── rag_tools.py      # RAG indexing + codebase chat
├── github_tools.py   # GitHub API integration
├── prompts.py        # Agent system prompts
├── requirements.txt
├── .env.example
└── templates/
    └── index.html    # Frontend UI
```

---

## 🧠 How the Multi-Agent Pipeline Works

BugEye uses the **ReAct (Reason + Act)** agentic pattern via LangGraph:

1. **RAG Agent** — mandatory first step. Clones repo, chunks code files, creates embeddings, stores in ChromaDB. If this fails, pipeline stops — no hallucinated reviews.
2. **Fetch Agent** — uses RAG context to load relevant codebase sections
3. **Security Scanner** — specialized LLM agent focused purely on vulnerability detection
4. **Review Agent** — performs deep code quality analysis with full codebase context
5. **Critic Agent** — reads the review and improves it (self-critique loop)
6. **Fix Suggester** — generates concrete before/after code replacements

Results stream to the browser in real time via **Server-Sent Events**.

---

## 🔑 Key Concepts Demonstrated

- **Multi-Agent Orchestration** — coordinating specialized agents in a pipeline
- **RAG (Retrieval Augmented Generation)** — grounding LLM responses in real codebase data
- **Agentic AI** — autonomous agents that decide and act without hardcoded logic
- **Streaming AI** — real-time SSE for live agent progress updates
- **Self-Critique Pattern** — agents improving their own output
- **Tool Use** — LLM agents calling GitHub API as tools

---

## 📄 License

MIT License — feel free to use and build on this.

---

<div align="center">
Built with ❤️ using LangChain, LangGraph, Groq, and ChromaDB
</div>