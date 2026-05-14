"""
saas-agent  —  SaaS Framework Discovery Agent CLI

Usage examples
--------------
  python -m qa_agent.saas_cli --categories "AI & LLM Tools" "Developer Tools"
  python -m qa_agent.saas_cli --custom-query "topic:crm stars:>500" --output report.md
  python -m qa_agent.saas_cli --categories "Productivity & No-Code" --verbose
"""

import argparse
import os
import sys

from .saas_agent import ANTHROPIC_MODELS, PRESET_CATEGORIES, SaaSFrameworkAgent


def build_parser() -> argparse.ArgumentParser:
    category_list = "\n  ".join(f"• {k}" for k in PRESET_CATEGORIES)

    parser = argparse.ArgumentParser(
        prog="saas-agent",
        description="Discover open-source GitHub frameworks for SaaS packaging on Whop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Available preset categories:\n  {category_list}\n\n"
            "Examples:\n"
            "  python -m qa_agent.saas_cli --categories 'AI & LLM Tools' 'Developer Tools'\n"
            "  python -m qa_agent.saas_cli --custom-query 'topic:crm stars:>500 language:python'\n"
        ),
    )
    parser.add_argument(
        "--categories", "-c",
        nargs="+",
        metavar="CATEGORY",
        default=[],
        help=(
            "One or more preset category names to search. "
            f"Available: {', '.join(PRESET_CATEGORIES)}."
        ),
    )
    parser.add_argument(
        "--custom-query", "-q",
        metavar="QUERY",
        default="",
        help="Additional free-form GitHub search query (e.g. 'topic:crm stars:>500').",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        help="Save the Markdown report to this file path.",
    )
    parser.add_argument(
        "--model", "-m",
        metavar="MODEL",
        default="claude-opus-4-7",
        help=f"Anthropic model to use. Options: {sorted(ANTHROPIC_MODELS)}. (default: claude-opus-4-7)",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API key (overrides ANTHROPIC_API_KEY env var).",
    )
    parser.add_argument(
        "--gh-token",
        metavar="TOKEN",
        help="GitHub personal access token (overrides GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Stream the model's extended thinking output.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.categories and not args.custom_query:
        parser.error(
            "Provide at least one --categories name or a --custom-query string.\n"
            f"Preset categories: {', '.join(PRESET_CATEGORIES)}"
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

    gh_token = (args.gh_token or "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()

    if args.model not in ANTHROPIC_MODELS:
        parser.error(
            f"Unknown model '{args.model}'. Choose from: {', '.join(sorted(ANTHROPIC_MODELS))}"
        )

    try:
        agent = SaaSFrameworkAgent(
            api_key=anthropic_key,
            model=args.model,
            gh_token=gh_token,
        )
        agent.discover(
            categories=args.categories,
            custom_query=args.custom_query,
            output_report=args.output,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\n\n⚠️   Interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"❌  Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
