---
title: Production Scalability QA Agent
emoji: 🔍
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 6.13.0
app_file: app.py
pinned: false
---

# Production Scalability QA Agent

Analyses any codebase for production scalability issues using Claude (`claude-opus-4-7`).
Point it at a GitHub repo URL or a local folder and get a structured report with severity
ratings and actionable fix recommendations.

## Features

- **GitHub integration** — paste any public repo URL; the agent shallow-clones it and analyses it
- **Local path support** — point at any folder on disk
- **Structured reports** — findings rated CRITICAL / HIGH / MEDIUM / LOW with exact file paths, line numbers, and fixes
- **Analysis history** — every run saved to SQLite; track improvement over time in the History tab
- **Streaming output** — watch the agent work in real time

## Categories analysed

Database access patterns · Caching strategy · Concurrency & thread safety ·
API design & rate limiting · Memory management · Connection pooling ·
Error handling under load · Configuration & secrets · Dependency risks · Observability

## Local setup

```bash
git clone https://github.com/mlgxiii/QA-Agent
cd QA-Agent
git checkout claude/qa-scalability-agent-5jNOd

uv venv --python 3.12
source .venv/bin/activate
uv pip install anthropic gradio
uv pip install -e .

export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

Then open http://localhost:7860.

## Hugging Face Spaces deployment

1. Fork this repo to your HF account
2. **Settings → Repository secrets** → add `ANTHROPIC_API_KEY`
3. *(Optional)* **Settings → Persistent storage** → enable it, then add a Space variable `DATA_DIR=/data` so the history DB survives redeploys

## Requirements

- Python ≥ 3.10
- `anthropic >= 0.52.0`
- `gradio >= 4.0.0`
- An [Anthropic API key](https://console.anthropic.com)
