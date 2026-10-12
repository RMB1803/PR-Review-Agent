-- ============================================================
-- AI PR Review Agent
-- Simplified PostgreSQL + pgvector schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================
-- 1. PR REVIEW RECORDS
-- One record per reviewed PR revision.
-- ============================================================

CREATE TABLE pr_review_records (
    id                  TEXT PRIMARY KEY,
    repo_full_name      TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    pr_title            TEXT NOT NULL DEFAULT '',
    head_commit_sha     TEXT NOT NULL,
    diff_hash           TEXT,
    verdict             TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    review_body         TEXT,
    github_review_id    BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_review_status
        CHECK (
            status IN ('pending', 'in_progress', 'completed', 'failed')
        ),

    CONSTRAINT valid_review_verdict
        CHECK (
            verdict IS NULL
            OR verdict IN ('approve', 'request_changes')
        )
);

CREATE INDEX pr_review_repo_idx
    ON pr_review_records (repo_full_name);

CREATE INDEX pr_review_created_at_idx
    ON pr_review_records (created_at DESC);


-- ============================================================
-- 2. FINDING RECORDS
-- One record per issue identified by a review agent.
-- ============================================================

CREATE TABLE finding_records (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   TEXT NOT NULL
                    REFERENCES pr_review_records(id)
                    ON DELETE CASCADE,
    agent_type  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    category    TEXT NOT NULL,
    summary     TEXT NOT NULL,
    file_path   TEXT,
    line_start  INTEGER,
    line_end    INTEGER,
    suggestion  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_finding_agent
        CHECK (agent_type IN ('security', 'quality')),

    CONSTRAINT valid_finding_severity
        CHECK (severity IN ('critical', 'high', 'medium', 'low'))
);

CREATE INDEX finding_review_idx
    ON finding_records (review_id);

CREATE INDEX finding_severity_idx
    ON finding_records (severity);


-- ============================================================
-- 3. REPOSITORY FILE INDEX
-- Tracks which versions of repository files have been indexed.
-- ============================================================

CREATE TABLE repo_file_index (
    repo_full_name  TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    file_sha        TEXT NOT NULL,
    last_indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    token_count     INTEGER,
    chunk_count     INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (repo_full_name, file_path)
);


-- ============================================================
-- 4. CODE CHUNKS
-- Stores source-code chunks and embeddings for RAG.
-- ============================================================

CREATE TABLE code_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_full_name  TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding       VECTOR(256) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_code_chunk
        UNIQUE (repo_full_name, file_path, chunk_index)
);

-- Filter chunks by repository and file.
CREATE INDEX code_chunks_repo_path_idx
    ON code_chunks (repo_full_name, file_path);

-- Approximate nearest-neighbor search using cosine distance.
CREATE INDEX code_chunks_embedding_idx
    ON code_chunks
    USING hnsw (embedding vector_cosine_ops);