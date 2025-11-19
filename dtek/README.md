# DTEK Power Outage Telegram Bot

This Telegram bot provides real-time power outage schedules for the Dnipro region (DTEK Grids). It allows users to check schedules for specific addresses, subscribe to automatic updates, and receive alerts before scheduled outages occur.

## 🏗 Architecture

The project is built using **Python 3.9+** and relies on the following key technologies:

*   **[Aiogram 3.x](https://docs.aiogram.dev/)**: An asynchronous framework for Telegram Bot API.
*   **[Aiosqlite](https://github.com/omnilib/aiosqlite)**: Asynchronous SQLite driver for data persistence.
*   **Docker & Docker Compose**: For containerization and easy deployment.

### Core Components

1.  **Bot Instance (`dtek_telegram_bot.py`)**: The main entry point. It handles user interaction, command processing, and manages background tasks.
2.  **Parser Module**: Responsible for fetching HTML from the DTEK website, solving simple CAPTCHAs (math problems), and parsing the schedule tables into structured data.
3.  **Database (`bot.db`)**: A SQLite database storing:
    *   `subscriptions`: User subscriptions (address, check interval, alert settings).
    *   `user_last_check`: Caches the last address checked by a user for quick access.
4.  **In-Memory Cache**:
    *   `ADDRESS_CACHE`: Stores the last known schedule hash for addresses to detect changes.
    *   `SCHEDULE_DATA_CACHE`: Caches the full schedule data to minimize external API calls during high-frequency alert checks.

## ⚙️ Principle of Operation

The bot operates on an event-driven model with concurrent background tasks:

### 1. User Interaction
Users interact with the bot via commands. When a user requests a schedule (`/check`), the bot:
1.  Parses the address.
2.  Fetches data from the DTEK website (handling sessions and CAPTCHAs).
3.  Returns a formatted message with the current status (Light ON/OFF) and a visual schedule.

### 2. Subscription System (`subscription_checker_task`)
*   Runs continuously in the background.
*   Checks the database for subscriptions due for an update.
*   Fetches the latest schedule.
*   Compares the new schedule hash with the stored hash.
*   If the schedule has changed (e.g., "Grey" zone became "Black"), it sends a notification to the user.

### 3. Alert System (`alert_checker_task`)
*   Runs every **60 seconds**.
*   Iterates through users who have alerts enabled (default 15 min).
*   Checks the cached schedule (`SCHEDULE_DATA_CACHE`) to see if a power change (ON -> OFF or OFF -> ON) is about to happen within the user's specified lead time.
*   Sends a warning message (e.g., "⚠️ Power outage in 15 minutes").
*   Updates the database to prevent duplicate alerts for the same event.

## 🚀 Usage

### Commands

*   `/start` - Initialize the bot.
*   `/help` - Show the list of available commands.
*   `/check [City, Street, House]` - Check the schedule for a specific address.
    *   *Example*: `/check м. Дніпро, вул. Сонячна набережна, 6`
    *   If arguments are omitted, the bot enters interactive mode.
*   `/subscribe [interval]` - Subscribe to updates for the last checked address.
    *   *Example*: `/subscribe 1` (Check every hour).
    *   **Note**: Subscribing automatically enables alerts 15 minutes before outages.
*   `/unsubscribe` - Stop receiving updates.
*   `/alert [minutes]` - Configure the lead time for outage warnings.
    *   *Example*: `/alert 30` (Notify 30 mins before).
    *   *Example*: `/alert 0` (Disable alerts).
*   `/repeat` - Repeat the last `/check` command.
*   `/cancel` - Cancel the current operation.

## 🛠 Installation & Setup

### Prerequisites
*   Docker & Docker Compose
*   A Telegram Bot Token (obtained from [@BotFather](https://t.me/BotFather))

### Deployment via Docker (Recommended)

1.  **Clone the repository**:
    ```bash
    git clone <repository-url>
    cd <repository-folder>
    ```

2.  **Configure Environment**:
    Create a `.env` file in the root directory:
    ```env
    BOT_TOKEN=your_telegram_bot_token_here
    ```

3.  **Run**:
    ```bash
    docker-compose up --build -d
    ```

### Local Development

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Run the Bot**:
    ```bash
    export BOT_TOKEN=your_token
    python dtek_telegram_bot.py
    ```

## 🧪 Testing

The project includes a comprehensive test suite covering handlers, database logic, and parsing utilities.

To run tests:
```bash
./run_tests.sh
```
Or manually using `pytest`:
```bash
python3 -m pytest
```
