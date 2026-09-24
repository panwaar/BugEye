"""Verifier agent: a skeptical second pass that checks each finding against the real code."""
from collections.abc import Callable

from agents import prompts
from agents.findings import Finding
from agents.reviewer import ReviewPlan, format_symbol_map

_CONTEXT_LINES = 40  # Lines shown around a finding when its file is too large to include whole


def plan_verification(findings: list[Finding], plan: ReviewPlan, batch_chars: int) -> list[list[Finding]]:
    """Group findings so that each verification request stays within the batch size."""
    by_file: dict[str, list[Finding]] = {}
    for finding in findings:
        by_file.setdefault(finding.file, []).append(finding)

    groups: list[list[Finding]] = []
    size = 0
    for path, file_findings in by_file.items():
        needed = len(_code_for(path, file_findings, plan.files[path], batch_chars))
        if groups and size + needed <= batch_chars:
            groups[-1].extend(file_findings)
            size += needed
        else:
            groups.append(list(file_findings))
            size = needed
    return groups


def render_verification(group: list[Finding], plan: ReviewPlan, batch_chars: int) -> str:
    parts = []
    symbol_map = format_symbol_map(plan.symbols)
    if symbol_map:
        parts.append("## Names defined in the repository\n" + symbol_map)

    code_blocks, listed = [], []
    for finding in group:
        if finding.file not in listed:
            listed.append(finding.file)
            same_file = [f for f in group if f.file == finding.file]
            code_blocks.append(_code_for(finding.file, same_file, plan.files[finding.file], batch_chars))
    parts.append("## Code\n" + "\n\n".join(code_blocks))

    claims = [
        f"[{i}] {f.severity} {f.category}: {f.title}\n"
        f"    Location: `{f.file}` lines {f.start_line}-{f.end_line}\n"
        f"    Claim: {f.explanation}"
        for i, f in enumerate(group, start=1)
    ]
    parts.append("## Findings to check\n" + "\n\n".join(claims))
    return "\n\n".join(parts)


def apply_verdicts(group: list[Finding], data: dict) -> list[Finding]:
    """Keep only the findings the verifier explicitly confirmed."""
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    confirmed: set[int] = set()
    for verdict in verdicts if isinstance(verdicts, list) else []:
        if not isinstance(verdict, dict) or verdict.get("valid") is not True:
            continue
        try:
            confirmed.add(int(verdict.get("id")))
        except (TypeError, ValueError):
            continue
    kept = []
    for i, finding in enumerate(group, start=1):
        if i in confirmed:
            finding.double_checked = True
            kept.append(finding)
    return kept


def verify_group(group: list[Finding], plan: ReviewPlan, batch_chars: int,
                 ask_json: Callable[[str, str], dict]) -> list[Finding]:
    content = render_verification(group, plan, batch_chars)
    return apply_verdicts(group, ask_json(prompts.VERIFY_PROMPT, content))


def _code_for(path: str, findings: list[Finding], text: str, batch_chars: int) -> str:
    """The whole file if it fits comfortably, otherwise windows of lines around each finding."""
    lines = text.splitlines()
    if len(text) <= batch_chars // 2:
        return f"### `{path}` (complete file)\n```\n{text}\n```"

    ranges: list[list[int]] = []
    for finding in sorted(findings, key=lambda f: f.start_line):
        start = max(1, finding.start_line - _CONTEXT_LINES)
        end = min(len(lines), finding.end_line + _CONTEXT_LINES)
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    return "\n\n".join(
        f"### `{path}` (lines {start}-{end} of {len(lines)})\n```\n" + "\n".join(lines[start - 1:end]) + "\n```"
        for start, end in ranges
    )
