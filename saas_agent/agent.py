"""
SaaS Framework Discovery Agent -- powered by Claude with extended thinking,
streaming output, and prompt caching.
"""

import json
import os
from typing import Optional

import anthropic

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool


ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}

PRESET_CATEGORIES: dict[str, str] = {
    "AI & LLM Tools":            "topic:llm OR topic:ai-assistant OR topic:chatbot stars:>1000",
    "Productivity & No-Code":    "topic:productivity OR topic:no-code OR topic:automation stars:>1000",
    "Developer Tools":           "topic:developer-tools OR topic:devops OR topic:cli stars:>2000",
    "Analytics & Dashboards":    "topic:analytics OR topic:dashboard OR topic:business-intelligence stars:>1000",
    "E-commerce & Payments":     "topic:ecommerce OR topic:payments OR topic:stripe stars:>500",
    "CMS & Content":             "topic:cms OR topic:headless-cms OR topic:blog stars:>1000",
    "Customer Support & CRM":    "topic:crm OR topic:helpdesk OR topic:customer-support stars:>500",
    "Security & Auth":           "topic:authentication OR topic:oauth OR topic:security stars:>2000",
}


class SaaSDiscoveryAgent:
    """
    Discovers and evaluates open-source GitHub projects for SaaS packaging on Whop.
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

    def discover(
        self,
        categories: list[str],
        custom_query: str = "",
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        user_message = self._build_user_message(categories, custom_query, output_report)
        messages: list[dict] = [{"role": "user", "content": user_message}]

        label = ", ".join(categories) if categories else "custom query"
        print(f"\nDiscovering SaaS frameworks -- {label}  [{self.MODEL}]")
        if not self.gh_token:
            print("No GITHUB_TOKEN set -- rate limited to ~60 requests/hour.")
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
                print("\n\nDiscovery complete!")
                self._print_stats(response, total_tool_calls)
                break

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response)
                messages.append({"role": "user", "content": tool_results})
                print()

            elif response.stop_reason == "pause_turn":
                pass

            else:
                print(f"\nUnexpected stop_reason: {response.stop_reason!r}")
                break

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

        search_tasks: list[str] = []
        for cat in categories:
            if cat in PRESET_CATEGORIES:
                search_tasks.append(f'- **{cat}**: `{PRESET_CATEGORIES[cat]}`')
            else:
                search_tasks.append(f'- **Custom**: `{cat}`')

        if custom_query.strip():
            search_tasks.append(f'- **Custom query**: `{custom_query.strip()}`')

        tasks_block = "\n".join(search_tasks) if search_tasks else "- No categories specified"

        return f"""Discover and evaluate the best open-source GitHub projects that can be quickly packaged as SaaS products and listed on Whop.

## Search Categories

{tasks_block}

## Instructions

1. For each category, call `search_github_repos` with the given query to get the top results.
2. For the top 4-6 results per category, call `get_repo_details` to get licence and metadata.
   Skip any project with AGPL or no licence.
3. Call `fetch_readme` for projects scoring >= 6 on Licence Freedom.
4. Score every evaluated project on the five criteria in your system prompt.
5. Select the top 10 overall and write a comprehensive ranked report.

{report_note}

Prioritise projects with >= 1 000 GitHub stars, a permissive licence, a documented self-hosting path, and a clear paying audience.
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
                    "text": SYSTEM_PROMPT,
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
                        print("\nThinking...", flush=True)
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
            print(f"\nPrompt cached: {cache_created:,} tokens written", flush=True)
        elif cache_read and iteration == 1:
            print(f"\nCache hit: {cache_read:,} tokens served from cache", flush=True)

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
                args_preview = args_preview[:87] + "..."

            print(f"\n[{call_index}] {block.name}({args_preview})", flush=True)

            result_text = execute_tool(block.name, block.input, self.gh_token)

            preview = result_text.replace("\n", " ")
            if len(preview) > 110:
                preview = preview[:107] + "..."
            print(f"   -> {preview}", flush=True)

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
        print("\nSession stats:")
        print(f"   Tool calls:    {total_tool_calls}")
        print(f"   Output tokens: {getattr(usage, 'output_tokens', 0) or 0:,}")
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_read:
            print(f"   Cache read:    {cache_read:,} tokens")
        fresh = getattr(usage, "input_tokens", 0) or 0
        if fresh:
            print(f"   Fresh input:   {fresh:,} tokens")
