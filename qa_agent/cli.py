"""
qa-agent  —  Production Scalability QA Agent CLI

Usage examples
--------------
  qa-agent /path/to/project
  qa-agent ./backend --output scalability-report.md
  qa-agent /srv/app   --output /reports/app.md --verbose
  qa-agent .          --api-key sk-ant-...
  qa-agent .          --model gpt-4o --openai-api-key sk-...
"""

import argparse
import os
import sys
from pathlib import Path

from .agent import ANTHROPIC_MODELS, ScalabilityQAAgent
from .agent_openai import OPENAI_MODELS, OpenAIQAAgent

ALL_MODELS = sorted(ANTHROPIC_MODELS | OPENAI_MODELS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-agent",
        description="Analyse a codebase for production scalability issues.",
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
        "--model", "-m",
        metavar="MODEL",
        default="claude-opus-4-7",
        help=(
            f"Model to use. Anthropic: {sorted(ANTHROPIC_MODELS)}. "
            f"OpenAI: {sorted(OPENAI_MODELS)}. "
            f"(default: claude-opus-4-7)"
        ),
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API key (overrides the ANTHROPIC_API_KEY environment variable).",
    )
    parser.add_argument(
        "--openai-api-key",
        metavar="KEY",
        help="OpenAI API key (overrides the OPENAI_API_KEY environment variable).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    model = args.model

    target = Path(args.path)
    if not target.exists():
        parser.error(f"Path does not exist: {target}")

    try:
        if model in OPENAI_MODELS:
            openai_key = (
                (args.openai_api_key or "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
            )
            if not openai_key:
                parser.error(
                    f"OpenAI API key is required for model '{model}'.\n"
                    "  Set the OPENAI_API_KEY environment variable, or pass --openai-api-key KEY."
                )
            agent = OpenAIQAAgent(api_key=openai_key, model=model)
        else:
            if model not in ANTHROPIC_MODELS:
                parser.error(
                    f"Unknown model '{model}'. Choose from: {', '.join(ALL_MODELS)}"
                )
            anthropic_key = (
                (args.api_key or "").strip()
                or os.environ.get("ANTHROPIC_API_KEY", "").strip()
            )
            if not anthropic_key:
                parser.error(
                    "Anthropic API key is required.\n"
                    "  Set the ANTHROPIC_API_KEY environment variable, or pass --api-key KEY."
                )
            agent = ScalabilityQAAgent(api_key=anthropic_key, model=model)

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
