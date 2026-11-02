# Adaptive-Context Debugger — production container.
# Multi-stage keeps the final image small; git is included for the "clone a public repo" source.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# git is needed only for the git_url source; drop it if you disable ALLOW_GIT_CLONE.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY examples ./examples

# run as a non-root user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV PORT=8000 \
    DEFAULT_LLM=sim \
    ALLOW_LOCAL_PATH=0 \
    ALLOW_GIT_CLONE=1

EXPOSE 8000
WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health').status==200 else 1)"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
