
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app

# Install uv (fast Python package manager)
RUN pip install uv

# Install Python dependencies
RUN uv pip install -r pyproject.toml

# Set environment variables (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1

# Expose port if needed (uncomment if your server listens on a port)
EXPOSE 8000

# Default command to run the MCP server
CMD ["python", "src/zendesk_mcp_server/server.py"]
