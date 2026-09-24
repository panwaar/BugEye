"""Finished reviews kept in memory, so reviewing an unchanged repository again costs no tokens."""
import threading
from collections import OrderedDict

CacheKey = tuple[str, str, str, str]  # (repo, commit, review model, verify model)


class ReviewCache:
    """Thread-safe store of report dicts that keeps only the most recently used entries."""

    def __init__(self, max_entries: int):
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, dict[str, str]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(repo_name: str, commit: str, review_model: str, verify_model: str) -> CacheKey:
        return repo_name.lower(), commit, review_model, verify_model

    def get(self, key: CacheKey) -> dict[str, str] | None:
        with self._lock:
            reports = self._entries.get(key)
            if reports is not None:
                self._entries.move_to_end(key)
            return reports

    def put(self, key: CacheKey, reports: dict[str, str]) -> None:
        if self._max_entries <= 0:
            return
        with self._lock:
            self._entries[key] = reports
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
