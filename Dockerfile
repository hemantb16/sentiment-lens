FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /home/app/src

# Dependencies first, so this layer is reused until the lock file changes.
# Every package must match a hash recorded in requirements.txt.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.txt

# The app runs from this folder (not installed as a package), so the static
# files and the model sit next to the code exactly as they do in the repo.
COPY app ./app
COPY models ./models

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

# Hosts such as Render inject PORT; one worker fits a 512 MB instance.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}"]
