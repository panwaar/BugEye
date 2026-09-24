"""Report writer: turns verified findings into the Markdown shown in the three result tabs.

No LLM is involved here, so the reports can only contain findings that survived verification,
and every code block labelled "current code" is copied from the real file.
"""
import os
from dataclasses import dataclass, field

from agents.findings import Finding, code_at

_SECTIONS = (
    ("Critical and high", ("critical", "high")),
    ("Medium", ("medium",)),
    ("Low", ("low",)),
)
_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".kt": "kotlin", ".swift": "swift", ".html": "html",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".sql": "sql", ".sh": "bash",
}


@dataclass
class ReviewStats:
    files_reviewed: int
    lines_reviewed: int
    skipped_files: list[str] = field(default_factory=list)  # Over the size limit
    failed_files: list[str] = field(default_factory=list)  # Their review request failed
    review_error: str = ""  # Why those files failed
    unsupported: int = 0  # Quoted code that doesn't exist in the repository
    rejected: int = 0  # Rejected by the verifier
    unverified: int = 0  # The verifier couldn't run for these, so they are not shown
    verify_error: str = ""  # Why the verifier couldn't run

    @property
    def complete(self) -> bool:
        """Everything was reviewed and every finding was double-checked (safe to cache)."""
        return not self.failed_files and not self.unverified


def render_reports(findings: list[Finding], files: dict[str, str], stats: ReviewStats) -> dict[str, str]:
    findings = sorted(findings, key=lambda f: (f.rank, f.file, f.start_line))
    security = [f for f in findings if f.category == "security"]
    quality = [f for f in findings if f.category != "security"]
    summary = _summary(stats)
    return {
        "review": summary + _findings_section(quality, files, "No verified bugs or quality issues found."),
        "security": summary + _findings_section(security, files, "No verified security issues found."),
        "fixes": _fixes_section(findings, files),
    }


def _summary(stats: ReviewStats) -> str:
    lines = [f"**Reviewed {stats.files_reviewed} files ({stats.lines_reviewed:,} lines) in full.**"]
    discarded = stats.unsupported + stats.rejected
    if discarded:
        lines.append(f"{discarded} candidate findings were discarded because the code did not support them.")
    if stats.unverified:
        lines.append(f"{stats.unverified} findings could not be double-checked and are not shown "
                     f"({_sentence(stats.verify_error)}).")
    if stats.skipped_files:
        lines.append(f"Not reviewed (over the size limit): {_list_paths(stats.skipped_files)}. "
                     "Raise MAX_REVIEW_CHARS to include them.")
    if stats.failed_files:
        lines.append(f"Not reviewed ({_sentence(stats.review_error)}): {_list_paths(stats.failed_files)}.")
    return "\n\n".join(lines) + "\n\n"


def _findings_section(findings: list[Finding], files: dict[str, str], empty_message: str) -> str:
    if not findings:
        return empty_message + "\n"
    out = []
    for heading, severities in _SECTIONS:
        group = [f for f in findings if f.severity in severities]
        if not group:
            continue
        out.append(f"## {heading}")
        for finding in group:
            out.append(f"### {finding.title}")
            out.append(f"{_location(finding)} · {finding.severity} · {finding.category}")
            if finding.explanation:
                out.append(finding.explanation)
            out.append(_code_block(finding.file, code_at(files, finding)))
    return "\n\n".join(out) + "\n"


def _fixes_section(findings: list[Finding], files: dict[str, str]) -> str:
    fixable = [f for f in findings if f.suggested_fix]
    if not fixable:
        return "No code fixes to suggest.\n"
    out = []
    for finding in fixable:
        out += [
            "---",
            f"### Fix for: {finding.title}",
            f"**File:** {_location(finding)}",
            "**Current code:**",
            _code_block(finding.file, code_at(files, finding)),
            "**Suggested fix:**",
            _code_block(finding.file, finding.suggested_fix),
            f"**Why:** {finding.explanation}" if finding.explanation else "",
        ]
    return "\n\n".join(part for part in out if part) + "\n"


def _location(finding: Finding) -> str:
    lines = (f"line {finding.start_line}" if finding.start_line == finding.end_line
             else f"lines {finding.start_line}-{finding.end_line}")
    return f"`{finding.file}` {lines}"


def _sentence(message: str) -> str:
    """An error message shaped to sit inside parentheses mid-sentence."""
    return message.rstrip(". ") or "unknown error"


def _code_block(path: str, code: str) -> str:
    language = _LANGUAGES.get(os.path.splitext(path)[1].lower(), "")
    fence = "````" if "```" in code else "```"
    return f"{fence}{language}\n{code}\n{fence}"


def _list_paths(paths: list[str], limit: int = 8) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")
