FROM python:3.12-slim

# Install system dependencies including git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency specifications first for layer caching
COPY pyproject.toml ./

# Copy entire source tree
COPY . .

# Install package in editable mode with dependencies
RUN pip install --no-cache-dir -e ".[all]"

# Build the demo ecommerce repo and initialize ContextPilot index
RUN python examples/make_demo_repo.py demo/ecommerce-core && \
    cd demo/ecommerce-core && \
    git config user.name "ContextPilot Demo" && \
    git config user.email "demo@contextpilot.local" && \
    git init && \
    git add . && \
    git commit -m "Initial commit for demo ecommerce core" && \
    ctxly init

# Environment variables for Cloud Run
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV CTXLY_OFFLINE=1

EXPOSE 8080

# Run Google Cloud Run entrypoint
CMD ["python", "-m", "contextpilot.cloud_run"]
