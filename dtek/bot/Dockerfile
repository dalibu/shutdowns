FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Botasaurus
RUN apt-get update && apt-get install -y \
    fonts-dejavu-core \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY dtek/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium for Botasaurus
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Copy common library
COPY common/ ./common/

# Copy DTEK parser
COPY dtek/parser/ ./dtek/parser/
COPY dtek/__init__.py ./dtek/
COPY dtek/data_source.py ./dtek/

# Copy DTEK bot
COPY dtek/bot/bot.py ./dtek/bot/

# Copy resources
COPY dtek/resources/ ./resources/

# Create data directory
RUN mkdir -p /data

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "-m", "dtek.bot.bot"]
