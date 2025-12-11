# DTEK Telegram Bot

Independent Telegram bot for checking DTEK power outage schedules.

## Features

- 🔍 Checking outage schedules by address
- 📖 **Address Book** - save multiple addresses for quick access
- 📊 Visualization of the schedule for 48 hours (pie chart)
- 🔔 **Multi-Subscriptions** - subscribe to multiple addresses
- ⚠️ Notifications N minutes before shutdown/startup
- 🤖 Protection against bots (CAPTCHA)
- 💾 Local SQLite database with migrations

## Quick Start

### 1. Get a bot token

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Create a new bot with the command `/newbot`
3. Copy the token you receive

### 2. Configure the environment

```bash
# Copy the configuration example
cp .env.example .env

# Edit .env and paste your token
nano .env
```

### 3. Launch the bot

```bash
# From the current directory (dtek/bot/)
docker-compose up -d

# Or from the project's root directory
cd dtek/bot && docker-compose up -d
```

### 4. Check status

```bash
# View logs
docker-compose logs -f dtek_bot

# Check status
docker-compose ps
```

## Bot commands

- `/start` or `/help` - Show help
- `/check City, Street, House` - Check schedule
- `/check` - Step-by-step address entry (or select from address book)
- `/repeat` - Repeat last check (or select from address book)
- `/addresses` - Manage saved addresses
- `/subscribe [hours]` - Subscribe to updates (default is 1 hour)
- `/unsubscribe` - Unsubscribe (supports multiple subscriptions)
- `/alert [minutes]` - Set up notifications (0 = turn off)
- `/cancel` - Cancel the current action

## Usage example

```
/check м. Дніпро, вул. Сонячна набережна, 6
/subscribe 3
/alert 30
```

## Data structure

The database is stored in `/data/dtek_bot.db` (Docker volume `dtek_data`).

Tables:
- `subscriptions` - User subscriptions (supports multiple per user)
- `user_last_check` - Last check for each user
- `user_addresses` - Address book
- `user_activity` - User activity tracking
- `schema_version` - Migration version tracking

## Database Migrations

Before first run (or after updates), apply migrations:

```bash
# From project root
python -m common.migrate --db-path dtek/data/dtek_bot.db

# Check status
python -m common.migrate --db-path dtek/data/dtek_bot.db --status
```

## Updates

```bash
# Stop bot
docker-compose down

# Update the code (git pull or other)
git pull

# Rebuild and run
docker-compose up -d --build
```

## Backup

```bash
# Create a database backup
docker cp dtek_bot:/data/dtek_bot.db ./backup_$(date +%Y%m%d).db

# Restore from backup
docker cp ./backup_20231122.db dtek_bot:/data/dtek_bot.db
```

## Debugging

### View logs
```bash
docker-compose logs -f dtek_bot
```

### Run in development mode
```bash
# Stop Docker version
docker-compose down

# Set dependencies
pip install -r ../../requirements.txt

# Run locally
export BOT_TOKEN="your_token"
export DB_PATH="./dtek_bot.db"
export FONT_PATH="../../resources/DejaVuSans.ttf"
python -m dtek.bot.bot
```

## Technical details

- **Python**: 3.12
- **Framework**: aiogram 3.x
- **Database**: SQLite (aiosqlite)
- **Parser**: Playwright (headless Chrome)
- **Visualization**: Pillow (PIL)

## Support

If you encounter any problems:
1. Check the logs: `docker-compose logs -f dtek_bot`
2. Check the `.env` file
3. Make sure the bot token is correct
4. Restart the bot: `docker-compose restart dtek_bot`
