# tests/test_webhook.py
"""
Tests for GitHub webhook signature validation and endpoint handling.
"""

import hashlib
import hmac
import json
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import app
from app.config import get_settings


def make_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_webhook_missing_signature():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/github", content=b"{}")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_invalid_signature():
    transport = ASGITransport(app=app)
    headers = {"X-Hub-Signature-256": "sha256=invalid"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/github", content=b"{}", headers=headers)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_ignore_non_pr_event():
    secret = get_settings().github_webhook_secret
    body = json.dumps({"action": "created"}).encode("utf-8")
    sig = make_signature(secret, body)
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook/github", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_successful_enqueue():
    secret = get_settings().github_webhook_secret
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "title": "feat: login",
            "body": "Add login feature",
            "diff": "diff --git a/auth.py b/auth.py",
            "head": {"sha": "abc1234567890", "ref": "feat-login"},
            "base": {"ref": "main"},
            "user": {"login": "devuser"},
        },
        "repository": {
            "full_name": "org/repo",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = make_signature(secret, body)
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    with patch("app.api.webhook.enqueue_review_job", new_callable=AsyncMock) as mock_enqueue:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/webhook/github", content=body, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "queued"
            assert data["workflow_id"] == "org/repo:42:abc1234567890"
            mock_enqueue.assert_awaited_once()
