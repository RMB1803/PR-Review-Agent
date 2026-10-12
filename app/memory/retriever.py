import logging

from google import genai
from google.genai import types
from sqlalchemy import select

from app.config import get_settings
from app.database.connection import SessionLocal
from app.database.models import CodeChunkRecord

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = 8000
MAX_QUERY_CHARS = 2000
MIN_SIMILARITY_SCORE = 0.5


async def embed_text(text: str, settings=None) -> list[float]:
    """Generate a 256-dimensional Gemini embedding."""
    if not text or not text.strip():
        return []

    cfg = settings or get_settings()

    if not cfg.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    client = genai.Client(api_key=cfg.gemini_api_key)

    try:
        response = await client.aio.models.embed_content(
            model=cfg.gemini_embedding_model,
            contents=text[:MAX_EMBED_CHARS],
            config=types.EmbedContentConfig(
                output_dimensionality=cfg.tiger_embedding_dim,
            ),
        )

        if not response.embeddings:
            raise RuntimeError("Gemini returned no embeddings.")

        vector = response.embeddings[0].values

        if vector is None:
            raise RuntimeError("Gemini returned an empty embedding.")

        if len(vector) != cfg.tiger_embedding_dim:
            raise RuntimeError(
                f"Expected {cfg.tiger_embedding_dim} embedding dimensions, "
                f"received {len(vector)}."
            )

        return vector

    finally:
        await client.aio.aclose()


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