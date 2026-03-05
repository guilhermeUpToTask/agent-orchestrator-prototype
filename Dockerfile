FROM python:3.11-slim

WORKDIR /app

# Install git (needed by git workspace adapter)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv --no-cache-dir

COPY pyproject.toml .
COPY src/ src/
COPY workflow/ workflow/

# Install dependencies via uv
RUN uv pip install --system -e .

CMD ["python", "-m", "src.cli", "--help"]
