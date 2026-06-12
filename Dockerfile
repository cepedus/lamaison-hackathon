FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv lock --check
RUN uv export --format requirements.txt --frozen --no-dev --no-emit-project --no-hashes > requirements.txt



FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1


WORKDIR /app

COPY --from=builder /app/requirements.txt ./
COPY src/ ./src/
COPY pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host"]