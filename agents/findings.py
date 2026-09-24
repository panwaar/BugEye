"""Review findings: parsing the model's JSON, anchoring each one to real code, removing duplicates."""
from dataclasses import dataclass

SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = ("security", "bug", "performance", "maintainability")
_MIN_EVIDENCE_CHARS = 6


@dataclass
class Finding:
    file: str
    severity: str
    category: str
    title: str
    explanation: str
    evidence: str  # Code the model quoted; only used to locate the finding in the real file
    suggested_fix: str = ""
    # Filled in by locate_findings() from the real file, never taken from the model:
    start_line: int = 0
    end_line: int = 0

    @property
    def rank(self) -> int:
        return SEVERITIES.index(self.severity)


def parse_findings(data: dict) -> list[Finding]:
    """Turn the model's {"findings": [...]} reply into Findings, skipping malformed entries."""
    items = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    findings = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fields = {key: str(item.get(key) or "").strip() for key in
                  ("file", "severity", "category", "title", "explanation", "evidence", "suggested_fix")}
        if not (fields["file"] and fields["title"] and fields["evidence"]):
            continue
        severity = fields["severity"].lower()
        category = fields["category"].lower()
        findings.append(Finding(
            file=fields["file"].strip("`"),
            severity=severity if severity in SEVERITIES else "medium",
            category=category if category in CATEGORIES else "maintainability",
            title=fields["title"],
            explanation=fields["explanation"],
            evidence=_strip_fences(fields["evidence"]),
            suggested_fix=_strip_fences(fields["suggested_fix"]),
        ))
    return findings


def locate_evidence(evidence: str, text: str) -> tuple[int, int] | None:
    """Find the quoted code in a file, ignoring whitespace differences.

    Returns the 1-based (start_line, end_line) of the match, or None if the file doesn't contain it.
    """
    wanted = [_normalize(line) for line in evidence.splitlines() if _normalize(line)]
    if not wanted or len("".join(wanted)) < _MIN_EVIDENCE_CHARS:
        return None
    lines = [(number, _normalize(line)) for number, line in enumerate(text.splitlines(), start=1)]
    lines = [(number, line) for number, line in lines if line]
    for i in range(len(lines) - len(wanted) + 1):
        window = [line for _, line in lines[i:i + len(wanted)]]
        if len(wanted) == 1:
            matched = wanted[0] in window[0]
        else:
            # The first/last quoted lines may be partial; the ones in between must match exactly.
            matched = wanted[0] in window[0] and wanted[-1] in window[-1] and window[1:-1] == wanted[1:-1]
        if matched:
            return lines[i][0], lines[i + len(wanted) - 1][0]
    return None


def locate_findings(findings: list[Finding], files: dict[str, str]) -> tuple[list[Finding], int]:
    """Anchor every finding to the real file and lines its evidence comes from.

    Findings whose quoted code isn't in the repository are dropped (the model made it up).
    Returns (located findings, number dropped).
    """
    located = []
    for finding in findings:
        candidates = [finding.file] if finding.file in files else []
        candidates += [path for path in files if path != finding.file]  # the model may cite the wrong file
        for path in candidates:
            span = locate_evidence(finding.evidence, files[path])
            if span:
                finding.file = path
                finding.start_line, finding.end_line = span
                located.append(finding)
                break
    return located, len(findings) - len(located)


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Keep the most severe finding when several point at overlapping lines of the same file."""
    kept: list[Finding] = []
    for finding in sorted(findings, key=lambda f: f.rank):
        if not any(other.file == finding.file and other.category == finding.category
                   and other.start_line <= finding.end_line and finding.start_line <= other.end_line
                   for other in kept):
            kept.append(finding)
    return kept


def code_at(files: dict[str, str], finding: Finding) -> str:
    """The real source lines a finding points at."""
    lines = files[finding.file].splitlines()
    return "\n".join(lines[finding.start_line - 1:finding.end_line])


def _normalize(line: str) -> str:
    return " ".join(line.split())


def _strip_fences(code: str) -> str:
    lines = code.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)
