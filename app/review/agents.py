import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> list[dict[str, Any]]:
    """Extract findings from the model's JSON response."""
    text = text.strip()

    # Handle JSON wrapped in Markdown code fences.
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        if isinstance(data, dict):
            findings = data.get("findings", [])
            if isinstance(findings, list):
                return [item for item in findings if isinstance(item, dict)]

    except json.JSONDecodeError:
        logger.warning("The model returned invalid JSON.")

    return []


class BaseReviewAgent:
    """Shared functionality for code review agents."""

    agent_type = "agent"
    model = "gpt-4o-mini"
    focus = ""

    async def analyze(
        self,
        diff: str,
        pr_title: str,
        pr_body: str,
        repo_name: str,
        context: str = "",
    ) -> tuple[list[dict[str, Any]], float, str]:
        """Analyze a PR diff and return findings, confidence, and verdict."""
        cfg = get_settings()

        if not diff or not diff.strip():
            return [], 1.0, "approve"

        if not cfg.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        prompt = f"""
You are a senior {self.focus} engineer reviewing a pull request.

Repository: {repo_name}
PR title: {pr_title}
PR description: {pr_body}

Relevant repository context:
{context}

PR diff:
{diff[:20000]}

Identify genuine, actionable issues related to your area of expertise.

Return a JSON object in this format:
{{
  "findings": [
    {{
      "file_path": "path/to/file.py",
      "line_start": 42,
      "severity": "high",
      "category": "{self.agent_type}",
      "summary": "Short description",
      "description": "Explanation of the issue",
      "suggestion": "Recommended fix"
    }}
  ]
}}

Allowed severity values: critical, high, medium, low, info.
If no issues are found, return {{"findings": []}}.
Return JSON only. Do not invent file paths or line numbers.
"""

        try:
            client = AsyncOpenAI(api_key=cfg.openai_api_key)

            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You perform precise code reviews. "
                                "Report only actionable issues and return valid JSON."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                )
            finally:
                await client.close()

            text = response.choices[0].message.content or ""
            findings = _extract_json(text)

        except Exception:
            logger.exception("%s agent failed", self.agent_type)
            raise

        for finding in findings:
            finding["agent_type"] = self.agent_type
            finding["severity"] = str(
                finding.get("severity", "low")
            ).lower()
            finding["category"] = finding.get(
                "category", self.agent_type
            )

        has_high_severity = any(
            finding["severity"] in ("critical", "high")
            for finding in findings
        )

        verdict = "request_changes" if has_high_severity else "approve"

        # Confidence is retained only for compatibility with the current graph.
        return findings, 1.0, verdict


class SecurityAgent(BaseReviewAgent):
    """Find security vulnerabilities in PR changes."""

    agent_type = "security"
    model = "gpt-4o"
    focus = "application security"

    def __init__(self):
        self.agent_type = "security"


class QualityAgent(BaseReviewAgent):
    """Find code quality and maintainability issues."""

    agent_type = "quality"
    model = "gpt-4o-mini"
    focus = "code quality and maintainability"

    def __init__(self):
        self.agent_type = "quality"