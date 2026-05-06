"""CLI entry point for the Website Design Analyzer."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="design-agent",
        description="Analyse the design of any website or repo using Claude.",
    )
    parser.add_argument(
        "target",
        help="Live website URL (https://…) or local repo path.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="PATH",
        help="Write the Markdown report to this file.",
    )
    parser.add_argument(
        "--model", "-m",
        default="claude-opus-4-7",
        help="Anthropic model to use (default: claude-opus-4-7).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Stream the model's extended thinking output.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("❌  ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    from .agent import DesignAnalyzerAgent

    agent = DesignAnalyzerAgent(api_key=api_key, model=args.model)
    try:
        agent.analyze(
            target=args.target,
            output_report=args.output,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"❌  {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
