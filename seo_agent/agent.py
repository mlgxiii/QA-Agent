import json
import os
from typing import Optional

import anthropic

from .prompts import SEO_GEO_SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool

ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}


class SEOGEOAgent:
    """
    SEO + GEO (Generative Engine Optimization) analysis and rewriting agent.

    Powered by Claude with extended thinking, tool use, and prompt caching.
    Two modes:
      - 'analyze': audit a URL or content for SEO/GEO issues and opportunities
      - 'rewrite': return an optimised rewrite of the content
    """

    DEFAULT_MODEL = "claude-opus-4-7"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.model = model or self.DEFAULT_MODEL

    def run(
        self,
        content: str,
        target_keywords: list[str] | None = None,
        competitor_urls: list[str] | None = None,
        mode: str = "analyze",
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        """
        Run the SEO/GEO agent.

        Args:
            content:          URL (https://...) or raw text/HTML content.
            target_keywords:  List of target keywords to optimise for.
            competitor_urls:  Optional competitor URLs to benchmark against.
            mode:             'analyze' | 'rewrite' | 'both'
            output_report:    If given, saves the report to this path.
            verbose:          Print Claude's thinking blocks.
        """
        user_message = self._build_user_message(
            content, target_keywords, competitor_urls, mode, output_report
        )
        messages: list[dict] = [{"role": "user", "content": user_message}]

        print(f"\n🔍  SEO/GEO Agent  [{self.model}]  mode={mode}")
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
                print("\n\n✅  Analysis complete!")
                self._print_stats(response, total_tool_calls)
                break

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response)
                messages.append({"role": "user", "content": tool_results})
                print()

            elif response.stop_reason == "pause_turn":
                pass

            else:
                print(f"\n⚠️  Unexpected stop_reason: {response.stop_reason!r}")
                break

    def _build_user_message(
        self,
        content: str,
        target_keywords: list[str] | None,
        competitor_urls: list[str] | None,
        mode: str,
        output_report: Optional[str],
    ) -> str:
        is_url = content.strip().startswith(("http://", "https://"))

        kw_section = ""
        if target_keywords:
            kw_section = f"\n**Target keywords:** {', '.join(target_keywords)}"

        competitor_section = ""
        if competitor_urls:
            urls = "\n".join(f"- {u}" for u in competitor_urls)
            competitor_section = (
                f"\n\n**Competitor URLs to benchmark against:**\n{urls}\n"
                "Fetch each competitor and compare their SEO/GEO signals to the target."
            )

        if mode == "rewrite":
            task = (
                "Produce an optimised **rewrite** of the page content. "
                "Return: (1) the full rewritten body content, "
                "(2) an optimised title tag, "
                "(3) an optimised meta description, "
                "(4) recommended JSON-LD schema markup. "
                "Show the original versus new value for every changed element."
            )
        elif mode == "both":
            task = (
                "First produce a full SEO/GEO analysis report, then produce an optimised "
                "rewrite of the content with improved meta tags and schema markup."
            )
        else:
            task = (
                "Produce a comprehensive SEO/GEO analysis report with scoring, "
                "prioritised recommendations, optimised meta tags, and suggested schema markup."
            )

        report_note = (
            f"Save the final report using `write_seo_report` to: {output_report}"
            if output_report
            else "Present the complete report in your final response."
        )

        if is_url:
            input_section = (
                f"**URL to analyse:** {content.strip()}\n\n"
                "Use `fetch_url` to retrieve the page, then `extract_page_data` to parse it."
            )
        else:
            input_section = (
                "**Content to analyse (raw text/HTML provided):**\n\n"
                f"{content[:8000]}"
                + ("\n\n[... content truncated for brevity ...]" if len(content) > 8000 else "")
            )

        return f"""{input_section}
{kw_section}
{competitor_section}

**Task:** {task}

**Analysis steps:**
1. {'Fetch the URL and extract page data.' if is_url else 'Extract page data from the provided content.'}
2. Run `analyze_readability` on the body text.
3. Run `analyze_keyword_density` for each target keyword.
4. Run `score_geo_signals` to evaluate AI citation readiness.
5. {'Fetch and compare competitor pages.' if competitor_urls else 'Skip competitor comparison (none provided).'}
6. Synthesise all findings into the structured report.

{report_note}

Be specific — always show **current value → recommended value**. Cite exact text that needs changing.
"""

    def _run_turn(
        self,
        messages: list[dict],
        verbose: bool,
        iteration: int,
    ) -> tuple[anthropic.types.Message, int]:
        tool_calls_seen = 0

        with self.client.messages.stream(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 8000},
            system=[
                {
                    "type": "text",
                    "text": SEO_GEO_SYSTEM_PROMPT,
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
            print(f"\n\n📦 Prompt cached: {cache_created:,} tokens written", flush=True)
        elif cache_read and iteration == 1:
            print(f"\n\n⚡ Cache hit: {cache_read:,} tokens from cache", flush=True)

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

            result_text = execute_tool(block.name, block.input)

            preview = result_text.replace("\n", " ")
            if len(preview) > 110:
                preview = preview[:107] + "…"
            print(f"   ✓ {preview}", flush=True)

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        return results

    @staticmethod
    def _print_stats(response: anthropic.types.Message, total_tool_calls: int) -> None:
        usage = response.usage
        print("\n📊 Session stats:")
        print(f"   Tool calls:    {total_tool_calls}")
        print(f"   Output tokens: {getattr(usage, 'output_tokens', 0):,}")
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_read:
            print(f"   Cache read:    {cache_read:,} tokens")
