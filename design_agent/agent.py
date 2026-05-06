"""
Website Design Analyzer Agent (Anthropic).

Accepts either:
  - A live website URL  (e.g. https://linear.app)
  - A local repo path   (e.g. /tmp/cloned-repo)

Uses Claude with extended thinking + prompt caching to deeply analyse
the design against world-class SaaS design standards.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import anthropic

from .prompts import DESIGN_SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool


ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}


class DesignAnalyzerAgent:
    """
    Website Design Analyzer powered by Claude.

    Streams analysis in real-time, supports both live-URL and repo-path inputs,
    and produces a structured Markdown design audit report.
    """

    DEFAULT_MODEL = "claude-opus-4-7"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.MODEL = model or self.DEFAULT_MODEL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        target: str,
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        """
        Analyse a website or repo for design quality.

        Args:
            target:        A live URL (https://…) or a local directory path.
            output_report: If given, the agent writes the final report here.
            verbose:       Print the model's extended thinking output.
        """
        mode, resolved = self._resolve_target(target)

        user_message = self._build_user_message(mode, resolved, output_report)
        messages: list[dict] = [{"role": "user", "content": user_message}]

        mode_label = "🌐 Web" if mode == "web" else "📁 Repo"
        print(f"\n🎨  Design Analyzer  [{mode_label} mode]  model={self.MODEL}")
        print(f"    Target: {resolved}")
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
                tool_results = self._execute_tool_calls(response, resolved if mode == "repo" else "")
                messages.append({"role": "user", "content": tool_results})
                print()

            elif response.stop_reason == "pause_turn":
                # Server-side iteration cap reached — re-submit.
                pass

            else:
                print(f"\n⚠️   Unexpected stop_reason: {response.stop_reason!r}")
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_target(self, target: str) -> tuple[str, str]:
        """Return (mode, resolved_target).  mode is 'web' or 'repo'."""
        stripped = target.strip()
        parsed = urlparse(stripped)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return "web", stripped
        p = Path(stripped).expanduser().resolve()
        if p.exists() and p.is_dir():
            return "repo", str(p)
        raise ValueError(
            f"Target '{target}' is neither a valid URL nor an existing local directory."
        )

    def _build_user_message(
        self,
        mode: str,
        target: str,
        output_report: Optional[str],
    ) -> str:
        report_note = (
            f"When the analysis is complete call `write_report` and save the report to: {output_report}"
            if output_report
            else "Present the complete Markdown report in your final response."
        )

        if mode == "web":
            return f"""Perform a comprehensive SaaS design audit of the website at:

    {target}

Follow this analysis strategy:

1. **Homepage** — `fetch_page("{target}")` to map heading hierarchy, navigation structure, \
CTAs, and detect the design system in use.
2. **CSS / Design Tokens** — `fetch_css("{target}")`, then immediately run \
`extract_design_tokens` on the result to audit colour palette, typography scale, and spacing grid.
3. **Key pages** — Navigate to /pricing, /features, /login, /signup, /docs, /dashboard \
(or equivalent) using `fetch_page` on each discovered link. Look for inconsistencies \
across pages.
4. **Cross-page audit** — Note any differences in button styles, colour usage, typography, \
spacing, or navigation structure between pages.
5. **Synthesis** — Compile all findings into the design report.

Focus especially on:
- Visual hierarchy clarity
- Colour palette consistency (how many unique colours, are they tokenised?)
- Typography scale (how many sizes, are they modular?)
- CTA design and copy quality
- Spacing consistency (4/8 px grid compliance)
- Simplification opportunities (what can be removed?)

{report_note}

Reference world-class SaaS products (Linear, Stripe, Vercel, Notion, Loom) when \
describing how to fix each issue. Be specific — cite page URLs and CSS selectors."""

        else:
            return f"""Perform a comprehensive SaaS design audit of the repository at:

    {target}

Follow this analysis strategy:

1. **Structure** — `list_directory("{target}")` to understand the project layout \
(Next.js / Vite / Nuxt / plain HTML, etc.).
2. **Design files** — `find_design_files("{target}")` to locate all CSS, SCSS, Tailwind \
config, design token files, and component files.
3. **Design tokens** — Read the Tailwind config, global CSS, or tokens JSON first. \
These define the design system foundation.
4. **Component sampling** — Read 5–10 representative components (Button, Card, Nav, \
Layout, Input, Modal). Look for consistency in spacing, colour usage, and typography.
5. **Anti-pattern search** — Use `search_in_file` to hunt for:
   - Hardcoded hex colours in component files (not using tokens)
   - Inline styles (`style={{`) that bypass the design system
   - Off-grid spacing values (non-multiples of 4)
   - Multiple conflicting button implementations
6. **Synthesis** — Compile findings into the design report.

{report_note}

Reference world-class SaaS products when describing fixes. Cite exact file paths \
and line numbers for every issue."""

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
            thinking={"type": "enabled", "budget_tokens": 10000},
            system=[
                {
                    "type": "text",
                    "text": DESIGN_SYSTEM_PROMPT,
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
                f"\n\n📦 Prompt cached: {cache_created:,} tokens written",
                flush=True,
            )
        elif cache_read and iteration == 1:
            print(
                f"\n\n⚡ Cache hit: {cache_read:,} tokens served from cache",
                flush=True,
            )

        return final, tool_calls_seen

    def _execute_tool_calls(
        self, response: anthropic.types.Message, root: str
    ) -> list[dict]:
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

            result_text = execute_tool(block.name, block.input, root)

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
