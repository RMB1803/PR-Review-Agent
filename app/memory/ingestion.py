# app/memory/ingestion.py
"""
Repository code ingestion pipeline.
Fetches source code files from GitHub, chunks/embeds them, and saves to code_chunks.
"""

from __future__ import annotations

import logging
from typing import Any
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import get_session_factory, get_tiger_pool
from app.database.models import RepoFileIndexRecord
from app.memory.retriever import embed_text

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".java", ".rb", ".rs", ".cpp",
    ".c", ".h", ".cs", ".swift", ".kt",
}
MAX_FILE_BYTES = 100_000


async def get_stale_files(
    session: AsyncSession,
    repo_full_name: str,
    file_sha_map: dict[str, str],
) -> dict[str, str]:
    if not file_sha_map:
        return {}

    stmt = select(
        RepoFileIndexRecord.file_path,
        RepoFileIndexRecord.file_sha,
    ).where(RepoFileIndexRecord.repo_full_name == repo_full_name)

    result = await session.execute(stmt)
    rows = result.all()
    embedded = {row.file_path: row.file_sha for row in rows}

    stale: dict[str, str] = {}
    for path, sha in file_sha_map.items():
        if embedded.get(path) != sha:
            stale[path] = sha

    return stale


async def ingest_repository(repo_full_name: str) -> dict[str, Any]:
    """
    Ingests source files from a GitHub repository into code_chunks.
    """
    cfg = get_settings()
    summary = {
        "repo": repo_full_name,
        "total_files": 0,
        "stale_files": 0,
        "embedded": 0,
        "errors": 0,
    }

    if not cfg.github_token:
        logger.warning("ingest_repository | skipped — github_token not set")
        return summary

    headers = {
        "Authorization": f"Bearer {cfg.github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-review-agent/1.0",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
            resp = await client.get(
                f"{cfg.github_api_base_url.rstrip('/')}/repos/{repo_full_name}/git/trees/HEAD?recursive=1"
            )
            if resp.status_code != 200:
                logger.warning("ingest_repository | tree fetch failed | status=%s", resp.status_code)
                return summary

            tree_data = resp.json().get("tree", [])
            code_files = {
                item["path"]: item["sha"]
                for item in tree_data
                if item.get("type") == "blob"
                and any(item["path"].endswith(ext) for ext in CODE_EXTENSIONS)
            }
            summary["total_files"] = len(code_files)

            session_factory = get_session_factory()
            async with session_factory() as session:
                stale = await get_stale_files(session, repo_full_name, code_files)
                summary["stale_files"] = len(stale)

            pool = get_tiger_pool()
            if pool is None:
                logger.warning("ingest_repository | tiger pool not available")
                return summary

            for path, sha in stale.items():
                try:
                    f_resp = await client.get(
                        f"{cfg.github_api_base_url.rstrip('/')}/repos/{repo_full_name}/contents/{path}"
                    )
                    if f_resp.status_code != 200:
                        continue
                    file_content = f_resp.text
                    vector = await embed_text(file_content, cfg)
                    if all(v == 0.0 for v in vector):
                        continue

                    vector_str = f"[{','.join(str(x) for x in vector)}]"
                    chunk_id = str(uuid.uuid4())

                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            INSERT INTO code_chunks (id, repo, path, content, embedding)
                            VALUES ($1, $2, $3, $4, $5::vector)
                            ON CONFLICT (id) DO NOTHING;
                            """,
                            chunk_id, repo_full_name, path, file_content[:4000], vector_str
                        )
                    summary["embedded"] += 1
                except Exception as file_err:
                    logger.debug("ingest_repository | file_error | path=%s err=%s", path, file_err)
                    summary["errors"] += 1

    except Exception as exc:
        logger.warning("ingest_repository | error=%s", exc)
        summary["errors"] += 1

    return summary
