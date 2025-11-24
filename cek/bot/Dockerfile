FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY cek/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright
RUN playwright install --with-deps chromium

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
