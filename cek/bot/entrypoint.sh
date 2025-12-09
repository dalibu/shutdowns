#!/bin/bash
set -e

# Run database migrations
echo "Running database migrations..."
python -m common.migrate --db-path "${DB_PATH:-/data/bot.db}"

# Start the bot
echo "Starting bot..."
exec python -m cek.bot.bot
