# Current Outage Detection - Testing Guide

## Feature Overview

The parser now detects and handles active power outages at addresses (displayed via `showCurOutage` element on DTEK website).

## What Was Changed

### Parser Logic (`dtek/parser/dtek_parser.py`)

**Order of operations:**
1. Enter address (city, street, house)
2. Wait 2 seconds for results to load
3. **First** check for `div#showCurOutage.active` element
4. If found → Extract outage details and return immediately
5. If not found → Check for schedule table
6. If neither exists → Throw error

**Extracted information:**
- `reason` - Причина відключення (e.g., "Аварійні ремонтні роботи")
- `start_time` - Час початку
- `expected_restoration` - Орієнтовний час відновлення
- `update_time` - Дата оновлення інформації

### Bot Response (`common/handlers.py`)

When `current_outage` data is present, the bot displays:

```
🏠 Адреса: `м. Дніпро, вул. Скіфська, 20`
👥 Черга: `1.6`

⚡ **За вашою адресою зараз відсутня електроенергія**

🔧 **Причина:** Аварійні ремонтні роботи
⏰ **Час початку:** 10:28 11.12.2025
🔋 **Очікуваний час відновлення:** до 17:28 11.12.2025

📅 _Дата оновлення: 16:36 11.12.2025_
```

## Testing

### Test Address with Current Outage

Use this address in the bot:
```
/check м. Дніпро, вул. Скіфська, 20
```

**Expected result:**
- Bot should display outage information (not schedule table)
- Message should include reason, start time, expected restoration
- No errors in logs

### Test Normal Address (No Outage)

Use a normal address:
```
/check м. Дніпро, вул. Сонячна набережна, 6
```

**Expected result:**
- Bot should display schedule table as usual
- 48-hour diagram if there are shutdowns tomorrow
- No changes to existing functionality

## Debugging

### Check Parser Logs

```bash
docker-compose -f dtek/bot/docker-compose.yml logs -f dtek_bot
```

**Look for:**
- `INFO:dtek.parser.dtek_parser:Обнаружено текущее отключение (showCurOutage)` - Outage detected
- No "Таблица результатов не появилась" errors for addresses with outages

### Manual Parser Test

You can test the parser directly:

```bash
# Inside the running container
docker exec -it dtek_bot python -c "
import asyncio
from dtek.parser.dtek_parser import run_parser_service

async def test():
    result = await run_parser_service('м. Дніпро', 'вул. Скіфська', '20')
    print(result)

asyncio.run(test())
"
```

### Check Screenshot on Error

If parser fails, check the error screenshot:
```bash
docker exec -it dtek_bot ls -la /app/error_logs/
```

## Implementation Details

### Regex Patterns

The parser uses these patterns to extract information from text:

```python
reason_match = re.search(r'Причина:\s*(.+?)(?:\n|$)', outage_text)
start_match = re.search(r'Час початку\s*–\s*(.+?)(?:\n|$)', outage_text)
restoration_match = re.search(r'Орієнтовний час відновлення електроенергії\s*–\s*(.+?)(?:\n|$)', outage_text)
update_match = re.search(r'Дата оновлення інформації\s*–\s*(.+?)(?:\n|$)', outage_text)
```

### Return Structure

When outage is detected, parser returns:

```python
{
    "data": {
        "city": "м. Дніпро",
        "street": "вул. Скіфська",
        "house_num": "20",
        "group": "1.6",  # May be empty
        "current_outage": {
            "has_current_outage": True,
            "message": "Full HTML text...",
            "reason": "Аварійні ремонтні роботи",
            "start_time": "10:28 11.12.2025",
            "expected_restoration": "до 17:28 11.12.2025",
            "update_time": "16:36 11.12.2025"
        },
        "schedule": {}  # Empty when there's an outage
    }
}
```

## Notes

- The check happens **before** waiting for schedule table (prevents false errors)
- If both `showCurOutage` and schedule table are missing → error is thrown
- Outage information takes precedence over schedule when both are present
- Subscription suggestions are still shown to non-subscribed users
