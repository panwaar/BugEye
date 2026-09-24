"""Everything that talks to GitHub: repo name parsing, cloning, and pull request diffs."""
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from github import Auth, Github, GithubException

from exceptions import BugEyeError

logger = logging.getLogger(__name__)

_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_NAME = r"[A-Za-z0-9._-]{1,100}"
_SHORT_RE = re.compile(rf"^({_OWNER})/({_NAME})/?$")
_URL_RE = re.compile(rf"^(?:https?://)?(?:www\.)?github\.com/({_OWNER})/({_NAME})(?:[/?#].*)?$", re.IGNORECASE)

_MAX_PATCH_CHARS_PER_FILE = 3000


def parse_repo(value: str) -> str:
    """Normalise 'owner/repo' or any github.com URL to 'owner/repo'.

    Raises BugEyeError for anything else, so the result is always safe to put in a URL.
    """
    text = (value or "").strip()
    match = _URL_RE.match(text) or _SHORT_RE.match(text)
    if not match:
        raise BugEyeError("Enter a repository as 'owner/repo' or a github.com URL.")
    owner, name = match.groups()
    if name.lower().endswith(".git"):
        name = name[:-4]
    if name in ("", ".", ".."):
        raise BugEyeError("Enter a repository as 'owner/repo' or a github.com URL.")
    return f"{owner}/{name}"


@contextmanager
def cloned_repo(repo_name: str, timeout: int) -> Iterator[str]:
    """Shallow-clone a public GitHub repo into a temp dir that is deleted on exit."""
    path = tempfile.mkdtemp(prefix="bugeye-")
    try:
        _clone(repo_name, path, timeout)
        yield path
    finally:
        shutil.rmtree(path, onerror=_make_writable_and_retry)


def _clone(repo_name: str, dest: str, timeout: int) -> None:
    url = f"https://github.com/{parse_repo(repo_name)}.git"
    # Never prompt for credentials (a missing repo would otherwise hang) and skip LFS blobs.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    command = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--", url, dest]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise BugEyeError("git is not installed on the server.", status_code=503) from e
    except subprocess.TimeoutExpired as e:
        raise BugEyeError(f"Cloning {repo_name} took longer than {timeout}s — the repository may be too large.") from e
    if result.returncode != 0:
        logger.warning("git clone of %s failed: %s", repo_name, result.stderr.strip())
        raise BugEyeError(f"Could not clone {repo_name}. Check that the repository exists and is public.")


def _make_writable_and_retry(func, path, _exc_info):
    # Git marks pack files read-only, which makes rmtree fail on Windows.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        logger.warning("Could not remove temporary file %s", path)


# ── Pull requests ─────────────────────────────────────────────

@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    changed_files: list[str]
    markdown: str  # Title, description and (truncated) diffs, ready to hand to the LLM


def fetch_pull_request(repo_name: str, number: int, *, token: str | None, max_chars: int) -> PullRequest:
    client = Github(auth=Auth.Token(token), timeout=15) if token else Github(timeout=15)
    try:
        return _load(client, repo_name, number, max_chars)
    except GithubException as e:
        if e.status == 404:
            raise BugEyeError(f"PR #{number} was not found in {repo_name}.", status_code=404) from e
        if e.status in (401, 403):
            raise BugEyeError("GitHub API refused the request (rate limit or bad GITHUB_TOKEN).", status_code=503) from e
        logger.warning("GitHub API error for %s#%s: %s", repo_name, number, e)
        raise BugEyeError(f"GitHub API error ({e.status}) while fetching PR #{number}.", status_code=502) from e
    finally:
        client.close()


def _load(client: Github, repo_name: str, number: int, max_chars: int) -> PullRequest:
    pr = client.get_repo(repo_name).get_pull(number)
    files = list(pr.get_files())

    lines = [
        f"### PR #{number}: {pr.title}",
        f"Author: {pr.user.login} | Branch: {pr.head.ref} -> {pr.base.ref}",
        f"Changes: +{pr.additions} -{pr.deletions} across {pr.changed_files} files",
        "",
        "Description:",
        pr.body.strip() if pr.body else "(none provided)",
        "",
    ]
    budget = max_chars
    omitted = 0
    for file in files:
        header = f"#### `{file.filename}` ({file.status}, +{file.additions} -{file.deletions})"
        patch = file.patch or "(binary file or no diff available)"
        if len(patch) > _MAX_PATCH_CHARS_PER_FILE:
            patch = patch[:_MAX_PATCH_CHARS_PER_FILE] + "\n... (diff truncated)"
        block = f"{header}\n```diff\n{patch}\n```"
        if len(block) > budget:
            omitted += 1
            continue
        budget -= len(block)
        lines.append(block)
    if omitted:
        lines.append(f"({omitted} more changed files omitted to stay within the context limit)")

    return PullRequest(
        number=number,
        title=pr.title,
        changed_files=[f.filename for f in files],
        markdown="\n".join(lines),
    )
