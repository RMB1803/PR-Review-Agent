from typing import Any

from app.review.state import (
    FindingSeverity,
    PRReviewState,
    ReviewVerdict,
)


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "info": 4,
    }

    return sorted(
        findings,
        key=lambda finding: order.get(
            finding.get("severity", "low").lower(), 3
        ),
    )


def aggregate_agent_findings(state: PRReviewState) -> dict[str, Any]:
    """Combine agent findings and determine the review verdict."""
    results = state.get("agent_results", [])

    security_result = next(
        (result for result in results if result["agent_type"] == "security"),
        None,
    )

    # Do not approve a PR if the security agent failed.
    if security_result is None or not security_result["success"]:
        return {
            "verdict": ReviewVerdict.REQUEST_CHANGES,
            "final_findings": [],
        }

    findings = []

    for result in results:
        if result["success"]:
            findings.extend(result.get("findings", []))

    findings = sort_findings(findings)

    has_blocking_issues = any(
        finding.get("severity", "").lower() in ("critical", "high")
        for finding in findings
    )

    verdict = (
        ReviewVerdict.REQUEST_CHANGES
        if has_blocking_issues
        else ReviewVerdict.APPROVE
    )

    return {
        "verdict": verdict,
        "final_findings": findings,
    }


def format_review_markdown(state: PRReviewState) -> str:
    """Format the findings as a Markdown review."""
    verdict = state.get("verdict")
    findings = state.get("final_findings", [])

    if verdict == ReviewVerdict.APPROVE:
        heading = "## ✅ Review Passed"
    else:
        heading = "## ❌ Changes Requested"

    counts = {
        severity: sum(
            1
            for finding in findings
            if finding.get("severity", "low").lower() == severity
        )
        for severity in ("critical", "high", "medium", "low", "info")
    }

    summary = ", ".join(
        f"{count} {severity}"
        for severity, count in counts.items()
        if count
    ) or "No findings"

    lines = [heading, f"**Summary:** {summary}", "", "### Findings"]

    if not findings:
        lines.append("No issues found.")
    else:
        for finding in findings:
            severity = finding.get("severity", "low").upper()
            agent = finding.get("agent_type", "agent").capitalize()
            file_path = finding.get("file_path") or "General"
            line = finding.get("line_start")
            location = f"`{file_path}:{line}`" if line else f"`{file_path}`"
            description = finding.get("summary", "")
            suggestion = finding.get("suggestion", "")

            lines.append(
                f"- **[{severity}]** {location} — {description} ({agent})"
            )

            if suggestion:
                lines.append(f"  - Suggestion: {suggestion}")

    return "\n".join(lines)