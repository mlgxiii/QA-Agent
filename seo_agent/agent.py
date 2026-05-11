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
    SEO + GEO Optimizer agent.

    Modes:
      analyze  — full scored audit of a URL or content
      rewrite  — optimised content rewrite + meta tags + schema
      both     — audit + rewrite in one run
      brief    — content brief for a keyword (new content creation)
      gap      — competitor content gap analysis (requires competitor_urls)
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
        audience: str = "",
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        user_message = self._build_user_message(
            content, target_keywords, competitor_urls, mode, audience, output_report
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
                print("\n\n✅  Done!")
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

    # ------------------------------------------------------------------
    # Message builders per mode
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        content: str,
        target_keywords: list[str] | None,
        competitor_urls: list[str] | None,
        mode: str,
        audience: str,
        output_report: Optional[str],
    ) -> str:
        report_note = (
            f"Save the final report using `write_seo_report` to: {output_report}"
            if output_report
            else "Present the complete report in your final response."
        )

        if mode == "brief":
            return self._brief_message(content, target_keywords, competitor_urls, audience, report_note)
        if mode == "gap":
            return self._gap_message(content, target_keywords, competitor_urls, report_note)
        return self._analyze_message(content, target_keywords, competitor_urls, mode, report_note)

    def _analyze_message(
        self,
        content: str,
        target_keywords: list[str] | None,
        competitor_urls: list[str] | None,
        mode: str,
        report_note: str,
    ) -> str:
        is_url = content.strip().startswith(("http://", "https://"))
        kw_line = f"\n**Target keywords:** {', '.join(target_keywords)}" if target_keywords else ""

        competitor_section = ""
        if competitor_urls:
            urls = "\n".join(f"- {u}" for u in competitor_urls)
            competitor_section = (
                f"\n\n**Competitor URLs:**\n{urls}\n"
                "After analysing the target, fetch each competitor, extract their page data, "
                "run `compare_content_gap` to identify gaps, and include findings in the report."
            )

        if mode == "rewrite":
            task = (
                "Produce an optimised **rewrite** of the page content. "
                "Return: (1) rewritten body content with all GEO signals applied, "
                "(2) optimised title tag (current → new), "
                "(3) optimised meta description (current → new), "
                "(4) JSON-LD schema. Show original vs new for every change."
            )
        elif mode == "both":
            task = (
                "Produce a full SEO/GEO analysis report, then an optimised rewrite "
                "with improved meta tags and schema markup."
            )
        else:
            task = (
                "Produce a comprehensive SEO/GEO analysis report with scores, "
                "prioritised recommendations, AI answer preview, FAQ opportunities, "
                "optimised meta tags, and schema markup."
            )

        if is_url:
            input_section = (
                f"**URL:** {content.strip()}\n\n"
                "Use `fetch_url` → `extract_page_data` to retrieve the page."
            )
        else:
            input_section = (
                "**Content provided (raw text/HTML):**\n\n"
                + content[:8000]
                + ("\n\n[... truncated ...]" if len(content) > 8000 else "")
            )

        return f"""{input_section}
{kw_line}
{competitor_section}

**Task:** {task}

**Steps:**
1. {'Fetch URL + extract page data.' if is_url else 'Extract page data from provided content.'}
2. `analyze_readability` on body text.
3. `analyze_keyword_density` for each target keyword.
4. `score_geo_signals` — full research-backed GEO audit.
5. `extract_ai_citable_content` — simulate what AI engines would cite.
6. `extract_faq_opportunities` — generate ready-to-paste FAQ section + schema.
7. {'Fetch competitors + `compare_content_gap` for each.' if competitor_urls else 'No competitors provided.'}
8. Synthesise all findings. {report_note}

Show **current value → recommended value** for every finding. Always cite exact text.
"""

    def _gap_message(
        self,
        content: str,
        target_keywords: list[str] | None,
        competitor_urls: list[str] | None,
        report_note: str,
    ) -> str:
        if not competitor_urls:
            return (
                "Error: gap mode requires at least one competitor URL. "
                "Please provide competitor_urls."
            )
        kw_line = f"\n**Target keywords:** {', '.join(target_keywords)}" if target_keywords else ""
        comp_list = "\n".join(f"- {u}" for u in competitor_urls)
        is_url = content.strip().startswith(("http://", "https://"))

        return f"""**Competitor Content Gap Analysis**

**Target:** {content.strip()}
{kw_line}

**Competitors to benchmark against:**
{comp_list}

**Steps:**
1. {'Fetch target URL + `extract_page_data`.' if is_url else 'Extract page data from provided content.'}
2. For each competitor: `fetch_url` + `extract_page_data`.
3. Run `score_geo_signals` on target AND each competitor.
4. Run `compare_content_gap` for each target vs. competitor pair.
5. Run `extract_ai_citable_content` on target to show citability gaps.
6. Produce a structured gap report:
   - Side-by-side GEO signal comparison table
   - Ranked list of heading/topic gaps (competitor covers, target doesn't)
   - Word count comparison with target
   - Top 10 specific additions to make the target outperform all competitors
   - Ready-to-add headings + FAQ questions to fill the gaps
7. {report_note}
"""

    def _brief_message(
        self,
        keyword: str,
        target_keywords: list[str] | None,
        competitor_urls: list[str] | None,
        audience: str,
        report_note: str,
    ) -> str:
        related_kws = ", ".join(target_keywords) if target_keywords else "none provided"
        comp_list = "\n".join(f"- {u}" for u in competitor_urls) if competitor_urls else "none provided"
        audience_line = f"\n**Target audience:** {audience}" if audience else ""

        return f"""**Content Brief Request**

**Primary keyword / topic:** {keyword}
**Related keywords:** {related_kws}{audience_line}

**Competitor URLs to analyse for benchmarking:**
{comp_list}

**Steps:**
1. If competitor URLs provided: `fetch_url` + `extract_page_data` for each.
2. Run `score_geo_signals` on each competitor to understand the GEO bar to beat.
3. Run `extract_faq_opportunities` on competitor content to surface questions to answer.
4. Run `compare_content_gap` across competitors to identify topic coverage patterns.
5. Synthesise into a complete content brief (format below).

**Output — Content Brief format:**

```
# Content Brief: [keyword]

## Target Audience
[who this is for, their intent, knowledge level]

## Search Intent
[informational / commercial / transactional — and why]

## Recommended Title Tag (≤60 chars)
[title]

## Recommended Meta Description (≤160 chars)
[description]

## Recommended URL Slug
[slug]

## Target Word Count
[range — based on competitor analysis or 1200–2000 if no competitors]

## Content Outline
### H1: [question-formatted title]
### H2: [section 1 — answer the core question directly]
  - Key point to cover
  - Statistic to find and include
### H2: [section 2]
  ...
### H2: Frequently Asked Questions
  - Q: [question 1]
  - Q: [question 2]
  ...

## Statistics & Data to Find
[5–8 specific data points to research and include, with suggested source types]

## Attribution Targets
[3–5 types of named sources to cite: industry reports, institutions, studies]

## E-E-A-T Signals to Include
[specific credibility elements: author bio, methodology, date, primary sources]

## Schema Markup to Add
[recommended types with brief rationale]

## GEO Optimisation Checklist
- [ ] Direct answer in first 80 words
- [ ] ≥3 statistics per 500 words
- [ ] ≥3 named-source attributions
- [ ] ≥8 quotable sentences (10–25 words)
- [ ] ≥5 question-formatted H2/H3 headings
- [ ] FAQ section with FAQPage schema
- [ ] Article schema with datePublished + author
```

{report_note}
"""

    # ------------------------------------------------------------------
    # Core engine (shared across all modes)
    # ------------------------------------------------------------------

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
