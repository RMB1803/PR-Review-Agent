import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.config import get_settings
from app.database.connection import SessionLocal
from app.database.models import CodeChunkRecord, RepoFileIndexRecord
from app.memory.retriever import embed_text

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".java", ".rb", ".rs", ".cpp",
    ".c", ".h", ".cs", ".swift", ".kt",
}

MAX_FILE_BYTES = 100_000
CHUNK_SIZE = 3000
CHUNK_OVERLAP = 300


def split_into_chunks(text: str) -> list[str]:
    """Split source code into overlapping text chunks."""
    chunks = []
    start = 0

    while start < len(text):
        chunk = text[start:start + CHUNK_SIZE]

        if chunk.strip():
            chunks.append(chunk)

        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


async def ingest_repository(repo_full_name: str) -> dict[str, Any]:
    """Index repository source files in Tiger Cloud."""
    cfg = get_settings()

    summary = {
        "repo": repo_full_name,
        "total_files": 0,
        "stale_files": 0,
        "embedded": 0,
        "errors": 0,
    }

    if not cfg.github_token:
        logger.warning("GitHub token is not configured.")
        return summary

    headers = {
        "Authorization": f"Bearer {cfg.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    base_url = cfg.github_api_base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
        ) as client:
            # Fetch the repository file tree.
            response = await client.get(
                f"{base_url}/repos/{repo_full_name}/git/trees/HEAD",
                params={"recursive": "1"},
            )
            response.raise_for_status()

            tree = response.json().get("tree", [])

            code_files = {
                item["path"]: item["sha"]
                for item in tree
                if item.get("type") == "blob"
                and item.get("size", 0) <= MAX_FILE_BYTES
                and any(
                    item["path"].endswith(ext)
                    for ext in CODE_EXTENSIONS
                )
            }

            summary["total_files"] = len(code_files)

            # Find files that are new or have changed.
            async with SessionLocal() as session:
                result = await session.execute(
                    select(
                        RepoFileIndexRecord.file_path,
                        RepoFileIndexRecord.file_sha,
                    ).where(
                        RepoFileIndexRecord.repo_full_name == repo_full_name
                    )
                )

                existing_files = {
                    row.file_path: row.file_sha
                    for row in result.all()
                }

            stale_files = {
                path: sha
                for path, sha in code_files.items()
                if existing_files.get(path) != sha
            }

            summary["stale_files"] = len(stale_files)

            # Fetch, chunk, embed, and store changed files.
            for path, sha in stale_files.items():
                try:
                    response = await client.get(
                        f"{base_url}/repos/{repo_full_name}/contents/{path}"
                    )
                    response.raise_for_status()

                    file_data = response.json()
                    encoded_content = file_data.get("content", "")

                    if not encoded_content:
                        continue

                    content = base64.b64decode(
                        encoded_content
                    ).decode("utf-8", errors="replace")

                    chunks = split_into_chunks(content)

                    if not chunks:
                        continue

                    embedded_chunks = []

                    for index, chunk in enumerate(chunks):
                        vector = await embed_text(chunk, cfg)

                        if len(vector) != cfg.tiger_embedding_dim:
                            raise ValueError(
                                f"Expected {cfg.tiger_embedding_dim} "
                                f"embedding dimensions, got {len(vector)}"
                            )

                        embedded_chunks.append(
                            CodeChunkRecord(
                                id=uuid.uuid4(),
                                repo_full_name=repo_full_name,
                                file_path=path,
                                chunk_index=index,
                                content=chunk,
                                embedding=vector,
                            )
                        )

                    # Replace the old chunks and update the file index.
                    async with SessionLocal() as session:
                        await session.execute(
                            delete(CodeChunkRecord).where(
                                CodeChunkRecord.repo_full_name
                                == repo_full_name,
                                CodeChunkRecord.file_path == path,
                            )
                        )

                        session.add_all(embedded_chunks)

                        index_result = await session.execute(
                            select(RepoFileIndexRecord).where(
                                RepoFileIndexRecord.repo_full_name
                                == repo_full_name,
                                RepoFileIndexRecord.file_path == path,
                            )
                        )

                        index_record = index_result.scalar_one_or_none()

                        if index_record is None:
                            index_record = RepoFileIndexRecord(
                                repo_full_name=repo_full_name,
                                file_path=path,
                                file_sha=sha,
                            )
                            session.add(index_record)

                        index_record.file_sha = sha
                        index_record.last_indexed_at = datetime.now(
                            timezone.utc
                        )
                        index_record.token_count = len(content.split())
                        index_record.chunk_count = len(embedded_chunks)

                        await session.commit()

                    summary["embedded"] += 1

                except Exception:
                    logger.exception("Failed to index %s", path)
                    summary["errors"] += 1

    except Exception:
        logger.exception("Repository ingestion failed: %s", repo_full_name)
        summary["errors"] += 1

    return summary