import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.workers.review_worker import enqueue_review_job


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


def validate_github_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> None:
    """Verify the GitHub webhook HMAC-SHA256 signature."""

    if not signature or not signature.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed GitHub signature.",
        )

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    received_signature = signature.removeprefix("sha256=")

    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub signature.",
        )


@router.post("/github")
async def receive_github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    cfg: Settings = Depends(get_settings),
) -> JSONResponse:
    """Validate a GitHub webhook and enqueue a PR review."""

    raw_body = await request.body()

    # Verify the webhook signature before processing the payload.
    validate_github_signature(
        payload=raw_body,
        signature=x_hub_signature_256,
        secret=cfg.github_webhook_secret,
    )

    # Ignore events other than pull_request.
    if x_github_event != "pull_request":
        return JSONResponse(
            content={"status": "ignored", "reason": "Unsupported event"},
        )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    # Process only relevant pull request actions.
    action = payload.get("action")
    if action not in {"opened", "synchronize", "reopened"}:
        return JSONResponse(
            content={"status": "ignored", "reason": "Unsupported action"},
        )

    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}

    repo_full_name = repo.get("full_name")
    pr_number = payload.get("number") or pr.get("number")
    head_sha = (pr.get("head") or {}).get("sha")

    if not repo_full_name or not pr_number or not head_sha:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing repository, PR number, or head commit SHA.",
        )

    workflow_id = f"{repo_full_name}:{pr_number}:{head_sha}"

    input_data = {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "pr_title": pr.get("title") or "",
        "pr_body": pr.get("body") or "",
        "author_login": (pr.get("user") or {}).get("login", ""),
        "head_commit_sha": head_sha,
        "base_branch": (pr.get("base") or {}).get("ref", "main"),
    }

    # Enqueue the review; the worker performs the actual analysis.
    try:
        await enqueue_review_job(workflow_id, input_data)
    except Exception:
        logger.exception("Failed to enqueue review job: %s", workflow_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue PR review.",
        )

    return JSONResponse(
        content={
            "status": "queued",
            "workflow_id": workflow_id,
        },
        status_code=status.HTTP_202_ACCEPTED,
    )