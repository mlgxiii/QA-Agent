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
            "Score Generative Engine Optimization (GEO) signals in the content. "
            "Checks for: direct-answer opening, definition paragraphs, FAQ sections, "
            "numbered step lists, quotable statistics, entity mentions, and schema markup. "
            "Returns a GEO score 0-100 plus per-signal breakdown."
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
) -> str:
    headings = headings or []
    structured_data = structured_data or []
    text_lower = text.lower()
    heading_text = " ".join(headings).lower()

    signals: dict[str, Any] = {}
    score_parts = {}

    # Direct answer format (30%)
    direct_answer_score = 0
    has_definition = bool(re.search(
        r"\b(is|are|refers to|means|defined as|can be defined)\b",
        first_paragraph.lower()
    ))
    has_short_opener = len(first_paragraph.split()) < 60 if first_paragraph else False
    inverted_pyramid = has_definition or has_short_opener
    if inverted_pyramid:
        direct_answer_score += 50
    question_words_in_headings = sum(
        1 for h in headings if re.match(r"(?i)^(what|how|why|when|who|where|which)\b", h)
    )
    if question_words_in_headings >= 2:
        direct_answer_score += 30
    elif question_words_in_headings == 1:
        direct_answer_score += 15
    if first_paragraph and len(first_paragraph) > 20:
        direct_answer_score = min(direct_answer_score + 20, 100)
    signals["direct_answer"] = {
        "score": direct_answer_score,
        "has_definition_opener": has_definition,
        "has_short_opener": has_short_opener,
        "question_headings_count": question_words_in_headings,
    }
    score_parts["direct_answer"] = direct_answer_score * 0.30

    # Entity & factual clarity (25%)
    entity_score = 0
    stat_pattern = re.compile(r"\d+(?:\.\d+)?%|\$[\d,]+|\b\d{4}\b|\b\d+ (?:million|billion|thousand)\b")
    stat_count = len(stat_pattern.findall(text))
    if stat_count >= 5:
        entity_score += 40
    elif stat_count >= 2:
        entity_score += 25
    elif stat_count >= 1:
        entity_score += 10
    citation_count = len(re.findall(r"according to|study by|research from|reported by|source:", text_lower))
    if citation_count >= 3:
        entity_score += 40
    elif citation_count >= 1:
        entity_score += 20
    signals["entity_clarity"] = {
        "score": entity_score,
        "statistics_found": stat_count,
        "citations_found": citation_count,
    }
    score_parts["entity_clarity"] = entity_score * 0.25

    # Quotability (25%)
    quotability_score = 0
    sentences = re.split(r"[.!?]+", text)
    short_factual = [s.strip() for s in sentences if 10 <= len(s.split()) <= 25]
    if len(short_factual) >= 10:
        quotability_score += 60
    elif len(short_factual) >= 5:
        quotability_score += 40
    elif len(short_factual) >= 2:
        quotability_score += 20
    has_bullets = bool(re.search(r"^\s*[-•*]\s|\n[-•*]\s", text))
    has_numbered_list = bool(re.search(r"\n\s*\d+\.", text))
    if has_bullets or has_numbered_list:
        quotability_score += 30
    has_table = bool(re.search(r"\|.+\|", text))
    if has_table:
        quotability_score += 10
    quotability_score = min(quotability_score, 100)
    signals["quotability"] = {
        "score": quotability_score,
        "short_factual_sentences": len(short_factual),
        "has_bullet_lists": has_bullets,
        "has_numbered_lists": has_numbered_list,
        "has_tables": has_table,
    }
    score_parts["quotability"] = quotability_score * 0.25

    # Structured data & schema (20%)
    schema_score = 0
    schema_types = [b.get("@type", "") for b in structured_data]
    high_value_schemas = {"FAQPage", "HowTo", "Article", "NewsArticle", "BlogPosting"}
    found_high_value = [t for t in schema_types if t in high_value_schemas]
    schema_score += min(len(found_high_value) * 30, 60)
    has_faq_heading = bool(re.search(r"(?i)\bfaq\b|frequently asked", heading_text))
    if has_faq_heading:
        schema_score += 20
    has_howto_structure = sum(1 for h in headings if re.match(r"(?i)^step \d", h)) >= 3
    if has_howto_structure:
        schema_score += 20
    schema_score = min(schema_score, 100)
    signals["schema_and_structure"] = {
        "score": schema_score,
        "schema_types_found": schema_types,
        "high_value_schemas_found": found_high_value,
        "has_faq_section": has_faq_heading,
        "has_howto_structure": has_howto_structure,
    }
    score_parts["schema_and_structure"] = schema_score * 0.20

    geo_total = round(sum(score_parts.values()))

    if geo_total >= 75:
        verdict = "Excellent — high AI citation potential"
    elif geo_total >= 50:
        verdict = "Good — moderate AI citation potential, room to improve"
    elif geo_total >= 25:
        verdict = "Weak — needs significant GEO improvements"
    else:
        verdict = "Very Poor — unlikely to be cited by AI systems"

    return json.dumps({
        "geo_score": geo_total,
        "verdict": verdict,
        "signal_breakdown": signals,
        "score_parts": {k: round(v, 1) for k, v in score_parts.items()},
    }, indent=2)


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
