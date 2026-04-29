import json
import os
from pathlib import Path
from typing import Optional

import anthropic

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool


ANTHROPIC_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
}


class ScalabilityQAAgent:
    """
    Production Scalability QA Agent (Anthropic).

    Uses extended thinking, streaming output, and prompt caching on the system
    prompt to efficiently analyse codebases for scalability issues.
    """

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
        target_path: str,
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        """
        Analyse a codebase for production scalability issues.

        Args:
            target_path:   Directory or file to analyse.
            output_report: If given, the agent will write the final report
                           to this path via the write_report tool.
            verbose:       Show the model's thinking/reasoning output.
        """
        target = Path(target_path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")

        user_message = self._build_user_message(target, output_report)
        messages: list[dict] = [{"role": "user", "content": user_message}]

        print(f"\n🔍  Analysing: {target}  [{self.MODEL}]")
        print("=" * 70)

        iteration = 0
        total_tool_calls = 0

        while True:
            iteration += 1
            response, tool_calls_this_turn = self._run_turn(
                messages, verbose=verbose, iteration=iteration
            )
            total_tool_calls += tool_calls_this_turn

            # Append the assistant turn verbatim (preserves all content blocks).
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                print("\n\n✅  Analysis complete!")
                self._print_stats(response, total_tool_calls)
                break

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tool_calls(response, str(target))
                messages.append({"role": "user", "content": tool_results})
                print()  # visual gap before the next streamed response

            elif response.stop_reason == "pause_turn":
                # Server-side tool loop reached its iteration cap; re-submit.
                pass

            else:
                print(f"\n⚠️   Unexpected stop_reason: {response.stop_reason!r}")
                break

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(self, target: Path, output_report: Optional[str]) -> str:
        report_note = (
            f"When the analysis is complete call `write_report` and save the report to: {output_report}"
            if output_report
            else "Present the complete Markdown report in your final response."
        )
        return f"""Perform a comprehensive production scalability review of the codebase at:

    {target}

Steps:
1. Use `list_directory` to map the project structure (start non-recursively, then drill down).
2. Use `find_files` to quickly locate all source files by extension.
3. Read the most critical files: entry points, API route handlers, database models,
   service classes, middleware, and configuration.
4. Use `search_in_file` to hunt for specific anti-patterns (e.g. bare HTTP calls,
   unbounded loops over query results, global mutable state).
5. Compile all findings into a structured report with severity ratings,
   exact file paths & line numbers, and actionable fix recommendations.

{report_note}

Prioritise code that runs on every request. Be specific — cite exact line numbers."""

    def _run_turn(
        self,
        messages: list[dict],
        verbose: bool,
        iteration: int,
    ) -> tuple[anthropic.types.Message, int]:
        """Stream one model turn and return the final message + tool-call count."""
        tool_calls_seen = 0

        with self.client.messages.stream(
            model=self.MODEL,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Cache the large system prompt — significant token savings on
                    # repeated analysis runs or multi-turn tool loops.
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
                        print()  # newline after thinking block

            final = stream.get_final_message()

        # Print cache stats once (on the first turn where the prompt is written).
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

    def _execute_tool_calls(
        self, response: anthropic.types.Message, root: str
    ) -> list[dict]:
        """Execute every tool_use block in the response and return tool_result list."""
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
