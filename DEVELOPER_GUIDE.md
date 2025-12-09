# Developer Quick Start Guide

## Initial Setup (First Time)

### 1. Check Python Version

This project requires **Python 3.13 or 3.14** (recommended: **3.14.1**).

Check your Python version:
```bash
python --version
# or
python3 --version
```

### 2. Create Virtual Environment

**Option A: Using Conda (Recommended)**
```bash
# Create environment with specific Python version
conda create -n shutdowns python=3.14
conda activate shutdowns
```

**Option B: Using venv**
```bash
# Make sure you have Python 3.13+ installed
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

**Automated (Recommended):**
```bash
./setup_dev.sh
```

This script will:
- Check your Python version
- Detect virtual environment
- Install all dependencies
- Verify installation

**Manual:**
```bash
pip install -r requirements-dev.txt
```

### 4. Verify Installation

Run tests to make sure everything works:
```bash
./run_tests.sh
```

You should see all tests passing! ✅

---

## Daily Development Workflow

### Activate Environment

**Conda:**
```bash
conda activate shutdowns
```

**Venv:**
```bash
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Run Tests

```bash
# All tests
./run_tests.sh

# Specific provider
./run_tests.sh all dtek
./run_tests.sh all cek

# Only unit tests
./run_tests.sh unit all

# With coverage
./run_tests.sh coverage all
```

### Database Migrations

Before running bots locally, apply database migrations:

```bash
# Apply migrations (creates tables if needed)
python -m common.migrate --db-path ./data/bot.db

# Check current schema version
python -m common.migrate --db-path ./data/bot.db --status
```

Migration files are in `common/migrations/` as versioned SQL files.

### Run Bots Locally

```bash
# DTEK bot
export DTEK_BOT_TOKEN="your_token_here"
python -m common.migrate --db-path ./dtek_bot.db  # First time
python -m dtek.bot.bot

# CEK bot
export CEK_BOT_TOKEN="your_token_here"
python -m common.migrate --db-path ./cek_bot.db  # First time
python -m cek.bot.bot
```

---

## Troubleshooting

### "pytest not installed" error

Make sure you're in the virtual environment:
```bash
# Check which Python you're using
which python

# Should show path to venv or conda environment
# NOT /usr/bin/python
```

Then install dependencies:
```bash
pip install -r requirements-dev.txt
```

### "ModuleNotFoundError: No module named 'botasaurus'"

Install all dependencies:
```bash
pip install -r requirements-dev.txt
```

The `requirements-dev.txt` now includes **all** dependencies needed for development, including `botasaurus`.

### Wrong Python version

If you see a Python version warning:

**Conda:**
```bash
# Recreate environment with correct version
conda deactivate
conda create -n shutdowns python=3.14 --force
conda activate shutdowns
./setup_dev.sh
```

**Venv:**
```bash
# Delete old venv
rm -rf venv

# Create new one with specific Python
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

---

## Project Structure

```
shutdowns/
├── .python-version          # Python version for pyenv
├── pyproject.toml           # Project metadata, dependencies, pytest config
├── requirements-dev.txt     # All dependencies (runtime + dev)
├── setup_dev.sh             # Automated setup script
├── run_tests.sh             # Test runner with auto-detection
│
├── common/                  # Shared library (core logic)
│   ├── bot_base.py          # BotContext, states, DB functions (~700 lines)
│   ├── handlers.py          # All command handlers (~1170 lines)
│   ├── tasks.py             # Background tasks (~505 lines)
│   ├── formatting.py        # Schedule formatting utilities
│   ├── visualization.py     # Schedule diagram generation
│   ├── migrate.py           # Migration CLI tool
│   ├── migrations/          # SQL migration files
│   │   ├── 001_initial_schema.sql
│   │   ├── 002_user_addresses.sql
│   │   └── 003_multi_subscriptions.sql
│   └── tests/               # Common library tests
│
├── dtek/                    # DTEK provider (~400 lines)
│   ├── bot/bot.py           # Thin wrappers + provider config
│   ├── parser/              # DTEK-specific parser
│   └── tests/
│
└── cek/                     # CEK provider (~400 lines)
    ├── bot/bot.py           # Thin wrappers + provider config
    ├── parser/              # CEK-specific parser
    └── tests/
```

### Architecture

Both bots use a **thin wrapper pattern**:
- Provider-specific `bot.py` files contain only configuration and wrappers (~400 lines each)
- All business logic is in `common/handlers.py` and `common/tasks.py`
- Wrappers call common handlers with `BotContext` for provider-specific settings

---

## Version Management Files

- **`.python-version`** - Used by pyenv, asdf, and other version managers
- **`pyproject.toml`** - Specifies `requires-python = ">=3.13,<3.15"`
- **`requirements-dev.txt`** - Includes all dependencies

These files ensure everyone uses compatible Python versions!

---

## Need Help?

1. Check this guide
2. Read [README.md](README.md)
3. Check provider-specific docs: `dtek/bot/README.md`, `cek/bot/README.md`
4. Ask the team
