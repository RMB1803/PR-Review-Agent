# PR-Review-Agent

An automated AI-powered Pull Request code review system built with FastAPI, LangGraph, ARQ, and PostgreSQL.

## Target Architecture

```
GitHub PR Webhook
       │
       ▼
FastAPI (/webhook/github)
       │ (HMAC-SHA256 signature verification)
       ▼
Redis & ARQ Queue (run_pr_review)
       │
       ▼
LangGraph Orchestration
   ├── build_context (GitHub diff & semantic context retrieval)
   ├── fan_out_agents (SecurityAgent & QualityAgent run concurrently)
   ├── aggregate_results (Arbitration & verdict)
   └── post_review (Terminal output, database persistence, GitHub PR review)
```

## Directory Structure

```
PR-Review-Agent/
├── app/
│   ├── main.py              # FastAPI app & lifespan
│   ├── config.py            # Pydantic Settings
│   ├── api/
│   │   └── webhook.py       # Webhook endpoint & HMAC validation
│   ├── workers/
│   │   └── review_worker.py # ARQ worker tasks & enqueuing
│   ├── review/
│   │   ├── graph.py         # LangGraph workflow StateGraph
│   │   ├── state.py         # Review TypedDict state definitions
│   │   ├── agents.py        # SecurityAgent & QualityAgent
│   │   └── aggregator.py    # Findings aggregation & formatting
│   ├── github/
│   │   └── client.py        # Async GitHub REST client
│   ├── memory/
│   │   ├── ingestion.py     # Code repository tree ingestion
│   │   └── retriever.py     # Semantic code context retrieval
│   └── database/
│       ├── connection.py    # Async SQLAlchemy & Tiger Cloud pool
│       ├── models.py        # ORM models (PRReviewRecord, FindingRecord, etc.)
│       └── repository.py    # DB save and query operations
├── migrations/
│   └── init.sql             # SQL schema migration
├── tests/
│   ├── test_webhook.py      # Webhook & HMAC signature tests
│   └── test_review.py       # Review graph & aggregation tests
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Install Dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Run FastAPI Application
```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Run ARQ Worker
```bash
arq app.workers.review_worker.WorkerSettings
```

### 5. Run Tests
```bash
pytest tests/
```
