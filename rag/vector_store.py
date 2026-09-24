"""Per-repository vector indexes and the registry that keeps them in memory."""
import logging
import threading
import uuid
from collections import OrderedDict
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def get_embeddings(model_name: str) -> Embeddings:
    """Load the embedding model once per process — it takes seconds and hundreds of MB."""
    return HuggingFaceEmbeddings(model_name=model_name)


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.EphemeralClient(settings=ChromaSettings(anonymized_telemetry=False))


class RepoIndex:
    """Vector index over one repository's code chunks, stored in its own Chroma collection."""

    def __init__(self, repo_name: str, chunks: list[Document], embeddings: Embeddings,
                 client: chromadb.ClientAPI | None = None):
        self.repo_name = repo_name
        self.files = sorted({c.metadata["source"] for c in chunks})
        self.chunk_count = len(chunks)
        self._embeddings = embeddings
        self._client = client or get_chroma_client()
        self._collection = self._client.create_collection(name=f"bugeye-{uuid.uuid4().hex}")
        try:
            self._add(chunks)
        except BaseException:
            self.drop()
            raise

    def _add(self, chunks: list[Document]) -> None:
        batch_size = self._client.get_max_batch_size()
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset:offset + batch_size]
            texts = [c.page_content for c in batch]
            self._collection.add(
                ids=[str(offset + i) for i in range(len(batch))],
                documents=texts,
                embeddings=self._embeddings.embed_documents(texts),
                metadatas=[c.metadata for c in batch],
            )

    def search(self, query: str, k: int) -> list[Document]:
        k = min(k, self.chunk_count)
        if k <= 0:
            return []
        result = self._collection.query(query_embeddings=[self._embeddings.embed_query(query)], n_results=k)
        return [
            Document(page_content=text, metadata={**meta, "id": chunk_id})
            for chunk_id, text, meta in zip(result["ids"][0], result["documents"][0], result["metadatas"][0])
        ]

    def drop(self) -> None:
        try:
            self._client.delete_collection(self._collection.name)
        except Exception:
            logger.warning("Failed to delete index for %s", self.repo_name, exc_info=True)


class IndexRegistry:
    """Thread-safe map of repo name -> RepoIndex that keeps only the most recently used entries."""

    def __init__(self, max_entries: int):
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, RepoIndex] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, index: RepoIndex) -> None:
        key = index.repo_name.lower()
        with self._lock:
            dropped = [self._entries.pop(key)] if key in self._entries else []
            self._entries[key] = index
            while len(self._entries) > self._max_entries:
                dropped.append(self._entries.popitem(last=False)[1])
        for old in dropped:
            old.drop()

    def get(self, repo_name: str) -> RepoIndex | None:
        key = repo_name.lower()
        with self._lock:
            index = self._entries.get(key)
            if index is not None:
                self._entries.move_to_end(key)
            return index

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
