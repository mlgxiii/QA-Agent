"""
qa-agent  —  Production Scalability QA Agent CLI

Usage examples
--------------
  qa-agent /path/to/project
  qa-agent ./backend --output scalability-report.md
  qa-agent /srv/app   --output /reports/app.md --verbose
  qa-agent .          --api-key sk-ant-...
"""

import argparse
import os
import sys
from pathlib import Path

from .agent import ScalabilityQAAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-agent",
        description="Analyse a codebase for production scalability issues using Claude.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        help="Path to the codebase directory (or a single file) to analyse.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save the Markdown report to this file path.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Stream the model's reasoning / thinking output to the terminal.",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API key (overrides the ANTHROPIC_API_KEY environment variable).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        parser.error(
            "Anthropic API key is required.\n"
            "  Set the ANTHROPIC_API_KEY environment variable, or pass --api-key KEY."
        )

    target = Path(args.path)
    if not target.exists():
        parser.error(f"Path does not exist: {target}")

    try:
        agent = ScalabilityQAAgent(api_key=api_key)
        agent.analyze(
            target_path=str(target),
            output_report=args.output,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\n\n⚠️   Interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except FileNotFoundError as exc:
        print(f"❌  {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"❌  Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
