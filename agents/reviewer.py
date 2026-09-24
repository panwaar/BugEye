"""Reviewer agent: plans batches that cover every reviewable file, then reviews each batch."""
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.documents import Document

from agents import prompts
from agents.findings import Finding, parse_findings
from exceptions import BugEyeError
from rag.code_loader import extract_symbols
from services.github_service import PullRequest

# Files that describe the project rather than run it; they are indexed for chat but not reviewed.
_NOT_REVIEWED_EXTENSIONS = frozenset({".md", ".txt", ".css"})
_CONFIG_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml", ".sh", ".sql"})
_MAX_SYMBOL_MAP_CHARS = 3000


@dataclass(frozen=True)
class FileSlice:
    """A whole file, or a line range of a file too large for one batch."""

    path: str
    start_line: int
    end_line: int
    total_lines: int
    text: str

    @property
    def header(self) -> str:
        if self.start_line == 1 and self.end_line == self.total_lines:
            return f"`{self.path}` (complete file, {self.total_lines} lines)"
        return f"`{self.path}` (lines {self.start_line}-{self.end_line} of {self.total_lines})"


@dataclass
class ReviewBatch:
    slices: list[FileSlice] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(s.text) for s in self.slices)

    @property
    def paths(self) -> list[str]:
        return list(dict.fromkeys(s.path for s in self.slices))

    def describe(self) -> str:
        paths = self.paths
        shown = ", ".join(paths[:3])
        return shown + (f" +{len(paths) - 3} more" if len(paths) > 3 else "")


@dataclass
class ReviewPlan:
    repo_name: str
    files: dict[str, str]  # Every indexed file (reviewed or not), for locating evidence
    symbols: dict[str, list[str]]  # Names each file defines
    batches: list[ReviewBatch]
    reviewed_files: list[str]
    skipped_files: list[str]  # Reviewable, but over the MAX_REVIEW_CHARS limit
    pr: PullRequest | None = None

    @property
    def reviewed_lines(self) -> int:
        return sum(self.files[path].count("\n") + 1 for path in self.reviewed_files)


def is_reviewable(path: str) -> bool:
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    if extension == ".txt":
        return name.lower().startswith("requirements")  # Dependency pins matter for security
    return extension not in _NOT_REVIEWED_EXTENSIONS


def plan_review(repo_name: str, documents: list[Document], *, batch_chars: int, max_total_chars: int,
                pr: PullRequest | None = None) -> ReviewPlan:
    """Split every reviewable file (or, for a PR, every changed file) into batches of whole files."""
    files = {doc.metadata["source"]: doc.page_content for doc in documents}
    symbols = {path: names for path, text in files.items() if (names := extract_symbols(path, text))}

    if pr:
        candidates = [path for path in pr.changed_files if path in files and is_reviewable(path)]
        if not candidates:
            raise BugEyeError(f"None of the files changed in PR #{pr.number} can be reviewed.")
    else:
        candidates = [path for path in files if is_reviewable(path)]
        if not candidates:
            raise BugEyeError("The repository has no source files to review.")
    candidates.sort(key=lambda path: (_priority(path), path))

    batches: list[ReviewBatch] = [ReviewBatch()]
    reviewed, skipped, total = [], [], 0
    for path in candidates:
        text = files[path]
        if total + len(text) > max_total_chars:
            skipped.append(path)
            continue
        total += len(text)
        reviewed.append(path)
        for piece in _slice_file(path, text, batch_chars):
            if batches[-1].slices and batches[-1].chars + len(piece.text) > batch_chars:
                batches.append(ReviewBatch())
            batches[-1].slices.append(piece)
    if not reviewed:
        raise BugEyeError("Every source file is larger than MAX_REVIEW_CHARS allows.")

    return ReviewPlan(repo_name=repo_name, files=files, symbols=symbols, batches=batches,
                      reviewed_files=reviewed, skipped_files=skipped, pr=pr)


def render_batch(plan: ReviewPlan, batch: ReviewBatch) -> str:
    """The user message for one review request."""
    parts = [f"# Repository: {plan.repo_name}"]
    symbol_map = format_symbol_map(plan.symbols, exclude=set(batch.paths))
    if symbol_map:
        parts.append("## Other files in the repository and the names they define\n"
                     "(These exist even though their code is not shown here.)\n" + symbol_map)
    if plan.pr:
        diffs = [f"#### `{path}`\n```diff\n{plan.pr.patches[path]}\n```"
                 for path in batch.paths if path in plan.pr.patches]
        parts.append(f"## Pull request under review\n{plan.pr.summary}\n\n"
                     "Focus on problems introduced or touched by these changes:\n" + "\n\n".join(diffs))
    files = "\n\n".join(f"### {piece.header}\n```\n{piece.text}\n```" for piece in batch.slices)
    parts.append(f"## Files to review\n{files}")
    return "\n\n".join(parts)


def review_batch(content: str, ask_json: Callable[[str, str], dict]) -> list[Finding]:
    return parse_findings(ask_json(prompts.REVIEW_PROMPT, content))


def format_symbol_map(symbols: dict[str, list[str]], exclude: set[str] = frozenset(),
                      max_chars: int = _MAX_SYMBOL_MAP_CHARS) -> str:
    lines, used = [], 0
    entries = [(path, names) for path, names in symbols.items() if path not in exclude]
    for i, (path, names) in enumerate(entries):
        line = f"- {path}: {', '.join(names)}"
        if used + len(line) > max_chars:
            lines.append(f"- ... and {len(entries) - i} more files")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _priority(path: str) -> int:
    """Review application code first, then configuration, then tests (if the size limit cuts in)."""
    name = os.path.basename(path).lower()
    if "test" in path.lower().split("/")[0] or name.startswith("test_") or ".test." in name or ".spec." in name:
        return 2
    extension = os.path.splitext(name)[1]
    if extension in _CONFIG_EXTENSIONS or name in ("dockerfile", "makefile") or name.startswith(("requirements", ".env")):
        return 1
    return 0


def _slice_file(path: str, text: str, max_chars: int) -> list[FileSlice]:
    lines = text.splitlines()
    total = len(lines)
    if len(text) <= max_chars:
        return [FileSlice(path, 1, total, total, text)]
    slices, start, size = [], 0, 0
    for i, line in enumerate(lines):
        if size and size + len(line) + 1 > max_chars:
            slices.append(FileSlice(path, start + 1, i, total, "\n".join(lines[start:i])))
            start, size = i, 0
        size += len(line) + 1
    slices.append(FileSlice(path, start + 1, total, total, "\n".join(lines[start:])))
    return slices
