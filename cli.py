"""Command-line interface: python cli.py owner/repo [--pr 42] [--out reports/]"""
import argparse
import logging
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

from agents.orchestrator import run_review
from config import get_settings
from rag.vector_store import IndexRegistry

console = Console()

SECTIONS = (("review", "Code Review"), ("security", "Security Report"), ("fixes", "Code Fixes"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bugeye", description="Multi-agent AI code review for GitHub repositories")
    parser.add_argument("repo", help="'owner/repo' or a github.com URL")
    parser.add_argument("--pr", type=int, help="review this pull request's changes")
    parser.add_argument("--out", type=Path, help="also write the reports as Markdown files into this directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.out and args.out.exists() and not args.out.is_dir():
        # Checked up front so a long review isn't lost to a crash at the very end.
        console.print(f"[bold red]Error:[/bold red] --out must be a directory, but {args.out} is a file.")
        return 2
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()

    console.print(Panel.fit("[bold blue]BugEye[/bold blue]\n[dim]Multi-agent AI code review[/dim]", border_style="blue"))
    result = None
    for event in run_review(args.repo, args.pr, settings=settings, registry=IndexRegistry(1)):
        kind = event["event"]
        if kind == "agent_start":
            console.print(f"\n[bold]{event['agent']}[/bold] {event['message']}")
        elif kind == "agent_step":
            console.print(f"  [dim]- {event['message']}[/dim]")
        elif kind == "agent_done":
            console.print(f"  [green]done:[/green] {event['message']}")
        elif kind == "agent_failed":
            console.print(f"  [red]failed:[/red] {event['message']}")
        elif kind in ("error", "rag_failed", "quota_exhausted"):
            console.print(f"\n[bold red]Error:[/bold red] {event['message']}")
            return 1
        elif kind == "complete":
            result = event

    if result is None:
        return 1
    for key, title in SECTIONS:
        console.print(Rule(f"[bold]{title}[/bold]"))
        console.print(Markdown(result[key]))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for key, _ in SECTIONS:
            (args.out / f"{key}.md").write_text(result[key], encoding="utf-8")
        console.print(f"\n[dim]Reports written to {args.out}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
