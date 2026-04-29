import os
from pathlib import Path
from typing import Any

MAX_FILE_SIZE_BYTES = 30 * 1024    # 30 KB per read before truncation
MAX_LIST_ITEMS = 300               # max items returned by list_directory / find_files
MAX_SEARCH_MATCHES = 25            # max matches returned by search_in_file

# Directories that should be skipped during analysis
_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    "venv", ".venv", "env", ".env",
    "dist", "build", ".build", "target",
    ".mypy_cache", ".pytest_cache", ".tox",
    "coverage", ".coverage",
    "vendor",  # Go / PHP vendoring
}

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "read_file",
        "description": (
            "Read the source code of a file. Returns the file contents with line numbers. "
            "Each read is capped at 30 KB — for large files use start_line/end_line to read "
            "specific sections. Use search_in_file to locate relevant line numbers first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file (relative to the analysis root or absolute).",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based line number to start reading from (default: 1).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-based line number to stop reading at, inclusive (default: end of file).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List the files and subdirectories inside a directory. "
            "Hidden directories, build artefacts, and vendored dependencies are automatically skipped. "
            "Use recursive=true to get a full tree."
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
                    "description": (
                        "Filter by file extensions, e.g. [\".py\", \".js\"]. "
                        "Omit to include all files."
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_files",
        "description": (
            "Recursively find all files matching given extensions or a glob name pattern. "
            "Useful for quickly locating all Python files, config files, test files, etc. "
            "across the entire codebase without iterating directory by directory."
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
                    "description": "File extensions to match, e.g. [\".py\", \".go\"].",
                },
                "name_pattern": {
                    "type": "string",
                    "description": (
                        "Optional glob pattern for file names, e.g. \"*.config.js\", "
                        "\"settings*\", \"Dockerfile*\"."
                    ),
                },
            },
            "required": ["root"],
        },
    },
    {
        "name": "search_in_file",
        "description": (
            "Case-insensitive search for a text pattern inside a file. "
            "Returns each matching line with surrounding context. "
            "Use this to hunt for specific anti-patterns such as bare `requests.get(`, "
            "`for ... in session.query`, `time.sleep(`, global variable mutations, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File to search.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive substring match).",
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
            "Save the final scalability analysis report as a Markdown file. "
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

def execute_tool(name: str, inputs: dict[str, Any], root_path: str) -> str:
    """Dispatch a tool call and return the result as a string."""
    try:
        match name:
            case "read_file":
                return _read_file(
                    inputs["path"],
                    root_path,
                    start_line=inputs.get("start_line"),
                    end_line=inputs.get("end_line"),
                )
            case "list_directory":
                return _list_directory(
                    inputs["path"],
                    root_path,
                    recursive=inputs.get("recursive", False),
                    extensions=inputs.get("extensions"),
                )
            case "find_files":
                return _find_files(
                    inputs["root"],
                    root_path,
                    extensions=inputs.get("extensions"),
                    name_pattern=inputs.get("name_pattern"),
                )
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
    except Exception as exc:
        return f"Error executing '{name}': {exc}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _resolve(path: str, root: str) -> Path:
    """Return an absolute Path; relative paths are resolved against root."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    return p.resolve()


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _read_file(path: str, root: str, start_line: int | None = None, end_line: int | None = None) -> str:
    target = _resolve(path, root)

    if not target.exists():
        return f"Error: file not found — {path}"
    if not target.is_file():
        return f"Error: path is a directory, not a file — {path}"

    raw_size = target.stat().st_size
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading file: {exc}"

    all_lines = text.splitlines()
    total_lines = len(all_lines)

    # Apply line range selection
    lo = max(1, start_line or 1)
    hi = min(total_lines, end_line or total_lines)
    lines = all_lines[lo - 1 : hi]

    numbered = "\n".join(f"{lo + i:5d} | {line}" for i, line in enumerate(lines))

    range_note = ""
    if start_line or end_line:
        range_note = f"  lines {lo}–{hi} of {total_lines}"

    if len(numbered.encode()) > MAX_FILE_SIZE_BYTES:
        truncated = numbered.encode()[:MAX_FILE_SIZE_BYTES].decode(errors="replace")
        shown = truncated.count("\n") + 1
        return (
            f"[File: {path}  ({raw_size:,} bytes, {total_lines} lines){range_note} — "
            f"showing first ~{shown} lines of selection]\n\n"
            f"{truncated}\n\n"
            f"[... TRUNCATED — use start_line/end_line to read other sections ...]"
        )

    return f"[File: {path}  ({raw_size:,} bytes, {total_lines} lines){range_note}]\n\n{numbered}"


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
        return f"Error: path is not a directory — {path}"

    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions} if extensions else None

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
                    else f"{sz // (1024 ** 2)} MB"
                )
                entries.append(f"{prefix}📄 {item.name}  ({size_str})")
                count += 1

    _walk(target)

    if not entries:
        return f"Directory is empty (or all items filtered): {path}"

    header = f"[Directory: {path}{'  (recursive)' if recursive else ''}]\n"
    return header + "\n".join(entries)


def _find_files(
    root: str,
    analysis_root: str,
    extensions: list[str] | None = None,
    name_pattern: str | None = None,
) -> str:
    target = _resolve(root, analysis_root)

    if not target.exists():
        return f"Error: root not found — {root}"
    if not target.is_dir():
        return f"Error: not a directory — {root}"

    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions} if extensions else None

    results: list[str] = []
    count = 0

    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        for fname in sorted(filenames):
            if count >= MAX_LIST_ITEMS:
                results.append(f"... (limit of {MAX_LIST_ITEMS} results reached)")
                break

            fpath = Path(dirpath) / fname

            if ext_set and fpath.suffix.lower() not in ext_set:
                continue
            if name_pattern and not fpath.match(name_pattern):
                continue

            rel = fpath.relative_to(target)
            sz = fpath.stat().st_size
            size_str = (
                f"{sz} B" if sz < 1024
                else f"{sz // 1024} KB" if sz < 1024 ** 2
                else f"{sz // (1024 ** 2)} MB"
            )
            results.append(f"{rel}  ({size_str})")
            count += 1

        if count >= MAX_LIST_ITEMS:
            break

    if not results:
        return f"No files found matching the given criteria under: {root}"

    filters = []
    if ext_set:
        filters.append(f"extensions={sorted(ext_set)}")
    if name_pattern:
        filters.append(f"pattern={name_pattern!r}")
    filter_str = f"  [{', '.join(filters)}]" if filters else ""
    header = f"[find_files: {root}{filter_str} — {count} result(s)]\n"
    return header + "\n".join(results)


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
            block_lines = []
            for j in range(start, end):
                marker = ">>>" if j == i else "   "
                block_lines.append(f"{j + 1:5d} {marker} {lines[j]}")
            match_blocks.append("\n".join(block_lines))

            if len(match_blocks) >= MAX_SEARCH_MATCHES:
                break

    if not match_blocks:
        return f"No matches for {pattern!r} in {path}"

    total_note = (
        f" (showing first {MAX_SEARCH_MATCHES})"
        if len(match_blocks) >= MAX_SEARCH_MATCHES
        else ""
    )
    header = f"[search: {pattern!r} in {path} — {len(match_blocks)} match(es){total_note}]\n"
    return header + "\n\n---\n".join(match_blocks)


def _write_report(content: str, output_path: str) -> str:
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return f"Report saved → {dest.resolve()}  ({len(content):,} chars)"
    except Exception as exc:
        return f"Error writing report: {exc}"
