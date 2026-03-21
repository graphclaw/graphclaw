FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install -e "." --no-cache-dir
COPY src/ ./src/
CMD ["python", "-m", "graphclaw.triggers.engine"]
