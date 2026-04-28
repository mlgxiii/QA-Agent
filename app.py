"""
Gradio web interface for the Production Scalability QA Agent.

Usage:
    python app.py
Then open http://localhost:7860
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import gradio as gr

import db

db.init_db()


def _parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a github.com URL, or None if not parseable."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/?.#]+)", url)
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git")


def _download_zip(owner: str, repo: str, dest: str, token: str = "") -> tuple[bool, str]:
    """Download repo as a ZIP via the GitHub API and extract it."""
    headers = {"Authorization": f"token {token}"} if token else {}
    last_err = ""
    for branch in ("main", "master"):
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        req = urllib.request.Request(zip_url, headers=headers)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                data = resp.read()
            break
        except Exception as exc:
            last_err = str(exc)
    else:
        return False, last_err

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            top = zf.namelist()[0].split("/")[0]
            zf.extractall(dest)
        inner = Path(dest) / top
        for item in inner.iterdir():
            shutil.move(str(item), dest)
        inner.rmdir()
    except Exception as exc:
        return False, str(exc)

    return True, ""


def _clone_repo(github_url: str, dest: str, token: str = "") -> tuple[bool, str]:
    env = os.environ.copy()
    # Prevent git from hanging trying to prompt for credentials in a headless env
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        parsed = _parse_github_url(github_url)
        if parsed:
            owner, repo = parsed
            github_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    result = subprocess.run(
        ["git", "clone", "--depth", "1", github_url, dest],
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode == 0, result.stderr.strip()


def _fetch_repo(github_url: str, dest: str, token: str = "") -> tuple[bool, str]:
    """Try git clone first; fall back to ZIP download for restricted envs (e.g. HF Spaces)."""
    ok, err = _clone_repo(github_url, dest, token)
    if ok:
        return True, ""
    parsed = _parse_github_url(github_url)
    if parsed:
        ok2, err2 = _download_zip(*parsed, dest, token)
        if ok2:
            return True, ""
        return False, f"git clone failed: {err}\nZIP download also failed: {err2}"
    return False, err


def run_analysis(github_url: str, local_path: str, api_key: str, gh_token: str, verbose: bool):
    api_key = api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    gh_token = gh_token.strip() or os.environ.get("GITHUB_TOKEN", "")
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
            log = f"⬇️  Fetching {github_url} …\n"
            yield log, ""
            ok, err = _fetch_repo(github_url, clone_dir, gh_token)
            if not ok:
                yield log + f"❌ Clone failed:\n{err}", ""
                return
            target = clone_dir
            source = github_url
            log += "✅ Fetched.\n\n"
            yield log, ""
        else:
            target = local_path
            source = local_path
            if not Path(target).exists():
                yield f"❌ Path does not exist: {target}", ""
                return
            log = ""

        report_file = tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w")
        report_path = report_file.name
        report_file.close()

        cmd = [sys.executable, "-m", "qa_agent.cli", target, "--output", report_path]
        if verbose:
            cmd.append("--verbose")

        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = api_key

        start = time.time()
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
            duration = time.time() - start

            report = ""
            if Path(report_path).exists():
                report = Path(report_path).read_text(encoding="utf-8")

            if report:
                db.save_analysis(source, report, duration)

            yield log, report
        finally:
            try:
                os.unlink(report_path)
            except OSError:
                pass

    finally:
        if clone_dir:
            shutil.rmtree(clone_dir, ignore_errors=True)


def load_history() -> list[list]:
    rows = db.get_history()
    if not rows:
        return []
    return [
        [
            r["id"],
            r["source"],
            r["timestamp"][:16].replace("T", " "),
            f'{int(r["duration_s"])}s' if r["duration_s"] else "—",
            r["critical"],
            r["high"],
            r["medium"],
            r["low"],
        ]
        for r in rows
    ]


def load_report(analysis_id) -> str:
    if not analysis_id:
        return ""
    return db.get_report(int(analysis_id))


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

    with gr.Row():
        gh_token_input = gr.Textbox(
            label="GitHub token (optional — required for private repos)",
            placeholder="ghp_…  (or set GITHUB_TOKEN env var)",
            type="password",
            value=os.environ.get("GITHUB_TOKEN", ""),
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
        with gr.Tab("History"):
            refresh_btn = gr.Button("Refresh", size="sm")
            history_df = gr.Dataframe(
                headers=["ID", "Source", "Date", "Duration", "Critical", "High", "Medium", "Low"],
                interactive=False,
                wrap=True,
            )
            gr.Markdown("### Load a past report")
            with gr.Row():
                id_input = gr.Number(label="Report ID", precision=0, scale=1)
                load_btn = gr.Button("Load", scale=1)
            past_report = gr.Markdown()

    run_btn.click(
        fn=run_analysis,
        inputs=[github_input, local_input, api_key_input, gh_token_input, verbose_cb],
        outputs=[log_output, report_output],
    )
    refresh_btn.click(fn=load_history, outputs=history_df)
    load_btn.click(fn=load_report, inputs=id_input, outputs=past_report)
    demo.load(fn=load_history, outputs=history_df)

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
