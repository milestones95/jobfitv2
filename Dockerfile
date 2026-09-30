# Hosted MCP server for claude.ai: serves streamable HTTP at /mcp.
# Keep the image tag's version in step with the playwright package in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper ./scraper

ENV HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "scraper.mcp_server", "--http"]
