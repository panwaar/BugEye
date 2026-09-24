"""Reading source files from a cloned repository and splitting them into line-numbered chunks."""
import logging
import os
import re
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cpp", ".c", ".h", ".go", ".rs", ".rb", ".php", ".cs", ".kt", ".swift",
    ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".sql", ".sh",
    ".md", ".txt",
})
CODE_FILENAMES = frozenset({"Dockerfile", "Makefile", ".env.example", ".env.sample"})
SKIP_FILENAMES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "composer.lock"})
SKIP_DIRS = frozenset({"node_modules", "venv", "__pycache__", "dist", "build", "vendor", "target"})


@dataclass(frozen=True)
class LoadedFiles:
    documents: list[Document]
    skipped: int  # Files that were too large, not UTF-8, or over the file limit


def is_code_file(filename: str) -> bool:
    if filename in SKIP_FILENAMES or filename.endswith(".min.js"):
        return False
    return filename in CODE_FILENAMES or os.path.splitext(filename)[1].lower() in CODE_EXTENSIONS


def load_codebase(repo_path: str, *, max_files: int, max_file_bytes: int) -> LoadedFiles:
    documents: list[Document] = []
    skipped = 0
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for filename in sorted(files):
            if not is_code_file(filename):
                continue
            path = os.path.join(root, filename)
            # Never follow symlinks: a malicious repo could point one at files on this server.
            if os.path.islink(path):
                continue
            try:
                if len(documents) >= max_files or os.path.getsize(path) > max_file_bytes:
                    skipped += 1
                    continue
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError) as e:
                logger.debug("Skipping %s: %s", path, e)
                skipped += 1
                continue
            if text.strip():
                source = os.path.relpath(path, repo_path).replace(os.sep, "/")
                documents.append(Document(page_content=text, metadata={"source": source}))
    return LoadedFiles(documents=documents, skipped=skipped)


def split_documents(documents: list[Document], *, chunk_size: int, chunk_overlap: int) -> list[Document]:
    """Split files into chunks, recording each chunk's 1-based start_line/end_line."""
    # strip_whitespace=False: the default strips the first line's indentation, which makes
    # correctly indented code look broken to the LLM (it then reports fake syntax errors).
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True, strip_whitespace=False
    )
    chunks = []
    for doc in documents:
        text = doc.page_content
        for chunk in splitter.split_documents([doc]):
            start = chunk.metadata.pop("start_index", -1)
            # Trim blank lines at the edges only, keeping indentation intact.
            content = chunk.page_content
            leading_newlines = len(content) - len(content.lstrip("\n"))
            content = content.strip("\n")
            if not content.strip():
                continue
            chunk.page_content = content
            if start >= 0:
                start_line = text.count("\n", 0, start + leading_newlines) + 1
                chunk.metadata["start_line"] = start_line
                chunk.metadata["end_line"] = start_line + content.count("\n")
            chunks.append(chunk)
    return chunks


_PY_SYMBOLS = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)|^([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE)
_JS_SYMBOLS = re.compile(
    r"\bfunction\s+([A-Za-z_$][\w$]*)|\bclass\s+([A-Za-z_$][\w$]*)"
    r"|^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",  # top-level variables only
    re.MULTILINE)
_SYMBOL_PATTERNS = {".py": _PY_SYMBOLS, ".js": _JS_SYMBOLS, ".jsx": _JS_SYMBOLS, ".ts": _JS_SYMBOLS, ".tsx": _JS_SYMBOLS}


def extract_symbols(path: str, text: str, limit: int = 40) -> list[str]:
    """Names a file defines (functions, classes, constants), so the LLM knows they exist
    even when that file's code isn't in the current request."""
    pattern = _SYMBOL_PATTERNS.get(os.path.splitext(path)[1].lower())
    if pattern is None:
        return []
    names: list[str] = []
    for match in pattern.finditer(text):
        name = next(group for group in match.groups() if group)
        if name not in names:
            names.append(name)
            if len(names) >= limit:
                break
    return names
