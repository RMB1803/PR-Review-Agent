# app/review/agents.py
"""
Review Agents: Security and Code Quality.
Uses LLMs to inspect PR diffs and output structured findings.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from app.config import get_settings

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> list[dict[str, Any]]:
    """Helper to extract JSON list or dict with findings from LLM text output."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "findings" in data and isinstance(data["findings"], list):
                return data["findings"]
            return [data]
    except Exception:
        pass
    return []


class SecurityAgent:
    """Specialist agent for detecting security vulnerabilities in code diffs."""

    def __init__(self) -> None:
        self.agent_type = "security"

    async def analyze(
        self,
        diff: str,
        pr_title: str,
        pr_body: str,
        repo_name: str,
        context: str = "",
    ) -> tuple[list[dict[str, Any]], float, str]:
        """
        Analyzes the diff for security flaws.
        Returns: (findings_list, confidence_score, per_verdict)
        """
        cfg = get_settings()
        if not diff or not diff.strip():
            return [], 0.9, "approve"

        prompt = f"""You are a senior application security engineer reviewing a pull request diff.
Repository: {repo_name}
PR Title: {pr_title}
PR Description: {pr_body}

Relevant Repository Context:
{context}

DIFF TO REVIEW:
{diff[:20000]}

Focus on:
1. Hardcoded secrets, keys, passwords, tokens
2. SQL injection, command injection, path traversal
3. Authentication / authorization bypass
4. Insecure cryptographic usages or deserialization
5. SSRF and unsafe external requests

Return a JSON array of findings with this exact schema:
[
  {{
    "file_path": "path/to/file.py",
    "line_start": 42,
    "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
    "category": "security",
    "summary": "Short 1-line description",
    "description": "Clear explanation of the risk",
    "suggestion": "How to fix it",
    "confidence": 0.95
  }}
]
If there are no security issues, return an empty JSON array: [].
Output JSON only. No explanations.
"""
        findings = []
        try:
            if cfg.anthropic_api_key:
                client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
                response = await client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=2000,
                    system="You are a precise security code review engine. You only return valid JSON.",
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text
                findings = _extract_json(text)
            elif cfg.openai_api_key:
                client = AsyncOpenAI(api_key=cfg.openai_api_key)
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are a security code review engine. Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                )
                text = response.choices[0].message.content or ""
                findings = _extract_json(text)
            else:
                logger.info("SecurityAgent: No API key configured; running in mock pass-through mode.")
                findings = []
        except Exception as e:
            logger.error("SecurityAgent failed: %s", e)
            return [], 0.5, "approve"

        # Determine agent verdict
        has_critical = any(f.get("severity", "").upper() == "CRITICAL" for f in findings)
        has_high = any(f.get("severity", "").upper() == "HIGH" for f in findings)
        if has_critical:
            verdict = "critical_block"
        elif has_high:
            verdict = "request_changes"
        else:
            verdict = "approve"

        confidences = [f.get("confidence", 0.8) for f in findings]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.9

        for f in findings:
            f["agent_type"] = "security"
            f["severity"] = f.get("severity", "LOW").lower()

        return findings, mean_conf, verdict


class QualityAgent:
    """Specialist agent for code quality, maintainability, and clean architecture."""

    def __init__(self) -> None:
        self.agent_type = "quality"

    async def analyze(
        self,
        diff: str,
        pr_title: str,
        pr_body: str,
        repo_name: str,
        context: str = "",
    ) -> tuple[list[dict[str, Any]], float, str]:
        cfg = get_settings()
        if not diff or not diff.strip():
            return [], 0.9, "approve"

        prompt = f"""You are a senior software engineer reviewing code quality and maintainability in a PR diff.
Repository: {repo_name}
PR Title: {pr_title}
PR Description: {pr_body}

Relevant Repository Context:
{context}

DIFF TO REVIEW:
{diff[:20000]}

Focus on:
1. Excessively complex functions (>50 lines or >3 nested conditionals)
2. SOLID principle violations and anti-patterns
3. Exception swallowing (bare except:, except Exception: pass)
4. Obvious code duplication or dead code
5. Magic numbers, poor variable naming, broken type safety

Return a JSON array of findings with this exact schema:
[
  {{
    "file_path": "path/to/file.py",
    "line_start": 42,
    "severity": "HIGH" | "MEDIUM" | "LOW",
    "category": "quality",
    "summary": "Short 1-line description",
    "description": "Clear explanation of the quality issue",
    "suggestion": "How to refactor or fix it",
    "confidence": 0.85
  }}
]
If there are no code quality issues, return an empty JSON array: [].
Output JSON only. No explanations.
"""
        findings = []
        try:
            if cfg.openai_api_key:
                client = AsyncOpenAI(api_key=cfg.openai_api_key)
                response = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a code quality review engine. Return valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                )
                text = response.choices[0].message.content or ""
                findings = _extract_json(text)
            elif cfg.anthropic_api_key:
                client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
                response = await client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=2000,
                    system="You are a code quality review engine. Return valid JSON only.",
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text
                findings = _extract_json(text)
            else:
                logger.info("QualityAgent: No API key configured; running in mock pass-through mode.")
                findings = []
        except Exception as e:
            logger.error("QualityAgent failed: %s", e)
            return [], 0.5, "approve"

        has_high = any(f.get("severity", "").upper() in ("HIGH", "CRITICAL") for f in findings)
        verdict = "request_changes" if has_high else "approve"

        confidences = [f.get("confidence", 0.8) for f in findings]
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.9

        for f in findings:
            f["agent_type"] = "quality"
            f["severity"] = f.get("severity", "LOW").lower()

        return findings, mean_conf, verdict
