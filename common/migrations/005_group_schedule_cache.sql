-- Migration: 005_group_schedule_cache
-- Description: Add group schedule cache and address-to-group mapping
-- This migration enables two key features:
-- 1. Caching schedule data by group instead of individual addresses (reduces provider load)
-- 2. Building a database of address-to-group mappings for future group-based search

-- ============================================================
-- 1. Group Schedule Cache Table
-- ============================================================
-- Stores the latest schedule data for each group
-- This is the "single source of truth" for group schedules
CREATE TABLE IF NOT EXISTS group_schedule_cache (
    group_name TEXT NOT NULL,
    provider TEXT NOT NULL,  -- 'dtek' or 'cek'
    last_schedule_hash TEXT NOT NULL,
    schedule_data TEXT,  -- JSON string of full schedule data
    last_updated TIMESTAMP NOT NULL,
    PRIMARY KEY (group_name, provider)  -- Composite key: same group can exist in different providers
);

-- Index for quick lookup by provider and freshness
CREATE INDEX IF NOT EXISTS idx_group_cache_provider_updated 
ON group_schedule_cache(provider, last_updated);

-- ============================================================
-- 2. Address-to-Group Mapping Table
-- ============================================================
-- Accumulates knowledge of which addresses belong to which groups
-- This grows over time as users check different addresses
-- Enables future feature: search by group number without knowing address
CREATE TABLE IF NOT EXISTS address_group_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,  -- 'dtek' or 'cek'
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    house TEXT NOT NULL,
    group_name TEXT NOT NULL,
    first_seen TIMESTAMP NOT NULL,  -- When we first learned about this mapping
    last_verified TIMESTAMP NOT NULL,  -- Last time we confirmed this mapping
    verification_count INTEGER DEFAULT 1,  -- How many times we've seen this mapping
    UNIQUE(provider, city, street, house)
);

-- Index for searching addresses by group (for future "find addresses in my group" feature)
CREATE INDEX IF NOT EXISTS idx_address_group_by_group 
ON address_group_mapping(provider, group_name);

-- Index for quick lookup when verifying/updating existing mappings
CREATE INDEX IF NOT EXISTS idx_address_group_lookup 
ON address_group_mapping(provider, city, street, house);
