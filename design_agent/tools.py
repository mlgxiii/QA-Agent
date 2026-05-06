"""
Tools available to the Website Design Analyzer agent.

Two operating modes:
  - WEB mode  : input is a live URL  → fetch_page / fetch_css / extract_design_tokens
  - REPO mode : input is a local dir → read_file / list_directory / find_design_files / search_in_file

Both modes share write_report.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

MAX_FILE_SIZE_BYTES = 150 * 1024
MAX_LIST_ITEMS = 300
MAX_SEARCH_MATCHES = 25
MAX_CSS_BYTES = 120 * 1024   # 120 KB of CSS returned to the model
MAX_HTML_CHARS = 60_000      # cap on raw HTML if requested

_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".build", "out", "target",
    ".mypy_cache", ".pytest_cache", ".tox",
    "coverage", ".coverage",
    "vendor",
    ".next", ".nuxt", ".svelte-kit", ".output",
}

_DESIGN_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less",
    ".jsx", ".tsx", ".vue", ".svelte",
}

_DESIGN_TOKEN_FILES = {
    "tailwind.config.js", "tailwind.config.ts", "tailwind.config.cjs",
    "theme.ts", "theme.js", "tokens.json", "tokens.ts", "tokens.js",
    "design-tokens.json", "design-tokens.ts",
    "variables.css", "variables.scss", "_variables.scss",
    "colors.ts", "colors.js", "colors.json",
    "typography.ts", "typography.js",
    "spacing.ts", "spacing.js",
    "globals.css", "global.css", "base.css", "reset.css",
}

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --------------------------------------------------------------------------- #
# Tool schemas                                                                 #
# --------------------------------------------------------------------------- #

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "fetch_page",
        "description": (
            "Fetch a web page and return its structural analysis: title, meta description, "
            "heading hierarchy (H1–H3), navigation links, CTA buttons, CSS class names "
            "(reveals design system), and key page sections. "
            "Use this to understand page layout and information architecture. "
            "Pass include_raw_html=true only when you need the raw markup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (must start with http:// or https://).",
                },
                "include_raw_html": {
                    "type": "boolean",
                    "description": "Also return the first 15 KB of raw HTML (default: false).",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "fetch_css",
        "description": (
            "Fetch all CSS from a web page — both <style> blocks and linked stylesheets — "
            "and return the combined CSS content. Call extract_design_tokens on the result "
            "to parse colours, fonts and spacing variables."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The page URL whose CSS you want.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "extract_design_tokens",
        "description": (
            "Parse raw CSS content and extract structured design information: "
            "CSS custom properties (design tokens), all unique colour values, "
            "font families, font sizes, font weights, border-radius values, "
            "and the spacing/sizing values used in padding/margin/gap rules. "
            "Run this after fetch_css to get an instant design audit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "css_content": {
                    "type": "string",
                    "description": "Raw CSS text to analyse (up to 120 KB).",
                },
            },
            "required": ["css_content"],
        },
    },
    # ---- repo / file-system tools ---------------------------------------- #
    {
        "name": "read_file",
        "description": (
            "Read a file from the cloned repository. Returns content with line numbers. "
            "Files larger than 150 KB are truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the repo root, or absolute.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and subdirectories inside a directory of the cloned repo. "
            "build artefacts and vendored dependencies are automatically skipped."
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
                    "description": "List all files recursively (default: false).",
                },
                "extensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by extensions, e.g. [\".css\", \".scss\"].",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_design_files",
        "description": (
            "Recursively find all design-related files in the repo: "
            "CSS/SCSS/LESS stylesheets, JSX/TSX/Vue/Svelte components, "
            "Tailwind config, design token files (JSON/TS), and global style files. "
            "Use this at the start of a repo analysis to map the design landscape."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {
                    "type": "string",
                    "description": "Root directory to search from (defaults to repo root).",
                },
            },
            "required": ["root"],
        },
    },
    {
        "name": "search_in_file",
        "description": (
            "Case-insensitive search for a text pattern inside a file. "
            "Returns matching lines with surrounding context. "
            "Use to find hardcoded colours (#hex, rgb()), off-grid spacing values, "
            "inline styles, or missing design token references."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File to search (relative or absolute).",
                },
                "pattern": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive).",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context around each match (default: 3).",
                },
            },
            "required": ["path", "pattern"],
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


# --------------------------------------------------------------------------- #
# Public dispatcher                                                            #
# --------------------------------------------------------------------------- #

def execute_tool(name: str, inputs: dict[str, Any], root_path: str) -> str:
    """Dispatch a tool call and return the result as a string."""
    try:
        match name:
            case "fetch_page":
                return _fetch_page(
                    inputs["url"],
                    include_raw_html=inputs.get("include_raw_html", False),
                )
            case "fetch_css":
                return _fetch_css(inputs["url"])
            case "extract_design_tokens":
                return _extract_design_tokens(inputs["css_content"])
            case "read_file":
                return _read_file(inputs["path"], root_path)
            case "list_directory":
                return _list_directory(
                    inputs["path"],
                    root_path,
                    recursive=inputs.get("recursive", False),
                    extensions=inputs.get("extensions"),
                )
            case "find_design_files":
                return _find_design_files(inputs["root"], root_path)
            case "search_in_file":
                return _search_in_file(
                    inputs["path"],
                    root_path,
                    inputs["pattern"],
                    context_lines=inputs.get("context_lines", 3),
                )
            case "write_report":
                return _write_report(inputs["content"], inputs["output_path"])
            case _:
                return f"Error: unknown tool '{name}'"
    except KeyError as exc:
        return f"Error: missing required parameter {exc} for tool '{name}'"
    except Exception as exc:  # noqa: BLE001
        return f"Error executing '{name}': {exc}"


# --------------------------------------------------------------------------- #
# Web tools                                                                    #
# --------------------------------------------------------------------------- #

def _http_get(url: str, timeout: int = 15) -> tuple[bytes, str]:
    """Fetch a URL and return (body_bytes, content_type).  Raises on HTTP error."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read(), resp.headers.get("content-type", "")


def _fetch_page(url: str, include_raw_html: bool = False) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return "Error: beautifulsoup4 is not installed. Run: pip install beautifulsoup4"

    try:
        body, _ = _http_get(url)
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    try:
        html = body.decode("utf-8", errors="replace")
    except Exception:
        html = body.decode("latin-1", errors="replace")

    soup = BeautifulSoup(html, "html.parser")

    # ---- meta ----
    title = soup.title.get_text(strip=True) if soup.title else "(no title)"
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag.get("content", "").strip() if meta_desc_tag else "(none)"

    # ---- headings ----
    headings: list[str] = []
    for level in (1, 2, 3):
        for h in soup.find_all(f"h{level}")[:10]:
            text = h.get_text(separator=" ", strip=True)[:120]
            if text:
                headings.append(f"  H{level}: {text}")

    # ---- navigation links ----
    nav_links: list[str] = []
    seen_hrefs: set[str] = set()
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for nav_tag in (soup.find_all("nav") or [soup]):
        for a in nav_tag.find_all("a", href=True)[:60]:
            href = a["href"].strip()
            text = a.get_text(strip=True)[:60]
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            full = urljoin(url, href)
            if full not in seen_hrefs and (full.startswith(base) or href.startswith("/")):
                seen_hrefs.add(full)
                nav_links.append(f"  {text or '(no label)'} → {full}")
        if nav_links:
            break  # use the first <nav>

    # ---- buttons / CTAs ----
    buttons: list[str] = []
    for btn in soup.find_all(["button", "a"], limit=40):
        classes = " ".join(btn.get("class", []))
        text = btn.get_text(strip=True)[:80]
        role = btn.name.upper()
        if any(k in classes.lower() for k in ("btn", "button", "cta", "primary", "signup", "start")):
            buttons.append(f"  [{role}] {text!r}  classes={classes[:80]}")
    buttons = buttons[:20]

    # ---- class names → design system fingerprinting ----
    all_classes: set[str] = set()
    for tag in soup.find_all(True):
        for cls in tag.get("class", []):
            all_classes.add(cls)
    # heuristics: known design systems
    ds_hints: list[str] = []
    class_str = " ".join(all_classes).lower()
    if re.search(r"\b(tw-|text-[a-z]+-\d{3}|bg-[a-z]+-\d{3}|px-\d|py-\d|gap-\d|flex|grid)\b", class_str):
        ds_hints.append("Tailwind CSS detected")
    if "MuiBox" in " ".join(all_classes) or "mui" in class_str:
        ds_hints.append("Material UI (MUI) detected")
    if re.search(r"\b(chakra|ch-)", class_str):
        ds_hints.append("Chakra UI detected")
    if re.search(r"\b(ant-|antd)", class_str):
        ds_hints.append("Ant Design detected")
    if re.search(r"\b(bootstrap|btn-primary|col-md-)", class_str):
        ds_hints.append("Bootstrap detected")
    if re.search(r"\b(radix|shadcn|cmdk)", class_str):
        ds_hints.append("Radix UI / shadcn/ui detected")
    # sample of unique class prefixes
    unique_prefixes = sorted({c.split("-")[0] for c in all_classes if c})[:30]

    # ---- section structure ----
    sections: list[str] = []
    for tag in soup.find_all(["section", "header", "main", "footer", "aside"])[:15]:
        cls = " ".join(tag.get("class", []))[:80]
        first_h = tag.find(re.compile("^h[1-6]$"))
        label = first_h.get_text(strip=True)[:60] if first_h else "(no heading)"
        sections.append(f"  <{tag.name} class='{cls}'> → {label}")

    # ---- image & form count ----
    img_count = len(soup.find_all("img"))
    form_count = len(soup.find_all("form"))
    input_count = len(soup.find_all("input"))
    link_count = len(soup.find_all("a", href=True))

    lines = [
        f"[Page: {url}]",
        f"Title: {title}",
        f"Meta description: {meta_desc}",
        "",
        "## Design System Hints",
        *(ds_hints or ["  No known design system fingerprint detected"]),
        f"  Class prefix sample: {', '.join(unique_prefixes)}",
        "",
        "## Heading Hierarchy",
        *(headings or ["  (no headings found)"]),
        "",
        "## Navigation Links",
        *(nav_links[:20] or ["  (no nav links found)"]),
        "",
        "## Buttons / CTAs (design-system candidates)",
        *(buttons or ["  (no obvious button elements found)"]),
        "",
        "## Page Sections",
        *(sections or ["  (no <section>/<main>/<header> tags found)"]),
        "",
        "## Page Metrics",
        f"  Images: {img_count}   Links: {link_count}   Forms: {form_count}   Inputs: {input_count}",
    ]

    if include_raw_html:
        lines += ["", "## Raw HTML (first 15 KB)", html[:15_000]]

    return "\n".join(lines)


def _fetch_css(url: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return "Error: beautifulsoup4 is not installed."

    try:
        body, _ = _http_get(url)
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    html = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    chunks: list[str] = []
    total = 0

    # Inline <style> blocks
    for i, style_tag in enumerate(soup.find_all("style")):
        text = style_tag.get_text()
        if text.strip():
            chunks.append(f"=== Inline <style> block {i + 1} ===\n{text}")
            total += len(text)

    # Linked stylesheets
    for link in soup.find_all("link", rel=lambda r: r and "stylesheet" in r):
        href = link.get("href", "")
        if not href:
            continue
        sheet_url = urljoin(url, href)
        if not sheet_url.startswith("http"):
            continue
        try:
            css_bytes, _ = _http_get(sheet_url, timeout=10)
            css_text = css_bytes.decode("utf-8", errors="replace")
            chunks.append(f"=== Stylesheet: {sheet_url} ({len(css_text):,} chars) ===\n{css_text}")
            total += len(css_text)
        except Exception as exc:
            chunks.append(f"=== Stylesheet: {sheet_url} — FAILED: {exc} ===")

        if total > MAX_CSS_BYTES:
            chunks.append(f"\n[... CSS truncated at {MAX_CSS_BYTES // 1024} KB ...]")
            break

    if not chunks:
        return f"No CSS found on {url}"

    header = f"[CSS from {url} — {len(chunks)} source(s), ~{total:,} chars total]\n\n"
    combined = "\n\n".join(chunks)
    if len(combined) > MAX_CSS_BYTES:
        combined = combined[:MAX_CSS_BYTES] + "\n\n[... truncated ...]"
    return header + combined


def _extract_design_tokens(css_content: str) -> str:
    if len(css_content) > MAX_CSS_BYTES:
        css_content = css_content[:MAX_CSS_BYTES]

    lines_out: list[str] = ["[Design Token Analysis]\n"]

    # CSS custom properties
    custom_props = re.findall(r"(--[\w-]+)\s*:\s*([^;}{]+);", css_content)
    if custom_props:
        lines_out.append("## CSS Custom Properties (Design Tokens)")
        seen: set[str] = set()
        for name, value in custom_props:
            entry = f"  {name}: {value.strip()}"
            if entry not in seen:
                seen.add(entry)
                lines_out.append(entry)
        lines_out.append("")

    # Colour values
    hex_colors = sorted(set(re.findall(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6,8})\b", css_content)))
    rgb_colors = sorted(set(re.findall(r"rgba?\([^)]+\)", css_content)))
    hsl_colors = sorted(set(re.findall(r"hsla?\([^)]+\)", css_content)))

    lines_out.append("## Colour Values")
    lines_out.append(f"  Hex colours ({len(hex_colors)}): {', '.join(hex_colors[:40])}")
    if rgb_colors:
        lines_out.append(f"  RGB/RGBA  ({len(rgb_colors)}): {', '.join(rgb_colors[:20])}")
    if hsl_colors:
        lines_out.append(f"  HSL/HSLA  ({len(hsl_colors)}): {', '.join(hsl_colors[:20])}")
    if len(hex_colors) > 8:
        lines_out.append(f"  ⚠️  {len(hex_colors)} unique hex values — potential colour inconsistency (ideal: ≤ 8 distinct hues)")
    lines_out.append("")

    # Font families
    font_families = sorted(set(re.findall(r"font-family\s*:\s*([^;}]+)", css_content, re.I)))
    lines_out.append("## Font Families")
    for ff in font_families[:10]:
        lines_out.append(f"  {ff.strip()}")
    if not font_families:
        lines_out.append("  (none found)")
    lines_out.append("")

    # Font sizes
    font_sizes = sorted(
        set(re.findall(r"font-size\s*:\s*([\d.]+(?:px|rem|em|pt|vw|vh))", css_content, re.I)),
        key=lambda s: float(re.search(r"[\d.]+", s).group()),
    )
    lines_out.append("## Font Sizes")
    lines_out.append(f"  {', '.join(font_sizes[:20]) or '(none found)'}")
    if len(font_sizes) > 8:
        lines_out.append(f"  ⚠️  {len(font_sizes)} distinct sizes — consider reducing to a modular type scale (6–8 steps)")
    lines_out.append("")

    # Font weights
    font_weights = sorted(set(re.findall(r"font-weight\s*:\s*(\d+|bold|normal|lighter|bolder)", css_content, re.I)))
    lines_out.append("## Font Weights")
    lines_out.append(f"  {', '.join(font_weights) or '(none found)'}")
    lines_out.append("")

    # Border radius
    radii = sorted(set(re.findall(r"border-radius\s*:\s*([\d.]+(?:px|rem|em|%))", css_content, re.I)))
    lines_out.append("## Border Radius Values")
    lines_out.append(f"  {', '.join(radii[:15]) or '(none found)'}")
    if len(radii) > 4:
        lines_out.append("  ⚠️  Many radius values — inconsistent rounding system likely")
    lines_out.append("")

    # Spacing — extract numeric px values from padding/margin/gap
    spacing_raw = re.findall(
        r"(?:padding|margin|gap|padding-top|padding-bottom|padding-left|padding-right"
        r"|margin-top|margin-bottom|margin-left|margin-right)\s*:\s*([^;}{]+);",
        css_content, re.I,
    )
    spacing_values: set[str] = set()
    for raw in spacing_raw:
        for val in re.findall(r"([\d.]+px)", raw):
            spacing_values.add(val)

    def _px_val(v: str) -> float:
        return float(re.search(r"[\d.]+", v).group())

    sorted_spacing = sorted(spacing_values, key=_px_val)[:30]
    lines_out.append("## Spacing Values (px) found in padding/margin/gap")
    lines_out.append(f"  {', '.join(sorted_spacing) or '(none found)'}")

    # Check grid alignment
    if sorted_spacing:
        off_grid = [v for v in sorted_spacing if _px_val(v) % 4 != 0]
        if off_grid:
            lines_out.append(f"  ⚠️  Off-grid values (not multiples of 4 px): {', '.join(off_grid[:10])}")
        else:
            lines_out.append("  ✅ All spacing values align to a 4 px grid")
    lines_out.append("")

    # z-index audit
    z_indices = sorted(set(re.findall(r"z-index\s*:\s*(\d+)", css_content)), key=int)
    if z_indices:
        lines_out.append("## Z-index Values")
        lines_out.append(f"  {', '.join(z_indices)}")
        if len(z_indices) > 6:
            lines_out.append("  ⚠️  Many z-index levels — stacking context may be fragile")
        lines_out.append("")

    return "\n".join(lines_out)


# --------------------------------------------------------------------------- #
# File-system tools (repo mode)                                               #
# --------------------------------------------------------------------------- #

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
        shown_lines = truncated.count("\n")
        return (
            f"[File: {path}  ({raw_size:,} bytes, {len(lines)} lines) — "
            f"showing first ~{shown_lines} lines]\n\n{truncated}\n\n"
            f"[... TRUNCATED — file has {len(lines)} lines total ...]"
        )
    return f"[File: {path}  ({raw_size:,} bytes, {len(lines)} lines)]\n\n{numbered}"


def _list_directory(
    path: str,
    root: str,
    recursive: bool = False,
    extensions: list[str] | None = None,
) -> str:
    target = _resolve(path, root)
    if not target.exists():
        return f"Error: directory not found — {path}"
    if not target.is_dir():
        return f"Error: not a directory — {path}"

    ext_set = (
        {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
        if extensions else None
    )
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
                entries.append(f"{prefix}... (limit of {MAX_LIST_ITEMS} items reached)")
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
                if ext_set and item.suffix.lower() not in ext_set:
                    continue
                sz = item.stat().st_size
                size_str = (
                    f"{sz} B" if sz < 1024
                    else f"{sz // 1024} KB" if sz < 1024 ** 2
                    else f"{sz // 1024 ** 2} MB"
                )
                entries.append(f"{prefix}📄 {item.name}  ({size_str})")
                count += 1

    _walk(target)
    if not entries:
        return f"Directory is empty (or all items filtered): {path}"
    header = f"[Directory: {path}{'  (recursive)' if recursive else ''}]\n"
    return header + "\n".join(entries)


def _find_design_files(root: str, analysis_root: str) -> str:
    target = _resolve(root, analysis_root)
    if not target.exists():
        return f"Error: root not found — {root}"
    if not target.is_dir():
        return f"Error: not a directory — {root}"

    results: list[str] = []
    token_files: list[str] = []
    count = 0

    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in sorted(filenames):
            if count >= MAX_LIST_ITEMS:
                results.append("... (limit reached)")
                break
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(target))
            is_design = fpath.suffix.lower() in _DESIGN_EXTENSIONS
            is_token = fname.lower() in _DESIGN_TOKEN_FILES
            if is_design or is_token:
                sz = fpath.stat().st_size
                size_str = f"{sz // 1024} KB" if sz >= 1024 else f"{sz} B"
                tag = "🎨 TOKEN" if is_token else "🖌 "
                entry = f"{tag} {rel}  ({size_str})"
                if is_token:
                    token_files.append(entry)
                else:
                    results.append(entry)
                count += 1
        if count >= MAX_LIST_ITEMS:
            break

    lines = [f"[Design files in: {root} — {count} found]\n"]
    if token_files:
        lines.append("### Design Token / Config Files (read these first!)")
        lines.extend(token_files)
        lines.append("")
    lines.append("### Component & Style Files")
    lines.extend(results or ["  (none found)"])
    return "\n".join(lines)


def _search_in_file(
    path: str,
    root: str,
    pattern: str,
    context_lines: int = 3,
) -> str:
    target = _resolve(path, root)
    if not target.exists():
        return f"Error: file not found — {path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"

    lines = text.splitlines()
    needle = pattern.lower()
    match_blocks: list[str] = []

    for i, line in enumerate(lines):
        if needle in line.lower():
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            block = []
            for j in range(start, end):
                marker = ">>>" if j == i else "   "
                block.append(f"{j + 1:5d} {marker} {lines[j]}")
            match_blocks.append("\n".join(block))
            if len(match_blocks) >= MAX_SEARCH_MATCHES:
                break

    if not match_blocks:
        return f"No matches for {pattern!r} in {path}"

    note = f" (showing first {MAX_SEARCH_MATCHES})" if len(match_blocks) >= MAX_SEARCH_MATCHES else ""
    header = f"[search: {pattern!r} in {path} — {len(match_blocks)} match(es){note}]\n"
    return header + "\n\n---\n".join(match_blocks)


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
