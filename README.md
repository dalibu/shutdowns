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
├── common/                 # Shared library (~70% of logic)
│   ├── bot_base.py         # Database, FSM, utilities, CAPTCHA
│   ├── handlers.py         # All command/callback handlers
│   ├── tasks.py            # Background tasks (subscriptions, alerts)
│   ├── data_source.py      # Abstract Data Source Interface
│   ├── formatting.py       # Schedule text formatting
│   └── visualization.py    # Schedule image generation
│
├── dtek/                   # DTEK Provider (~400 lines)
│   ├── parser/             # DTEK web scraper
│   ├── data_source.py      # DTEK Data Source Implementation
│   └── bot/                # Thin wrappers + config
│       ├── bot.py
│       └── docker-compose.yml
│
├── cek/                    # CEK Provider (~400 lines)  
│   ├── parser/             # CEK web scraper
│   ├── data_source.py      # CEK Data Source Implementation
│   └── bot/                # Thin wrappers + config
│       ├── bot.py
│       └── docker-compose.yml
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
- 📖 **Address Book** - Save multiple addresses for quick access (`/addresses` command)
- 📊 **Visual Diagrams** - Rotating circular clock-face visualization with triangle hour marker
- 🔔 **Multi-Subscriptions** - Subscribe to multiple addresses simultaneously
- ⚠️ **Alerts** - Notifications N minutes before power events
- 🤖 **CAPTCHA Protection** - Bot protection
- 💾 **Local Database** - SQLite with migration support
- 📈 **Statistics** - Admin-only `/stats` command provides usage summary and CSV export

### Provider-Specific Features

| Feature | DTEK | CEK |
|---------|------|-----|
| Visualization | 48 hours (2 days) | 24 hours (today) |
| Group Caching | No | Yes (faster repeat checks) |
| Schedule Display | All days | Today only |

## Deployed Bots
Users can subscribe to updates and interact with the bots directly in Telegram.
The bots are already deployed, running on Telegram, and can be found under the following names:
- **DTEK**: `@dtek_disconnections_bot`
- **CEK**: `@cek_disconnections_bot`

## Common Library

The `common/` directory contains shared logic used by both bots:

- **`bot_base.py`** (~700 lines)
  - Database connection (SQLite)
  - FSM states for user interaction
  - CAPTCHA logic
  - Address parsing & Address Book CRUD
  - Schedule hashing for change detection
  - Multi-subscription management

- **`handlers.py`** (~1170 lines)
  - All command handlers (start, check, subscribe, repeat, stats, etc.)
  - Callback handlers for address selection
  - Response formatting and sending
  - Parametrized for provider-specific settings via `BotContext`

- **`tasks.py`** (~505 lines)
  - Background tasks (subscription_checker, alert_checker)
  - Alert processing logic

- **`migrate.py`** - Database migration CLI
  - Version-tracked schema migrations
  - Run before bot startup for new schemas

- **`formatting.py`** (~350 lines)
  - Schedule text formatting
  - Current status messages
  - Time slot merging

- **`visualization.py`** (~800 lines)
  - Rotating circular diagrams (48h and 24h)
  - Triangle hour marker pointing upward
  - Smart date positioning in center
  - PIL/Pillow image generation

## Data Sources (Pluggable Architecture)

The project supports a **pluggable data source architecture**, allowing providers to switch between different data retrieval methods (Web Parser, Database, API) without changing the bot's core logic.

- **Interface**: Defined in `common/data_source.py` (`ShutdownDataSource`).
- **Implementation**: Each provider implements its own data source (e.g., `dtek/data_source.py`).
- **Configuration**: Controlled via `DATA_SOURCE_TYPE` environment variable.

For detailed implementation guide, see [DATA_SOURCES.md](DATA_SOURCES.md).

## Development

### Python Version

This project requires **Python 3.13 or 3.14** (recommended: 3.14.1).

The Python version is managed via:
- `.python-version` - Used by pyenv and other version managers
- `pyproject.toml` - Specifies `requires-python = ">=3.13,<3.15"`

### Quick Setup

**Automated setup (recommended):**
```bash
# Create conda environment
conda create -n shutdowns python=3.14
conda activate shutdowns

# Run automated setup script
./setup_dev.sh
```

**Manual setup:**
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt
```

### Project Structure Benefits

1. **DRY Principle** - Common logic in one place
2. **Independence** - Each provider is self-contained
3. **Scalability** - Easy to add new providers
4. **Maintainability** - Bug fixes apply to all bots
5. **Client-Specific** - Deploy separate instances per client

### Database Migrations

Before running bots for the first time (or after schema changes), apply migrations:

```bash
# Apply migrations to database
python -m common.migrate --db-path ./data/bot.db

# Check migration status
python -m common.migrate --db-path ./data/bot.db --status
```

### Running Locally

```bash
# Run DTEK bot
export DTEK_BOT_TOKEN="your_token"
export DTEK_DB_PATH="./dtek_bot.db"
python -m common.migrate --db-path ./dtek_bot.db  # First time only
python -m dtek.bot.bot

# Run CEK bot
export CEK_BOT_TOKEN="your_token"
export CEK_DB_PATH="./cek_bot.db"
python -m common.migrate --db-path ./cek_bot.db  # First time only
python -m cek.bot.bot
```

### Testing

```bash
# Run all tests for all components
./run_tests.sh

# Run tests for specific provider
./run_tests.sh all dtek
./run_tests.sh all cek

# Run specific test types
./run_tests.sh unit all      # Unit tests only
./run_tests.sh coverage all  # With coverage report
./run_tests.sh quick all     # Skip slow tests

# Or use pytest directly
pytest                        # All tests
pytest dtek/tests/           # DTEK tests only
pytest cek/tests/            # CEK tests only
pytest common/tests/         # Common library tests
```

## Technical Stack

- **Python**: 3.14 (compatible with 3.13-3.14)
- **Bot Framework**: aiogram 3.x
- **Database**: SQLite (aiosqlite)
- **Web Scraping**: Botasaurus (headless Chrome)
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
