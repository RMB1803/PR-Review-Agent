# backend/database/models.py
#
# SQLAlchemy ORM table definitions for the structured persistence layer.
#
# TABLES:
#   PRReviewRecord   — one row per PR review run
#   FindingRecord    — one row per finding within a review
#
# DESIGN DECISIONS:
#
# 1. UUID PRIMARY KEYS (from Storage-Engines.md wiki):
#    "Use UUIDs for distributed-safe primary keys."
#    Integer auto-increment IDs create a bottleneck: every insert must hit the
#    sequence. UUIDs are generated client-side (no DB round-trip) and work
#    safely across multiple application instances. We generate them in Python
#    (uuid.uuid4()) not in the DB, so the app controls the ID before insert.
#
# 2. COMPOSITE INDEX ON (repo_full_name, severity):
#    (From Storage-Engines.md wiki, "Index Design"):
#    "Index the columns you filter on, in the order you filter them."
#    The most common query is: "all HIGH+ findings for repo X in last 30 days"
#    That's WHERE repo_full_name = ? AND severity IN (?, ?).
#    A composite index on (repo_full_name, severity) serves this query in O(log n)
#    instead of a full table scan.
#    ORDER: repo_full_name first (higher cardinality prefix = better selectivity).
#
# 3. ENUM AS VARCHAR:
#    (From Production-Hardening.md wiki, "Operational Flexibility"):
#    Storing enum values as VARCHAR rather than native DB ENUM types means
#    we can add new enum values without a DB migration.
#    Native ENUM requires ALTER TYPE in Postgres — a DDL operation that takes
#    an exclusive lock. VARCHAR avoids that operational risk.
#    Trade-off: no DB-level enum validation. We enforce that in the Python layer
#    (ReviewStatus, FindingSeverity etc. are validated before insert).
#
# 4. RELATIONSHIP LOADING:
#    FindingRecord has a FK to PRReviewRecord.
#    We use lazy="selectin" so that when we load a PRReviewRecord, SQLAlchemy
#    automatically loads its findings in a second SELECT (not N+1 queries).
#    "selectin" is the async-safe loading strategy — "lazy" loading (the default)
#    does not work with async sessions.
#    (Storage-Engines.md: "N+1 queries are the #1 async ORM footgun.")

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class PRReviewRecord(Base):
    __tablename__ = "pr_review_records"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    repo_full_name: Mapped[str] = mapped_column(String, nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    head_commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    diff_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False
    )
    review_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_review_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FindingRecord(Base):
    __tablename__ = "finding_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    review_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pr_review_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("finding_review_idx", "review_id"),
        Index("finding_severity_idx", "severity"),
    )


class RepoFileIndexRecord(Base):
    __tablename__ = "repo_file_index"

    repo_full_name: Mapped[str] = mapped_column(
        Text, primary_key=True
    )
    file_path: Mapped[str] = mapped_column(
        Text, primary_key=True
    )
    file_sha: Mapped[str] = mapped_column(String, nullable=False)
    last_indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )


class CodeChunkRecord(Base):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    repo_full_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "repo_full_name",
            "file_path",
            "chunk_index",
            name="unique_code_chunk",
        ),
        Index("code_chunks_repo_path_idx", "repo_full_name", "file_path"),
        Index(
            "code_chunks_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )