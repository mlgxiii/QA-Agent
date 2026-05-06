"""
Tools for the Website Design Analyzer Agent.

Supports both live-website analysis (fetch + screenshot via Playwright)
and GitHub repo analysis (file reading, UI file discovery).
"""

import base64
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

MAX_FILE_SIZE_BYTES = 150 * 1024
MAX_LIST_ITEMS = 300
MAX_CSS_SIZE = 80 * 1024  # 80 KB — stylesheets can be huge

_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".build", "target",
    ".mypy_cache", ".pytest_cache", ".tox",
    "coverage", ".coverage",
    "vendor",
    ".next", ".nuxt", ".svelte-kit",
    "public",  # usually compiled assets
    "static",
}

_UI_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm",
    ".jsx", ".tsx",
    ".vue", ".svelte",
    ".js", ".ts",
}


# ---------------------------------------------------------------------------
# Tool schemas for the Anthropic API
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "navigate_page",
        "description": (
            "Visit a URL and return a full-page screenshot plus a summary of the page's "
            "HTML structure (headings, navigation, main sections, CTA buttons). "
            "The screenshot is returned as an image for visual design analysis. "
            "Use this on the homepage and every key page of the product."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to visit (https://…).",
                },
                "viewport_width": {
                    "type": "integer",
                    "description": "Viewport width in pixels. Default: 1440 (desktop). Use 375 for mobile view.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_site_links",
        "description": (
            "Fetch a page and extract all internal navigation links "
            "(nav menus, header/footer links). Returns a deduplicated list of URLs "
            "so you know which pages to visit next."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Page URL to extract links from.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch a URL and return the raw HTML with design-relevant analysis: "
            "heading hierarchy, CSS classes on key elements, linked stylesheets, "
            "meta tags, form structures, and button/CTA inventory. "
            "Use this to understand HTML structure without needing a screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_stylesheet",
        "description": (
            "Download and return the contents of a CSS/SCSS stylesheet URL. "
            "Use this to analyse the design token system: CSS custom properties "
            "(--color-*, --spacing-*, --font-*), Tailwind config, or SCSS variables. "
            "Identify the colour palette, spacing scale, and typography scale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL of the stylesheet to fetch.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and subdirectories in a local project directory. "
            "Use this when analysing a GitHub repository to map the project structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List recursively (default: false).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_ui_files",
        "description": (
            "Recursively find all UI-related files in a local project: "
            ".css, .scss, .html, .jsx, .tsx, .vue, .svelte files. "
            "Use this on a GitHub repo to locate all design-relevant source files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Root directory to search from.",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "File extensions to match. Defaults to all UI extensions: "
                        ".css .scss .html .jsx .tsx .vue .svelte"
                    ),
                },
            },
            "required": ["root"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of a local file with line numbers. "
            "Use this to examine CSS, component files, design token files, "
            "Tailwind config, theme files, etc. in a GitHub repository."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (absolute or relative to the analysis root).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_report",
        "description": (
            "Save the final design analysis report as a Markdown file. "
            "Call this exactly once when the full analysis is complete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Complete Markdown report content.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Destination file path for the report.",
                },
            },
            "required": ["content", "output_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def execute_tool(name: str, inputs: dict[str, Any], root_path: str) -> Any:
    """
    Dispatch a tool call and return either:
      - str  → plain text result
      - dict with {"type": "image_result", ...} → screenshot to pass as vision content
      - dict with {"type": "error", "text": ...} → error message
    """
    try:
        match name:
            case "navigate_page":
                return _navigate_page(
                    inputs["url"],
                    viewport_width=inputs.get("viewport_width", 1440),
                )
            case "get_site_links":
                return _get_site_links(inputs["url"])
            case "fetch_page":
                return _fetch_page(inputs["url"])
            case "fetch_stylesheet":
                return _fetch_stylesheet(inputs["url"])
            case "list_directory":
                return _list_directory(
                    inputs["path"],
                    root_path,
                    recursive=inputs.get("recursive", False),
                )
            case "find_ui_files":
                return _find_ui_files(
                    inputs["root"],
                    root_path,
                    extensions=inputs.get("extensions"),
                )
            case "read_file":
                return _read_file(inputs["path"], root_path)
            case "write_report":
                return _write_report(inputs["content"], inputs["output_path"])
            case _:
                return f"Error: unknown tool '{name}'"
    except KeyError as exc:
        return f"Error: missing required parameter {exc} for tool '{name}'"
    except Exception as exc:
        return f"Error executing '{name}': {exc}"


# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------

def _get_http_session():
    """Return a requests.Session with browser-like headers."""
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _navigate_page(url: str, viewport_width: int = 1440) -> Any:
    """Take a screenshot and return structural summary."""
    screenshot_result = _take_screenshot(url, viewport_width)
    structure = _fetch_page(url)

    if isinstance(screenshot_result, dict) and screenshot_result.get("type") == "image_result":
        # Prepend the structure summary as text in the same result
        screenshot_result["text"] = f"Page structure for {url}:\n\n{structure}\n\n---\nScreenshot:"
        return screenshot_result

    # Screenshot failed — return text-only analysis
    return f"[Screenshot unavailable: {screenshot_result}]\n\n{structure}"


def _take_screenshot(url: str, viewport_width: int = 1440) -> Any:
    """Capture a full-page screenshot using Playwright."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return "Playwright not installed — visual analysis unavailable."

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.new_page(viewport={"width": viewport_width, "height": 900})

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                # Brief wait for above-fold rendering
                page.wait_for_timeout(1500)
            except PWTimeout:
                pass  # take screenshot of whatever loaded

            png_bytes = page.screenshot(full_page=True, type="png")
            browser.close()

        b64 = base64.b64encode(png_bytes).decode()
        return {
            "type": "image_result",
            "text": f"Screenshot of {url}",
            "media_type": "image/png",
            "data": b64,
        }
    except Exception as exc:
        return f"Screenshot failed: {exc}"


def _fetch_page(url: str) -> str:
    """Fetch HTML and return a design-relevant structural summary."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _fetch_page_raw(url)

    try:
        session = _get_http_session()
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    soup = BeautifulSoup(resp.text, "html.parser")
    lines: list[str] = [f"## Page Analysis: {url}\n"]

    # Meta
    title = soup.find("title")
    lines.append(f"**Title:** {title.get_text(strip=True) if title else '(none)'}")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        lines.append(f"**Meta description:** {meta_desc.get('content', '')[:200]}")

    # Viewport
    meta_vp = soup.find("meta", attrs={"name": "viewport"})
    lines.append(f"**Viewport meta:** {meta_vp.get('content') if meta_vp else 'MISSING — not mobile-optimised'}")

    # Heading hierarchy
    lines.append("\n### Heading Hierarchy")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    if headings:
        for h in headings[:30]:
            indent = "  " * (int(h.name[1]) - 1)
            lines.append(f"{indent}<{h.name}> {h.get_text(strip=True)[:100]}")
    else:
        lines.append("No headings found.")

    # Navigation
    lines.append("\n### Navigation")
    nav_els = soup.find_all("nav")
    if nav_els:
        for nav in nav_els[:3]:
            links = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)]
            lines.append(f"Nav ({len(links)} items): {' | '.join(links[:15])}")
    else:
        header = soup.find("header")
        if header:
            links = [a.get_text(strip=True) for a in header.find_all("a") if a.get_text(strip=True)]
            lines.append(f"Header links ({len(links)}): {' | '.join(links[:15])}")
        else:
            lines.append("No <nav> or <header> found.")

    # CTAs (buttons and prominent links)
    lines.append("\n### Buttons & CTAs")
    buttons = soup.find_all("button")
    cta_links = [a for a in soup.find_all("a") if a.get("class") and any(
        kw in " ".join(a.get("class", [])).lower()
        for kw in ("btn", "button", "cta", "primary", "action")
    )]
    all_ctas = buttons[:20] + cta_links[:10]
    if all_ctas:
        for el in all_ctas:
            txt = el.get_text(strip=True)[:80]
            classes = " ".join(el.get("class", []))[:60]
            lines.append(f"  [{el.name}] {txt!r}  classes={classes!r}")
    else:
        lines.append("No obvious button/CTA elements found.")

    # Forms
    lines.append("\n### Forms")
    forms = soup.find_all("form")
    if forms:
        for form in forms[:5]:
            inputs = form.find_all(["input", "textarea", "select"])
            action = form.get("action", "(no action attr)")
            lines.append(f"  Form → {action}  ({len(inputs)} fields)")
            for inp in inputs[:8]:
                label = inp.get("placeholder") or inp.get("name") or inp.get("type") or "?"
                lines.append(f"    - {inp.name} type={inp.get('type','text')} label/placeholder={label!r}")
    else:
        lines.append("No forms found.")

    # Linked stylesheets
    lines.append("\n### Linked Stylesheets")
    stylesheets = soup.find_all("link", rel=lambda r: r and "stylesheet" in r)
    if stylesheets:
        for link in stylesheets[:10]:
            href = link.get("href", "")
            absolute = urljoin(url, href)
            lines.append(f"  {absolute}")
    else:
        lines.append("No external stylesheets found (likely inline or CSS-in-JS).")

    # Inline style hints
    style_tags = soup.find_all("style")
    if style_tags:
        lines.append(f"\n### Inline <style> blocks: {len(style_tags)}")
        combined = "\n".join(st.get_text() for st in style_tags)
        vars_found = re.findall(r"(--[\w-]+)\s*:", combined)
        if vars_found:
            lines.append(f"CSS custom properties found: {', '.join(sorted(set(vars_found))[:30])}")

    # CSS framework hints
    all_classes = " ".join(
        cls
        for el in soup.find_all(class_=True)
        for cls in el.get("class", [])
    )
    framework_hints: list[str] = []
    if re.search(r"\b(tw-|text-\w+-\d{3}|bg-\w+-\d{3}|flex|grid|gap-\d)", all_classes):
        framework_hints.append("Tailwind CSS (utility classes detected)")
    if re.search(r"\b(MuiButton|MuiBox|MuiCard)\b", all_classes):
        framework_hints.append("Material UI (MUI)")
    if re.search(r"\b(chakra-|chakra)\b", all_classes):
        framework_hints.append("Chakra UI")
    if re.search(r"\bbtn\b|col-\d|row\b|container\b", all_classes):
        framework_hints.append("Bootstrap")
    if framework_hints:
        lines.append(f"\n### Detected CSS Framework(s): {', '.join(framework_hints)}")

    # Image alt text check
    imgs = soup.find_all("img")
    imgs_missing_alt = [img for img in imgs if not img.get("alt")]
    if imgs:
        lines.append(f"\n### Images: {len(imgs)} total, {len(imgs_missing_alt)} missing alt text")

    return "\n".join(lines)


def _fetch_page_raw(url: str) -> str:
    """Fallback fetch when BeautifulSoup is unavailable."""
    try:
        session = _get_http_session()
        resp = session.get(url, timeout=15)
        text = resp.text[:8000]
        return f"[Raw HTML excerpt from {url}]\n\n{text}"
    except Exception as exc:
        return f"Error fetching {url}: {exc}"


def _get_site_links(url: str) -> str:
    """Extract internal navigation links from a page."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return "BeautifulSoup not installed — link extraction unavailable."

    try:
        session = _get_http_session()
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    parsed_base = urlparse(url)
    base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"
    soup = BeautifulSoup(resp.text, "html.parser")

    seen: set[str] = set()
    nav_links: list[str] = []
    other_links: list[str] = []

    # Prioritise nav/header/footer links
    priority_els = soup.find_all(["nav", "header", "footer"])
    priority_anchors = [a for el in priority_els for a in el.find_all("a", href=True)]
    all_anchors = priority_anchors + soup.find_all("a", href=True)

    for a in all_anchors:
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        absolute = urljoin(base_domain, href)
        parsed = urlparse(absolute)
        if parsed.netloc != parsed_base.netloc:
            continue
        # Skip obvious non-page resources
        if re.search(r"\.(png|jpg|jpeg|gif|svg|ico|pdf|zip|css|js|woff2?)$", absolute, re.I):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        label = a.get_text(strip=True) or "(no label)"
        entry = f"  {label[:40]:<42} {absolute}"
        if a in priority_anchors:
            nav_links.append(entry)
        else:
            other_links.append(entry)

    lines = [f"## Links found on {url}\n"]
    if nav_links:
        lines.append(f"### Navigation links ({len(nav_links)})")
        lines.extend(nav_links[:40])
    if other_links:
        lines.append(f"\n### Other internal links ({len(other_links)})")
        lines.extend(other_links[:30])
    if not nav_links and not other_links:
        lines.append("No internal links found.")

    return "\n".join(lines)


def _fetch_stylesheet(url: str) -> str:
    """Fetch a CSS file and return it with design token analysis."""
    try:
        session = _get_http_session()
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        css = resp.text
    except Exception as exc:
        return f"Error fetching stylesheet {url}: {exc}"

    if len(css) > MAX_CSS_SIZE:
        css_excerpt = css[:MAX_CSS_SIZE]
        truncated = True
    else:
        css_excerpt = css
        truncated = False

    # Extract design tokens
    css_vars = re.findall(r"(--[\w-]+)\s*:\s*([^;}{]+);", css_excerpt)
    token_section = ""
    if css_vars:
        color_tokens = [(k, v.strip()) for k, v in css_vars if any(
            kw in k.lower() for kw in ("color", "colour", "bg", "text", "border", "fill", "stroke")
        )]
        space_tokens = [(k, v.strip()) for k, v in css_vars if any(
            kw in k.lower() for kw in ("space", "spacing", "gap", "margin", "padding", "size")
        )]
        font_tokens = [(k, v.strip()) for k, v in css_vars if any(
            kw in k.lower() for kw in ("font", "text", "type", "size", "weight", "line", "letter")
        )]
        other_tokens = [(k, v.strip()) for k, v in css_vars
                        if (k, v.strip()) not in color_tokens + space_tokens + font_tokens]

        parts = [f"\n### CSS Custom Properties (Design Tokens)"]
        if color_tokens:
            parts.append(f"\n**Colours ({len(color_tokens)}):**")
            parts.extend(f"  {k}: {v}" for k, v in color_tokens[:40])
        if space_tokens:
            parts.append(f"\n**Spacing ({len(space_tokens)}):**")
            parts.extend(f"  {k}: {v}" for k, v in space_tokens[:20])
        if font_tokens:
            parts.append(f"\n**Typography ({len(font_tokens)}):**")
            parts.extend(f"  {k}: {v}" for k, v in font_tokens[:20])
        if other_tokens:
            parts.append(f"\n**Other tokens ({len(other_tokens)}):**")
            parts.extend(f"  {k}: {v}" for k, v in other_tokens[:20])
        token_section = "\n".join(parts)

    header = f"## Stylesheet: {url}"
    if truncated:
        header += f"  (truncated to {MAX_CSS_SIZE // 1024} KB)"
    return f"{header}{token_section}\n\n### Raw CSS:\n```css\n{css_excerpt}\n```"


# ---------------------------------------------------------------------------
# Local file tools (for GitHub repo analysis)
# ---------------------------------------------------------------------------

def _resolve(path: str, root: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    return p.resolve()


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


def _read_file(path: str, root: str) -> str:
    target = _resolve(path, root)
    if not target.exists():
        return f"Error: file not found — {path}"
    if not target.is_file():
        return f"Error: path is a directory — {path}"

    raw_size = target.stat().st_size
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"

    lines = text.splitlines()
    numbered = "\n".join(f"{i + 1:5d} | {line}" for i, line in enumerate(lines))

    if raw_size > MAX_FILE_SIZE_BYTES:
        truncated = numbered[:MAX_FILE_SIZE_BYTES]
        shown = truncated.count("\n")
        return (
            f"[File: {path}  ({raw_size:,} bytes) — showing first ~{shown} lines]\n\n"
            f"{truncated}\n\n[… TRUNCATED …]"
        )
    return f"[File: {path}  ({raw_size:,} bytes, {len(lines)} lines)]\n\n{numbered}"


def _list_directory(path: str, root: str, recursive: bool = False) -> str:
    target = _resolve(path, root)
    if not target.exists():
        return f"Error: directory not found — {path}"
    if not target.is_dir():
        return f"Error: not a directory — {path}"

    entries: list[str] = []
    count = 0

    def _walk(directory: Path, prefix: str = "") -> None:
        nonlocal count
        try:
            items = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        for item in items:
            if count >= MAX_LIST_ITEMS:
                entries.append(f"{prefix}... (limit reached)")
                return
            if item.is_dir():
                if _should_skip_dir(item.name):
                    continue
                entries.append(f"{prefix}📁 {item.name}/")
                count += 1
                if recursive:
                    _walk(item, prefix + "  ")
            elif item.is_file():
                if item.name.startswith("."):
                    continue
                sz = item.stat().st_size
                size_str = f"{sz} B" if sz < 1024 else f"{sz // 1024} KB"
                entries.append(f"{prefix}📄 {item.name}  ({size_str})")
                count += 1

    _walk(target)
    if not entries:
        return f"Directory is empty: {path}"
    return f"[Directory: {path}]\n" + "\n".join(entries)


def _find_ui_files(root: str, analysis_root: str, extensions: list[str] | None = None) -> str:
    target = _resolve(root, analysis_root)
    if not target.exists():
        return f"Error: root not found — {root}"
    if not target.is_dir():
        return f"Error: not a directory — {root}"

    ext_set = (
        {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
        if extensions
        else _UI_EXTENSIONS
    )

    results: list[str] = []
    count = 0

    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in sorted(filenames):
            if count >= MAX_LIST_ITEMS:
                results.append("... (limit reached)")
                break
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in ext_set:
                continue
            rel = fpath.relative_to(target)
            sz = fpath.stat().st_size
            size_str = f"{sz} B" if sz < 1024 else f"{sz // 1024} KB"
            results.append(f"{rel}  ({size_str})")
            count += 1
        if count >= MAX_LIST_ITEMS:
            break

    if not results:
        return f"No UI files found under: {root}"
    return f"[UI files in {root} — {count} result(s)]\n" + "\n".join(results)


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
