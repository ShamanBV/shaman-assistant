# MagicAnswer Slack Bot
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU-only first (faster, smaller)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directory for ChromaDB (will be mounted as volume)
RUN mkdir -p /app/knowledge_base

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/knowledge_base

# Run the Slack bot
CMD ["python", "slack_bot.py"]
