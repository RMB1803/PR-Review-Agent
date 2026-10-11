# app/memory/retriever.py
"""
Semantic memory retrieval for PR-Review-Agent.
Embeds PR diff query and retrieves relevant code chunks from vector storage.
"""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import Settings, get_settings
from app.database.connection import get_tiger_pool

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = 8_000
EMBEDDING_DIMENSIONS = 256
MAX_QUERY_CHARS = 2_000
MIN_SIMILARITY_SCORE = 0.5


class EmbeddingError(Exception):
    pass


async def embed_text(text: str, settings: Optional[Settings] = None) -> list[float]:
    """Embed a text string into an embedding vector."""
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSIONS

    if len(text) > MAX_EMBED_CHARS:
        text = text[:MAX_EMBED_CHARS]

    cfg = settings or get_settings()
    if not cfg.openai_api_key:
        logger.debug("embed_text | openai_api_key not configured, returning zero vector")
        return [0.0] * EMBEDDING_DIMENSIONS

    client = AsyncOpenAI(api_key=cfg.openai_api_key)
    try:
        response = await client.embeddings.create(
            model=cfg.openai_embedding_model,
            input=text,
            dimensions=cfg.openai_embedding_dimensions,
        )
        return response.data[0].embedding
    except Exception as e:
        raise EmbeddingError(f"OpenAI embedding call failed: {e}") from e


async def retrieve_context_for_diff(
    diff: str,
    repo_full_name: str,
    settings: Optional[Settings] = None,
) -> str:
    """
    Retrieves relevant code context for a PR diff.
    Gracefully returns an empty string on any error.
    """
    if not diff or not diff.strip():
        return ""

    cfg = settings or get_settings()
    query_text = diff[:MAX_QUERY_CHARS].strip()

    try:
        vector = await embed_text(query_text, cfg)
        if not vector or all(v == 0.0 for v in vector):
            return ""

        pool = get_tiger_pool()
        if pool is None:
            return ""

        vector_str = f"[{','.join(str(x) for x in vector)}]"
        query = """
            SELECT path, content, 1 - (embedding <=> $1::vector) AS similarity
            FROM code_chunks
            WHERE repo = $2
            ORDER BY embedding <=> $1::vector ASC
            LIMIT 5;
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, vector_str, repo_full_name)

        if not rows:
            return ""

        chunks_text = []
        for r in rows:
            sim = float(r["similarity"])
            if sim >= MIN_SIMILARITY_SCORE:
                chunks_text.append(f"[{r['path']} (similarity: {sim:.2f})]\n{r['content']}")

        if not chunks_text:
            return ""

        return (
            "--- Relevant Repository Context ---\n"
            + "\n---\n".join(chunks_text)
            + "\n--- End Context ---\n"
        )
    except Exception as e:
        logger.warning("retrieve_context_for_diff | error=%s", e)
        return ""
