import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    import textstat
    _TEXTSTAT_AVAILABLE = True
except ImportError:
    _TEXTSTAT_AVAILABLE = False


_USER_AGENT = (
    "Mozilla/5.0 (compatible; SEOGEOAgent/1.0; +https://huggingface.co/spaces)"
)
_FETCH_TIMEOUT = 20
_MAX_BODY_CHARS = 80_000


# ---------------------------------------------------------------------------
# Tool definitions (passed to Claude as the tools list)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "fetch_url",
        "description": (
            "Fetch the HTML content of a URL. Returns the raw HTML (truncated to 80 KB). "
            "Use this first when the user provides a URL to analyse."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to fetch (https://...)."},
            },
            "required": ["url"],
        },
    },
    {
        "name": "extract_page_data",
        "description": (
            "Parse HTML and extract all SEO-relevant data: title, meta tags, headings (H1–H6), "
            "body text, internal/external links, image alt tags, canonical, Open Graph, "
            "Twitter Card, and existing JSON-LD structured data. "
            "Pass the raw HTML from fetch_url."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "Raw HTML content of the page."},
                "base_url": {
                    "type": "string",
                    "description": "The page URL, used to classify links as internal vs external.",
                },
            },
            "required": ["html"],
        },
    },
    {
        "name": "analyze_readability",
        "description": (
            "Score the readability of body text using standard formulas: "
            "Flesch Reading Ease, Flesch-Kincaid Grade Level, Gunning Fog Index, "
            "average sentence length, average word length. "
            "Pass the plain-text body content (not HTML)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Plain text to score."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "analyze_keyword_density",
        "description": (
            "Analyse how target keywords appear in the content: frequency, density %, "
            "placement (title / H1 / first-paragraph / body), and LSI/variant coverage. "
            "Checks for keyword stuffing (>3% density) and under-use (<0.5%)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Full page text (title + headings + body combined).",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Target keywords to analyse.",
                },
                "title": {"type": "string", "description": "Page title text."},
                "h1": {"type": "string", "description": "H1 heading text."},
                "first_paragraph": {
                    "type": "string",
                    "description": "First body paragraph text.",
                },
            },
            "required": ["text", "keywords"],
        },
    },
    {
        "name": "score_geo_signals",
        "description": (
            "Score Generative Engine Optimization (GEO) signals using research-backed weights "
            "from the Princeton GEO paper (ACL 2024) and C-SEO Bench (NeurIPS 2025). "
            "Scores six signals proven to increase AI citation rates: "
            "(1) statistics density — +30% citation lift per the GEO paper; "
            "(2) named attribution — +23% lift; "
            "(3) quotable sentences — +29% lift; "
            "(4) direct-answer opening — foundational; "
            "(5) FAQ structure — top structural signal per C-SEO Bench; "
            "(6) schema markup. "
            "Returns a GEO score 0-100, per-signal scores, samples of quotable sentences, "
            "missing attribution opportunities, and a ranked action list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Plain text body content."},
                "headings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of all heading texts (H1–H6).",
                },
                "structured_data": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Existing JSON-LD objects found on the page.",
                },
                "first_paragraph": {
                    "type": "string",
                    "description": "First body paragraph text.",
                },
                "title": {
                    "type": "string",
                    "description": "Page title text.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "extract_ai_citable_content",
        "description": (
            "Simulates what an AI search engine (Perplexity, ChatGPT, Google AI Overviews) "
            "would extract and cite from this content. "
            "Returns: the likely opening citation, top citable statistics, definition sentences, "
            "FAQ answer extracts, coverage gaps that reduce citation probability, "
            "and a structured AI answer preview the agent can present to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Full plain-text body content."},
                "headings": {
                    "type": "array", "items": {"type": "string"},
                    "description": "All heading texts H1–H6.",
                },
                "first_paragraph": {"type": "string", "description": "First body paragraph."},
                "title": {"type": "string", "description": "Page title."},
                "target_keywords": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Target keywords to check coverage for.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "compare_content_gap",
        "description": (
            "Compares a target page against a competitor page to identify content gaps. "
            "Finds headings and topics the competitor covers that the target page is missing, "
            "compares word counts and GEO signal presence, and returns ranked gap recommendations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_title": {"type": "string"},
                "target_headings": {"type": "array", "items": {"type": "string"}},
                "target_text": {"type": "string"},
                "competitor_title": {"type": "string"},
                "competitor_headings": {"type": "array", "items": {"type": "string"}},
                "competitor_text": {"type": "string"},
                "competitor_url": {"type": "string", "description": "Competitor URL for labelling."},
            },
            "required": ["target_headings", "target_text", "competitor_headings", "competitor_text"],
        },
    },
    {
        "name": "extract_faq_opportunities",
        "description": (
            "Scans content to extract existing Q&A pairs and identify new FAQ opportunities. "
            "Returns: existing answerable questions found in headings, suggested new questions "
            "the content could answer, and ready-to-paste FAQ HTML + FAQPage JSON-LD schema. "
            "Use this to generate the FAQ section that C-SEO Bench identified as the top "
            "structural signal for AI citation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Plain text body content."},
                "headings": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "page_url": {"type": "string", "description": "Page URL for schema markup."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "write_seo_report",
        "description": (
            "Save the final SEO/GEO analysis report as a Markdown file. "
            "Call this exactly once when the full analysis is complete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Complete Markdown report content."},
                "output_path": {"type": "string", "description": "Destination file path."},
            },
            "required": ["content", "output_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name: str, inputs: dict[str, Any]) -> str:
    try:
        match name:
            case "fetch_url":
                return _fetch_url(inputs["url"])
            case "extract_page_data":
                return _extract_page_data(inputs["html"], inputs.get("base_url", ""))
            case "analyze_readability":
                return _analyze_readability(inputs["text"])
            case "analyze_keyword_density":
                return _analyze_keyword_density(
                    inputs["text"],
                    inputs["keywords"],
                    title=inputs.get("title", ""),
                    h1=inputs.get("h1", ""),
                    first_paragraph=inputs.get("first_paragraph", ""),
                )
            case "score_geo_signals":
                return _score_geo_signals(
                    inputs["text"],
                    headings=inputs.get("headings", []),
                    structured_data=inputs.get("structured_data", []),
                    first_paragraph=inputs.get("first_paragraph", ""),
                    title=inputs.get("title", ""),
                )
            case "extract_ai_citable_content":
                return _extract_ai_citable_content(
                    inputs["text"],
                    headings=inputs.get("headings", []),
                    first_paragraph=inputs.get("first_paragraph", ""),
                    title=inputs.get("title", ""),
                    target_keywords=inputs.get("target_keywords", []),
                )
            case "compare_content_gap":
                return _compare_content_gap(
                    target_title=inputs.get("target_title", ""),
                    target_headings=inputs.get("target_headings", []),
                    target_text=inputs["target_text"],
                    competitor_title=inputs.get("competitor_title", ""),
                    competitor_headings=inputs.get("competitor_headings", []),
                    competitor_text=inputs["competitor_text"],
                    competitor_url=inputs.get("competitor_url", ""),
                )
            case "extract_faq_opportunities":
                return _extract_faq_opportunities(
                    inputs["text"],
                    headings=inputs.get("headings", []),
                    title=inputs.get("title", ""),
                    keywords=inputs.get("keywords", []),
                    page_url=inputs.get("page_url", ""),
                )
            case "write_seo_report":
                return _write_report(inputs["content"], inputs["output_path"])
            case _:
                return f"Error: unknown tool '{name}'"
    except KeyError as exc:
        return f"Error: missing required parameter {exc} for tool '{name}'"
    except Exception as exc:
        return f"Error executing '{name}': {exc}"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _fetch_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()
            html = resp.read().decode(encoding, errors="replace")
    except Exception as exc:
        return f"Error fetching URL: {exc}"

    if len(html) > _MAX_BODY_CHARS:
        html = html[:_MAX_BODY_CHARS] + "\n\n[... TRUNCATED ...]"

    return f"[Fetched: {url}  ({len(html):,} chars)]\n\n{html}"


def _extract_page_data(html: str, base_url: str = "") -> str:
    if not _BS4_AVAILABLE:
        return "Error: beautifulsoup4 not installed. Run: pip install beautifulsoup4"

    # Strip fetch header if present
    if html.startswith("[Fetched:"):
        html = "\n".join(html.split("\n")[2:])

    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, Any] = {}

    # Title
    title_tag = soup.find("title")
    data["title"] = title_tag.get_text(strip=True) if title_tag else ""
    data["title_length"] = len(data["title"])

    # Meta tags
    meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    data["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else ""
    data["meta_description_length"] = len(data["meta_description"])

    meta_kw = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
    data["meta_keywords"] = meta_kw.get("content", "").strip() if meta_kw else ""

    meta_robots = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    data["meta_robots"] = meta_robots.get("content", "").strip() if meta_robots else ""

    canonical = soup.find("link", rel="canonical")
    data["canonical"] = canonical.get("href", "").strip() if canonical else ""

    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    data["viewport"] = viewport.get("content", "").strip() if viewport else ""

    lang_tag = soup.find("html")
    data["lang"] = lang_tag.get("lang", "") if lang_tag else ""

    # Open Graph
    og_tags = {}
    for tag in soup.find_all("meta", property=re.compile(r"^og:", re.I)):
        og_tags[tag.get("property", "")] = tag.get("content", "")
    data["open_graph"] = og_tags

    # Twitter Card
    tw_tags = {}
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:", re.I)}):
        tw_tags[tag.get("name", "")] = tag.get("content", "")
    data["twitter_card"] = tw_tags

    # Headings
    headings = []
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            headings.append({"level": level, "text": h.get_text(strip=True)})
    data["headings"] = headings
    data["h1_count"] = sum(1 for h in headings if h["level"] == 1)
    data["h1_text"] = next((h["text"] for h in headings if h["level"] == 1), "")

    # Links
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = parsed_base.netloc.lower().replace("www.", "")
    internal_links, external_links, broken_anchors = [], [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        anchor = a.get_text(strip=True)
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            if not href or href == "#":
                broken_anchors.append(anchor or "(empty)")
            continue
        if href.startswith("http"):
            domain = urllib.parse.urlparse(href).netloc.lower().replace("www.", "")
            if domain == base_domain:
                internal_links.append({"href": href, "anchor": anchor})
            else:
                external_links.append({"href": href, "anchor": anchor})
        else:
            internal_links.append({"href": href, "anchor": anchor})
    data["internal_links_count"] = len(internal_links)
    data["external_links_count"] = len(external_links)
    data["internal_links_sample"] = internal_links[:10]
    data["external_links_sample"] = external_links[:10]
    data["empty_anchors"] = broken_anchors[:10]

    # Images
    images_missing_alt, images_with_alt, images_empty_alt = [], [], []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt")
        if alt is None:
            images_missing_alt.append(src)
        elif alt.strip() == "":
            images_empty_alt.append(src)
        else:
            images_with_alt.append({"src": src, "alt": alt.strip()})
    data["images_total"] = len(images_missing_alt) + len(images_with_alt) + len(images_empty_alt)
    data["images_missing_alt"] = images_missing_alt[:10]
    data["images_empty_alt"] = images_empty_alt[:10]
    data["images_with_alt_count"] = len(images_with_alt)

    # Structured data (JSON-LD)
    schema_blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            schema_blocks.append(json.loads(script.string or "{}"))
        except json.JSONDecodeError:
            pass
    data["structured_data"] = schema_blocks
    data["structured_data_types"] = [b.get("@type", "unknown") for b in schema_blocks]

    # Body text
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    body_text = soup.get_text(separator=" ", strip=True)
    body_text = re.sub(r"\s{2,}", " ", body_text)
    data["word_count"] = len(body_text.split())
    data["body_text_preview"] = body_text[:2000]

    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    data["first_paragraph"] = paragraphs[0] if paragraphs else ""
    data["paragraph_count"] = len(paragraphs)

    return json.dumps(data, indent=2, ensure_ascii=False)


def _analyze_readability(text: str) -> str:
    if not text.strip():
        return "Error: no text provided."

    word_count = len(text.split())
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    sentence_count = max(len(sentences), 1)
    avg_sentence_length = round(word_count / sentence_count, 1)

    # Count syllables (simple approximation)
    def _syllables(word: str) -> int:
        word = word.lower().strip(".,!?;:")
        if not word:
            return 0
        count = len(re.findall(r"[aeiouy]+", word))
        if word.endswith("e") and count > 1:
            count -= 1
        return max(count, 1)

    words = text.split()
    syllable_counts = [_syllables(w) for w in words]
    total_syllables = sum(syllable_counts)
    avg_syllables = round(total_syllables / max(len(words), 1), 2)

    if _TEXTSTAT_AVAILABLE:
        flesch = round(textstat.flesch_reading_ease(text), 1)
        fk_grade = round(textstat.flesch_kincaid_grade(text), 1)
        fog = round(textstat.gunning_fog(text), 1)
        smog = round(textstat.smog_index(text), 1)
        cli = round(textstat.coleman_liau_index(text), 1)
        ari = round(textstat.automated_readability_index(text), 1)
    else:
        # Approximate Flesch manually
        flesch = round(206.835 - 1.015 * avg_sentence_length - 84.6 * avg_syllables, 1)
        fk_grade = round(0.39 * avg_sentence_length + 11.8 * avg_syllables - 15.59, 1)
        fog = smog = cli = ari = None

    def _flesch_label(score: float) -> str:
        if score >= 90: return "Very Easy (5th grade)"
        if score >= 80: return "Easy (6th grade)"
        if score >= 70: return "Fairly Easy (7th grade)"
        if score >= 60: return "Standard (8th–9th grade)"
        if score >= 50: return "Fairly Difficult (10th–12th grade)"
        if score >= 30: return "Difficult (college level)"
        return "Very Confusing (college graduate level)"

    result = {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_sentence_length_words": avg_sentence_length,
        "avg_syllables_per_word": avg_syllables,
        "flesch_reading_ease": flesch,
        "flesch_label": _flesch_label(flesch),
        "flesch_kincaid_grade": fk_grade,
        "recommendation": (
            "Good — suitable for general web audiences." if flesch >= 60
            else "Too complex — simplify sentences and vocabulary for better engagement."
        ),
    }
    if _TEXTSTAT_AVAILABLE:
        result.update({"gunning_fog": fog, "smog_index": smog, "coleman_liau": cli, "ari": ari})

    return json.dumps(result, indent=2)


def _analyze_keyword_density(
    text: str,
    keywords: list[str],
    title: str = "",
    h1: str = "",
    first_paragraph: str = "",
) -> str:
    text_lower = text.lower()
    words = re.findall(r"\b\w+\b", text_lower)
    total_words = max(len(words), 1)

    results = []
    for kw in keywords:
        kw_lower = kw.lower()
        # Multi-word keyword matching
        count = len(re.findall(re.escape(kw_lower), text_lower))
        density = round(count / total_words * 100, 2)

        in_title = kw_lower in title.lower() if title else False
        in_h1 = kw_lower in h1.lower() if h1 else False
        in_first_para = kw_lower in first_paragraph.lower() if first_paragraph else False

        if density > 3.0:
            density_verdict = "STUFFING — reduce usage"
        elif density >= 0.5:
            density_verdict = "Good"
        elif density > 0:
            density_verdict = "Under-optimised — increase usage"
        else:
            density_verdict = "Missing — add this keyword"

        results.append({
            "keyword": kw,
            "occurrences": count,
            "density_pct": density,
            "density_verdict": density_verdict,
            "in_title": in_title,
            "in_h1": in_h1,
            "in_first_paragraph": in_first_para,
            "placement_score": f"{sum([in_title, in_h1, in_first_para])}/3 key positions",
        })

    return json.dumps({"total_word_count": total_words, "keywords": results}, indent=2)


def _score_geo_signals(
    text: str,
    headings: list[str] | None = None,
    structured_data: list[dict] | None = None,
    first_paragraph: str = "",
    title: str = "",
) -> str:
    """
    Research-backed GEO scoring derived from:
    - GEO paper (Princeton / ACL 2024): measured citation lift per strategy
      statistics +30%, quotable quotes +29%, citations/attribution +23%
    - C-SEO Bench (NeurIPS 2025): FAQ format = top structural signal (~40% lift)
    - "What Evidence Do LLMs Find Convincing?" (2024): named institutions +
      specific dates drive higher citation rates than anonymous claims

    Signal weights (sum to 100%):
      statistics_density   20%  — highest single-signal lift in GEO paper
      named_attribution    20%  — named sources + dates + institutions
      quotable_sentences   20%  — 10-25 word self-contained factual sentences
      direct_answer        20%  — inverted pyramid, answers Q in first 2 sentences
      faq_structure        15%  — top structural signal per C-SEO Bench
      schema_markup         5%  — supporting machine-readability signal
    """
    headings = headings or []
    structured_data = structured_data or []
    text_lower = text.lower()
    heading_text = " ".join(headings).lower()
    all_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    word_count = len(text.split())

    signals: dict[str, Any] = {}
    score_parts: dict[str, float] = {}
    actions: list[dict] = []  # ranked improvement actions

    # ------------------------------------------------------------------
    # 1. STATISTICS DENSITY  (weight: 20%)
    # Target: ≥3 data points per 500 words — GEO paper benchmark
    # ------------------------------------------------------------------
    stat_pattern = re.compile(
        r"\b\d+(?:\.\d+)?%"                        # percentages: 42%, 3.5%
        r"|\$[\d,]+(?:\.\d+)?[KMBkmb]?"            # dollar amounts
        r"|\b\d+(?:\.\d+)?[x×] "                   # multipliers: 3x, 4.5×
        r"|\b\d{1,3}(?:,\d{3})+"                   # large numbers: 1,000,000
        r"|\b\d+ (?:million|billion|trillion|thousand)\b"
        r"|\b(?:doubled|tripled|halved)\b"          # relative scale words
    )
    stats_found = stat_pattern.findall(text)
    stats_per_500 = round(len(stats_found) / max(word_count, 1) * 500, 1)
    target_stats = max(1, round(word_count / 500) * 3)

    if stats_per_500 >= 3:
        stat_score = 100
    elif stats_per_500 >= 2:
        stat_score = 70
    elif stats_per_500 >= 1:
        stat_score = 40
    elif len(stats_found) >= 1:
        stat_score = 15
    else:
        stat_score = 0

    signals["statistics_density"] = {
        "score": stat_score,
        "research_weight": "20% — +30% citation lift (GEO paper)",
        "stats_found_count": len(stats_found),
        "stats_per_500_words": stats_per_500,
        "target": "≥3 per 500 words",
        "stats_sample": stats_found[:10],
        "gap": max(0, target_stats - len(stats_found)),
    }
    score_parts["statistics_density"] = stat_score * 0.20
    if stat_score < 70:
        actions.append({
            "priority": 1,
            "signal": "statistics_density",
            "action": (
                f"Add {max(0, target_stats - len(stats_found))} more data points. "
                "Replace vague claims ('many companies', 'significant growth') with "
                "specific numbers, percentages, or sourced figures."
            ),
            "expected_lift": "+30% citation rate (GEO paper)",
        })

    # ------------------------------------------------------------------
    # 2. NAMED ATTRIBUTION  (weight: 20%)
    # Named institutions + dates outperform anonymous claims
    # per "What Evidence Do LLMs Find Convincing?" (2024)
    # ------------------------------------------------------------------
    # Phrase-level attribution markers
    phrase_attribution = re.findall(
        r"(?:according to|found by|reported by|published by|per|cited by|"
        r"study by|research (?:from|by)|data from|source:)",
        text_lower,
    )
    # Named institution signals (capitalised multi-word proper nouns near numbers or claims)
    named_institution = re.findall(
        r"\b(?:University|Institute|Research|Lab|Foundation|Association|"
        r"Agency|Bureau|Center|Centre|Council|Group|Inc\.|Corp\.|Ltd\.)\b",
        text,
    )
    # Year citations like (2023) or ", 2024"
    year_citations = re.findall(r"\(\d{4}\)|\b20(?:1[5-9]|2[0-9])\b", text)
    # Quoted expert / person attribution
    quoted_expert = re.findall(r'"[^"]{10,120}"', text)

    phrase_count = len(phrase_attribution)
    institution_count = len(set(named_institution))
    year_count = len(year_citations)
    quote_count = len(quoted_expert)

    attr_score = min(
        phrase_count * 20
        + institution_count * 15
        + min(year_count, 5) * 8
        + quote_count * 10,
        100,
    )

    signals["named_attribution"] = {
        "score": attr_score,
        "research_weight": "20% — +23% citation lift (GEO paper); named sources preferred by LLMs",
        "phrase_attributions": phrase_count,
        "named_institutions": institution_count,
        "year_citations": year_count,
        "quoted_experts": quote_count,
        "target": "≥3 phrase attributions + named sources with dates",
        "sample_phrases": phrase_attribution[:5],
    }
    score_parts["named_attribution"] = attr_score * 0.20
    if attr_score < 60:
        actions.append({
            "priority": 2,
            "signal": "named_attribution",
            "action": (
                "Add named source attribution to your top claims. "
                "Format: 'According to [Named Institution] (Year), ...' "
                "Anonymous claims get cited far less than attributed ones. "
                f"Currently {phrase_count} phrase attributions — target ≥3."
            ),
            "expected_lift": "+23% citation rate (GEO paper)",
        })

    # ------------------------------------------------------------------
    # 3. QUOTABLE SENTENCES  (weight: 20%)
    # 10-25 word self-contained factual sentences = AI extractable units
    # +29% lift — second-highest signal in GEO paper
    # ------------------------------------------------------------------
    quotable = []
    non_quotable_long = []
    for s in all_sentences:
        wc = len(s.split())
        # Self-contained: contains a verb and doesn't start with a pronoun referencing prior context
        has_verb = bool(re.search(r"\b(?:is|are|was|were|has|have|had|can|will|does|do|provide|show|increase|reduce|improve|enable|allow|result)\b", s.lower()))
        starts_with_ref = bool(re.match(r"^(?:This|That|These|Those|It|They|He|She|We)\b", s))
        if 10 <= wc <= 25 and has_verb and not starts_with_ref:
            quotable.append(s)
        elif wc > 40:
            non_quotable_long.append(s)

    has_bullets = bool(re.search(r"(?m)^\s*[-•*]\s.+", text))
    has_numbered_list = bool(re.search(r"(?m)^\s*\d+[.)]\s.+", text))
    has_table = bool(re.search(r"\|.+\|", text))

    quotable_score = 0
    if len(quotable) >= 12:
        quotable_score = 100
    elif len(quotable) >= 8:
        quotable_score = 80
    elif len(quotable) >= 4:
        quotable_score = 55
    elif len(quotable) >= 2:
        quotable_score = 30
    elif len(quotable) >= 1:
        quotable_score = 15

    if has_bullets or has_numbered_list:
        quotable_score = min(quotable_score + 15, 100)
    if has_table:
        quotable_score = min(quotable_score + 10, 100)

    signals["quotable_sentences"] = {
        "score": quotable_score,
        "research_weight": "20% — +29% citation lift (GEO paper)",
        "quotable_sentence_count": len(quotable),
        "target": "≥8 quotable sentences (10–25 words, self-contained, factual)",
        "quotable_samples": quotable[:5],
        "long_sentences_to_split": len(non_quotable_long),
        "long_sentence_samples": [s[:120] + "…" for s in non_quotable_long[:3]],
        "has_bullet_lists": has_bullets,
        "has_numbered_lists": has_numbered_list,
        "has_tables": has_table,
    }
    score_parts["quotable_sentences"] = quotable_score * 0.20
    if quotable_score < 60:
        gap = max(0, 8 - len(quotable))
        actions.append({
            "priority": 3,
            "signal": "quotable_sentences",
            "action": (
                f"Create {gap} more quotable sentences. "
                "Target: 10–25 words, contain one clear fact, no pronoun references. "
                f"Also split {len(non_quotable_long)} sentences that are >40 words — "
                "AI models rarely extract long complex sentences verbatim."
            ),
            "expected_lift": "+29% citation rate (GEO paper)",
        })

    # ------------------------------------------------------------------
    # 4. DIRECT ANSWER / INVERTED PYRAMID  (weight: 20%)
    # Answer the core question in the first 1-2 sentences
    # ------------------------------------------------------------------
    direct_score = 0
    fp_words = len(first_paragraph.split()) if first_paragraph else 0
    fp_lower = first_paragraph.lower()

    has_definition_opener = bool(re.search(
        r"\b(?:is|are|refers to|means|defined as|can be defined as|describes)\b",
        fp_lower,
    ))
    has_concise_opener = 20 <= fp_words <= 80
    has_direct_verb = bool(re.search(
        r"\b(?:helps?|enables?|allows?|provides?|gives?|shows?|explains?|covers?)\b",
        fp_lower,
    ))
    question_headings = [h for h in headings if re.match(r"(?i)^(what|how|why|when|who|where|which|can|does|is|are)\b", h)]
    has_summary_section = bool(re.search(r"(?i)\b(?:summary|overview|tldr|tl;dr|key (?:points|takeaways|facts))\b", heading_text))

    if has_definition_opener:
        direct_score += 35
    if has_concise_opener:
        direct_score += 25
    if has_direct_verb:
        direct_score += 15
    if len(question_headings) >= 2:
        direct_score += 15
    elif len(question_headings) == 1:
        direct_score += 8
    if has_summary_section:
        direct_score += 10
    direct_score = min(direct_score, 100)

    signals["direct_answer"] = {
        "score": direct_score,
        "research_weight": "20% — foundational GEO signal; AI extracts opening paragraph most",
        "has_definition_opener": has_definition_opener,
        "first_paragraph_word_count": fp_words,
        "first_paragraph_concise": has_concise_opener,
        "question_formatted_headings": len(question_headings),
        "question_headings_sample": question_headings[:5],
        "has_summary_section": has_summary_section,
        "first_paragraph_preview": first_paragraph[:200] if first_paragraph else "",
    }
    score_parts["direct_answer"] = direct_score * 0.20
    if direct_score < 60:
        issues = []
        if not has_definition_opener and not has_concise_opener:
            issues.append("rewrite the opening paragraph to answer the core topic question in ≤80 words")
        if not question_headings:
            issues.append("reframe at least 2 H2 headings as questions (What/How/Why)")
        if not has_summary_section:
            issues.append("add a 'Key Takeaways' or 'Summary' section near the top")
        actions.append({
            "priority": 4,
            "signal": "direct_answer",
            "action": "; ".join(issues) or "Improve opening paragraph directness.",
            "expected_lift": "Foundational — AI systems extract the opening paragraph most heavily",
        })

    # ------------------------------------------------------------------
    # 5. FAQ STRUCTURE  (weight: 15%)
    # Top structural signal per C-SEO Bench (NeurIPS 2025): ~40% more citations
    # ------------------------------------------------------------------
    faq_score = 0

    has_faq_heading = bool(re.search(r"(?i)\bfaq\b|frequently asked", heading_text))
    # Count H3/H4 headings that are phrased as questions (strong FAQ signal even without "FAQ" label)
    question_h3_count = sum(
        1 for h in headings
        if re.match(r"(?i)^(what|how|why|when|who|where|which|can|does|is|are|will|should|do)\b", h)
    )
    # Count question marks in headings (another FAQ proxy)
    question_mark_headings = sum(1 for h in headings if "?" in h)
    # FAQ schema already present
    has_faq_schema = any(b.get("@type") == "FAQPage" for b in structured_data)
    # Detect Q&A patterns in body text
    qa_pattern_count = len(re.findall(r"(?im)^(?:Q:|Question:|\d+\.\s+(?:What|How|Why|When|Who))", text))

    if has_faq_schema:
        faq_score += 40
    if has_faq_heading:
        faq_score += 25
    if question_h3_count >= 4:
        faq_score += 25
    elif question_h3_count >= 2:
        faq_score += 15
    elif question_h3_count >= 1:
        faq_score += 8
    if question_mark_headings >= 3:
        faq_score += 10
    if qa_pattern_count >= 2:
        faq_score += 10
    faq_score = min(faq_score, 100)

    signals["faq_structure"] = {
        "score": faq_score,
        "research_weight": "15% — top structural signal, ~40% citation lift (C-SEO Bench, NeurIPS 2025)",
        "has_faq_section_heading": has_faq_heading,
        "has_faq_schema": has_faq_schema,
        "question_formatted_h3_headings": question_h3_count,
        "question_mark_headings": question_mark_headings,
        "qa_pattern_occurrences": qa_pattern_count,
        "target": "≥4 question-formatted headings + FAQPage schema",
    }
    score_parts["faq_structure"] = faq_score * 0.15
    if faq_score < 60:
        actions.append({
            "priority": 5,
            "signal": "faq_structure",
            "action": (
                "Add a dedicated FAQ section with ≥5 Q&A pairs. "
                "Format each question as an H3 heading, answer in the first sentence of the paragraph. "
                "Add FAQPage JSON-LD schema. "
                "This is the highest-ROI structural change per C-SEO Bench."
            ),
            "expected_lift": "~40% citation increase (C-SEO Bench, NeurIPS 2025)",
        })

    # ------------------------------------------------------------------
    # 6. SCHEMA MARKUP  (weight: 5%)
    # ------------------------------------------------------------------
    schema_score = 0
    schema_types = [b.get("@type", "") for b in structured_data]
    high_value = {"FAQPage", "HowTo", "Article", "NewsArticle", "BlogPosting", "WebPage"}
    found_high_value = [t for t in schema_types if t in high_value]
    has_howto = sum(1 for h in headings if re.match(r"(?i)^step \d", h)) >= 3
    has_article_schema = any(b.get("@type") in {"Article", "NewsArticle", "BlogPosting"} for b in structured_data)

    schema_score += min(len(found_high_value) * 35, 70)
    if has_howto:
        schema_score += 15
    if has_article_schema and any(b.get("datePublished") for b in structured_data):
        schema_score += 15  # date signals freshness to AI crawlers
    schema_score = min(schema_score, 100)

    signals["schema_markup"] = {
        "score": schema_score,
        "research_weight": "5% — machine-readability supporting signal",
        "schema_types_present": schema_types,
        "high_value_schemas": found_high_value,
        "missing_high_value": [s for s in ["FAQPage", "Article", "HowTo"] if s not in schema_types],
        "has_howto_structure": has_howto,
        "has_date_published": any(b.get("datePublished") for b in structured_data),
    }
    score_parts["schema_markup"] = schema_score * 0.05
    if schema_score < 50:
        missing = [s for s in ["FAQPage", "Article"] if s not in schema_types]
        actions.append({
            "priority": 6,
            "signal": "schema_markup",
            "action": f"Add JSON-LD schema: {', '.join(missing)}. Include datePublished, author, and publisher fields.",
            "expected_lift": "Improves machine-readability for AI crawlers",
        })

    # ------------------------------------------------------------------
    # Final score + verdict
    # ------------------------------------------------------------------
    geo_total = round(sum(score_parts.values()))

    if geo_total >= 80:
        verdict = "Excellent — very high AI citation potential"
    elif geo_total >= 65:
        verdict = "Good — solid GEO foundation, targeted improvements will push citations higher"
    elif geo_total >= 45:
        verdict = "Moderate — significant gaps in the highest-impact signals"
    elif geo_total >= 25:
        verdict = "Weak — unlikely to be cited by AI engines without major changes"
    else:
        verdict = "Very Poor — AI systems will not cite this content"

    # Sort actions by priority
    actions.sort(key=lambda a: a["priority"])

    return json.dumps({
        "geo_score": geo_total,
        "verdict": verdict,
        "scoring_basis": "Princeton GEO paper (ACL 2024) + C-SEO Bench (NeurIPS 2025)",
        "signal_breakdown": signals,
        "score_parts": {k: round(v, 1) for k, v in score_parts.items()},
        "ranked_actions": actions,
    }, indent=2)


def _extract_ai_citable_content(
    text: str,
    headings: list[str] | None = None,
    first_paragraph: str = "",
    title: str = "",
    target_keywords: list[str] | None = None,
) -> str:
    headings = headings or []
    target_keywords = target_keywords or []
    all_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 5]

    # Opening extract — first 1-2 sentences (highest AI extraction probability)
    opening_sentences = all_sentences[:2]

    # Definition sentences — "X is/means/refers to Y"
    definition_pattern = re.compile(
        r"[A-Z][^.!?]{10,120}(?:is|are|means|refers to|defined as|describes)[^.!?]{5,100}[.!?]",
        re.IGNORECASE,
    )
    definitions = [m.group(0).strip() for m in definition_pattern.finditer(text)][:5]

    # Statistics in context — stat + surrounding sentence
    stat_re = re.compile(
        r"\b\d+(?:\.\d+)?%|\$[\d,]+(?:\.\d+)?[KMBkmb]?|\b\d+(?:\.\d+)?[x×] "
        r"|\b\d{1,3}(?:,\d{3})+|\b\d+ (?:million|billion|trillion|thousand)\b"
    )
    stat_sentences = []
    for s in all_sentences:
        if stat_re.search(s) and 8 <= len(s.split()) <= 35:
            stat_sentences.append(s)
    stat_sentences = stat_sentences[:8]

    # FAQ extracts — first sentence under each question-formatted heading
    faq_extracts = []
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    for h in headings:
        if re.match(r"(?i)^(what|how|why|when|who|where|which|can|does|is|are|will|should)\b", h):
            # Find paragraph that follows this heading (heuristic: find text after heading)
            heading_lower = h.lower()
            for i, para in enumerate(paragraphs):
                if heading_lower in para.lower() and i + 1 < len(paragraphs):
                    answer_para = paragraphs[i + 1]
                    first_answer_sentence = re.split(r"(?<=[.!?])\s+", answer_para)[0]
                    faq_extracts.append({"question": h, "likely_cited_answer": first_answer_sentence})
                    break
    faq_extracts = faq_extracts[:6]

    # Quotable units — 10-25 words, self-contained, with a verb, no pronoun opener
    quotable = []
    for s in all_sentences:
        wc = len(s.split())
        has_verb = bool(re.search(
            r"\b(?:is|are|was|were|has|have|had|can|will|does|do|provide|show|"
            r"increase|reduce|improve|enable|allow|result|help|make|give|find)\b", s.lower()
        ))
        starts_with_ref = bool(re.match(r"^(?:This|That|These|Those|It|They|He|She|We)\b", s))
        if 10 <= wc <= 25 and has_verb and not starts_with_ref:
            quotable.append(s)
    quotable = quotable[:8]

    # Coverage gaps — signals absent that reduce citation probability
    gaps = []
    if not definitions:
        gaps.append("No definition sentence — AI cannot easily define the topic from this content")
    if not stat_sentences:
        gaps.append("No statistics — claims are unverifiable, reducing citation confidence")
    attribution_count = len(re.findall(
        r"(?:according to|found by|reported by|study by|research from|data from)", text.lower()
    ))
    if attribution_count == 0:
        gaps.append("No named attribution — anonymous claims are cited less than sourced claims")
    if not faq_extracts:
        gaps.append("No question-formatted headings — FAQ structure is missing (top C-SEO Bench signal)")
    if not first_paragraph or len(first_paragraph.split()) > 80:
        gaps.append("Opening paragraph too long or absent — AI truncates long openers")
    keyword_gaps = [kw for kw in target_keywords if kw.lower() not in text.lower()]
    if keyword_gaps:
        gaps.append(f"Target keywords missing from content: {', '.join(keyword_gaps)}")

    return json.dumps({
        "ai_answer_preview": {
            "likely_opening_citation": opening_sentences,
            "likely_cited_definitions": definitions,
            "likely_cited_statistics": stat_sentences,
            "likely_cited_faq_answers": faq_extracts,
            "top_quotable_units": quotable,
        },
        "coverage_gaps": gaps,
        "total_citable_units": len(definitions) + len(stat_sentences) + len(quotable) + len(faq_extracts),
        "citability_verdict": (
            "High" if len(gaps) <= 1 else
            "Medium" if len(gaps) <= 3 else
            "Low"
        ),
    }, indent=2, ensure_ascii=False)


def _compare_content_gap(
    target_title: str,
    target_headings: list[str],
    target_text: str,
    competitor_title: str,
    competitor_headings: list[str],
    competitor_text: str,
    competitor_url: str = "",
) -> str:
    target_words = len(target_text.split())
    competitor_words = len(competitor_text.split())

    # Normalise headings for fuzzy comparison
    def _normalise(h: str) -> str:
        return re.sub(r"[^a-z0-9 ]", "", h.lower()).strip()

    target_normalised = {_normalise(h) for h in target_headings}
    competitor_normalised = {_normalise(h): h for h in competitor_headings}

    # Headings in competitor not in target (content gaps)
    heading_gaps = [
        orig for norm, orig in competitor_normalised.items()
        if norm not in target_normalised
        and len(norm) > 4  # skip trivially short headings
    ]

    # Topic keyword extraction — simple: significant words in headings
    def _topic_words(headings: list[str]) -> set[str]:
        stopwords = {"a","an","the","is","are","to","of","in","on","for","and","or","with","by","at"}
        words = set()
        for h in headings:
            for w in re.findall(r"\b[a-z]{4,}\b", h.lower()):
                if w not in stopwords:
                    words.add(w)
        return words

    target_topics = _topic_words(target_headings)
    competitor_topics = _topic_words(competitor_headings)
    missing_topics = sorted(competitor_topics - target_topics)
    shared_topics = sorted(target_topics & competitor_topics)

    # GEO quick-score for both (count key signals)
    def _quick_geo(text: str, headings: list[str]) -> dict:
        stat_re = re.compile(r"\b\d+(?:\.\d+)?%|\$[\d,]+|\b\d+ (?:million|billion|thousand)\b")
        attr_re = re.compile(r"according to|study by|research from|reported by", re.I)
        q_headings = sum(1 for h in headings if re.match(r"(?i)^(what|how|why|when|who|where|which|can|does|is|are)\b", h))
        return {
            "word_count": len(text.split()),
            "statistics_count": len(stat_re.findall(text)),
            "attribution_count": len(attr_re.findall(text)),
            "question_headings": q_headings,
            "heading_count": len(headings),
        }

    target_geo = _quick_geo(target_text, target_headings)
    competitor_geo = _quick_geo(competitor_text, competitor_headings)

    # Ranked recommendations
    recommendations = []
    if heading_gaps:
        recommendations.append({
            "priority": 1,
            "action": f"Cover {len(heading_gaps)} topics the competitor addresses that you don't",
            "topics": heading_gaps[:8],
        })
    if competitor_geo["word_count"] > target_words * 1.3:
        deficit = competitor_geo["word_count"] - target_words
        recommendations.append({
            "priority": 2,
            "action": f"Expand content by ~{deficit} words to match competitor depth",
            "detail": f"Target: {target_words} words | Competitor: {competitor_geo['word_count']} words",
        })
    if competitor_geo["statistics_count"] > target_geo["statistics_count"]:
        recommendations.append({
            "priority": 3,
            "action": f"Add {competitor_geo['statistics_count'] - target_geo['statistics_count']} more statistics",
            "detail": f"You: {target_geo['statistics_count']} | Competitor: {competitor_geo['statistics_count']}",
        })
    if competitor_geo["question_headings"] > target_geo["question_headings"]:
        recommendations.append({
            "priority": 4,
            "action": f"Add {competitor_geo['question_headings'] - target_geo['question_headings']} more question-formatted headings",
            "detail": f"You: {target_geo['question_headings']} | Competitor: {competitor_geo['question_headings']}",
        })

    return json.dumps({
        "competitor_url": competitor_url,
        "heading_gaps": heading_gaps,
        "missing_topic_keywords": missing_topics[:20],
        "shared_topic_keywords": shared_topics[:10],
        "signal_comparison": {
            "target": target_geo,
            "competitor": competitor_geo,
        },
        "content_gap_score": round(len(heading_gaps) / max(len(competitor_headings), 1) * 100),
        "ranked_recommendations": recommendations,
    }, indent=2, ensure_ascii=False)


def _extract_faq_opportunities(
    text: str,
    headings: list[str] | None = None,
    title: str = "",
    keywords: list[str] | None = None,
    page_url: str = "",
) -> str:
    headings = headings or []
    keywords = keywords or []
    text_lower = text.lower()

    # Existing Q&A pairs — question headings with answer paragraphs
    existing_qa: list[dict] = []
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    for h in headings:
        if re.match(r"(?i)^(what|how|why|when|who|where|which|can|does|is|are|will|should)\b", h) or "?" in h:
            # Best-effort: grab first substantive sentence from text near heading
            heading_lower = h.lower()
            answer = ""
            for i, para in enumerate(paragraphs):
                if heading_lower[:20] in para.lower() and i + 1 < len(paragraphs):
                    sentences = re.split(r"(?<=[.!?])\s+", paragraphs[i + 1])
                    answer = sentences[0].strip() if sentences else ""
                    break
            if not answer and paragraphs:
                # fallback: search all paragraphs for overlap
                for para in paragraphs:
                    words_in_common = set(heading_lower.split()) & set(para.lower().split())
                    if len(words_in_common) >= 2:
                        sentences = re.split(r"(?<=[.!?])\s+", para)
                        answer = sentences[0].strip() if sentences else ""
                        break
            existing_qa.append({
                "question": h if h.endswith("?") else h + "?",
                "answer_first_sentence": answer[:200] if answer else "(answer not found in content)",
                "status": "existing_heading",
            })

    # Implicit Q&A from definition sentences
    def_pattern = re.compile(
        r"([A-Z][^.!?]{5,60})\s+(?:is|are|means|refers to|defined as)\s+([^.!?]{10,120})[.!?]"
    )
    for m in def_pattern.finditer(text):
        subject = m.group(1).strip()
        if 2 <= len(subject.split()) <= 8:
            existing_qa.append({
                "question": f"What is {subject}?",
                "answer_first_sentence": m.group(0).strip(),
                "status": "derived_from_definition",
            })
    existing_qa = existing_qa[:10]

    # Suggested new questions — based on keywords + common intent patterns
    question_templates = [
        "What is {kw}?",
        "How does {kw} work?",
        "Why is {kw} important?",
        "What are the benefits of {kw}?",
        "How do you improve {kw}?",
        "What is the difference between {kw} and alternatives?",
        "How long does {kw} take?",
        "What are common mistakes with {kw}?",
    ]
    suggested = []
    kw_sources = keywords if keywords else ([title] if title else [])
    for kw in kw_sources[:3]:
        for template in question_templates[:4]:
            q = template.format(kw=kw)
            # Only suggest if not already effectively covered
            if not any(q.lower()[:20] in eq["question"].lower() for eq in existing_qa):
                suggested.append(q)
    suggested = suggested[:8]

    # Generate FAQ HTML
    all_qa = existing_qa[:6]
    faq_items_html = []
    for qa in all_qa:
        q = qa["question"]
        a = qa["answer_first_sentence"]
        faq_items_html.append(
            f'  <div class="faq-item">\n'
            f'    <h3>{q}</h3>\n'
            f'    <p>{a}</p>\n'
            f'  </div>'
        )
    faq_html = (
        '<section class="faq">\n'
        '  <h2>Frequently Asked Questions</h2>\n'
        + "\n".join(faq_items_html)
        + "\n</section>"
    )

    # Generate FAQPage JSON-LD
    schema_entities = []
    for qa in all_qa:
        schema_entities.append({
            "@type": "Question",
            "name": qa["question"],
            "acceptedAnswer": {
                "@type": "Answer",
                "text": qa["answer_first_sentence"],
            },
        })
    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": schema_entities,
    }
    if page_url:
        faq_schema["url"] = page_url

    return json.dumps({
        "existing_qa_pairs": existing_qa,
        "suggested_new_questions": suggested,
        "qa_pairs_total": len(existing_qa),
        "faq_section_html": faq_html,
        "faq_schema_json_ld": json.dumps(faq_schema, indent=2),
        "next_step": (
            "Paste faq_section_html into your page before the closing </article> tag, "
            "and faq_schema_json_ld into a <script type='application/ld+json'> tag in <head>."
        ),
    }, indent=2, ensure_ascii=False)


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
