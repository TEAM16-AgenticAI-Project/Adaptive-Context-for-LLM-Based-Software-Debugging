# Adaptive-Context Debugger web UI.
# Build context is the repository root, so the image runs the engine in src/adaptive_context
# together with the bundled examples and the controlled cases.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is used to shallow-clone public repositories (ALLOW_GIT_CLONE)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ui/backend/requirements.txt ui/backend/requirements.txt
RUN pip install -r ui/backend/requirements.txt

COPY pyproject.toml ./
COPY src ./src
COPY examples ./examples
COPY data/controlled ./data/controlled
COPY ui/backend ./ui/backend
COPY ui/frontend ./ui/frontend
COPY ui/examples ./ui/examples

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

ENV PYTHONPATH=/app/src \
    PORT=8000 \
    DEFAULT_LLM=sim \
    ALLOW_LOCAL_PATH=0 \
    ALLOW_GIT_CLONE=1 \
    ALLOW_RUN_TESTS=0

EXPOSE 8000
WORKDIR /app/ui/backend

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)"

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30"]
