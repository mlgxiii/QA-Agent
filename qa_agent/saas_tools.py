"""
GitHub API tools for the SaaS Framework Discovery Agent.

All network calls go through the standard-library `urllib.request` so no extra
dependencies are required.  Pass a GitHub personal-access token via the
GITHUB_TOKEN environment variable (or the `gh_token` argument) to raise the
rate limit from 10 req/min (unauthenticated) to 5 000 req/hour.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_GH_API = "https://api.github.com"
_README_MAX_CHARS = 6_000   # truncate long READMEs to keep context manageable
_SEARCH_MAX = 10            # hard cap on search results returned to the model

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "search_github_repos",
        "description": (
            "Search GitHub for popular open-source repositories using GitHub's search syntax. "
            "Returns repos sorted by star count. "
            "Example queries: 'topic:ai language:python stars:>1000', "
            "'chatbot self-hosted stars:>2000 language:typescript'. "
            "Always include a stars threshold to filter for proven projects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "GitHub search query string. Supports qualifiers like "
                        "topic:, language:, stars:>N. "
                        "Example: 'topic:llm stars:>1000 language:python'"
                    ),
                },
                "per_page": {
                    "type": "integer",
                    "description": f"Number of results to return (1–{_SEARCH_MAX}, default 8).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_repo_details",
        "description": (
            "Fetch detailed metadata for a specific GitHub repository: "
            "star count, forks, license, topics, primary language, last push date, "
            "open issues count, and the repository description. "
            "Call this after search_github_repos to get licence information before "
            "deciding whether to read the README."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "Repository owner (GitHub username or organisation).",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name (the part after the slash in the URL).",
                },
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "fetch_readme",
        "description": (
            "Fetch and decode the README of a GitHub repository (up to 6 000 characters). "
            "Use this to understand what the project does, how to deploy it, and what "
            "integrations it supports — all key signals for SaaS packaging potential."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "Repository owner.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name.",
                },
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "write_report",
        "description": (
            "Save the final SaaS framework discovery report as a Markdown file. "
            "Call this exactly once when the complete analysis is ready."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Complete Markdown report content.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Destination file path for the report.",
                },
            },
            "required": ["content", "output_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name: str, inputs: dict[str, Any], gh_token: str = "") -> str:
    """Dispatch a tool call and return the result as a string."""
    token = gh_token or os.environ.get("GITHUB_TOKEN", "")
    try:
        match name:
            case "search_github_repos":
                return _search_github_repos(
                    inputs["query"],
                    per_page=min(int(inputs.get("per_page", 8)), _SEARCH_MAX),
                    token=token,
                )
            case "get_repo_details":
                return _get_repo_details(inputs["owner"], inputs["repo"], token=token)
            case "fetch_readme":
                return _fetch_readme(inputs["owner"], inputs["repo"], token=token)
            case "write_report":
                return _write_report(inputs["content"], inputs["output_path"])
            case _:
                return f"Error: unknown tool '{name}'"
    except KeyError as exc:
        return f"Error: missing required parameter {exc} for tool '{name}'"
    except Exception as exc:
        return f"Error executing '{name}': {exc}"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _gh_headers(token: str) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "saas-discovery-agent/1.0",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_get(url: str, token: str) -> dict | list:
    """Make a GitHub API GET request and return parsed JSON."""
    req = urllib.request.Request(url, headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "rate limit" in body.lower():
            raise RuntimeError(
                "GitHub API rate limit hit. Add a GITHUB_TOKEN to get 5 000 requests/hour "
                "instead of 60/hour."
            ) from exc
        raise RuntimeError(f"GitHub API {exc.code}: {body[:300]}") from exc


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _search_github_repos(query: str, per_page: int = 8, token: str = "") -> str:
    url = (
        f"{_GH_API}/search/repositories"
        f"?q={urllib.parse.quote(query)}"
        f"&sort=stars&order=desc&per_page={per_page}"
    )
    # Lazy import — `urllib.parse` is stdlib but not imported at top level
    import urllib.parse  # noqa: PLC0415

    url = (
        f"{_GH_API}/search/repositories"
        f"?q={urllib.parse.quote(query)}"
        f"&sort=stars&order=desc&per_page={per_page}"
    )

    data = _gh_get(url, token)
    items = data.get("items", [])
    total = data.get("total_count", 0)

    if not items:
        return f"No repositories found for query: {query!r}"

    lines = [f"[GitHub Search: {query!r} — {total:,} total results, showing top {len(items)}]\n"]
    for i, repo in enumerate(items, 1):
        license_name = (repo.get("license") or {}).get("spdx_id") or "Unknown"
        topics = ", ".join(repo.get("topics", [])[:6]) or "—"
        lines.append(
            f"{i}. **{repo['full_name']}** ⭐ {repo['stargazers_count']:,}\n"
            f"   Lang: {repo.get('language') or '—'}  |  License: {license_name}  |  "
            f"Forks: {repo['forks_count']:,}\n"
            f"   Topics: {topics}\n"
            f"   URL: {repo['html_url']}\n"
            f"   Description: {repo.get('description') or '—'}\n"
        )

    # Polite pause to stay within GitHub's secondary rate limits
    time.sleep(0.5)
    return "\n".join(lines)


def _get_repo_details(owner: str, repo: str, token: str = "") -> str:
    url = f"{_GH_API}/repos/{owner}/{repo}"
    data = _gh_get(url, token)

    license_info = data.get("license") or {}
    license_name = license_info.get("spdx_id") or license_info.get("name") or "No license"
    topics = ", ".join(data.get("topics", [])[:10]) or "—"
    pushed = (data.get("pushed_at") or "unknown")[:10]

    lines = [
        f"[Repository: {data['full_name']}]",
        f"Stars:        {data['stargazers_count']:,}",
        f"Forks:        {data['forks_count']:,}",
        f"Open issues:  {data['open_issues_count']:,}",
        f"Language:     {data.get('language') or '—'}",
        f"License:      {license_name}",
        f"Topics:       {topics}",
        f"Last push:    {pushed}",
        f"Homepage:     {data.get('homepage') or '—'}",
        f"URL:          {data['html_url']}",
        f"Description:  {data.get('description') or '—'}",
        f"Has wiki:     {data.get('has_wiki', False)}",
        f"Has pages:    {data.get('has_pages', False)}",
        f"Archived:     {data.get('archived', False)}",
    ]

    time.sleep(0.3)
    return "\n".join(lines)


def _fetch_readme(owner: str, repo: str, token: str = "") -> str:
    url = f"{_GH_API}/repos/{owner}/{repo}/readme"
    try:
        data = _gh_get(url, token)
    except RuntimeError as exc:
        if "404" in str(exc):
            return f"No README found for {owner}/{repo}."
        raise

    encoding = data.get("encoding", "")
    raw_content = data.get("content", "")

    if encoding == "base64":
        try:
            decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
        except Exception as exc:
            return f"Error decoding README: {exc}"
    else:
        decoded = raw_content

    if len(decoded) > _README_MAX_CHARS:
        decoded = decoded[:_README_MAX_CHARS] + f"\n\n[... README truncated at {_README_MAX_CHARS} chars ...]"

    time.sleep(0.3)
    return f"[README: {owner}/{repo}  ({len(decoded)} chars shown)]\n\n{decoded}"


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
