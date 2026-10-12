import logging

from openai import AsyncOpenAI
from sqlalchemy import select

from app.config import get_settings
from app.database.connection import SessionLocal
from app.database.models import CodeChunkRecord

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = 8000
MAX_QUERY_CHARS = 2000
MIN_SIMILARITY_SCORE = 0.5


async def embed_text(text: str, settings=None) -> list[float]:
    """Generate a 256-dimensional embedding using OpenAI."""
    if not text or not text.strip():
        return []

    cfg = settings or get_settings()

    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    client = AsyncOpenAI(api_key=cfg.openai_api_key)

    try:
        response = await client.embeddings.create(
            model=cfg.openai_embedding_model,
            input=text[:MAX_EMBED_CHARS],
            dimensions=cfg.tiger_embedding_dim,
        )

        return response.data[0].embedding

    finally:
        await client.close()


async def retrieve_context_for_diff(
    diff: str,
    repo_full_name: str,
    settings=None,
) -> str:
    """Retrieve repository code chunks relevant to a PR diff."""
    if not diff or not diff.strip():
        return ""

    try:
        cfg = settings or get_settings()
        vector = await embed_text(diff[:MAX_QUERY_CHARS], cfg)

        if not vector:
            return ""

        async with SessionLocal() as session:
            distance = CodeChunkRecord.embedding.cosine_distance(vector)

            query = (
                select(CodeChunkRecord, distance.label("distance"))
                .where(
                    CodeChunkRecord.repo_full_name == repo_full_name
                )
                .order_by(distance)
                .limit(5)
            )

            result = await session.execute(query)
            rows = result.all()

        chunks = []

        for record, distance_value in rows:
            similarity = 1 - float(distance_value)

            if similarity >= MIN_SIMILARITY_SCORE:
                chunks.append(
                    f"[{record.file_path} | similarity: {similarity:.2f}]\n"
                    f"{record.content}"
                )

        if not chunks:
            return ""

        return (
            "--- Relevant Repository Context ---\n"
            + "\n---\n".join(chunks)
            + "\n--- End Context ---"
        )

    except Exception:
        logger.exception("Failed to retrieve repository context")
        return ""