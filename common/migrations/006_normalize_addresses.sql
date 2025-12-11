-- Migration: 006_normalize_addresses
-- Description: Database normalization - create central addresses table and refactor all tables to use address_id
-- This eliminates redundant storage of (city, street, house) data across multiple tables

-- ============================================================
-- Step 1: Create central addresses table
-- ============================================================
CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL, -- 'dtek' or 'cek'
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    group_name TEXT, -- Cached group from address_group_mapping
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, city, street, house)
);

CREATE INDEX IF NOT EXISTS idx_addresses_provider ON addresses (provider);

CREATE INDEX IF NOT EXISTS idx_addresses_group ON addresses (provider, group_name);

-- ============================================================
-- Step 2: Migrate data from existing tables to addresses
-- ============================================================

-- First, populate from address_group_mapping (most complete data with verification)
INSERT OR IGNORE INTO
    addresses (
        provider,
        city,
        street,
        house,
        group_name,
        created_at,
        updated_at
    )
SELECT
    provider,
    city,
    street,
    house,
    group_name,
    first_seen,
    last_verified
FROM address_group_mapping;

-- Then add from subscriptions (if not already in addresses)
-- Use provider from address_group_mapping if exists, otherwise use a placeholder
INSERT OR IGNORE INTO
    addresses (
        provider,
        city,
        street,
        house,
        group_name
    )
SELECT DISTINCT
    COALESCE(
        (
            SELECT provider
            FROM address_group_mapping
            LIMIT 1
        ),
        'unknown'
    ) as provider,
    city,
    street,
    house,
    group_name
FROM subscriptions;

-- Add from user_last_check
INSERT OR IGNORE INTO
    addresses (
        provider,
        city,
        street,
        house,
        group_name
    )
SELECT DISTINCT
    COALESCE(
        (
            SELECT provider
            FROM address_group_mapping
            LIMIT 1
        ),
        'unknown'
    ) as provider,
    city,
    street,
    house,
    group_name
FROM user_last_check;

-- Add from user_addresses
INSERT OR IGNORE INTO
    addresses (
        provider,
        city,
        street,
        house,
        group_name
    )
SELECT DISTINCT
    COALESCE(
        (
            SELECT provider
            FROM address_group_mapping
            LIMIT 1
        ),
        'unknown'
    ) as provider,
    city,
    street,
    house,
    group_name
FROM user_addresses;

-- ============================================================
-- Step 3: Create new normalized tables
-- ============================================================

-- 3.1: New subscriptions table
CREATE TABLE IF NOT EXISTS subscriptions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    address_id INTEGER NOT NULL,
    interval_hours REAL NOT NULL,
    next_check TIMESTAMP NOT NULL,
    last_schedule_hash TEXT,
    notification_lead_time INTEGER DEFAULT 0,
    last_alert_event_start TIMESTAMP,
    UNIQUE (user_id, address_id),
    FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_new_user_id ON subscriptions_new (user_id);

CREATE INDEX IF NOT EXISTS idx_subscriptions_new_address_id ON subscriptions_new (address_id);

-- 3.2: New user_last_check table
CREATE TABLE IF NOT EXISTS user_last_check_new (
    user_id INTEGER PRIMARY KEY,
    address_id INTEGER NOT NULL,
    last_hash TEXT,
    FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_last_check_new_address_id ON user_last_check_new (address_id);

-- 3.3: New user_addresses table (address book)
CREATE TABLE IF NOT EXISTS user_addresses_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    address_id INTEGER NOT NULL,
    alias TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, address_id),
    FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_addresses_new_user_id ON user_addresses_new (user_id);

CREATE INDEX IF NOT EXISTS idx_user_addresses_new_address_id ON user_addresses_new (address_id);

-- ============================================================
-- Step 4: Migrate data to new tables
-- ============================================================

-- 4.1: Migrate subscriptions
INSERT INTO
    subscriptions_new (
        id,
        user_id,
        address_id,
        interval_hours,
        next_check,
        last_schedule_hash,
        notification_lead_time,
        last_alert_event_start
    )
SELECT s.id, s.user_id, a.id as address_id, s.interval_hours, s.next_check, s.last_schedule_hash, s.notification_lead_time, s.last_alert_event_start
FROM
    subscriptions s
    JOIN addresses a ON a.city = s.city
    AND a.street = s.street
    AND a.house = s.house;

-- 4.2: Migrate user_last_check
INSERT INTO
    user_last_check_new (
        user_id,
        address_id,
        last_hash
    )
SELECT ulc.user_id, a.id as address_id, ulc.last_hash
FROM
    user_last_check ulc
    JOIN addresses a ON a.city = ulc.city
    AND a.street = ulc.street
    AND a.house = ulc.house;

-- 4.3: Migrate user_addresses
INSERT INTO
    user_addresses_new (
        id,
        user_id,
        address_id,
        alias,
        created_at,
        last_used_at
    )
SELECT ua.id, ua.user_id, a.id as address_id, ua.alias, ua.created_at, ua.last_used_at
FROM
    user_addresses ua
    JOIN addresses a ON a.city = ua.city
    AND a.street = ua.street
    AND a.house = ua.house;

-- ============================================================
-- Step 5: Replace old tables with new ones
-- ============================================================

DROP TABLE IF EXISTS subscriptions;

ALTER TABLE subscriptions_new RENAME TO subscriptions;

DROP TABLE IF EXISTS user_last_check;

ALTER TABLE user_last_check_new RENAME TO user_last_check;

DROP TABLE IF EXISTS user_addresses;

ALTER TABLE user_addresses_new RENAME TO user_addresses;

-- ============================================================
-- Step 6: Drop address_group_mapping (now redundant)
-- ============================================================
-- The addresses table now serves as the single source of truth for address data
DROP TABLE IF EXISTS address_group_mapping;

-- ============================================================
-- Step 7: Update group_schedule_cache to reference addresses (optional)
-- ============================================================
-- Keep group_schedule_cache as is - it's indexed by group_name, not addresses
-- This is correct as multiple addresses belong to one group