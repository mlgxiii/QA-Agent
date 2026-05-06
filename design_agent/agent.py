"""
Website Design Analyzer Agent (Anthropic).

Analyses any website or GitHub repository for design quality and provides
actionable recommendations inspired by top SaaS products.

Supports:
  - Live website URLs  → Playwright screenshots + HTML/CSS analysis
  - GitHub repo paths  → Source file analysis (CSS, components, design tokens)
"""

import json
import os
from pathlib import Path
from typing import Optional

import anthropic

from .prompts import DESIGN_SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool


ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}


class DesignAnalyzerAgent:
    DEFAULT_MODEL = "claude-opus-4-7"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
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
        Analyse a website URL or local codebase for design quality.

        Args:
            target:        Website URL (https://...) or local directory path.
            output_report: If given, write the final report to this path.
            verbose:       Print model reasoning to stdout.
        """
        is_url = target.startswith("http://") or target.startswith("https://")

        if not is_url:
            path = Path(target).resolve()
            if not path.exists():
                raise FileNotFoundError(f"Path does not exist: {path}")
            target = str(path)

        user_message = self._build_user_message(target, is_url, output_report)
        messages: list[dict] = [{"role": "user", "content": user_message}]

        print(f"\n🎨  Analysing: {target}  [{self.MODEL}]")
        print("=" * 70)

        iteration = 0
        total_tool_calls = 0
        root_path = "" if is_url else target

        while True:
            iteration += 1
            response, tool_calls_this_turn = self._run_turn(messages, verbose, iteration)
            total_tool_calls += tool_calls_this_turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                print("\n\n✅  Design analysis complete!")
                self._print_stats(response, total_tool_calls)
                break

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response, root_path)
                messages.append({"role": "user", "content": tool_results})
                print()

            elif response.stop_reason == "pause_turn":
                pass  # re-submit

            else:
                print(f"\n⚠️   Unexpected stop_reason: {response.stop_reason!r}")
                break

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_user_message(self, target: str, is_url: bool, output_report: Optional[str]) -> str:
        report_note = (
            f"When the analysis is complete call `write_report` to save the report to: {output_report}"
            if output_report
            else "Present the complete Markdown report in your final response."
        )

        if is_url:
            return f"""Perform a comprehensive design analysis of the website at:

    {target}

Steps:
1. Use `get_site_links` on the homepage to map the site's navigation structure.
2. Use `navigate_page` on the homepage (desktop, then 375px mobile) to see the visual design.
3. Visit the 3–5 most important pages (features, pricing, dashboard/app, sign up/login) using `navigate_page`.
4. Use `fetch_page` to study HTML structure, heading hierarchy, CTAs, and forms.
5. Use `fetch_stylesheet` on any linked CSS files to analyse the design token system.
6. Synthesise your findings into a thorough design report.

{report_note}

Be specific. Reference top SaaS products (Linear, Stripe, Notion, Vercel, etc.) where relevant. \
Cite exact URLs and element descriptions. Prioritise the highest-ROI improvements."""
        else:
            return f"""Perform a comprehensive design analysis of the codebase at:

    {target}

Steps:
1. Use `list_directory` to understand the project structure.
2. Use `find_ui_files` to locate all CSS, SCSS, HTML, JSX/TSX, Vue, Svelte files.
3. Read the design token / theme files first: look for tailwind.config.*, \
theme.ts, tokens.css, variables.scss, or any file with "theme" or "design" in the name.
4. Read the main layout component(s) and 5–8 representative UI components.
5. Read key page components (home, dashboard, landing) to understand visual structure.
6. Use `find_ui_files` with `extensions: [".css", ".scss"]` to find all stylesheets.
7. Analyse design system consistency, colour tokens, spacing scale, and accessibility patterns.
8. Synthesise findings into a thorough design report.

{report_note}

Be specific. Reference top SaaS products. Cite exact file paths and line numbers. \
Prioritise the highest-ROI improvements."""

    def _run_turn(
        self,
        messages: list[dict],
        verbose: bool,
        iteration: int,
    ) -> tuple[anthropic.types.Message, int]:
        tool_calls_seen = 0

        with self.client.messages.stream(
            model=self.MODEL,
            max_tokens=8192,
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
                    current_block_type = event.content_block.type
                    if current_block_type == "tool_use":
                        tool_calls_seen += 1

                elif event.type == "content_block_delta":
                    dtype = event.delta.type
                    if dtype == "text_delta":
                        print(event.delta.text, end="", flush=True)
                    elif dtype == "thinking_delta" and verbose:
                        print(event.delta.thinking, end="", flush=True)

            final = stream.get_final_message()

        # Cache stats
        usage = final.usage
        cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_created and iteration == 1:
            print(f"\n\n📦 Prompt cached: {cache_created:,} tokens written", flush=True)
        elif cache_read and iteration == 1:
            print(f"\n\n⚡ Cache hit: {cache_read:,} tokens from cache", flush=True)

        return final, tool_calls_seen

    def _execute_tool_calls(
        self, response: anthropic.types.Message, root_path: str
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

            result = execute_tool(block.name, block.input, root_path)

            # Build tool_result content — may include an image block for screenshots
            if isinstance(result, dict):
                if result.get("type") == "image_result":
                    content: list[dict] | str = [
                        {"type": "text", "text": result.get("text", "Screenshot:")},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": result["media_type"],
                                "data": result["data"],
                            },
                        },
                    ]
                    print(f"   ✓ Screenshot captured ({len(result['data']) // 1024} KB)", flush=True)
                else:
                    content = result.get("text", str(result))
                    print(f"   ✗ {content[:110]}", flush=True)
            else:
                content = str(result)
                preview = content.replace("\n", " ")[:110]
                print(f"   ✓ {preview}", flush=True)

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                }
            )

        return results

    @staticmethod
    def _print_stats(response: anthropic.types.Message, total_tool_calls: int) -> None:
        usage = response.usage
        print("\n📊 Session stats:")
        print(f"   Tool calls:    {total_tool_calls}")
        print(f"   Output tokens: {getattr(usage, 'output_tokens', 0) or 0:,}")
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_read:
            print(f"   Cache read:    {cache_read:,} tokens")
