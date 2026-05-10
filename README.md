---
title: SEO / GEO Optimizer
emoji: 🔍
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.13.0
app_file: app.py
pinned: false
---

# SEO / GEO Optimizer

AI-powered SEO and **GEO (Generative Engine Optimization)** agent built on Claude with extended thinking. Point it at any URL or paste your content and get a detailed, scored report with actionable recommendations — plus an optional full content rewrite.

## What it does

**SEO analysis**
- Title tag, meta description, heading hierarchy audit (with exact current → recommended values)
- Keyword density and placement scoring (title, H1, first paragraph, body)
- Internal/external link analysis, image alt text coverage
- Open Graph, Twitter Card, canonical tag, robots meta review
- Readability scoring (Flesch, Flesch-Kincaid, Gunning Fog)
- Existing JSON-LD structured data detection

**GEO analysis** — optimise for AI search engines (Google AI Overviews, Perplexity, ChatGPT, Bing Copilot)
- Direct-answer format scoring (inverted pyramid, definition openers)
- Entity and factual clarity (statistics, citations, named entities)
- Quotability scoring (short self-contained facts, lists, tables)
- Schema markup recommendations (FAQPage, HowTo, Article JSON-LD)

**Outputs**
- SEO score (0–100) and GEO score (0–100)
- Prioritised action plan ranked by impact
- Ready-to-copy optimised title tag and meta description
- Ready-to-paste JSON-LD schema block
- Optional full content rewrite with GEO-optimised structure

## Modes

| Mode | What you get |
|------|-------------|
| `analyze` | Full scored report + recommendations |
| `rewrite` | Optimised rewrite of your content + new meta tags + schema |
| `both` | Report + rewrite in one run |

## Local setup

```bash
git clone https://github.com/mlgxiii/QA-Agent
cd QA-Agent
git checkout claude/seo-optimization-agent-30Ome

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...
python app.py
# open http://localhost:7860
```

## Deploy to Hugging Face Spaces

1. Create a new Space → SDK: **Gradio**
2. Connect to this repo, branch `claude/seo-optimization-agent-30Ome`
3. **Settings → Secrets** → add `ANTHROPIC_API_KEY`
4. Your Space URL will look like `https://huggingface.co/spaces/yourname/seo-geo-optimizer`

Users on the public Space won't need to enter an API key — it reads from the secret you set.

## Call it as an API (plug into any website)

```python
from gradio_client import Client

client = Client("yourname/seo-geo-optimizer")
log, report = client.predict(
    url_input="https://yoursite.com/page",
    pasted_content="",
    keywords_raw="your target keyword, secondary keyword",
    competitor_url="",
    mode="both",            # analyze | rewrite | both
    model="claude-opus-4-7",
    anthropic_key="",       # blank = reads from Space secret
    verbose=False,
    api_name="/run_seo_analysis",
)
print(report)
```

## Requirements

- Python ≥ 3.10
- `anthropic >= 0.52.0`
- `gradio >= 4.0.0`
- `beautifulsoup4`, `textstat`, `lxml`, `requests`
- An [Anthropic API key](https://console.anthropic.com)
