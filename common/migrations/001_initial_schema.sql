-- Migration: 001_initial_schema
-- Description: Initial database schema for bot
-- Tables: subscriptions, user_last_check, user_activity

-- Subscriptions table (single subscription per user - legacy schema)
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    interval_hours REAL NOT NULL,
    next_check TIMESTAMP NOT NULL,
    last_schedule_hash TEXT,
    notification_lead_time INTEGER DEFAULT 0,
    last_alert_event_start TIMESTAMP,
    group_name TEXT
);

-- User last check table
CREATE TABLE IF NOT EXISTS user_last_check (
    user_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    last_hash TEXT,
    group_name TEXT
);

-- User activity table
CREATE TABLE IF NOT EXISTS user_activity (
    user_id INTEGER PRIMARY KEY,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    last_city TEXT,
    last_street TEXT,
    last_house TEXT,
    username TEXT,
    last_group TEXT,
    first_name TEXT,
    last_name TEXT
);
