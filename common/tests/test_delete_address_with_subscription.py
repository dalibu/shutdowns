"""
Test: Deleting address should also delete subscription
"""
import pytest
import aiosqlite
from common.bot_base import delete_user_address


@pytest.mark.asyncio
async def test_delete_address_also_deletes_subscription():
    """
    When user deletes an address from their address book,
    any subscription for that address should also be deleted.
    """
    # Setup in-memory database
    async with aiosqlite.connect(":memory:") as db:
        # Create tables
        await db.execute("""
            CREATE TABLE user_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL
            )
        """)
        
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
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address_id INTEGER NOT NULL,
                interval_hours REAL NOT NULL,
                next_check TIMESTAMP NOT NULL,
                last_schedule_hash TEXT,
                notification_lead_time INTEGER DEFAULT 0
            )
        """)
        
        await db.commit()
        
        user_id = 12345
        
        # Insert test address into addresses table
        await db.execute(
            "INSERT INTO addresses (id, provider, city, street, house, group_name) VALUES (?, ?, ?, ?, ?, ?)",
            (100, "dtek", "м. Дніпро", "вул. Сонячна", "6", "3.1")
        )
        
        # Insert test address into user_addresses
        await db.execute(
            "INSERT INTO user_addresses (id, user_id, city, street, house) VALUES (?, ?, ?, ?, ?)",
            (1, user_id, "м. Дніпро", "вул. Сонячна", "6")
        )
        
        # Insert subscription for this address
        await db.execute(
            "INSERT INTO subscriptions (user_id, address_id, interval_hours, next_check) VALUES (?, ?, ?, datetime('now'))",
            (user_id, 1, 6.0)
        )
        
        await db.commit()
        
        # Verify subscription exists
        async with db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND address_id = ?",
            (user_id, 1)
        ) as cursor:
            count_before = (await cursor.fetchone())[0]
        
        assert count_before == 1, "Subscription should exist before deletion"
        
        # Delete address (this should also delete subscription)
        success = await delete_user_address(db, user_id, 1)
        
        assert success, "Address deletion should succeed"
        
        # Verify address is deleted from user_addresses
        async with db.execute(
            "SELECT COUNT(*) FROM user_addresses WHERE id = ? AND user_id = ?",
            (1, user_id)
        ) as cursor:
            address_count = (await cursor.fetchone())[0]
        
        assert address_count == 0, "Address should be deleted"
        
        # Verify subscription is also deleted
        async with db.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND address_id = ?",
            (user_id, 1)
        ) as cursor:
            subscription_count = (await cursor.fetchone())[0]
        
        assert subscription_count == 0, "Subscription should also be deleted"


@pytest.mark.asyncio
async def test_delete_address_without_subscription():
    """
    Deleting an address without a subscription should work fine.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create tables
        await db.execute("""
            CREATE TABLE user_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL
            )
        """)
        
        await db.execute("""
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address_id INTEGER NOT NULL,
                interval_hours REAL NOT NULL,
                next_check TIMESTAMP NOT NULL
            )
        """)
        
        await db.commit()
        
        user_id = 12345
        
        # Insert test address (no subscription)
        await db.execute(
            "INSERT INTO user_addresses (id, user_id, city, street, house) VALUES (?, ?, ?, ?, ?)",
            (1, user_id, "м. Дніпро", "вул. Сонячна", "6")
        )
        
        await db.commit()
        
        # Delete address
        success = await delete_user_address(db, user_id, 1)
        
        assert success, "Address deletion should succeed even without subscription"
        
        # Verify address is deleted
        async with db.execute(
            "SELECT COUNT(*) FROM user_addresses WHERE id = ? AND user_id = ?",
            (1, user_id)
        ) as cursor:
            count = (await cursor.fetchone())[0]
        
        assert count == 0, "Address should be deleted"
