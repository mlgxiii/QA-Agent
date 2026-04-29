import json
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, execute_tool

OPENAI_MODELS = {
    "gpt-4o",
    "gpt-4o-mini",
}


def _to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_tools
    ]


class OpenAIQAAgent:
    """
    Production Scalability QA Agent (OpenAI).

    Uses GPT-4o with function calling and streaming to analyse codebases for
    scalability issues. Shares the same tools and system prompt as the
    Anthropic agent; extended thinking is not available on this provider.
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self.model = model or self.DEFAULT_MODEL
        self._tools = _to_openai_tools(TOOL_DEFINITIONS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        target_path: str,
        output_report: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        target = Path(target_path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {target}")

        user_message = self._build_user_message(target, output_report)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        print(f"\n🔍  Analysing: {target}  [{self.model}]")
        print("=" * 70)

        total_tool_calls = 0

        while True:
            assistant_msg, finish_reason, n_calls = self._run_turn(messages, verbose)
            total_tool_calls += n_calls
            messages.append(assistant_msg)

            if finish_reason == "stop":
                print("\n\n✅  Analysis complete!")
                print(f"\n📊 Session stats:\n   Tool calls: {total_tool_calls}")
                break
            elif finish_reason == "tool_calls":
                tool_results = self._execute_tool_calls(assistant_msg, str(target))
                messages.extend(tool_results)
                print()
            else:
                print(f"\n⚠️   Unexpected finish_reason: {finish_reason!r}")
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
        self, messages: list[dict], verbose: bool
    ) -> tuple[dict, str, int]:
        """Stream one model turn; return (assistant_msg_dict, finish_reason, tool_call_count)."""
        collected_content = ""
        # Accumulate streamed tool-call fragments by their index
        collected_calls: dict[int, dict] = {}
        finish_reason = "stop"

        with self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self._tools,
            tool_choice="auto",
            stream=True,
        ) as stream:
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                delta = choice.delta

                if delta.content:
                    collected_content += delta.content
                    print(delta.content, end="", flush=True)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in collected_calls:
                            collected_calls[idx] = {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        entry = collected_calls[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function and tc.function.name:
                            entry["function"]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            entry["function"]["arguments"] += tc.function.arguments

        tool_calls_list = [collected_calls[i] for i in sorted(collected_calls)]

        assistant_msg: dict = {"role": "assistant", "content": collected_content or None}
        if tool_calls_list:
            assistant_msg["tool_calls"] = tool_calls_list

        return assistant_msg, finish_reason, len(tool_calls_list)

    def _execute_tool_calls(self, assistant_msg: dict, root: str) -> list[dict]:
        results: list[dict] = []
        for i, tc in enumerate(assistant_msg.get("tool_calls", []), 1):
            name = tc["function"]["name"]
            try:
                inputs = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                inputs = {}

            args_preview = tc["function"]["arguments"]
            if len(args_preview) > 90:
                args_preview = args_preview[:87] + "…"
            print(f"\n🔧 [{i}] {name}({args_preview})", flush=True)

            result_text = execute_tool(name, inputs, root)

            preview = result_text.replace("\n", " ")
            if len(preview) > 110:
                preview = preview[:107] + "…"
            print(f"   ✓ {preview}", flush=True)

            results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })

        return results
