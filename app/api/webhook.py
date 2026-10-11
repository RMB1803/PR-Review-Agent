# app/api/webhook.py
"""
GitHub webhook receiver endpoint.
Validates HMAC signature and enqueues PR review jobs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.workers.review_worker import enqueue_review_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


def validate_github_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str,
) -> None:
    """Validates HMAC-SHA256 signature from GitHub."""
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header.",
        )

    if not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed signature header.",
        )

    received_sig = signature_header[len("sha256="):]
    expected_sig = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, received_sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature mismatch.",
        )


@router.post("/github", status_code=status.HTTP_200_OK)
async def receive_github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    """
    Receives and processes GitHub pull_request webhook events.
    """
    raw_body = await request.body()

    # 1. Signature check
    validate_github_signature(
        payload_bytes=raw_body,
        signature_header=x_hub_signature_256,
        secret=cfg.github_webhook_secret,
    )

    # 2. Event type check
    if x_github_event != "pull_request":
        return JSONResponse(
            content={"status": "ignored", "reason": f"event '{x_github_event}' not handled"},
            status_code=status.HTTP_200_OK,
        )

    # 3. Payload parsing
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {exc}",
        ) from exc

    action = payload.get("action")
    if action not in ("opened", "synchronize", "reopened"):
        return JSONResponse(
            content={"status": "ignored", "reason": f"action '{action}' not handled"},
            status_code=status.HTTP_200_OK,
        )

    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", "")
    pr_number = payload.get("number") or pr.get("number")
    head_sha = pr.get("head", {}).get("sha", "")

    if not repo_full_name or not pr_number or not head_sha:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required PR metadata (repo, number, or sha)",
        )

    workflow_id = f"{repo_full_name}:{pr_number}:{head_sha}"
    input_data = {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "pr_title": pr.get("title", ""),
        "pr_body": pr.get("body") or "",
        "pr_diff": pr.get("diff", ""),
        "author_login": pr.get("user", {}).get("login", ""),
        "head_commit_sha": head_sha,
        "base_branch": pr.get("base", {}).get("ref", "main"),
    }

    # 4. Enqueue into ARQ queue
    try:
        await enqueue_review_job(workflow_id, input_data)
    except Exception as exc:
        logger.error("Failed to enqueue review job: %s", exc)
        return JSONResponse(
            content={"status": "queued_error", "error": str(exc)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return JSONResponse(
        content={"status": "queued", "workflow_id": workflow_id},
        status_code=status.HTTP_200_OK,
    )
