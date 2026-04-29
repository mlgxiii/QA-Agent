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
import threading
import urllib.request
import uuid
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


class _NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip the Authorization header before following cross-origin redirects.

    GitHub API returns 302 → codeload.github.com. Forwarding the Bearer token
    to that domain causes auth errors and can crash the download silently.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req.remove_header("Authorization")
        return new_req


def _download_zip(owner: str, repo: str, dest: str, token: str = "") -> tuple[bool, str]:
    """Download repo as a ZIP via the GitHub API and extract it."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    opener = urllib.request.build_opener(_NoAuthRedirectHandler)

    last_err = ""
    for branch in ("main", "master"):
        zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
        req = urllib.request.Request(zip_url, headers=headers)  # noqa: S310
        try:
            with opener.open(req, timeout=60) as resp:
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
        if inner.exists():
            for item in inner.iterdir():
                shutil.move(str(item), dest)
            shutil.rmtree(str(inner), ignore_errors=True)
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
    parsed = _parse_github_url(github_url)

    # HuggingFace Spaces blocks outbound git operations via a credential interceptor,
    # so skip straight to ZIP download when running there.
    in_hf_spaces = bool(os.environ.get("SPACE_ID"))

    if not in_hf_spaces:
        ok, err = _clone_repo(github_url, dest, token)
        if ok:
            return True, ""
    else:
        err = "git clone skipped (HuggingFace Spaces environment)"

    if parsed:
        ok2, err2 = _download_zip(*parsed, dest, token)
        if ok2:
            return True, ""
        return False, f"git clone failed: {err}\nZIP download also failed: {err2}"
    return False, err


_MAX_LOG_LINES = 500   # keep tail of log to avoid unbounded memory growth on HF Spaces
_ANALYSIS_TIMEOUT = 600  # 10-minute hard cap on the subprocess


def _tail_log(log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    lines = log.splitlines(keepends=True)
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        return f"[… {dropped} earlier lines dropped …]\n" + "".join(lines[-max_lines:])
    return log


def run_analysis(github_url: str, local_path: str, api_key: str, gh_token: str, verbose: bool, session_id: str = ""):
    session_id = session_id or ""
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
        timed_out = False
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                cwd=str(Path(__file__).parent),
            )

            # Enforce a hard timeout so a hung Anthropic API call can't stall HF Spaces forever.
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
            duration = time.time() - start

            if timed_out:
                log += f"\n⏱️  Analysis timed out after {_ANALYSIS_TIMEOUT // 60} minutes.\n"
                yield log, ""
                return

            report = ""
            if Path(report_path).exists():
                report = Path(report_path).read_text(encoding="utf-8")

            if report:
                db.save_analysis(source, report, duration, session_id)

            yield log, report
        finally:
            try:
                os.unlink(report_path)
            except OSError:
                pass

    finally:
        if clone_dir:
            shutil.rmtree(clone_dir, ignore_errors=True)


def load_history(session_id: str = "") -> list[list]:
    rows = db.get_history(session_id or "")
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


def load_report(analysis_id, session_id: str = "") -> str:
    if not analysis_id:
        return ""
    return db.get_report(int(analysis_id), session_id or "")


with gr.Blocks(title="Scalability QA Agent") as demo:
    session_state = gr.State(lambda: str(uuid.uuid4()))
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
            scale=2,
        )

    with gr.Row():
        gh_token_input = gr.Textbox(
            label="GitHub token (optional — required for private repos)",
            placeholder="ghp_…  (or set GITHUB_TOKEN env var)",
            type="password",
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
        inputs=[github_input, local_input, api_key_input, gh_token_input, verbose_cb, session_state],
        outputs=[log_output, report_output],
    )
    refresh_btn.click(fn=load_history, inputs=session_state, outputs=history_df)
    load_btn.click(fn=load_report, inputs=[id_input, session_state], outputs=past_report)
    demo.load(fn=load_history, inputs=session_state, outputs=history_df)

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
