"""
SEO / GEO Optimizer — Hugging Face Spaces entry point.

Usage:
    python app.py
Then open http://localhost:7860
"""

import os
import subprocess
import sys
import tempfile
import time
import threading
from pathlib import Path

import gradio as gr

from seo_agent.agent import SEOGEOAgent, ANTHROPIC_MODELS

_ALL_MODELS = sorted(ANTHROPIC_MODELS)
_DEFAULT_MODEL = "claude-opus-4-7"

_ANALYSIS_TIMEOUT = 600   # 10-minute hard cap
_MAX_LOG_LINES = 500


def _tail_log(log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    lines = log.splitlines(keepends=True)
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        return f"[… {dropped} earlier lines dropped …]\n" + "".join(lines[-max_lines:])
    return log


def run_seo_analysis(
    url_input: str,
    pasted_content: str,
    keywords_raw: str,
    competitor_url: str,
    mode: str,
    model: str,
    anthropic_key: str,
    verbose: bool,
):
    api_key = anthropic_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield "❌ Anthropic API key is required (or set ANTHROPIC_API_KEY env var).", ""
        return

    content = url_input.strip() or pasted_content.strip()
    if not content:
        yield "❌ Provide a URL or paste your content.", ""
        return

    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    competitor_urls = [competitor_url.strip()] if competitor_url.strip() else []

    is_url = content.startswith(("http://", "https://"))
    label = content if is_url else "(pasted content)"

    log = f"🚀  Starting SEO/GEO analysis: {label}\n"
    if keywords:
        log += f"🎯  Target keywords: {', '.join(keywords)}\n"
    if competitor_urls:
        log += f"🏁  Competitor: {competitor_urls[0]}\n"
    log += f"⚙️   Mode: {mode}  |  Model: {model}\n"
    log += "=" * 60 + "\n\n"
    yield log, ""

    report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
    report_path = report_file.name
    report_file.close()

    cmd = [
        sys.executable, "-c",
        f"""
import sys, os
sys.path.insert(0, {str(Path(__file__).parent)!r})
os.environ['ANTHROPIC_API_KEY'] = {api_key!r}
from seo_agent.agent import SEOGEOAgent
agent = SEOGEOAgent(model={model!r})
agent.run(
    content={content!r},
    target_keywords={keywords!r},
    competitor_urls={competitor_urls!r},
    mode={mode!r},
    output_report={report_path!r},
    verbose={verbose!r},
)
""",
    ]

    start = time.time()
    timed_out = False

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def _kill_on_timeout():
            nonlocal timed_out
            try:
                process.wait(timeout=_ANALYSIS_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()

        watchdog = threading.Thread(target=_kill_on_timeout, daemon=True)
        watchdog.start()

        for line in process.stdout:
            log += line
            log = _tail_log(log)
            yield log, ""

        process.wait()
        watchdog.join(timeout=1)

        if timed_out:
            log += f"\n⏱️  Timed out after {_ANALYSIS_TIMEOUT // 60} minutes.\n"
            yield log, ""
            return

        report = ""
        if Path(report_path).exists():
            report = Path(report_path).read_text(encoding="utf-8")

        duration = time.time() - start
        log += f"\n\n✅  Done in {int(duration)}s"
        yield log, report

    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


with gr.Blocks(title="SEO / GEO Optimizer", theme=gr.themes.Soft()) as demo:

    gr.Markdown(
        "# SEO / GEO Optimizer\n"
        "Powered by Claude with extended thinking — analyse any URL or content for "
        "**search engine visibility** and **AI citation readiness** (Google AI Overviews, "
        "Perplexity, ChatGPT search, Bing Copilot). Get a scored report, prioritised "
        "recommendations, optimised meta tags, schema markup, and an optional full rewrite."
    )

    with gr.Row():
        url_input = gr.Textbox(
            label="URL to analyse",
            placeholder="https://yourwebsite.com/page",
            scale=4,
        )
        mode_dropdown = gr.Dropdown(
            label="Mode",
            choices=["analyze", "rewrite", "both"],
            value="analyze",
            scale=1,
            info="analyze = report | rewrite = optimised content | both = report + rewrite",
        )

    content_input = gr.Textbox(
        label="— or — paste your content / HTML directly",
        placeholder="Paste raw text or HTML here if you don't have a live URL.",
        lines=6,
    )

    with gr.Row():
        keywords_input = gr.Textbox(
            label="Target keywords (comma-separated)",
            placeholder="e.g.  content marketing, AI search optimisation, GEO strategy",
            scale=3,
        )
        competitor_input = gr.Textbox(
            label="Competitor URL (optional — benchmarks their SEO/GEO signals)",
            placeholder="https://competitor.com/page",
            scale=2,
        )

    with gr.Row():
        api_key_input = gr.Textbox(
            label="Anthropic API key",
            placeholder="sk-ant-…  (or set ANTHROPIC_API_KEY env var)",
            type="password",
            scale=3,
        )
        model_dropdown = gr.Dropdown(
            label="Model",
            choices=_ALL_MODELS,
            value=_DEFAULT_MODEL,
            scale=2,
        )
        verbose_cb = gr.Checkbox(
            label="Show model reasoning",
            value=False,
            scale=1,
        )

    run_btn = gr.Button("Run SEO / GEO Analysis", variant="primary", size="lg")

    with gr.Tabs():
        with gr.Tab("Agent log"):
            log_output = gr.Textbox(
                label="Live output",
                lines=30,
                max_lines=30,
                interactive=False,
            )
        with gr.Tab("Report / Rewrite"):
            report_output = gr.Markdown(label="SEO/GEO Report")

    gr.Markdown(
        "---\n"
        "**Plug into any website:** this Space exposes a Gradio API — "
        "click **Use via API** below to call it programmatically from your CMS, "
        "browser extension, or CI pipeline using `pip install gradio_client`."
    )

    url_input.change(
        fn=lambda u: gr.update(
            placeholder=(
                "URL provided — paste box ignored."
                if u.strip()
                else "Paste raw text or HTML here if you don't have a live URL."
            )
        ),
        inputs=url_input,
        outputs=content_input,
    )

    run_btn.click(
        fn=run_seo_analysis,
        inputs=[
            url_input, content_input,
            keywords_input, competitor_input,
            mode_dropdown, model_dropdown,
            api_key_input, verbose_cb,
        ],
        outputs=[log_output, report_output],
    )

if __name__ == "__main__":
    demo.launch()
