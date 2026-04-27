"""
Gradio web interface for the Production Scalability QA Agent.

Usage:
    pip install gradio
    python app.py
Then open http://localhost:7860
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import gradio as gr


def run_analysis(path: str, api_key: str, verbose: bool):
    path = path.strip()
    if not path:
        yield "❌ Please enter a path to analyse.", ""
        return

    api_key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield "❌ Anthropic API key is required.", ""
        return

    if not Path(path).exists():
        yield f"❌ Path does not exist: {path}", ""
        return

    report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
    report_path = report_file.name
    report_file.close()

    cmd = [sys.executable, "-m", "qa_agent.cli", path, "--output", report_path]
    if verbose:
        cmd.append("--verbose")

    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = api_key

    log = ""
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent),
        )
        for line in process.stdout:
            log += line
            yield log, ""

        process.wait()

        report = ""
        if Path(report_path).exists():
            report = Path(report_path).read_text(encoding="utf-8")

        yield log, report
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


with gr.Blocks(title="Scalability QA Agent") as demo:
    gr.Markdown("# Production Scalability QA Agent")
    gr.Markdown(
        "Analyses a codebase for scalability issues using Claude. "
        "Point it at any local directory."
    )

    with gr.Row():
        path_input = gr.Textbox(
            label="Codebase path",
            placeholder="/path/to/your/project",
            scale=3,
        )
        api_key_input = gr.Textbox(
            label="Anthropic API key",
            placeholder="sk-ant-…  (or set ANTHROPIC_API_KEY env var)",
            type="password",
            value=os.environ.get("ANTHROPIC_API_KEY", ""),
            scale=2,
        )

    verbose_cb = gr.Checkbox(label="Verbose — show Claude's reasoning", value=False)
    run_btn = gr.Button("Analyse", variant="primary", size="lg")

    with gr.Tabs():
        with gr.Tab("Agent log"):
            log_output = gr.Textbox(
                label="Live output",
                lines=25,
                max_lines=25,
                interactive=False,
            )
        with gr.Tab("Report"):
            report_output = gr.Markdown(label="Scalability report")

    run_btn.click(
        fn=run_analysis,
        inputs=[path_input, api_key_input, verbose_cb],
        outputs=[log_output, report_output],
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
