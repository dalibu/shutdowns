# Testing Guide - Group Features

## 🔧 Setup

Before testing:
```bash
# Ensure DB is migrated (migration 007 should auto-apply on bot start)
cd /home/dalibu/Development/python/shutdowns

# Start bot (will apply migration automatically)
# For DTEK:
docker compose up dtek_bot

# For CEK:
docker compose up cek_bot
```

---

## ✅ Test Scenarios

### 1. **Universal `/check` Command**

**Test A: Group Check**
```
/check 3.1
Expected: Shows schedule for group 3.1 instantly from cache
```

**Test B: Address Check**
```
/check м. Дніпро, вул. Сонячна набережна, 6
Expected: Shows schedule for specific address
```

**Test C: Invalid Group**
```
/check 7.1
Expected: Treats as address (searches for "7.1")
```

---

### 2. **Direct Group Subscription**

**Test A: Subscribe to Group**
```
User: /subscribe 3.1
Expected:
✅ Підписка створена!
👥 Черга: 3.1
⏰ Інтервал: 6 год
🔔 Сповіщення: увімкнено (за 15 хв)
💡 Ви отримаєте повідомлення про зміни графіка для цієї черги.
```

**Test B: Subscribe with Custom Interval**
```
User: /subscribe 3.1 12
Expected: Creates subscription with 12h interval
```

**Test C: Subscribe to Group + Have Addresses**
```
1. First: /check м. Дніпро, вул. Сонячна...
2. Then: /subscribe (creates address subscription)
3. Then: /subscribe 3.1
Expected: Shows "У вас також є 1 адреса в цій черзі: ..."
```

**Test D: Already Subscribed**
```
User: /subscribe 3.1 (second time)
Expected: "Ви вже підписані на чергу 3.1"
```

---

### 3. **Unsubscribe with Groups**

**Test A: Single Address Subscription**
```
User: /unsubscribe
Expected: Immediately unsubscribes without keyboard
```

**Test B: Single Group Subscription**
```
User: /unsubscribe
Expected: "🚫 Підписку скасовано для черги: 3.1"
```

**Test C: Multiple Subscriptions**
```
User: /unsubscribe
Expected: Keyboard with:
[👥 Черга 3.1]
[📍 м. Дніпро, вул. Сонячна...]
[📍 м. Дніпро, вул. Робоча...]
[🗑️ Відписатися від усіх]
```

**Test D: Click Group in Keyboard**
```
User clicks: [👥 Черга 3.1]
Expected: "🚫 Підписку скасовано для черги: 3.1"
          "Залишилось підписок: X"
```

**Test E: Click "Delete ALL"**
```
User clicks: [🗑️ Відписатися від усіх]
Expected: "🗑️ Всі підписки скасовано (X шт.)"
```

---

### 4. **Grouped Notifications**

**Test A: Multiple Addresses Same Group**
```
Setup:
1. /check м. Дніпро, вул. Сонячна... (group 3.1)
2. /subscribe
3. /check м. Дніпро, вул. Робоча... (group 3.1)
4. /subscribe

Wait for schedule change...

Expected: ONE notification with:
👥 Черга: 3.1
📍 Ваші адреси в цій черзі:
   • м. Дніпро, вул. Сонячна...
   • м. Дніпро, вул. Робоча...
[график]
```

**Test B: Group Subscription Only**
```
Setup:
1. /subscribe 3.1

Wait for schedule change...

Expected: ONE notification with:
👥 Черга: 3.1
[график]
(no address list)
```

**Test C: Group + Addresses**
```
Setup:
1. /check м. Дніпро, вул. Сонячна... (group 3.1)
2. /subscribe
3. /subscribe 3.1

Wait for schedule change...

Expected: ONE notification with address list
(not TWO notifications!)
```

---

### 5. **Alert Notifications**

**Test: Multiple Addresses Same Group**
```
Setup:
1. Subscribe to 2+ addresses in same group
2. Enable alerts (/alert 15)
3. Wait for event within lead time

Expected: ONE alert with:
⚠️ Увага! Через X хв у XX:XX очікується включення/відключення світла.

👥 Черга: 3.1
📍 Ваші адреси:
   • ...
   • ...
```

---

## 🐛 Known Potential Issues

### Database
- Migration 007 must be applied (auto on bot start)
- If old DB without migration: message "no such table: group_subscriptions"

### Provider Code
- `ctx.provider_code` must be set ('dtek' or 'cek')
- If missing: group subscriptions won't be fetched in /unsubscribe

### Edge Cases to Watch
1. **Empty addresses table for group**: Should show error gracefully
2. **NULL group_name**: Should work with "unknown_X" groups
3. **Multiple providers**: User can have DTEK and CEK subs separately

---

## 🔍 Debug Commands

**Check Database Manually:**
```bash
sqlite3 data/dtek_bot.db

# Check migration status
SELECT * FROM schema_version ORDER BY version;

# Check group subscriptions
SELECT * FROM group_subscriptions;

# Check address subscriptions with groups
SELECT s.id, s.user_id, a.group_name, a.city, a.street 
FROM subscriptions s 
JOIN addresses a ON a.id = s.address_id;
```

**Check Logs:**
```bash
# Real-time logs
docker compose logs -f dtek_bot

# Look for:
# - "Group subscription created"
# - "Unsubscribed from group"
# - "Checking X unique groups for Y user-group combinations"
# - "Notification sent for group X.X"
```

---

## ✅ Success Criteria

All features working if:
- ✅ `/check 3.1` works instantly
- ✅ `/subscribe 3.1` creates group subscription
- ✅ `/unsubscribe` shows both types with correct icons
- ✅ Only ONE notification for multiple addresses in same group
- ✅ Only ONE alert for multiple addresses in same group
- ✅ No errors in logs

---

## 📞 If Issues Found

Please note:
1. **What command** was used
2. **Expected** vs **Actual** result
3. **Error message** (if any)
4. **Logs** from docker compose logs

Good luck with testing! 🚀
