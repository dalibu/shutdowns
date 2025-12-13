"""
Tests for /subscribe command after migration 006 (normalized database).

This test suite verifies that /subscribe command works correctly with the 
new database structure where user_last_check has address_id instead of 
direct city/street/house columns.

Bug fix: After migration 006, /subscribe failed with "no such column: city"
because it tried to SELECT city, street, house directly from user_last_check.
"""

import pytest
import aiosqlite
from datetime import datetime, timedelta
import pytz


@pytest.mark.asyncio
async def test_subscribe_after_migration_006():
    """
    Test that /subscribe works with normalized database structure.
    
    This test simulates the database state after migration 006 where:
    - addresses table contains city, street, house, group_name
    - user_last_check contains only (user_id, address_id, last_hash)
    
    Bug: Old code tried to SELECT city, street, house FROM user_last_check
    Fix: Now does JOIN with addresses table
    """
    # Create in-memory database with migration 006 structure
    async with aiosqlite.connect(":memory:") as db:
        # Create addresses table (migration 006)
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL,
                group_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (provider, city, street, house)
            )
        """)
        
        # Create user_last_check table (migration 006 structure)
        await db.execute("""
            CREATE TABLE user_last_check (
                user_id INTEGER PRIMARY KEY,
                address_id INTEGER NOT NULL,
                last_hash TEXT,
                FOREIGN KEY (address_id) REFERENCES addresses (id) ON DELETE CASCADE
            )
        """)
        
        # Create subscriptions table
        await db.execute("""
            CREATE TABLE subscriptions (
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
            )
        """)
        
        await db.commit()
        
        # Insert test data
        test_user_id = 123456
        test_provider = "dtek"
        test_city = "м. Дніпро"
        test_street = "вул. Сонячна набережна"
        test_house = "6"
        test_group = "3.1"
        test_hash = "test_hash_123"
        
        # Insert address
        cursor = await db.execute("""
            INSERT INTO addresses (provider, city, street, house, group_name)
            VALUES (?, ?, ?, ?, ?)
        """, (test_provider, test_city, test_street, test_house, test_group))
        address_id = cursor.lastrowid
        await db.commit()
        
        # Insert user_last_check (as /check would do)
        await db.execute("""
            INSERT INTO user_last_check (user_id, address_id, last_hash)
            VALUES (?, ?, ?)
        """, (test_user_id, address_id, test_hash))
        await db.commit()
        
        # Test: Fetch data like /subscribe does (with JOIN)
        cursor = await db.execute("""
            SELECT a.city, a.street, a.house, ulc.last_hash 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (test_user_id,))
        row = await cursor.fetchone()
        
        # Verify that JOIN works correctly
        assert row is not None, "Should find user_last_check entry"
        city, street, house, hash_value = row
        
        assert city == test_city
        assert street == test_street
        assert house == test_house
        assert hash_value == test_hash
        
        # Test: Create subscription using the data
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)
        next_check = now + timedelta(hours=6)
        
        await db.execute("""
            INSERT INTO subscriptions 
            (user_id, address_id, interval_hours, next_check, last_schedule_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (test_user_id, address_id, 6.0, next_check, test_hash))
        await db.commit()
        
        # Verify subscription was created
        cursor = await db.execute("""
            SELECT s.user_id, a.city, a.street, a.house, a.group_name, s.interval_hours
            FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.user_id = ?
        """, (test_user_id,))
        sub_row = await cursor.fetchone()
        
        assert sub_row is not None
        assert sub_row[0] == test_user_id
        assert sub_row[1] == test_city
        assert sub_row[2] == test_street
        assert sub_row[3] == test_house
        assert sub_row[4] == test_group
        assert sub_row[5] == 6.0


@pytest.mark.asyncio
async def test_subscribe_no_last_check():
    """
    Test that /subscribe handles missing user_last_check gracefully.
    
    If user hasn't done /check yet, there's no user_last_check entry,
    and the JOIN should return no rows.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create tables
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL,
                group_name TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE user_last_check (
                user_id INTEGER PRIMARY KEY,
                address_id INTEGER NOT NULL,
                last_hash TEXT
            )
        """)
        
        await db.commit()
        
        # Try to fetch for user that hasn't done /check
        test_user_id = 999999
        
        cursor = await db.execute("""
            SELECT a.city, a.street, a.house, ulc.last_hash 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (test_user_id,))
        row = await cursor.fetchone()
        
        # Should return None (no row found)
        assert row is None, "Should return None when user_last_check is empty"


@pytest.mark.asyncio
async def test_old_query_would_fail():
    """
    Verify that the OLD query (without JOIN) would indeed fail.
    
    This test documents the bug that was fixed.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create migration 006 structure
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL,
                group_name TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE user_last_check (
                user_id INTEGER PRIMARY KEY,
                address_id INTEGER NOT NULL,
                last_hash TEXT
            )
        """)
        
        await db.commit()
        
        # Insert test data
        cursor = await db.execute("""
            INSERT INTO addresses (provider, city, street, house, group_name)
            VALUES ('dtek', 'Kyiv', 'Main St', '1', '3.1')
        """)
        address_id = cursor.lastrowid
        
        await db.execute("""
            INSERT INTO user_last_check (user_id, address_id, last_hash)
            VALUES (123, ?, 'hash123')
        """, (address_id,))
        await db.commit()
        
        # OLD query (without JOIN) - this should FAIL
        with pytest.raises(aiosqlite.OperationalError) as exc_info:
            cursor = await db.execute(
                "SELECT city, street, house, last_hash FROM user_last_check WHERE user_id = ?",
                (123,)
            )
            await cursor.fetchone()
        
        # Verify it's the expected error
        assert "no such column: city" in str(exc_info.value).lower()
        
        # NEW query (with JOIN) - this should SUCCEED
        cursor = await db.execute("""
            SELECT a.city, a.street, a.house, ulc.last_hash 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (123,))
        row = await cursor.fetchone()
        
        assert row is not None
        assert row[0] == "Kyiv"
        assert row[1] == "Main St"
        assert row[2] == "1"
        assert row[3] == "hash123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
