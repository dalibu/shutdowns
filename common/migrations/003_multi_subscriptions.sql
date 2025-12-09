-- Migration: 003_multi_subscriptions
-- Description: Migrate subscriptions to support multiple subscriptions per user
-- Changes: Add id column as PK, add UNIQUE constraint on (user_id, city, street, house)

-- Step 1: Create new table with correct schema
CREATE TABLE IF NOT EXISTS subscriptions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    interval_hours REAL NOT NULL,
    next_check TIMESTAMP NOT NULL,
    last_schedule_hash TEXT,
    notification_lead_time INTEGER DEFAULT 0,
    last_alert_event_start TIMESTAMP,
    group_name TEXT,
    UNIQUE(user_id, city, street, house)
);

-- Step 2: Copy data from old table (if exists and has data)
INSERT OR IGNORE INTO subscriptions_new (user_id, city, street, house, interval_hours, next_check, 
                                         last_schedule_hash, notification_lead_time, last_alert_event_start, group_name)
SELECT user_id, city, street, house, interval_hours, next_check, 
       last_schedule_hash, notification_lead_time, last_alert_event_start, group_name
FROM subscriptions WHERE 1=1;

-- Step 3: Drop old table
DROP TABLE IF EXISTS subscriptions;

-- Step 4: Rename new table
ALTER TABLE subscriptions_new RENAME TO subscriptions;

-- Step 5: Create index
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
