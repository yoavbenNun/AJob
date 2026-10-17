FROM python:3.12-slim

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2,<27" \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY src ./src
COPY config/companies.yaml ./config/companies.yaml

RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "src.main", "scan", "--config", "config/companies.yaml"]
