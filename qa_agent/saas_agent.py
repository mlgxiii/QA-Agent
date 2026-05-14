"""
SaaS Framework Discovery Agent (Anthropic).

Searches GitHub for popular open-source projects and evaluates each one for
SaaS packaging and Whop-listing potential using Claude with extended thinking,
streaming output, and prompt caching.
"""

import json
import os
from typing import Optional

import anthropic

from .saas_prompts import SAAS_SYSTEM_PROMPT
from .saas_tools import TOOL_DEFINITIONS, execute_tool


ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}

# Categories exposed in the UI. Each maps to a curated GitHub search query.
PRESET_CATEGORIES: dict[str, str] = {
    "AI & LLM Tools": "topic:llm OR topic:ai-assistant OR topic:chatbot stars:>1000",
    "Productivity & No-Code": "topic:productivity OR topic:no-code OR topic:automation stars:>1000",
    "Developer Tools": "topic:developer-tools OR topic:devops OR topic:cli stars:>2000",
    "Analytics & Dashboards": "topic:analytics OR topic:dashboard OR topic:business-intelligence stars:>1000",
    "E-commerce & Payments": "topic:ecommerce OR topic:payments OR topic:stripe stars:>500",
    "CMS & Content": "topic:cms OR topic:headless-cms OR topic:blog stars:>1000",
    "Customer Support & CRM": "topic:crm OR topic:helpdesk OR topic:customer-support stars:>500",
    "Security & Auth": "topic:authentication OR topic:oauth OR topic:security stars:>2000",
}


class SaaSFrameworkAgent:
    """
    Discovers and evaluates open-source GitHub frameworks for SaaS packaging on Whop.

    Uses extended thinking, streaming, and prompt caching for cost-efficient
    multi-turn tool loops.
    """

    DEFAULT_MODEL = "claude-opus-4-7"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        gh_token: Optional[str] = None,
    ) -> None:
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.MODEL = model or self.DEFAULT_MODEL
        self.gh_token = gh_token or os.environ.get("GITHUB_TOKEN", "")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(
        self,
        categories: list[str],
        custom_query: str = "",
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        """
        Run the SaaS framework discovery pipeline.

        Args:
            categories:    List of preset category names (from PRESET_CATEGORIES keys)
                           or raw GitHub search query strings.
            custom_query:  An additional free-form GitHub search query.
            output_report: Path to save the Markdown report. If None, prints to stdout.
            verbose:       Stream the model's extended thinking output.
        """
        user_message = self._build_user_message(categories, custom_query, output_report)
        messages: list[dict] = [{"role": "user", "content": user_message}]

        category_label = ", ".join(categories) if categories else "custom query"
        print(f"\n🔍  Discovering SaaS frameworks — {category_label}  [{self.MODEL}]")
        if not self.gh_token:
            print(
                "⚠️   No GITHUB_TOKEN set. Rate limited to ~10 searches/min. "
                "Add a token for 5 000 req/hour."
            )
        print("=" * 70)

        iteration = 0
        total_tool_calls = 0

        while True:
            iteration += 1
            response, tool_calls_this_turn = self._run_turn(
                messages, verbose=verbose, iteration=iteration
            )
            total_tool_calls += tool_calls_this_turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                print("\n\n✅  Discovery complete!")
                self._print_stats(response, total_tool_calls)
                break

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response)
                messages.append({"role": "user", "content": tool_results})
                print()

            elif response.stop_reason == "pause_turn":
                pass  # server-side tool loop cap; re-submit

            else:
                print(f"\n⚠️   Unexpected stop_reason: {response.stop_reason!r}")
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        categories: list[str],
        custom_query: str,
        output_report: Optional[str],
    ) -> str:
        report_note = (
            f"When the full analysis is complete call `write_report` and save to: {output_report}"
            if output_report
            else "Present the complete Markdown report in your final response."
        )

        # Resolve preset category names to search queries, pass raw strings through
        search_tasks: list[str] = []
        for cat in categories:
            if cat in PRESET_CATEGORIES:
                search_tasks.append(f'- **{cat}**: `{PRESET_CATEGORIES[cat]}`')
            else:
                search_tasks.append(f'- **Custom**: `{cat}`')

        if custom_query.strip():
            search_tasks.append(f'- **Custom query**: `{custom_query.strip()}`')

        tasks_block = "\n".join(search_tasks) if search_tasks else "- No categories specified"

        return f"""Discover and evaluate the best open-source GitHub projects that can be quickly \
packaged as SaaS products and listed on Whop.

## Search Categories

{tasks_block}

## Instructions

1. For each category above, call `search_github_repos` with the given query to get the top results.
2. For the top 4–6 results per category, call `get_repo_details` to get license and metadata.
   Skip any project with AGPL or no license — they cannot be commercialised easily.
3. Call `fetch_readme` for projects scoring ≥ 6 on License Freedom to assess deployment \
simplicity and feature completeness.
4. Score every evaluated project on the five criteria in your system prompt.
5. Select the top 10 overall (across all categories) and write a comprehensive report.

{report_note}

Focus on projects with:
- ≥ 1 000 GitHub stars
- MIT / Apache 2.0 / BSD / MPL license preferred
- A documented self-hosting path (Docker, docker-compose, or single-command install)
- A clear paying audience and recurring-revenue potential
"""

    def _run_turn(
        self,
        messages: list[dict],
        verbose: bool,
        iteration: int,
    ) -> tuple[anthropic.types.Message, int]:
        tool_calls_seen = 0

        with self.client.messages.stream(
            model=self.MODEL,
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 8000},
            system=[
                {
                    "type": "text",
                    "text": SAAS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOL_DEFINITIONS,
            messages=messages,
        ) as stream:
            current_block_type: str | None = None

            for event in stream:
                if event.type == "content_block_start":
                    btype = event.content_block.type
                    current_block_type = btype
                    if btype == "thinking" and verbose:
                        print("\n💭 Thinking…", flush=True)
                    elif btype == "tool_use":
                        tool_calls_seen += 1

                elif event.type == "content_block_delta":
                    dtype = event.delta.type
                    if dtype == "thinking_delta" and verbose:
                        print(event.delta.thinking, end="", flush=True)
                    elif dtype == "text_delta":
                        print(event.delta.text, end="", flush=True)

                elif event.type == "content_block_stop":
                    if current_block_type == "thinking" and verbose:
                        print()

            final = stream.get_final_message()

        usage = final.usage
        cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_created and iteration == 1:
            print(
                f"\n\n📦 Prompt cached: {cache_created:,} tokens written "
                f"(future runs will be cheaper)",
                flush=True,
            )
        elif cache_read and iteration == 1:
            print(
                f"\n\n⚡ Cache hit: {cache_read:,} tokens served from cache",
                flush=True,
            )

        return final, tool_calls_seen

    def _execute_tool_calls(self, response: anthropic.types.Message) -> list[dict]:
        results: list[dict] = []
        call_index = 0

        for block in response.content:
            if block.type != "tool_use":
                continue

            call_index += 1
            args_preview = json.dumps(block.input)
            if len(args_preview) > 90:
                args_preview = args_preview[:87] + "…"

            print(f"\n🔧 [{call_index}] {block.name}({args_preview})", flush=True)

            result_text = execute_tool(block.name, block.input, self.gh_token)

            preview = result_text.replace("\n", " ")
            if len(preview) > 110:
                preview = preview[:107] + "…"
            print(f"   ✓ {preview}", flush=True)

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                }
            )

        return results

    @staticmethod
    def _print_stats(response: anthropic.types.Message, total_tool_calls: int) -> None:
        usage = response.usage
        out_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        fresh = getattr(usage, "input_tokens", 0) or 0

        print("\n📊 Session stats:")
        print(f"   Tool calls:    {total_tool_calls}")
        print(f"   Output tokens: {out_tokens:,}")
        if cache_read:
            print(f"   Cache read:    {cache_read:,} tokens")
        if fresh:
            print(f"   Fresh input:   {fresh:,} tokens")
