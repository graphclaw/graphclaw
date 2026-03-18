FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata first to leverage layer caching
COPY pyproject.toml ./

# Install project dependencies (without editable install for the layer)
RUN pip install --no-cache-dir \
    "anthropic>=0.40.0" \
    "psycopg[binary]>=3.2.0" \
    "psycopg-pool>=3.2.0" \
    "pydantic>=2.9.0" \
    "typer>=0.12.0" \
    "rich>=13.9.0" \
    "pgvector>=0.3.0" \
    "python-dotenv>=1.0.0" \
    "redis[hiredis]>=5.0.0" \
    "boto3>=1.35.0" \
    "litellm>=1.50.0" \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.34.0" \
    "aiosmtplib>=3.0.0" \
    "PyYAML>=6.0" \
    "hatchling"

# Copy source code
COPY src/ ./src/

# Install the package in editable mode so the graphclaw CLI entry point is available
RUN pip install --no-cache-dir -e ".[dev]"

ENTRYPOINT ["graphclaw"]
