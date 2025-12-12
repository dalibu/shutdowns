-- Migration: 007_add_group_subscriptions
-- Description: Add support for direct group subscriptions without specific address
-- Allows users to subscribe to a group (e.g., "3.1") without specifying an address

-- Create group_subscriptions table
CREATE TABLE IF NOT EXISTS group_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL, -- 'dtek' or 'cek'
    group_name TEXT NOT NULL, -- e.g., "3.1", "4.2"
    interval_hours REAL NOT NULL,
    next_check TIMESTAMP NOT NULL,
    last_schedule_hash TEXT,
    notification_lead_time INTEGER DEFAULT 0,
    last_alert_event_start TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, provider, group_name),
    CHECK (
        group_name IS NOT NULL
        AND group_name != ''
    )
);

CREATE INDEX IF NOT EXISTS idx_group_subscriptions_user_id ON group_subscriptions (user_id);

CREATE INDEX IF NOT EXISTS idx_group_subscriptions_provider_group ON group_subscriptions (provider, group_name);

CREATE INDEX IF NOT EXISTS idx_group_subscriptions_next_check ON group_subscriptions (next_check);