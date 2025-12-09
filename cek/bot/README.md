# CEK Telegram Bot

Independent Telegram bot for checking power outage schedules from CEK (Central Energy Kompany).

## Features

- 🔍 Checking outage schedules by address
- 📖 **Address Book** - save multiple addresses for quick access
- 📊 24-hour chart visualization (pie chart)
- ⚡ **Optimization with group caching** - faster re-verification
- 🔔 **Multi-Subscriptions** - subscribe to multiple addresses
- ⚠️ Notification N minutes before shutdown/startup
- 🤖 Protection against bots (CAPTCHA)
- 💾 Local SQLite database with migrations

## Quick start

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
# З current directory (cek/bot/)
docker-compose up -d

# Or from the project's root directory
cd cek/bot && docker-compose up -d
```

### 4. Status check

```bash
# View logs
docker-compose logs -f cek_bot

# Status check
docker-compose ps
```

## Bot commands

- `/start` or `/help` - Show reference
- `/check Місто, Вулиця, Будинок` - Check schedule
- `/check` - Step-by-step address entry (or select from address book)
- `/repeat` - Repeat last check (or select from address book)
- `/addresses` - Manage saved addresses
- `/subscribe [години]` - Subscribe to updates (default setting 1 hour)
- `/unsubscribe` - Cancel subscription (supports multiple subscriptions)
- `/alert [хвилини]` - Configure notifications (0 = turn off)
- `/cancel` - Cancel current action

## Usage example

```
/check м. Павлоград, вул. Нова, 7
/subscribe 2
/alert 15
```

## CEK Bot technical features

### Group caching
The CEK bot automatically saves the queue (group) number for each address. When rechecking the same address, the parser skips the queue determination step, which significantly speeds up the process.

### 24-hour schedule
CEK only displays the graph for the current day (24 hours).

## Data structure

The database is stored in `/data/cek_bot.db` (Docker volume `cek_data`).

Tables:
- `subscriptions` - User subscriptions (supports multiple per user, includes `group_name`)
- `user_last_check` - Last check of each user (includes `group_name`)
- `user_addresses` - Address book
- `user_activity` - User activity tracking
- `schema_version` - Migration version tracking

## Database Migrations

Before first run (or after updates), apply migrations:

```bash
# From project root
python -m common.migrate --db-path cek/data/cek_bot.db

# Check status
python -m common.migrate --db-path cek/data/cek_bot.db --status
```

## Updates

```bash
# Stop the bot
docker-compose down

# Update the code (git pull or similar)
git pull

# Rebuild and run
docker-compose up -d --build
```

## Backup

```bash
# Create database backup
docker cp cek_bot:/data/cek_bot.db ./backup_$(date +%Y%m%d).db

# Restore from backup
docker cp ./backup_20231122.db cek_bot:/data/cek_bot.db
```

## Debugging

### Review logs
```bash
docker-compose logs -f cek_bot
```

### Run in development mode
```bash
# Stop Docker version
docker-compose down

# Establish dependencies
pip install -r ../../requirements.txt

# Run locally
export CEK_BOT_TOKEN="your_token"
export CEK_DB_PATH="./cek_bot.db"
export CEK_FONT_PATH="../../resources/DejaVuSans.ttf"
python -m cek.bot.bot
```

## Technical details

- **Python**: 3.12
- **Framework**: aiogram 3.x
- **Database**: SQLite (aiosqlite)
- **Parser**: Playwright (headless Chrome)
- **Visualization**: Pillow (PIL)
- **Optimization**: Queue number caching for quick rechecking

## Differences from DTEK bot

| Function | DTEK | CEK |
|----------|------|-----|
| Visualization | 48 hours (2 days) | 24 hours (today) |
| Caching | No | Yes (queue number) |
| Text graph | All days | Today only |
| Parser | dtek_parser | cek_parser |

## Support

If you encounter problems:
1. Check the logs: `docker-compose logs -f cek_bot`
2. Check the `.env` file
3. Make sure the bot token is correct
4. Restart the bot: `docker-compose restart cek_bot`
5. If the parser does not work, the structure of the CEK website may have changed
