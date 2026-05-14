"""
saas-discovery -- SaaS Framework Discovery Agent CLI

Usage:
  python -m saas_agent.cli --categories "AI & LLM Tools" "Developer Tools"
  python -m saas_agent.cli --custom-query "topic:crm stars:>500" --output report.md
  python -m saas_agent.cli --list-categories
"""

import argparse
import os
import sys

from .agent import ANTHROPIC_MODELS, PRESET_CATEGORIES, SaaSDiscoveryAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="saas-discovery",
        description="Discover open-source GitHub frameworks for SaaS packaging on Whop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--categories", "-c", nargs="+", metavar="CATEGORY", default=[])
    parser.add_argument("--custom-query", "-q", metavar="QUERY", default="")
    parser.add_argument("--output", "-o", metavar="FILE")
    parser.add_argument("--model", "-m", metavar="MODEL", default="claude-opus-4-7")
    parser.add_argument("--api-key", metavar="KEY")
    parser.add_argument("--gh-token", metavar="TOKEN")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--list-categories", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_categories:
        for name, query in PRESET_CATEGORIES.items():
            print(f"  {name}")
            print(f"    query: {query}")
        return

    if not args.categories and not args.custom_query:
        parser.error("Provide --categories or --custom-query. Use --list-categories to see options.")

    anthropic_key = (args.api_key or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        parser.error("Anthropic API key required. Set ANTHROPIC_API_KEY or pass --api-key KEY.")

    gh_token = (args.gh_token or "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()

    if args.model not in ANTHROPIC_MODELS:
        parser.error(f"Unknown model '{args.model}'. Choose from: {sorted(ANTHROPIC_MODELS)}")

    try:
        agent = SaaSDiscoveryAgent(api_key=anthropic_key, model=args.model, gh_token=gh_token)
        agent.discover(
            categories=args.categories,
            custom_query=args.custom_query,
            output_report=args.output,
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
