"""
design-analyzer  —  Website Design Analyzer CLI

Usage examples
--------------
  design-analyzer https://linear.app
  design-analyzer https://github.com/owner/repo
  design-analyzer ./my-frontend --output design-report.md
  design-analyzer https://stripe.com --output stripe-analysis.md --verbose
  design-analyzer https://example.com --model claude-sonnet-4-6 --api-key sk-ant-...
"""

import argparse
import os
import sys
from pathlib import Path

from .agent import ANTHROPIC_MODELS, DesignAnalyzerAgent

ALL_MODELS = sorted(ANTHROPIC_MODELS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="design-analyzer",
        description="Analyse any website or codebase for design quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "target",
        help=(
            "Website URL (https://...) or local directory path to analyse. "
            "GitHub URLs (https://github.com/...) analyse the live site unless --local is set."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save the Markdown report to this file.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Stream model reasoning to the terminal.",
    )
    parser.add_argument(
        "--model", "-m",
        metavar="MODEL",
        default="claude-opus-4-7",
        choices=ALL_MODELS,
        help=f"Model to use. Choices: {ALL_MODELS}. (default: claude-opus-4-7)",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API key (overrides ANTHROPIC_API_KEY env var).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    api_key = (
        (args.api_key or "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    if not api_key:
        parser.error(
            "Anthropic API key is required.\n"
            "  Set the ANTHROPIC_API_KEY environment variable, or pass --api-key KEY."
        )

    target = args.target.strip()
    is_url = target.startswith("http://") or target.startswith("https://")

    if not is_url:
        path = Path(target)
        if not path.exists():
            parser.error(f"Path does not exist: {target}")
        target = str(path)

    try:
        agent = DesignAnalyzerAgent(api_key=api_key, model=args.model)
        agent.analyze(
            target=target,
            output_report=args.output,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\n\n⚠️   Interrupted.", file=sys.stderr)
        sys.exit(130)
    except FileNotFoundError as exc:
        print(f"❌  {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"❌  Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
