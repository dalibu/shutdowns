FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY cek/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy common library
COPY common/ ./common/

# Copy CEK parser
COPY cek/parser/ ./cek/parser/
COPY cek/__init__.py ./cek/
COPY cek/data_source.py ./cek/

# Copy CEK bot
COPY cek/bot/bot.py ./cek/bot/

# Copy resources
COPY cek/resources/ ./resources/

# Create data directory
RUN mkdir -p /data

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "-m", "cek.bot.bot"]
