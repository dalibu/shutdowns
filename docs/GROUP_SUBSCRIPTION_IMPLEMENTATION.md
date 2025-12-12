# Direct Group Subscription - Implementation Plan

## Status: Backend Ready ✅ | Frontend Pending ⏳

### What's Done:

✅ **Backend Infrastructure (commit e3a9685)**
- `group_subscriptions` table created (migration 007)
- `subscription_checker_task` supports both address and group subscriptions
- Automatic merging of address+group subscriptions for same user+group
- Smart DB updates for both types
- Sample address lookup for group-only API calls

### What's Needed:

#### 1. Command Handler `/subscribe <GROUP>`

Modify `handle_subscribe_command` in `/common/handlers.py`:

```python
async def handle_subscribe_command(...):
    # At the beginning, detect input type
    text_args = message.text.replace('/subscribe', '', 1).strip()
    
    # If text_args looks like a group (e.g., "3.1"), handle group subscription
    input_type, value = detect_check_input_type(text_args)
    
    if input_type == "group":
        # Group subscription flow
        await handle_group_subscription(message, value, ctx)
        return
    
    # Otherwise, continue with existing address subscription logic...
```

#### 2. New Helper: `handle_group_subscription`

```python
async def handle_group_subscription(
    message: types.Message,
    group_name: str,
    ctx: BotContext
):
    """Handle direct group subscription."""
    user_id = message.from_user.id
    db_conn = ctx.db_conn
    provider_code = ctx.provider_code
    
    # Check if already subscribed
    cursor = await db_conn.execute("""
        SELECT interval_hours 
        FROM group_subscriptions
        WHERE user_id = ? AND provider = ? AND group_name = ?
    """, (user_id, provider_code, group_name))
    
    existing = await cursor.fetchone()
    
    if existing:
        await message.answer(f"✅ Ви вже підписані на чергу {group_name}")
        return
    
    # Check if user has address subscriptions in this group
    cursor = await db_conn.execute("""
        SELECT COUNT(*), GROUP_CONCAT(a.city || ', ' || a.street || ', ' || a.house, '\n• ')
        FROM subscriptions s
        JOIN addresses a ON a.id = s.address_id
        WHERE s.user_id = ? AND a.provider = ? AND a.group_name = ?
    """, (user_id, provider_code, group_name))
    
    row = await cursor.fetchone()
    addr_count, addr_list = row if row else (0, None)
    
    # Create group subscription
    import pytz
    kiev_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kiev_tz)
    next_check = now + timedelta(hours=DEFAULT_INTERVAL_HOURS)
    
    await db_conn.execute("""
        INSERT INTO group_subscriptions 
        (user_id, provider, group_name, interval_hours, next_check, last_schedule_hash)
        VALUES (?, ?, ?, ?, ?, 'NO_SCHEDULE_FOUND_AT_SUBSCRIPTION')
    """, (user_id, provider_code, group_name, DEFAULT_INTERVAL_HOURS, next_check))
    
    await db_conn.commit()
    
    # Build response
    msg_parts = [
        f"✅ **Підписка створена!**",
        f"👥 Черга: `{group_name}`",
        f"⏰ Інтервал перевірки: {DEFAULT_INTERVAL_HOURS} год"
    ]
    
    if addr_count > 0:
        msg_parts.append(f"\n📍 У вас також є {addr_count} адреси в цій черзі:\n• {addr_list}")
        msg_parts.append("\n💡 Ви отримаєте **одне** повідомлення зі списком всіх адрес.")
    else:
        msg_parts.append("\n💡 Ви отримаєте повідомлення про зміни графіка для цієї черги.")
    
    await message.answer("\n".join(msg_parts), parse_mode="Markdown")
```

#### 3. Update `/mysubscriptions` Command

Show both types of subscriptions:

```python
# Address subscriptions
👥 Черга 3.1:
  📍 м. Дніпро, вул. Сонячна набережна, 6
  📍 м. Дніпро, вул. Робоча, 15
  
# Group-only subscriptions  
👥 Черга 4.2 (вся черга)
```

#### 4. Update `/unsubscribe` Command

Allow unsubscribing from:
- Specific addresses
- Entire group
- All subscriptions

### Testing Scenarios:

1. **Pure group subscription**
   - `/subscribe 3.1` → creates group_subscription
   - Gets notifications without address list

2. **Group + addresses**
   - User has addr1, addr2 in group 3.1
   - `/subscribe 3.1` → creates group_subscription
   - Gets ONE notification with address list

3. **Addresses → Group**
   - User subscribes to addr1 (3.1)
   - Then `/subscribe 3.1`
   - Should show "You already have 1 address in this group"

### Database Schema:

```sql
-- group_subscriptions table (already created in migration 007)
CREATE TABLE group_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    group_name TEXT NOT NULL,
    interval_hours REAL NOT NULL,
    next_check TIMESTAMP NOT NULL,
    last_schedule_hash TEXT,
    notification_lead_time INTEGER DEFAULT 0,
    last_alert_event_start TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, provider, group_name)
);
```

### Benefits:

- ✅ No duplicates (handled by backend)
- ✅ Flexible (can subscribe to group OR addresses OR both)
- ✅ Efficient (one API call per group)
- ✅ User-friendly (clear messaging about combined subscriptions)

### Token Budget Used:

~100k tokens used in session. Implementation complete for:
- ✅ Backend logic (all working)
- ⏳ Command handlers (plan documented, implementation pending)

Estimated work remaining: ~2-3 hours to implement command handlers and test.
