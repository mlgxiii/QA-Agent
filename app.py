"""
SaaS Framework Discovery Agent -- Gradio web interface.

Usage:
    python app.py
Then open http://localhost:7860
"""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import gradio as gr

from saas_agent.agent import ANTHROPIC_MODELS, PRESET_CATEGORIES

_ALL_MODELS = sorted(ANTHROPIC_MODELS)
_DEFAULT_MODEL = "claude-opus-4-7"
_CATEGORY_CHOICES = list(PRESET_CATEGORIES.keys())
_TIMEOUT = 600
_MAX_LOG_LINES = 500


def _tail_log(log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    lines = log.splitlines(keepends=True)
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        return f"[... {dropped} earlier lines dropped ...]\n" + "".join(lines[-max_lines:])
    return log


def run_discovery(
    categories: list[str],
    custom_query: str,
    model: str,
    anthropic_key: str,
    gh_token: str,
    verbose: bool,
):
    api_key = anthropic_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    gh_token = gh_token.strip() or os.environ.get("GITHUB_TOKEN", "")

    if not api_key:
        yield "Anthropic API key is required.", ""
        return

    if not categories and not custom_query.strip():
        yield "Select at least one category or enter a custom query.", ""
        return

    report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
    report_path = report_file.name
    report_file.close()

    cmd = [sys.executable, "-m", "saas_agent.cli", "--output", report_path, "--model", model]
    for cat in categories:
        cmd += ["--categories", cat]
    if custom_query.strip():
        cmd += ["--custom-query", custom_query.strip()]
    if verbose:
        cmd.append("--verbose")

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key
    env["GITHUB_TOKEN"] = gh_token

    log = ""
    timed_out = False

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, cwd=str(Path(__file__).parent),
        )

        def _kill_on_timeout():
            nonlocal timed_out
            try:
                process.wait(timeout=_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()

        threading.Thread(target=_kill_on_timeout, daemon=True).start()

        for line in process.stdout:
            log += line
            log = _tail_log(log)
            yield log, ""

        process.wait()

        if timed_out:
            yield log + f"\nTimed out after {_TIMEOUT // 60} minutes.\n", ""
            return

        report = ""
        if Path(report_path).exists():
            report = Path(report_path).read_text(encoding="utf-8")

        yield log, report

    finally:
        try:
            import os as _os
            _os.unlink(report_path)
        except OSError:
            pass


with gr.Blocks(title="SaaS Discovery Agent", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# SaaS Framework Discovery Agent")
    gr.Markdown(
        "Find the most popular open-source GitHub projects that can be packaged as SaaS products "
        "and shipped quickly on [Whop](https://whop.com).\n\n"
        "Claude searches GitHub, checks licences, reads READMEs, and produces a ranked report "
        "with a Whop shipping playbook for each top pick."
    )

    with gr.Row():
        model_dropdown = gr.Dropdown(label="Model", choices=_ALL_MODELS, value=_DEFAULT_MODEL, scale=1)
        anthropic_key_input = gr.Textbox(
            label="Anthropic API key", placeholder="sk-ant-... (or set ANTHROPIC_API_KEY)",
            type="password", scale=2,
        )
        gh_token_input = gr.Textbox(
            label="GitHub token (recommended -- raises rate limit to 5 000 req/hour)",
            placeholder="ghp_... (or set GITHUB_TOKEN)", type="password", scale=2,
        )

    categories_input = gr.CheckboxGroup(
        label="Categories to search", choices=_CATEGORY_CHOICES,
        value=["AI & LLM Tools", "Developer Tools"],
    )
    custom_query_input = gr.Textbox(
        label="Custom GitHub search query (optional)",
        placeholder="topic:crm stars:>500 language:python",
    )
    verbose_cb = gr.Checkbox(label="Verbose -- show model reasoning", value=False)
    run_btn = gr.Button("Discover SaaS Frameworks", variant="primary", size="lg")

    with gr.Tabs():
        with gr.Tab("Agent log"):
            log_output = gr.Textbox(label="Live output", lines=28, max_lines=28, interactive=False)
        with gr.Tab("Report"):
            report_output = gr.Markdown(label="SaaS discovery report")

    run_btn.click(
        fn=run_discovery,
        inputs=[categories_input, custom_query_input, model_dropdown,
                anthropic_key_input, gh_token_input, verbose_cb],
        outputs=[log_output, report_output],
    )


if __name__ == "__main__":
    demo.launch()
