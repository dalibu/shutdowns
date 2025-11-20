# Power Shutdowns Telegram Bot

A centralized Telegram bot service for tracking planned power shutdowns across multiple electricity providers in Ukraine.

## Overview

This service provides a unified interface for users to check power shutdown schedules regardless of their electricity provider. Users simply enter their address, and the system automatically determines their provider and displays the relevant shutdown schedule.

### Supported Providers

- **DTEK** (ДТЕК) - Serves Dnipro, Kyiv, Odesa and other regions
- **CEK** (ЦЕК) - Central Energy Company serving other regions

## Architecture

The project uses a centralized architecture with the following components:

### Core Components

- **`api.py`** - Central FastAPI service that:
  - Receives address requests from the bot
  - Determines the electricity provider based on the address
  - Routes requests to the appropriate parser (DTEK or CEK)
  - Returns unified shutdown schedule data

- **`bot.py`** - Telegram bot that:
  - Provides user interface for address input
  - Communicates with the central API
  - Displays shutdown schedules with visual graphics
  - Supports subscriptions for automatic updates

### Provider Parsers

- **`dtek/dtek_parser.py`** - Web scraper for DTEK shutdown schedules
- **`cek/cek_parser.py`** - Web scraper for CEK shutdown schedules (placeholder)

### Shared Resources

- **`resources/`** - Fonts and assets used by the bot for generating schedule images

## Project Structure

```
shutdowns/
├── api.py                  # Central API with provider resolution
├── bot.py                  # Telegram bot
├── resources/              # Shared fonts and assets
├── requirements.txt        # Python dependencies
├── Dockerfile.bot          # Bot container definition
├── Dockerfile.parser       # API container definition
├── docker-compose.yml      # Service orchestration
├── .env.example            # Environment variables template
├── dtek/
│   ├── __init__.py
│   └── dtek_parser.py      # DTEK-specific parser
└── cek/
    ├── __init__.py
    └── cek_parser.py       # CEK parser (to be implemented)
```

## How It Works

1. User sends their address to the Telegram bot
2. Bot forwards the request to the central API
3. API determines the provider based on the address (city-based logic)
4. API calls the appropriate parser (DTEK or CEK)
5. Parser scrapes the provider's website for shutdown schedules
6. API returns unified schedule data to the bot
7. Bot displays the schedule with visual graphics and status information

## Setup

### Prerequisites

- Docker and Docker Compose
- Telegram Bot Token (obtain from [@BotFather](https://t.me/botfather))

### Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your bot token:
   ```
   SHUTDOWNS_TELEGRAM_BOT_TOKEN=your_bot_token_here
   ```

### Running with Docker

```bash
docker-compose up --build
```

This will start two services:
- `api` - FastAPI service on port 8000
- `bot` - Telegram bot

### Development

For local development without Docker:

1. Create and activate conda environment:
   ```bash
   conda create -n shutdowns python=3.12
   conda activate shutdowns
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the API:
   ```bash
   python api.py
   ```

4. Run the bot (in another terminal):
   ```bash
   python bot.py
   ```

## Features

- **Automatic Provider Detection** - No need to specify your provider
- **Visual Schedule Graphics** - 48-hour circular clock display
- **Current Status** - Shows if power is currently on/off and next change time
- **Subscription System** - Automatic notifications when schedules change
- **Event Alerts** - Notifications before planned shutdowns

## Future Enhancements

- Implement CEK parser
- Enhanced provider resolution logic (database-based)
- Support for additional electricity providers
- Historical shutdown data tracking
- Mobile app integration

## License

[Add your license here]
