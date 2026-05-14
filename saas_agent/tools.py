"""
GitHub API tools for the SaaS Discovery Agent.

All network calls use the standard-library `urllib` -- no extra dependencies.
Pass a GitHub personal-access token via GITHUB_TOKEN (or the gh_token arg)
to raise the rate limit from 60 req/hour to 5 000 req/hour.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_GH_API = "https://api.github.com"
_README_MAX_CHARS = 6_000
_SEARCH_MAX = 10

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "search_github_repos",
        "description": (
            "Search GitHub for popular open-source repositories using GitHub search syntax. "
            "Results are sorted by star count. "
            "Example queries: 'topic:llm language:python stars:>1000', "
            "'self-hosted chatbot stars:>2000'. "
            "Always include a stars threshold to filter for proven projects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "GitHub search query. Supports qualifiers like topic:, language:, stars:>N. "
                        "Example: 'topic:ai-assistant stars:>1000 language:python'"
                    ),
                },
                "per_page": {
                    "type": "integer",
                    "description": f"Results to return (1-{_SEARCH_MAX}, default 8).",
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
            "open issues, and description. "
            "Call this after search_github_repos to confirm licence before reading the README."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "GitHub username or organisation."},
                "repo": {"type": "string", "description": "Repository name."},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "fetch_readme",
        "description": (
            "Fetch and decode the README of a GitHub repository (up to 6 000 characters). "
            "Use this to understand what the project does, how to deploy it, "
            "and what integrations it supports -- key signals for SaaS packaging potential."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner."},
                "repo": {"type": "string", "description": "Repository name."},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "write_report",
        "description": (
            "Save the final SaaS discovery report as a Markdown file. "
            "Call this exactly once when the full analysis is complete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Complete Markdown report."},
                "output_path": {"type": "string", "description": "File path to save to."},
            },
            "required": ["content", "output_path"],
        },
    },
]


def execute_tool(name: str, inputs: dict[str, Any], gh_token: str = "") -> str:
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
    req = urllib.request.Request(url, headers=_gh_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "rate limit" in body.lower():
            raise RuntimeError(
                "GitHub API rate limit hit. Set GITHUB_TOKEN to get 5 000 requests/hour."
            ) from exc
        raise RuntimeError(f"GitHub API {exc.code}: {body[:300]}") from exc


def _search_github_repos(query: str, per_page: int = 8, token: str = "") -> str:
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

    lines = [f"[GitHub Search: {query!r} -- {total:,} total results, showing top {len(items)}]\n"]
    for i, repo in enumerate(items, 1):
        license_name = (repo.get("license") or {}).get("spdx_id") or "Unknown"
        topics = ", ".join(repo.get("topics", [])[:6]) or "--"
        lines.append(
            f"{i}. **{repo['full_name']}** {repo['stargazers_count']:,} stars\n"
            f"   Lang: {repo.get('language') or '--'}  |  License: {license_name}  |  "
            f"Forks: {repo['forks_count']:,}\n"
            f"   Topics: {topics}\n"
            f"   URL: {repo['html_url']}\n"
            f"   Description: {repo.get('description') or '--'}\n"
        )

    time.sleep(0.5)
    return "\n".join(lines)


def _get_repo_details(owner: str, repo: str, token: str = "") -> str:
    data = _gh_get(f"{_GH_API}/repos/{owner}/{repo}", token)

    license_info = data.get("license") or {}
    license_name = license_info.get("spdx_id") or license_info.get("name") or "No license"
    topics = ", ".join(data.get("topics", [])[:10]) or "--"
    pushed = (data.get("pushed_at") or "unknown")[:10]

    lines = [
        f"[Repository: {data['full_name']}]",
        f"Stars:        {data['stargazers_count']:,}",
        f"Forks:        {data['forks_count']:,}",
        f"Open issues:  {data['open_issues_count']:,}",
        f"Language:     {data.get('language') or '--'}",
        f"License:      {license_name}",
        f"Topics:       {topics}",
        f"Last push:    {pushed}",
        f"Homepage:     {data.get('homepage') or '--'}",
        f"URL:          {data['html_url']}",
        f"Description:  {data.get('description') or '--'}",
        f"Archived:     {data.get('archived', False)}",
    ]

    time.sleep(0.3)
    return "\n".join(lines)


def _fetch_readme(owner: str, repo: str, token: str = "") -> str:
    try:
        data = _gh_get(f"{_GH_API}/repos/{owner}/{repo}/readme", token)
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
        return f"Report saved -> {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
