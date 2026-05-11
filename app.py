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

import db
from seo_agent.agent import SEOGEOAgent, ANTHROPIC_MODELS

db.init_db()

_ALL_MODELS = sorted(ANTHROPIC_MODELS)
_DEFAULT_MODEL = "claude-opus-4-7"

_ANALYSIS_TIMEOUT = 600
_MAX_LOG_LINES = 500


def _tail_log(log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    lines = log.splitlines(keepends=True)
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        return f"[… {dropped} earlier lines dropped …]\n" + "".join(lines[-max_lines:])
    return log


def _run_agent_subprocess(
    content: str,
    keywords: list[str],
    competitor_urls: list[str],
    mode: str,
    audience: str,
    model: str,
    api_key: str,
    report_path: str,
    verbose: bool,
) -> subprocess.Popen:
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
    audience={audience!r},
    output_report={report_path!r},
    verbose={verbose!r},
)
""",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# ---------------------------------------------------------------------------
# Analyser (analyze / rewrite / both / gap)
# ---------------------------------------------------------------------------

def run_seo_analysis(
    url_input: str,
    pasted_content: str,
    keywords_raw: str,
    competitor_urls_raw: str,
    mode: str,
    model: str,
    anthropic_key: str,
    verbose: bool,
    session_id: str = "",
):
    api_key = anthropic_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield "❌ Anthropic API key is required.", ""
        return

    content = url_input.strip() or pasted_content.strip()
    if not content:
        yield "❌ Provide a URL or paste your content.", ""
        return

    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    competitor_urls = [u.strip() for u in competitor_urls_raw.splitlines() if u.strip()]

    is_url = content.startswith(("http://", "https://"))
    label = content if is_url else "(pasted content)"

    log = f"🚀  Mode: {mode}  |  {label}\n"
    if keywords:
        log += f"🎯  Keywords: {', '.join(keywords)}\n"
    if competitor_urls:
        log += f"🏁  Competitors: {len(competitor_urls)}\n"
    log += f"⚙️   Model: {model}\n" + "=" * 60 + "\n\n"
    yield log, ""

    report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
    report_path = report_file.name
    report_file.close()

    start = time.time()
    timed_out = False

    try:
        process = _run_agent_subprocess(
            content, keywords, competitor_urls, mode, "",
            model, api_key, report_path, verbose,
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

        if report:
            db.save_seo_analysis(label, report, duration, mode, session_id or "")

        log += f"\n\n✅  Done in {int(duration)}s"
        yield log, report

    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Content Brief
# ---------------------------------------------------------------------------

def run_content_brief(
    keyword: str,
    related_keywords_raw: str,
    audience: str,
    competitor_urls_raw: str,
    model: str,
    anthropic_key: str,
    verbose: bool,
    session_id: str = "",
):
    api_key = anthropic_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        yield "❌ Anthropic API key is required.", ""
        return
    if not keyword.strip():
        yield "❌ Enter a primary keyword or topic.", ""
        return

    related = [k.strip() for k in related_keywords_raw.split(",") if k.strip()]
    competitor_urls = [u.strip() for u in competitor_urls_raw.splitlines() if u.strip()]

    log = f"📝  Content Brief: {keyword.strip()}\n"
    if related:
        log += f"🔑  Related: {', '.join(related)}\n"
    if audience:
        log += f"👥  Audience: {audience}\n"
    if competitor_urls:
        log += f"🏁  Competitors to analyse: {len(competitor_urls)}\n"
    log += "=" * 60 + "\n\n"
    yield log, ""

    report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
    report_path = report_file.name
    report_file.close()

    start = time.time()
    timed_out = False

    try:
        process = _run_agent_subprocess(
            keyword.strip(), related, competitor_urls, "brief", audience,
            model, api_key, report_path, verbose,
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
            log += f"\n⏱️  Timed out.\n"
            yield log, ""
            return

        report = ""
        if Path(report_path).exists():
            report = Path(report_path).read_text(encoding="utf-8")

        duration = time.time() - start
        if report:
            db.save_seo_analysis(keyword.strip(), report, duration, "brief", session_id or "")

        log += f"\n\n✅  Brief ready in {int(duration)}s"
        yield log, report

    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def load_seo_history(session_id: str = "") -> list[list]:
    rows = db.get_seo_history(session_id or "")
    if not rows:
        return []
    return [
        [
            r["id"],
            r["source"][:60],
            r["mode"],
            r["timestamp"][:16].replace("T", " "),
            f'{int(r["duration_s"])}s' if r["duration_s"] else "—",
            r["seo_score"] if r["seo_score"] is not None else "—",
            r["geo_score"] if r["geo_score"] is not None else "—",
        ]
        for r in rows
    ]


def load_seo_report(analysis_id, session_id: str = "") -> str:
    if not analysis_id:
        return ""
    return db.get_seo_report(int(analysis_id), session_id or "")


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="SEO / GEO Optimizer", theme=gr.themes.Soft()) as demo:

    session_state = gr.State(value="")

    gr.Markdown(
        "# SEO / GEO Optimizer\n"
        "Powered by Claude with extended thinking — analyse, rewrite, benchmark competitors, "
        "generate content briefs, and track progress over time."
    )

    with gr.Tabs():

        # ------------------------------------------------------------------ #
        # Tab 1 — Analyser                                                     #
        # ------------------------------------------------------------------ #
        with gr.Tab("Analyser"):
            gr.Markdown(
                "Audit any URL or content for **SEO score**, **GEO score** (AI citation readiness), "
                "and get an **AI answer preview**, **FAQ generator**, optimised meta tags, and schema."
            )

            with gr.Row():
                url_input = gr.Textbox(
                    label="URL to analyse",
                    placeholder="https://yourwebsite.com/page",
                    scale=4,
                )
                mode_dropdown = gr.Dropdown(
                    label="Mode",
                    choices=["analyze", "rewrite", "both", "gap"],
                    value="analyze",
                    scale=1,
                    info="analyze · rewrite · both · gap (competitor diff)",
                )

            content_input = gr.Textbox(
                label="— or — paste content / HTML directly",
                placeholder="Paste raw text or HTML if you don't have a live URL.",
                lines=5,
            )

            with gr.Row():
                keywords_input = gr.Textbox(
                    label="Target keywords (comma-separated)",
                    placeholder="content marketing, AI search optimisation, GEO strategy",
                    scale=3,
                )
                competitor_input = gr.Textbox(
                    label="Competitor URLs (one per line)",
                    placeholder="https://competitor1.com/page\nhttps://competitor2.com/page",
                    lines=3,
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
                    label="Model", choices=_ALL_MODELS, value=_DEFAULT_MODEL, scale=2,
                )
                verbose_cb = gr.Checkbox(label="Show reasoning", value=False, scale=1)

            run_btn = gr.Button("Run Analysis", variant="primary", size="lg")

            with gr.Tabs():
                with gr.Tab("Agent log"):
                    log_output = gr.Textbox(label="Live output", lines=28, max_lines=28, interactive=False)
                with gr.Tab("Report / Rewrite"):
                    report_output = gr.Markdown(label="Report")

            url_input.change(
                fn=lambda u: gr.update(placeholder="URL provided — paste box ignored." if u.strip() else "Paste raw text or HTML if you don't have a live URL."),
                inputs=url_input, outputs=content_input,
            )
            run_btn.click(
                fn=run_seo_analysis,
                inputs=[url_input, content_input, keywords_input, competitor_input,
                        mode_dropdown, model_dropdown, api_key_input, verbose_cb, session_state],
                outputs=[log_output, report_output],
            )

        # ------------------------------------------------------------------ #
        # Tab 2 — Content Brief                                                #
        # ------------------------------------------------------------------ #
        with gr.Tab("Content Brief"):
            gr.Markdown(
                "Generate a fully structured SEO/GEO content brief for **new content**. "
                "Provide a keyword, optionally benchmark against competitors, and get a "
                "complete outline with target stats, attribution sources, FAQ questions, "
                "and a GEO optimisation checklist — ready to hand to a writer."
            )

            with gr.Row():
                brief_keyword_input = gr.Textbox(
                    label="Primary keyword / topic",
                    placeholder="e.g.  generative engine optimization",
                    scale=3,
                )
                brief_audience_input = gr.Textbox(
                    label="Target audience (optional)",
                    placeholder="e.g.  marketing managers at B2B SaaS companies",
                    scale=2,
                )

            brief_related_input = gr.Textbox(
                label="Related keywords (comma-separated, optional)",
                placeholder="GEO strategy, AI search optimisation, Perplexity SEO",
            )

            brief_competitor_input = gr.Textbox(
                label="Competitor URLs to benchmark (one per line, optional)",
                placeholder="https://competitor.com/page",
                lines=3,
            )

            with gr.Row():
                brief_api_key_input = gr.Textbox(
                    label="Anthropic API key",
                    placeholder="sk-ant-…  (or set ANTHROPIC_API_KEY env var)",
                    type="password",
                    scale=3,
                )
                brief_model_dropdown = gr.Dropdown(
                    label="Model", choices=_ALL_MODELS, value=_DEFAULT_MODEL, scale=2,
                )
                brief_verbose_cb = gr.Checkbox(label="Show reasoning", value=False, scale=1)

            brief_run_btn = gr.Button("Generate Content Brief", variant="primary", size="lg")

            with gr.Tabs():
                with gr.Tab("Agent log"):
                    brief_log_output = gr.Textbox(label="Live output", lines=28, max_lines=28, interactive=False)
                with gr.Tab("Brief"):
                    brief_output = gr.Markdown(label="Content Brief")

            brief_run_btn.click(
                fn=run_content_brief,
                inputs=[brief_keyword_input, brief_related_input, brief_audience_input,
                        brief_competitor_input, brief_model_dropdown, brief_api_key_input,
                        brief_verbose_cb, session_state],
                outputs=[brief_log_output, brief_output],
            )

        # ------------------------------------------------------------------ #
        # Tab 3 — History                                                      #
        # ------------------------------------------------------------------ #
        with gr.Tab("History"):
            gr.Markdown(
                "Every analysis and brief is saved here. Load any past report to compare "
                "SEO/GEO scores before and after making changes."
            )

            history_refresh_btn = gr.Button("Refresh", size="sm")
            history_df = gr.Dataframe(
                headers=["ID", "Source", "Mode", "Date", "Duration", "SEO", "GEO"],
                interactive=False,
                wrap=True,
            )

            gr.Markdown("### Load a past report")
            with gr.Row():
                history_id_input = gr.Number(label="Report ID", precision=0, scale=1)
                history_load_btn = gr.Button("Load", scale=1)
            history_report = gr.Markdown()

            history_refresh_btn.click(
                fn=load_seo_history, inputs=session_state, outputs=history_df,
            )
            history_load_btn.click(
                fn=load_seo_report, inputs=[history_id_input, session_state], outputs=history_report,
            )

    gr.Markdown(
        "---\n"
        "**API access:** every function here is callable programmatically — "
        "click **Use via API** below for the endpoint and `gradio_client` example."
    )

    def _on_load() -> tuple[str, list]:
        import uuid
        sid = str(uuid.uuid4())
        return sid, load_seo_history(sid)

    demo.load(fn=_on_load, inputs=None, outputs=[session_state, history_df])


if __name__ == "__main__":
    demo.launch()
