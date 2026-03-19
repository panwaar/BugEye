import argparse
import sys
from rich.console import Console
from rich.panel import Panel

from agent import review_pr

console = Console()


def parse_args():
    parser = argparse.ArgumentParser(
        description="AI Code Review Agent — reviews GitHub PRs using Groq + LangChain"
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub repo in format 'owner/repo' e.g. 'facebook/react'"
    )
    parser.add_argument(
        "--pr",
        required=True,
        type=int,
        help="PR number to review e.g. 42"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    console.print(Panel.fit(
        "[bold blue]AI Code Review Agent[/bold blue]\n[dim]Powered by Groq + LangChain[/dim]",
        border_style="blue"
    ))

    console.print(f"\n[bold]Repository:[/bold] {args.repo}")
    console.print(f"[bold]PR Number:[/bold] #{args.pr}\n")
    console.print("[yellow]Running agent... this may take 30-60 seconds[/yellow]\n")

    try:
        result = review_pr(args.repo, args.pr)
        console.print(Panel(
            f"[green]{result}[/green]",
            title="[bold green]Agent Complete[/bold green]",
            border_style="green"
        ))
        console.print("\n[dim]Check your PR on GitHub to see the posted review![/dim]\n")

    except ValueError as e:
        console.print(f"\n[bold red]Config error:[/bold red] {e}")
        console.print("[dim]Check your .env file has GROQ_API_KEY and GITHUB_TOKEN set.[/dim]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()