FROM python:3.11-slim

# git is required for cloning the repositories under review
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user (Hugging Face Spaces expects UID 1000)
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# CPU-only torch keeps the image several GB smaller than the default CUDA build
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --user -r requirements.txt

# Download the embedding model at build time so the first request doesn't wait for it
# (keep in sync with EMBEDDING_MODEL)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY --chown=user *.py ./
COPY --chown=user agents ./agents
COPY --chown=user services ./services
COPY --chown=user rag ./rag
COPY --chown=user routes ./routes
COPY --chown=user templates ./templates
COPY --chown=user static ./static

EXPOSE 7860

# One worker: the vector indexes and rate limits live in process memory.
# --proxy-headers lets uvicorn see https behind the Hugging Face proxy.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
