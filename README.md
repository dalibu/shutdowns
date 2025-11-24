# Power Shutdowns Telegram Bots

Independent Telegram bot services for tracking planned power shutdowns across multiple electricity providers in Ukraine.

## Overview

This project provides **independent, provider-specific bots** for checking power shutdown schedules. Each provider (DTEK, CEK) has its own standalone bot that can be deployed separately for different clients.

### Supported Providers

- **DTEK** (ДТЕК) - Serves Dnipro, Kyiv, Odesa and other regions
- **CEK** (ЦЕК) - Central Energy Company (with group caching optimization)

## Architecture

The project uses a **multi-bot architecture** with shared common logic:

```
shutdowns/
├── common/                 # Shared library (DRY principle)
│   ├── bot_base.py        # Database, FSM, utilities, CAPTCHA
│   ├── data_source.py     # Abstract Data Source Interface
│   ├── formatting.py      # Schedule text formatting
│   └── visualization.py   # Schedule image generation
│
├── dtek/                   # DTEK Provider
│   ├── parser/            # DTEK web scraper
│   │   └── dtek_parser.py
│   ├── data_source.py     # DTEK Data Source Implementation
│   ├── bot/               # DTEK bot deployment
│   │   ├── bot.py
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   └── README.md
│   └── tests/             # DTEK tests
│
├── cek/                    # CEK Provider
│   ├── parser/            # CEK web scraper
│   │   └── cek_parser.py
│   ├── data_source.py     # CEK Data Source Implementation
│   ├── bot/               # CEK bot deployment
│   │   ├── bot.py
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── .env.example
│   │   └── README.md
│   └── tests/             # CEK tests
│
└── resources/              # Shared fonts and assets
```

### Key Features

✅ **Independent Deployment** - Each bot can be deployed separately  
✅ **Shared Logic** - Common library eliminates code duplication  
✅ **Provider-Specific** - Each bot optimized for its provider  
✅ **Easy Scaling** - Create multiple instances for different clients  
✅ **No API Layer** - Bots call parsers directly for better performance

## Quick Start

### Deploy DTEK Bot

```bash
cd dtek/bot
cp .env.example .env
# Edit .env and add your DTEK_BOT_TOKEN
docker-compose up -d
```

See [dtek/bot/README.md](dtek/bot/README.md) for detailed instructions.

### Deploy CEK Bot

```bash
cd cek/bot
cp .env.example .env
# Edit .env and add your CEK_BOT_TOKEN
docker-compose up -d
```

See [cek/bot/README.md](cek/bot/README.md) for detailed instructions.

## Bot Features

Both bots support:

- 🔍 **Address Lookup** - Check shutdown schedules by address
- 📊 **Visual Diagrams** - Circular clock-face schedule visualization
- 🔔 **Subscriptions** - Automatic updates when schedule changes
- ⚠️ **Alerts** - Notifications N minutes before power events
- 🤖 **CAPTCHA Protection** - Bot protection
- 💾 **Local Database** - SQLite for user data

### Provider-Specific Features

| Feature | DTEK | CEK |
|---------|------|-----|
| Visualization | 48 hours (2 days) | 24 hours (today) |
| Group Caching | No | Yes (faster repeat checks) |
| Schedule Display | All days | Today only |

## Common Library

The `common/` directory contains shared logic used by both bots:

- **`bot_base.py`** (229 lines)
  - Database initialization (SQLite)
  - FSM states for user interaction
  - CAPTCHA logic
  - Address parsing
  - Schedule hashing for change detection
  - Utility functions

- **`formatting.py`** (187 lines)
  - Schedule text formatting
  - Current status messages
  - Time slot merging

- **`visualization.py`** (406 lines)
  - 48-hour circular diagram (DTEK)
  - 24-hour circular diagram (CEK)
  - PIL/Pillow image generation

## Data Sources (Pluggable Architecture)

The project supports a **pluggable data source architecture**, allowing providers to switch between different data retrieval methods (Web Parser, Database, API) without changing the bot's core logic.

- **Interface**: Defined in `common/data_source.py` (`ShutdownDataSource`).
- **Implementation**: Each provider implements its own data source (e.g., `dtek/data_source.py`).
- **Configuration**: Controlled via `DATA_SOURCE_TYPE` environment variable.

For detailed implementation guide, see [DATA_SOURCES.md](DATA_SOURCES.md).

## Development

### Project Structure Benefits

1. **DRY Principle** - Common logic in one place
2. **Independence** - Each provider is self-contained
3. **Scalability** - Easy to add new providers
4. **Maintainability** - Bug fixes apply to all bots
5. **Client-Specific** - Deploy separate instances per client

### Running Locally

```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run DTEK bot
export DTEK_BOT_TOKEN="your_token"
export DTEK_DB_PATH="./dtek_bot.db"
python -m dtek.bot.bot

# Run CEK bot
export CEK_BOT_TOKEN="your_token"
export CEK_DB_PATH="./cek_bot.db"
python -m cek.bot.bot
```

### Testing

```bash
# Run all tests
pytest

# Run provider-specific tests
pytest dtek/tests/
pytest cek/tests/

# Run common library tests
pytest tests/test_common/
```

## Technical Stack

- **Python**: 3.12
- **Bot Framework**: aiogram 3.x
- **Database**: SQLite (aiosqlite)
- **Web Scraping**: Playwright (headless Chrome)
- **Image Generation**: Pillow (PIL)
- **Deployment**: Docker + Docker Compose

## Migration from Old Architecture

This project was refactored from a centralized architecture (single bot + API) to independent provider-specific bots. Benefits:

- ✅ No API layer needed (bots call parsers directly)
- ✅ Simpler deployment (one Docker container per bot)
- ✅ Better isolation (DTEK and CEK are independent)
- ✅ Easier to customize per client
- ✅ Shared logic via common library (DRY)

## Contributing

When adding new features:

1. **Common logic** → Add to `common/`
2. **Provider-specific** → Add to `provider/bot/` or `provider/parser/`
3. **Tests** → Add to `provider/tests/` or `tests/test_common/`

## License

MIT

## Support

For issues or questions:
- Check provider-specific READMEs: [dtek/bot/README.md](dtek/bot/README.md), [cek/bot/README.md](cek/bot/README.md)
- Review logs: `docker-compose logs -f <bot_name>`
- Open an issue on GitHub
