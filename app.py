"""
Gradio web interface for the Production Scalability QA Agent.

Usage:
    pip install gradio
    python app.py
Then open http://localhost:7860
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import gradio as gr


def _clone_repo(github_url: str, dest: str) -> tuple[bool, str]:
    """Shallow-clone a GitHub repo. Returns (success, message)."""
    result = subprocess.run(
        ["git", "clone", "--depth", "1", github_url, dest],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


def run_analysis(github_url: str, local_path: str, api_key: str, verbose: bool):
    api_key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield "❌ Anthropic API key is required.", ""
        return

    github_url = github_url.strip()
    local_path = local_path.strip()

    if not github_url and not local_path:
        yield "❌ Provide a GitHub URL or a local path.", ""
        return

    clone_dir = None
    try:
        if github_url:
            clone_dir = tempfile.mkdtemp(prefix="qa_agent_clone_")
            yield f"⬇️  Cloning {github_url} …\n", ""
            ok, err = _clone_repo(github_url, clone_dir)
            if not ok:
                yield f"❌ Clone failed:\n{err}", ""
                return
            target = clone_dir
            yield f"✅ Cloned to temporary directory.\n\n", ""
        else:
            target = local_path
            if not Path(target).exists():
                yield f"❌ Path does not exist: {target}", ""
                return

        report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
        report_path = report_file.name
        report_file.close()

        cmd = [sys.executable, "-m", "qa_agent.cli", target, "--output", report_path]
        if verbose:
            cmd.append("--verbose")

        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = api_key

        log = f"⬇️  Cloning {github_url} …\n✅ Cloned to temporary directory.\n\n" if github_url else ""
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

    finally:
        if clone_dir:
            shutil.rmtree(clone_dir, ignore_errors=True)


with gr.Blocks(title="Scalability QA Agent") as demo:
    gr.Markdown("# Production Scalability QA Agent")
    gr.Markdown(
        "Analyse any codebase for production scalability issues using Claude. "
        "Paste a GitHub URL **or** a local folder path."
    )

    with gr.Row():
        github_input = gr.Textbox(
            label="GitHub repo URL",
            placeholder="https://github.com/owner/repo",
            scale=3,
        )
        api_key_input = gr.Textbox(
            label="Anthropic API key",
            placeholder="sk-ant-…  (or set ANTHROPIC_API_KEY env var)",
            type="password",
            value=os.environ.get("ANTHROPIC_API_KEY", ""),
            scale=2,
        )

    local_input = gr.Textbox(
        label="— or — local path",
        placeholder="/path/to/your/project  (leave blank if using GitHub URL above)",
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
        inputs=[github_input, local_input, api_key_input, verbose_cb],
        outputs=[log_output, report_output],
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
