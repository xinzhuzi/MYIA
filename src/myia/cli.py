"""Command-line entry point (stdlib only; tests import this without deps)."""

from __future__ import annotations

import argparse


def _stub(command: str) -> int:
    print(f"{command} is not implemented in the v0.1 skeleton yet")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myia",
        description="MYIA — AI-native intelligence hub (pre-alpha skeleton).",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run a category pipeline from a plugin YAML")
    run.add_argument("yaml", help="Path to the category YAML file")

    sub.add_parser("list", help="List available plugins")
    sub.add_parser("test", help="Dry-run a plugin's sources and report health")

    add_source = sub.add_parser("add-source", help="Add a source to a plugin YAML")
    add_source.add_argument("yaml", help="Path to the category YAML file")

    sub.add_parser("init", help="Wizard that emits a structured prompt for agents to generate a category YAML")
    sub.add_parser("doctor", help="Print structured diagnostics for agent self-repair")
    sub.add_parser("dashboard", help="Terminal dashboard of category and source health")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return _stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
